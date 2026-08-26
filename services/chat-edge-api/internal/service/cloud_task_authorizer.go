// HTTP authorizer for cloud scheduled runs (FIX13).
//
// This is the concrete RunAuthorizer: it asks the backend's
// internal-secret-gated /internal/scheduled-run/authorize endpoint whether
// an unattended run may proceed for (tenant, user). The backend is the
// source of truth for portal_users.status (revoked) and the credit balance
// (no_balance); chat-edge (Go) never reads those tables directly.
//
// Security:
//   - Sends X-Internal-Service-Secret so the backend trusts the call, plus a
//     bearer (a service JWT) supplied by the injected bearerProvider — the
//     same trust anchors the GatewayClient uses.
//   - Fails CLOSED: any transport/parse error, or a non-Allow body, yields
//     {Allow:false}. The runner treats an authorizer error as "do not run".
//   - The request/response carry ONLY ids + an allow/reason enum — never a
//     token, balance figure, or PII.
package service

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
)

// HTTPRunAuthorizer implements RunAuthorizer against the backend endpoint.
type HTTPRunAuthorizer struct {
	baseURL        string
	internalSecret string
	// bearerProvider mints the outbound bearer (service JWT). Injected so the
	// signing detail stays with the caller that already owns it. May be nil
	// if the backend accepts internal-secret-only (defence-in-depth prefers
	// both).
	bearerProvider func() (string, error)
	client         *http.Client
}

func NewHTTPRunAuthorizer(baseURL, internalSecret string, bearerProvider func() (string, error)) *HTTPRunAuthorizer {
	return &HTTPRunAuthorizer{
		baseURL:        strings.TrimRight(baseURL, "/"),
		internalSecret: internalSecret,
		bearerProvider: bearerProvider,
		client:         &http.Client{Timeout: 10 * time.Second},
	}
}

type authorizeRequestBody struct {
	UserID   string `json:"user_id"`
	TenantID string `json:"tenant_id"`
}

type authorizeResponseBody struct {
	Allow  bool   `json:"allow"`
	Reason string `json:"reason"`
}

// Authorize asks the backend. Fail-closed on any error.
func (a *HTTPRunAuthorizer) Authorize(ctx context.Context, tenantID, userID uuid.UUID) (AuthzResult, error) {
	body, _ := json.Marshal(authorizeRequestBody{
		UserID:   userID.String(),
		TenantID: tenantID.String(),
	})
	url := a.baseURL + "/internal/scheduled-run/authorize"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return AuthzResult{Allow: false}, err
	}
	req.Header.Set("Content-Type", "application/json")
	if a.internalSecret != "" {
		req.Header.Set("X-Internal-Service-Secret", a.internalSecret)
	}
	if a.bearerProvider != nil {
		bearer, err := a.bearerProvider()
		if err != nil {
			return AuthzResult{Allow: false}, err
		}
		req.Header.Set("Authorization", "Bearer "+bearer)
	}

	resp, err := a.client.Do(req)
	if err != nil {
		return AuthzResult{Allow: false}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return AuthzResult{Allow: false}, fmt.Errorf("authorize: unexpected status %d", resp.StatusCode)
	}
	var out authorizeResponseBody
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return AuthzResult{Allow: false}, err
	}
	return AuthzResult{Allow: out.Allow, Reason: out.Reason}, nil
}

// HTTP middleware that resolves the Portal access JWT into the
// request context.
//
// Phase 1.5 migration: chat-edge no longer mints its own user JWTs.
// The Portal (FastAPI “services/portal/auth_service.py“) is the
// sole identity authority. Every protected route reads a Bearer
// token signed with “settings.portal_jwt_secret“; the middleware
// verifies the signature, expiry, and “type=="portal"“ marker,
// then upserts a shadow row in chat-edge's identity tables so chat
// FKs hold. Subsequent requests in the same process hit a sync.Map
// cache and skip the upsert.
package middleware

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// Caller is the resolved-user view downstream handlers see.
//
//   - UserID         — same UUID as the Portal's “sub“ claim.
//   - TenantID       — Portal tenant (or auto-derived personal tenant).
//   - Role           — kept for backward compatibility with the older
//     chat-edge code path; populated from
//     “tenant_role“ so handlers gating "owner-only"
//     actions can keep working.
//   - Email          — convenience for log lines + display defaults.
type Caller struct {
	UserID     uuid.UUID
	TenantID   string
	TenantUUID uuid.UUID
	Role       string
	Email      string
}

type ctxKey int

const callerKey ctxKey = 1

// CallerFrom pulls the resolved caller from a request context.
// Returns (zero, false) on a route that wasn't behind RequireAuth.
func CallerFrom(ctx context.Context) (Caller, bool) {
	c, ok := ctx.Value(callerKey).(Caller)
	return c, ok
}

// RequireAuth produces middleware that:
//
//  1. Parses Portal JWT from “Authorization: Bearer <jwt>“ (or
//     the “portal_access_token“ cookie for browser navigations).
//  2. Ensures a matching shadow row exists in identity.users /
//     identity.tenants (idempotent upsert).
//  3. Stuffs a Caller into the request context.
//
// “portalSecret“ MUST match FastAPI “portal_jwt_secret“.
// “provision“ is the ProvisionService instance from main.go.
func RequireAuth(
	portalSecret string,
	provision *service.ProvisionService,
) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			raw, err := extractToken(r)
			if err != nil {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			parsed, err := auth.ParsePortalToken(portalSecret, raw)
			if err != nil {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}

			tenantUUID, err := provision.EnsureUserExists(
				r.Context(),
				parsed.PortalUserID,
				parsed.Email,
				parsed.TenantRole,
				parsed.TenantID,
			)
			if err != nil {
				http.Error(w, "user provision failed", http.StatusInternalServerError)
				return
			}

			caller := Caller{
				UserID:     parsed.PortalUserID,
				TenantID:   parsed.TenantID,
				TenantUUID: tenantUUID,
				Role:       parsed.TenantRole,
				Email:      parsed.Email,
			}
			r = r.WithContext(context.WithValue(r.Context(), callerKey, caller))
			next.ServeHTTP(w, r)
		})
	}
}

func extractToken(r *http.Request) (string, error) {
	if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
		return strings.TrimPrefix(h, "Bearer "), nil
	}
	// Browser-side fallback: the Portal mirrors the access token to
	// a ``portal_access_token`` cookie (Path=/, SameSite=Lax) that
	// shares across localhost ports. Server-rendered pages that hit
	// chat-edge via SSR can rely on this without JS.
	if c, err := r.Cookie("portal_access_token"); err == nil && c.Value != "" {
		return c.Value, nil
	}
	return "", errors.New("no token")
}

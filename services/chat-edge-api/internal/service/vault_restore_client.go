package service

// Phase 4A.2.5 (B2) — Go→Vault V2 restore bridge.
//
// chat-edge (Go) cannot reach the Python/Redis Vault V2 directly, so the
// display-lifecycle restore-on-read path calls the gateway's internal endpoint
// POST /internal/dlp/vault-v2/restore. The endpoint derives the owner scope
// SERVER-SIDE from the authenticated context (the same gw key + X-User-ID +
// X-Conversation-ID headers chat-edge already sends for chat) and returns the
// raw values ONLY for tokens the owner is authorized to restore. This client
// then substitutes those raw values back into the tokenized canonical text to
// produce the restored display.
//
// Security: internal-trusted only (gw key + internal secret); the scope is
// NEVER trusted from this client's body — only the token list + consumer go
// over the wire. never_restore / wrong-scope / vault-disabled all come back as
// "denied" → the caller falls back to the tokenized representation. No raw
// value is ever logged here.

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

const vaultRestoreConsumer = "browser_reload_owner"

// VaultV2DisplayRestorer implements DisplayRestorer against the gateway's
// internal Vault V2 restore endpoint. Inert (returns no restore) unless both
// Vault V2 is enabled AND a gateway key + internal secret are configured.
type VaultV2DisplayRestorer struct {
	endpoint   string // full URL of /internal/dlp/vault-v2/restore
	gatewayKey string
	secret     string
	enabled    bool
	httpClient *http.Client
}

// NewVaultV2DisplayRestorer builds the bridge. “gatewayBaseURL“ is the same
// GATEWAY_API_URL chat-edge uses for chat (e.g. ".../v1"); the internal route
// lives at the host root, so a trailing “/v1“ is stripped.
func NewVaultV2DisplayRestorer(gatewayBaseURL, gatewayKey, internalSecret string, enabled bool) *VaultV2DisplayRestorer {
	base := strings.TrimRight(strings.TrimSpace(gatewayBaseURL), "/")
	base = strings.TrimSuffix(base, "/v1")
	return &VaultV2DisplayRestorer{
		endpoint:   base + "/internal/dlp/vault-v2/restore",
		gatewayKey: gatewayKey,
		secret:     internalSecret,
		enabled:    enabled,
		httpClient: &http.Client{Timeout: 5 * time.Second},
	}
}

type vaultRestoreRequest struct {
	Tokens   []string `json:"tokens"`
	Consumer string   `json:"consumer"`
}

type vaultRestoreResponse struct {
	Tokens map[string]struct {
		Restored         string `json:"restored"`
		RestorableToUser bool   `json:"restorable_to_user"`
		NeverRestore     bool   `json:"never_restore"`
	} `json:"tokens"`
	Denied map[string]string `json:"denied"`
}

// RestoreDisplay resolves the restorable tokens through the gateway and
// substitutes them into the message's canonical text. Returns (restored, true)
// only when at least one token was authorized + restored; otherwise
// (_, false) so the caller falls back to the tokenized/redacted form.
func (b *VaultV2DisplayRestorer) RestoreDisplay(
	ctx context.Context, actor DisplayActor, m model.Message, restorable []string,
) (string, bool) {
	if !b.enabled || b.secret == "" || b.gatewayKey == "" || len(restorable) == 0 {
		return "", false
	}
	if actor.UserID == uuid.Nil || m.ConversationID == uuid.Nil {
		return "", false
	}
	body, err := json.Marshal(vaultRestoreRequest{Tokens: restorable, Consumer: vaultRestoreConsumer})
	if err != nil {
		return "", false
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, b.endpoint, bytes.NewReader(body))
	if err != nil {
		return "", false
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+b.gatewayKey)
	req.Header.Set("X-Internal-Service-Secret", b.secret)
	req.Header.Set("X-User-ID", actor.UserID.String())
	req.Header.Set("X-Conversation-ID", m.ConversationID.String())

	resp, err := b.httpClient.Do(req)
	if err != nil {
		return "", false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", false
	}
	var out vaultRestoreResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return "", false
	}
	if len(out.Tokens) == 0 {
		return "", false
	}
	// Substitute restored raw values into the tokenized base (canonical
	// preferred; falls back to stored display). Denied tokens stay opaque.
	base := m.ContentMasked
	if m.ContentCanonical != nil {
		base = *m.ContentCanonical
	}
	restoredAny := false
	for tok, v := range out.Tokens {
		if v.Restored == "" {
			continue
		}
		if strings.Contains(base, tok) {
			base = strings.ReplaceAll(base, tok, v.Restored)
			restoredAny = true
		}
	}
	if !restoredAny {
		return "", false
	}
	return base, true
}

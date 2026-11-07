package service

// Phase 4A.2.5 (B2) — Go→Vault V2 restore bridge tests. A fake HTTP server
// stands in for the gateway's /internal/dlp/vault-v2/restore endpoint.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func dlMsg(canon string) model.Message {
	c := canon
	return model.Message{
		ConversationID:   uuid.New(),
		ContentMasked:    canon,
		ContentCanonical: &c,
		CanonicalStatus:  CanonicalStatusComplete,
		DisplayState:     DisplayStateCanonicalSafe,
	}
}

func TestVaultRestore_OwnerRestores(t *testing.T) {
	var gotConsumer, gotUser, gotConv, gotSecret string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotSecret = r.Header.Get("X-Internal-Service-Secret")
		gotUser = r.Header.Get("X-User-ID")
		gotConv = r.Header.Get("X-Conversation-ID")
		b, _ := io.ReadAll(r.Body)
		var req vaultRestoreRequest
		_ = json.Unmarshal(b, &req)
		gotConsumer = req.Consumer
		_ = json.NewEncoder(w).Encode(map[string]any{
			"tokens": map[string]any{
				"<<EMAIL_1>>": map[string]any{"restored": "alice@x.com", "restorable_to_user": true, "never_restore": false},
			},
			"denied": map[string]string{},
		})
	}))
	defer srv.Close()

	br := NewVaultV2DisplayRestorer(srv.URL+"/v1", "gw_key", "secret", true)
	actor := DisplayActor{UserID: uuid.New(), TenantID: uuid.New()}
	m := dlMsg("salary <<EMAIL_1>>")
	got, ok := br.RestoreDisplay(context.Background(), actor, m, []string{"<<EMAIL_1>>"})
	if !ok || got != "salary alice@x.com" {
		t.Fatalf("owner restore failed: %q ok=%v", got, ok)
	}
	if gotConsumer != "browser_reload_owner" {
		t.Fatalf("consumer header wrong: %q", gotConsumer)
	}
	if gotSecret != "secret" || gotUser != actor.UserID.String() || gotConv != m.ConversationID.String() {
		t.Fatalf("scope headers wrong: secret=%q user=%q conv=%q", gotSecret, gotUser, gotConv)
	}
	// Endpoint path must hit the host root, not /v1.
	if !strings.HasSuffix(br.endpoint, "/internal/dlp/vault-v2/restore") || strings.Contains(br.endpoint, "/v1/") {
		t.Fatalf("endpoint url wrong: %q", br.endpoint)
	}
}

func TestVaultRestore_DeniedFallsBack(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"tokens": map[string]any{},
			"denied": map[string]string{"<<EMAIL_1>>": "never_restore"},
		})
	}))
	defer srv.Close()
	br := NewVaultV2DisplayRestorer(srv.URL, "gw_key", "secret", true)
	_, ok := br.RestoreDisplay(context.Background(), DisplayActor{UserID: uuid.New()}, dlMsg("x <<EMAIL_1>>"), []string{"<<EMAIL_1>>"})
	if ok {
		t.Fatalf("denied token must fall back (ok=false)")
	}
}

func TestVaultRestore_DisabledOrUnconfigured(t *testing.T) {
	called := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
	}))
	defer srv.Close()
	actor := DisplayActor{UserID: uuid.New()}
	m := dlMsg("x <<EMAIL_1>>")
	// Vault V2 disabled → no HTTP call, no restore.
	if _, ok := NewVaultV2DisplayRestorer(srv.URL, "gw_key", "secret", false).RestoreDisplay(context.Background(), actor, m, []string{"<<EMAIL_1>>"}); ok {
		t.Fatalf("disabled bridge must not restore")
	}
	// Missing secret → no restore.
	if _, ok := NewVaultV2DisplayRestorer(srv.URL, "gw_key", "", true).RestoreDisplay(context.Background(), actor, m, []string{"<<EMAIL_1>>"}); ok {
		t.Fatalf("missing secret must not restore")
	}
	// No restorable tokens → no restore.
	if _, ok := NewVaultV2DisplayRestorer(srv.URL, "gw_key", "secret", true).RestoreDisplay(context.Background(), actor, m, nil); ok {
		t.Fatalf("no tokens must not restore")
	}
	if called {
		t.Fatalf("no HTTP call should have been made on the disabled/unconfigured paths")
	}
}

// Integration with the representation policy: owner reload + wired restorer →
// restored; wrong actor still goes through the endpoint (server denies).
func TestVaultRestore_WiredIntoOwnerReload(t *testing.T) {
	owner := uuid.New()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Server authorizes only the owner (mirrors server-side scope check).
		if r.Header.Get("X-User-ID") == owner.String() {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"tokens": map[string]any{"<<EMAIL_1>>": map[string]any{"restored": "alice@x.com"}},
				"denied": map[string]string{},
			})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"tokens": map[string]any{}, "denied": map[string]string{"<<EMAIL_1>>": "not_found"}})
	}))
	defer srv.Close()

	svc := &MessageService{}
	svc.SetDisplayLifecycle(true, false, false)
	svc.SetDisplayRestorer(NewVaultV2DisplayRestorer(srv.URL, "gw_key", "secret", true))
	m := dlMsg("salary <<EMAIL_1>>")

	// Owner → restored.
	if rep := svc.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, m, DisplayActor{UserID: owner}); !rep.Restored || rep.Content != "salary alice@x.com" {
		t.Fatalf("owner reload should restore via bridge: %q restored=%v", rep.Content, rep.Restored)
	}
	// Wrong actor → server denies → tokenized fallback.
	if rep := svc.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, m, DisplayActor{UserID: uuid.New()}); rep.Restored {
		t.Fatalf("wrong actor must not restore")
	}
}

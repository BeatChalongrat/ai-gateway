package handler

// FIX121 C4 — the import endpoint over HTTP: the one size limit is on the
// REQUEST and it answers 413 with a code; a bad item is reported in the
// 200 body next to the counts; the desktop's error_attempts reach the row.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type fix121ImportStack struct {
	srv    *httptest.Server
	token  string
	convID uuid.UUID
	msgs   *repository.MessageRepo
	tenant uuid.UUID
}

func newFix121ImportStack(t *testing.T) *fix121ImportStack {
	t.Helper()
	pool := probePool(t)
	t.Cleanup(pool.Close)
	userID, tenantID := uuid.New(), uuid.New()
	prov := service.NewProvisionService(pool)
	tenantUUID, err := prov.EnsureUserExists(context.Background(), userID, "fix121-import@local", "owner", tenantID.String())
	if err != nil {
		t.Fatalf("provision: %v", err)
	}
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenantUUID, UserID: userID, ModelID: "gpt-4o-mini", Title: "FIX121 import"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	ih := NewImportHandler(service.NewImportService(pool, cr, mr, repository.NewFileRepo(pool), service.NewConversationService(cr, probeModels{})))
	r := chi.NewRouter()
	r.Route("/api/v1", func(r chi.Router) {
		r.Use(middleware.RequireAuth(probePortalSecret, prov, nil))
		r.Post("/conversations/{id}/messages/import", ih.ImportMessages)
	})
	srv := httptest.NewServer(r)
	t.Cleanup(srv.Close)
	return &fix121ImportStack{srv: srv, token: mintProbeToken(userID, tenantID.String()), convID: conv.ID, msgs: mr, tenant: tenantUUID}
}

func (s *fix121ImportStack) post(t *testing.T, body io.Reader) (int, string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, fmt.Sprintf("%s/api/v1/conversations/%s/messages/import", s.srv.URL, s.convID), body)
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+s.token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("import: %v", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(raw)
}

func TestFix121ImportHTTP_LongMessagePassesAndABadItemIsReportedNotFatal(t *testing.T) {
	st := newFix121ImportStack(t)
	long := strings.Repeat("ข", 60_000) // 180,000 bytes — refused with 422 before FIX121
	body, _ := json.Marshal(map[string]any{"messages": []any{
		map[string]any{"client_id": "u_1", "role": "user", "content": long, "created_at": "2026-09-17T09:00:00Z"},
		map[string]any{"client_id": "a_1", "role": "assistant", "content": "", "created_at": "2026-09-17T09:00:01Z",
			"status": "error", "terminal_cause": "error", "error_code": "all_models_failed",
			"error_attempts": []any{map[string]any{"provider": "sranai", "model": "qwen3.8-27b-fp8", "error_code": "provider_timeout"}}},
		map[string]any{"client_id": "u_2", "role": "user", "content": "x", "created_at": "not a time"},
	}})
	code, raw := st.post(t, bytes.NewReader(body))
	if code != http.StatusOK {
		t.Fatalf("want 200, got %d: %s", code, raw)
	}
	var res struct {
		Imported int `json:"imported"`
		Skipped  int `json:"skipped"`
		Rejected []struct {
			Index    int    `json:"index"`
			ClientID string `json:"client_id"`
			Reason   string `json:"reason"`
		} `json:"rejected"`
	}
	if err := json.Unmarshal([]byte(raw), &res); err != nil {
		t.Fatalf("decode: %v (%s)", err, raw)
	}
	if res.Imported != 2 || len(res.Rejected) != 1 || res.Rejected[0].Index != 2 || res.Rejected[0].ClientID != "u_2" || !strings.Contains(res.Rejected[0].Reason, "created_at") {
		t.Fatalf("response = %s", raw)
	}
	rows, _, err := st.msgs.ListByConversation(context.Background(), st.tenant, st.convID, 10, uuid.Nil)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	var sawLong, sawAttempts bool
	for _, m := range rows {
		if m.Role == "user" && len(m.ContentMasked) == len(long) {
			sawLong = true
		}
		if m.Role == "assistant" && strings.Contains(string(m.Attempts), "qwen3.8-27b-fp8") {
			sawAttempts = true
		}
	}
	if !sawLong || !sawAttempts {
		t.Fatalf("long message kept=%v, attempts kept=%v", sawLong, sawAttempts)
	}
}

func TestFix121ImportHTTP_AllStoredResponseHasNoRejectedKey(t *testing.T) {
	st := newFix121ImportStack(t)
	code, raw := st.post(t, strings.NewReader(`{"messages":[{"client_id":"u_1","role":"user","content":"hi","created_at":"2026-09-17T09:00:00Z"}]}`))
	if code != http.StatusOK || strings.Contains(raw, "rejected") {
		t.Fatalf("an older client must see the response it always saw, got %d %s", code, raw)
	}
}

// oversizeBody streams a syntactically valid import request just past the
// transport ceiling without holding it in memory.
type oversizeBody struct {
	head, tail []byte
	fill       int64
	pos        int64
}

func (o *oversizeBody) Read(p []byte) (int, error) {
	total := int64(len(o.head)) + o.fill + int64(len(o.tail))
	if o.pos >= total {
		return 0, io.EOF
	}
	n := 0
	for n < len(p) && o.pos < total {
		switch {
		case o.pos < int64(len(o.head)):
			p[n] = o.head[o.pos]
		case o.pos < int64(len(o.head))+o.fill:
			p[n] = 'a'
		default:
			p[n] = o.tail[o.pos-int64(len(o.head))-o.fill]
		}
		n++
		o.pos++
	}
	return n, nil
}

func TestFix121ImportHTTP_OverTheTransportCeilingIs413WithACode(t *testing.T) {
	st := newFix121ImportStack(t)
	body := &oversizeBody{
		head: []byte(`{"messages":[{"client_id":"u_1","role":"user","created_at":"2026-09-17T09:00:00Z","content":"`),
		tail: []byte(`"}]}`),
		fill: service.ImportMaxBodyBytes + 1,
	}
	code, raw := st.post(t, body)
	if code != http.StatusRequestEntityTooLarge || !strings.Contains(raw, CodeRequestTooLarge) {
		t.Fatalf("want 413 %s, got %d: %.200s", CodeRequestTooLarge, code, raw)
	}
	rows, _, _ := st.msgs.ListByConversation(context.Background(), st.tenant, st.convID, 10, uuid.Nil)
	if len(rows) != 0 {
		t.Fatalf("a refused request must store nothing, stored %d", len(rows))
	}
}

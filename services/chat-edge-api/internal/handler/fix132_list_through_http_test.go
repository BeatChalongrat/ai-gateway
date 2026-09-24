package handler

// FIX132 F12 — GET /conversations/{id}/messages?before=<oldest>&through=<hit>
// fills the gap down to a search hit in one request (and through=first down
// to the first message). Before, `through` was ignored: 50 rows per click.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

func TestFIX132_ListMessagesThroughOverHTTP(t *testing.T) {
	pool := probePool(t)
	t.Cleanup(pool.Close)
	userID, tenantID := uuid.New(), uuid.New()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	svc := service.NewMessageService(cr, mr, nil, nil, probeModels{}, 10)
	h := NewMessageHandler(svc)
	prov := service.NewProvisionService(pool)
	r := chi.NewRouter()
	r.Route("/api/v1", func(r chi.Router) {
		r.Use(middleware.RequireAuth(probePortalSecret, prov, nil))
		r.Get("/conversations/{id}/messages", h.List)
	})
	srv := httptest.NewServer(r)
	t.Cleanup(srv.Close)
	tenantUUID, err := prov.EnsureUserExists(context.Background(), userID, "fix132-through@local", "owner", tenantID.String())
	if err != nil {
		t.Fatalf("provision: %v", err)
	}
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenantUUID, UserID: userID, ModelID: "m", Title: "FIX132"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	var ids []string
	for i := 0; i < 140; i++ {
		role := []string{"user", "assistant"}[i%2]
		m, err := mr.Create(context.Background(), &model.Message{ConversationID: conv.ID, TenantID: tenantUUID, UserID: userID, Role: role, Status: "completed", ContentMasked: fmt.Sprint(i)})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, m.ID.String())
	}
	token := mintProbeToken(userID, tenantID.String())
	get := func(q string) (int, []string, bool) {
		req, _ := http.NewRequest(http.MethodGet, fmt.Sprintf("%s/api/v1/conversations/%s/messages?%s", srv.URL, conv.ID, q), nil)
		req.Header.Set("Authorization", "Bearer "+token)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(resp.Body)
		var body struct {
			Messages []struct {
				ID string `json:"id"`
			} `json:"messages"`
			HasMore bool `json:"has_more"`
		}
		_ = json.Unmarshal(raw, &body)
		out := make([]string, 0, len(body.Messages))
		for _, m := range body.Messages {
			out = append(out, m.ID)
		}
		return resp.StatusCode, out, body.HasMore
	}

	// The page holds the newest 50 (ids[90..139]); the hit is message 5.
	code, got, more := get(fmt.Sprintf("limit=1000&before=%s&through=%s", ids[90], ids[5]))
	if code != 200 || len(got) != 85 || got[0] != ids[5] || got[len(got)-1] != ids[89] || !more {
		t.Fatalf("through hit: code=%d n=%d first=%v more=%v", code, len(got), len(got) > 0 && got[0] == ids[5], more)
	}
	// through=first: everything older than the cursor, in one response.
	code, got, more = get(fmt.Sprintf("before=%s&through=first", ids[90]))
	if code != 200 || len(got) != 90 || got[0] != ids[0] || more {
		t.Fatalf("through first: code=%d n=%d more=%v", code, len(got), more)
	}
	// A bad value is a 400, not a silent ordinary page.
	if code, _, _ := get("through=not-a-uuid"); code != http.StatusBadRequest {
		t.Fatalf("bad through: code=%d", code)
	}
	// Without `through` the page size is what it was.
	if code, got, more := get(fmt.Sprintf("limit=50&before=%s", ids[90])); code != 200 || len(got) != 50 || !more {
		t.Fatalf("plain page: code=%d n=%d", code, len(got))
	}
}

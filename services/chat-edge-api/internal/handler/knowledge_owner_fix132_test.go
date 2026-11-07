package handler

// FIX132 B — a knowledge base is private to the user who created it: a
// colleague in the same tenant gets 404 from every endpoint (and cannot bind
// it to a conversation or a project); the owner keeps full access.

import (
	"context"
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

func TestFIX132_KnowledgeBasesArePrivateToTheirCreator(t *testing.T) {
	k := newKBHTTP(t)
	owner, colleague := uuid.New(), uuid.New()
	kbID := k.createKB(t, owner, "สัญญาลูกค้า")
	up := k.upload(t, owner, kbID, "contract.txt", "text/plain", "Annual leave policy. Employees get ten days of annual leave each year.")
	if up.Code != http.StatusAccepted {
		t.Fatalf("owner upload: %d", up.Code)
	}
	var doc struct {
		ID string `json:"id"`
	}
	decode(t, up, &doc)
	k.drain(t)
	base := "/knowledge-bases/" + kbID

	// The colleague: the list is empty and every route answers 404.
	var list struct {
		KBs []map[string]any `json:"knowledge_bases"`
	}
	decode(t, k.do(t, colleague, http.MethodGet, "/knowledge-bases", nil), &list)
	if len(list.KBs) != 0 {
		t.Fatalf("a colleague must not see the owner's knowledge base: %v", list.KBs)
	}
	denied := []struct{ method, path string }{
		{http.MethodGet, base},
		{http.MethodPatch, base},
		{http.MethodDelete, base},
		{http.MethodGet, base + "/documents"},
		{http.MethodDelete, base + "/documents/" + doc.ID},
		{http.MethodPost, base + "/documents/" + doc.ID + "/reindex"},
		{http.MethodPost, base + "/search"},
	}
	for _, d := range denied {
		var body any
		switch d.method {
		case http.MethodPatch:
			body = map[string]any{"name": "mine now"}
		case http.MethodPost:
			body = map[string]any{"query": "annual leave"}
		}
		if rec := k.do(t, colleague, d.method, d.path, body); rec.Code != http.StatusNotFound {
			t.Errorf("colleague %s %s → %d, want 404 (%s)", d.method, d.path, rec.Code, rec.Body.String())
		}
	}
	if rec := k.upload(t, colleague, kbID, "x.txt", "text/plain", "hello"); rec.Code != http.StatusNotFound {
		t.Errorf("colleague upload → %d, want 404", rec.Code)
	}

	// Nothing the colleague did changed anything; the owner has full access.
	var got struct {
		KB   map[string]any   `json:"knowledge_base"`
		Docs []map[string]any `json:"documents"`
	}
	rec := k.do(t, owner, http.MethodGet, base, nil)
	decode(t, rec, &got)
	if rec.Code != http.StatusOK || got.KB["name"] != "สัญญาลูกค้า" || len(got.Docs) != 1 || got.Docs[0]["status"] != "indexed" {
		t.Fatalf("owner get: %d %s", rec.Code, rec.Body.String())
	}
	if rec := k.do(t, owner, http.MethodPost, base+"/search", map[string]any{"query": "annual leave days"}); rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "contract.txt") {
		t.Fatalf("owner search: %d %s", rec.Code, rec.Body.String())
	}
	if rec := k.do(t, owner, http.MethodPatch, base, map[string]any{"name": "Contracts"}); rec.Code != http.StatusOK {
		t.Fatalf("owner patch: %d", rec.Code)
	}
	if rec := k.do(t, owner, http.MethodPost, base+"/documents/"+doc.ID+"/reindex", nil); rec.Code != http.StatusAccepted {
		t.Fatalf("owner reindex: %d %s", rec.Code, rec.Body.String())
	}
	if rec := k.do(t, owner, http.MethodDelete, base+"/documents/"+doc.ID, nil); rec.Code != http.StatusNoContent {
		t.Fatalf("owner delete document: %d", rec.Code)
	}
	if rec := k.do(t, owner, http.MethodDelete, base, nil); rec.Code != http.StatusNoContent {
		t.Fatalf("owner delete: %d", rec.Code)
	}

	// A knowledge base from before migration 049 has no owner: only the admin
	// endpoints reach it.
	var legacy uuid.UUID
	_ = k.pool.QueryRow(context.Background(), `INSERT INTO chat.knowledge_bases (tenant_id, name) VALUES ($1, 'legacy') RETURNING id`, k.tenant).Scan(&legacy)
	decode(t, k.do(t, owner, http.MethodGet, "/knowledge-bases", nil), &list)
	if len(list.KBs) != 0 {
		t.Fatalf("an ownerless knowledge base is not listed to users: %v", list.KBs)
	}
	if err := k.svc.AdminDeleteKB(context.Background(), k.tenant, legacy); err != nil {
		t.Fatalf("admin delete stays cross-user: %v", err)
	}
}

// PATCH /conversations/{id} {knowledge_base_id}: only your own KB binds.
func TestFIX132_ConversationBindsOnlyYourOwnKnowledgeBase(t *testing.T) {
	k := newKBHTTP(t)
	owner, colleague := uuid.New(), uuid.New()
	kbID := k.createKB(t, owner, "HR")

	convRepo := repository.NewConversationRepo(k.pool)
	ch := NewConversationHandler(service.NewConversationService(convRepo, nil))
	ch.SetKnowledgeService(k.svc)
	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			uid := uuid.MustParse(req.Header.Get("X-Test-User"))
			next.ServeHTTP(w, req.WithContext(middleware.WithCaller(req.Context(), middleware.Caller{UserID: uid, TenantUUID: k.tenant, TenantID: k.tenant.String()})))
		})
	})
	r.Patch("/conversations/{id}", ch.Update)
	patch := func(user uuid.UUID, convID uuid.UUID) int {
		req := httptest.NewRequest(http.MethodPatch, "/conversations/"+convID.String(), strings.NewReader(`{"knowledge_base_id":"`+kbID+`"}`))
		req.Header.Set("X-Test-User", user.String())
		req.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()
		r.ServeHTTP(rec, req)
		return rec.Code
	}
	colleagueConv, _ := convRepo.Create(context.Background(), &model.Conversation{TenantID: k.tenant, UserID: colleague, ModelID: "m", Title: "c"})
	if code := patch(colleague, colleagueConv.ID); code != http.StatusNotFound {
		t.Fatalf("binding a colleague's knowledge base → %d, want 404", code)
	}
	if c, _ := convRepo.GetByID(context.Background(), k.tenant, colleagueConv.ID); c.KnowledgeBaseID != nil {
		t.Fatal("the binding must not be stored")
	}
	ownerConv, _ := convRepo.Create(context.Background(), &model.Conversation{TenantID: k.tenant, UserID: owner, ModelID: "m", Title: "o"})
	if code := patch(owner, ownerConv.ID); code != http.StatusOK {
		t.Fatalf("the owner binds their own knowledge base → %d", code)
	}
}

// PUT /projects/{id}/knowledge-base: the repo guard refuses a colleague's KB.
func TestFIX132_ProjectBindsOnlyYourOwnKnowledgeBase(t *testing.T) {
	k := newKBHTTP(t)
	owner, colleague := uuid.New(), uuid.New()
	kb := uuid.MustParse(k.createKB(t, owner, "HR"))
	projects := repository.NewProjectRepo(k.pool)
	ctx := context.Background()
	theirs, err := projects.Create(ctx, k.tenant, colleague, "colleague", "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := projects.SetKnowledgeBase(ctx, k.tenant, colleague, theirs.ID, &kb); err == nil {
		t.Fatal("a colleague's project must not attach the owner's knowledge base")
	}
	mine, _ := projects.Create(ctx, k.tenant, owner, "mine", "")
	if p, err := projects.SetKnowledgeBase(ctx, k.tenant, owner, mine.ID, &kb); err != nil || p.KnowledgeBaseID == nil || *p.KnowledgeBaseID != kb {
		t.Fatalf("the owner's project attaches it: %v %v", p, err)
	}
}

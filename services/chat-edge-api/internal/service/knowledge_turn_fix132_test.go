package service

// FIX132 B — a chat turn in a conversation bound to a knowledge base: the
// passages reach the model in the system prompt with [Source: …] labels, and
// the knowledge_chunk / knowledge_pending sources are saved on the answer by
// Finish (before the handler writes message_complete), readable the way
// GET /messages reads them.

import (
	"context"
	"encoding/json"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

type kbTurnFixture struct {
	*kbHarness
	s    *MessageService
	gw   *fix125Gateway
	cr   *repository.ConversationRepo
	src  *repository.ResponseContextSourceRepo
	conv *model.Conversation
}

func newKBTurnFixture(t *testing.T, modelID string) *kbTurnFixture {
	t.Helper()
	h := newKBHarness(t)
	cr := repository.NewConversationRepo(h.pool)
	mr := repository.NewMessageRepo(h.pool)
	gw := newFix125Gateway(t)
	s := NewMessageService(cr, mr, NewGatewayClient(gw.server.URL, "test-key", "", time.Minute, ""), nil, allowAllModels{}, 50)
	s.SetRAGReader(NewRAGService(h.repo, h.models, DefaultRAGConfig()))
	src := repository.NewResponseContextSourceRepo(h.pool)
	s.SetResponseSourceRepo(src)
	s.SetContextCompiler(NewContextCompiler(nil))
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: h.tenant, UserID: h.user, ModelID: modelID, Title: "KB"})
	if err != nil {
		t.Fatal(err)
	}
	if err := cr.UpdateKnowledgeBase(context.Background(), h.tenant, conv.ID, h.kb.ID); err != nil {
		t.Fatal(err)
	}
	conv.KnowledgeBaseID = &h.kb.ID
	return &kbTurnFixture{kbHarness: h, s: s, gw: gw, cr: cr, src: src, conv: conv}
}

func (f *kbTurnFixture) ask(t *testing.T, q string) (system string, sources []model.ResponseContextSource) {
	t.Helper()
	calls := f.gw.calls()
	hd, err := f.s.SendStream(context.Background(), SendInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, Content: q, RequestID: uuid.NewString()})
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	drainHandle(hd) // Finish persists the answer and its sources
	if f.gw.calls() != calls+1 {
		t.Fatalf("gateway calls %d → %d", calls, f.gw.calls())
	}
	msgs, _ := f.gw.body(-1)["messages"].([]any)
	for _, raw := range msgs {
		m, _ := raw.(map[string]any)
		if m["role"] == "system" {
			s, _ := m["content"].(string)
			system += s
		}
	}
	byMsg, err := f.src.ListByMessages(context.Background(), []uuid.UUID{hd.AssistantID}, f.user, f.tenant)
	if err != nil {
		t.Fatal(err)
	}
	return system, byMsg[hd.AssistantID]
}

func kbSources(srcs []model.ResponseContextSource, typ string) []model.ResponseContextSource {
	var out []model.ResponseContextSource
	for _, s := range srcs {
		if s.SourceType == typ {
			out = append(out, s)
		}
	}
	return out
}

func TestFIX132_TurnGivesThePassagesAndSavesTheirSources(t *testing.T) {
	f := newKBTurnFixture(t, "qwen3.8-27b-fp8")
	leave := f.upload(t, "leave.txt", "text/plain", "Annual leave policy. Employees get ten days of annual leave each year, booked through the HR portal.")
	f.upload(t, "travel.txt", "text/plain", "Travel rules. Book economy class for flights under six hours.")
	f.drain(t)
	pending := f.upload(t, "สัญญา 2027.pdf", "application/pdf", "not indexed yet")

	queriesBefore := countQueries(f.kbHarness)
	system, srcs := f.ask(t, "How many days of annual leave do employees get?")
	if !strings.Contains(system, `<knowledge_base name="คู่มือพนักงาน">`) || !strings.Contains(system, "[1] [Source: leave.txt]") ||
		!strings.Contains(system, "ten days of annual leave") || !strings.Contains(system, "cite it as [Source: <filename>]") {
		t.Fatalf("the passages must reach the model:\n%s", system)
	}
	if !strings.Contains(system, "1 file is still being processed and cannot be searched yet (สัญญา 2027.pdf)") {
		t.Fatalf("the pending file must be named in the block:\n%s", system)
	}
	if n := countQueries(f.kbHarness) - queriesBefore; n != 1 {
		t.Fatalf("one query embedding per turn, got %d", n)
	}

	chunks := kbSources(srcs, "knowledge_chunk")
	if len(chunks) == 0 {
		t.Fatalf("knowledge_chunk sources must be saved with the answer: %+v", srcs)
	}
	first := chunks[0]
	var meta map[string]any
	_ = json.Unmarshal(first.Metadata, &meta)
	if first.DisplayLabel != "leave.txt" || first.SourceID == nil || meta["document_id"] != leave.ID.String() ||
		meta["kb_id"] != f.kb.ID.String() || meta["rank"] != float64(1) || !strings.Contains(meta["excerpt"].(string), "ten days") {
		t.Fatalf("knowledge_chunk source: %+v %v", first, meta)
	}
	var chunkDoc string
	_ = f.pool.QueryRow(context.Background(), `SELECT document_id::text FROM chat.knowledge_chunks WHERE id=$1`, *first.SourceID).Scan(&chunkDoc)
	if chunkDoc != leave.ID.String() {
		t.Fatalf("source_id is the chunk id")
	}
	pend := kbSources(srcs, "knowledge_pending")
	var pm map[string]any
	if len(pend) != 1 {
		t.Fatalf("one knowledge_pending source, got %d", len(pend))
	}
	_ = json.Unmarshal(pend[0].Metadata, &pm)
	if pm["pending"] != float64(1) || pm["kb_id"] != f.kb.ID.String() {
		t.Fatalf("knowledge_pending: %v", pm)
	}
	_ = pending
}

func TestFIX132_OffTopicTurnGetsNoPassages(t *testing.T) {
	f := newKBTurnFixture(t, "qwen3.8-27b-fp8")
	f.upload(t, "leave.txt", "text/plain", "Annual leave policy. Employees get ten days of annual leave each year.")
	f.drain(t)
	system, srcs := f.ask(t, "zebra migration patterns")
	if strings.Contains(system, "<knowledge_base") || strings.Contains(system, "Annual leave policy") {
		t.Fatalf("nothing passed KB_MIN_SCORE → no block:\n%s", system)
	}
	if len(kbSources(srcs, "knowledge_chunk")) != 0 || len(kbSources(srcs, "knowledge_pending")) != 0 {
		t.Fatalf("no knowledge sources: %+v", srcs)
	}
}

// A small-window model gets a smaller block: a quarter of its window.
func TestFIX132_TurnBlockFitsAQuarterOfTheWindow(t *testing.T) {
	f := newKBTurnFixture(t, "Typhoon-S-ThaiLLM-8B-Instruct") // 16,384 advertised → 14,500 working
	var body strings.Builder
	for i := 0; i < 40; i++ {
		body.WriteString("Annual leave rule number ")
		body.WriteString(strings.Repeat("x", 3))
		body.WriteString(": employees get ten days of annual leave, carried over up to five days, booked through the HR portal. ")
		body.WriteString(strings.Repeat("ราคาห้องพัก ", 30))
		body.WriteString("\n\n")
	}
	f.upload(t, "leave-long.txt", "text/plain", body.String())
	f.drain(t)
	system, srcs := f.ask(t, "annual leave days")
	start := strings.Index(system, "<knowledge_base")
	end := strings.Index(system, "</knowledge_base>")
	if start < 0 || end < 0 {
		t.Fatalf("block missing:\n%s", system)
	}
	block := system[start : end+len("</knowledge_base>")]
	if EstimateTokens(block) > 14500/4 {
		t.Fatalf("block %d tokens > a quarter of the window (%d)", EstimateTokens(block), 14500/4)
	}
	n := len(kbSources(srcs, "knowledge_chunk"))
	given := len(regexp.MustCompile(`\[\d+\] \[Source: `).FindAllString(block, -1))
	if n == 0 || n >= 20 || n != given {
		t.Fatalf("sources = the passages actually given (%d sources, %d passages)", n, given)
	}
}

// Someone else's knowledge base bound to a conversation reads nothing.
func TestFIX132_TurnDoesNotReadAnotherUsersKnowledgeBase(t *testing.T) {
	f := newKBTurnFixture(t, "qwen3.8-27b-fp8")
	f.upload(t, "leave.txt", "text/plain", "Annual leave policy. Employees get ten days of annual leave each year.")
	f.drain(t)
	other := uuid.New()
	_, _ = f.pool.Exec(context.Background(), `UPDATE chat.knowledge_bases SET user_id=$2 WHERE id=$1`, f.kb.ID, other)
	system, srcs := f.ask(t, "How many days of annual leave do employees get?")
	if strings.Contains(system, "ten days of annual leave") || strings.Contains(system, "<knowledge_base") || len(srcs) != 0 {
		t.Fatalf("another user's KB must not be read:\n%s", system)
	}
}

func countQueries(h *kbHarness) int {
	n := 0
	for _, p := range h.fake.Priorities {
		if p == "query" {
			n++
		}
	}
	return n
}

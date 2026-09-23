package service

// FIX130 — the parts of the memory card that need the new surface
// (ExplicitRememberRequest-driven saves, the memory_used / memory_saved
// frames, the do-not-repeat list, the Memory page's source chat, the scoped
// use_count bump, two accounts in two tenants, the extraction marks).

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// A save the user explicitly asked for is active at once, and the answer
// learns about it: a memory_saved frame now and a memory_saved source for
// the reload.
func TestFIX130_ExplicitRememberSaveIsActiveAndAnnounced(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true)
	s := f.service()
	var frames []StreamUpdate
	acc := NewSourcesAccumulator()
	msgID := uuid.New()
	out := s.toolExec.Execute(context.Background(), saveCall("ชื่อเล่นว่าบีท", "user_requested"), ToolExecContext{
		TenantID: f.tenant, UserID: f.user, ConversationID: conv.ID, MessageID: msgID, Sources: acc,
		UserMessage: "จำไว้นะ ผมชื่อเล่นว่าบีท",
		Emit:        func(u StreamUpdate) { frames = append(frames, u) },
	})
	active := f.rows(t, model.MemoryStatusActive)
	if len(active) != 1 || active[0].SourceType != model.MemorySourceUserExplicit {
		t.Fatalf("an explicit request must save an active memory (result %q)", out)
	}
	var saved *MemorySavedNotice
	for _, u := range frames {
		if u.MemorySaved != nil {
			saved = u.MemorySaved
		}
	}
	if saved == nil || saved.MessageID != msgID.String() || len(saved.Items) != 1 ||
		saved.Items[0].Status != model.MemoryStatusActive || saved.Items[0].ID != active[0].ID.String() {
		t.Fatalf("memory_saved frame = %+v", saved)
	}
	var src *ContextSource
	for _, c := range acc.Snapshot() {
		if c.SourceType == model.SourceTypeMemorySaved {
			c := c
			src = &c
		}
	}
	if src == nil || src.SourceID != active[0].ID.String() || !strings.Contains(string(src.Metadata), `"status":"active"`) {
		t.Fatalf("memory_saved context source = %+v", src)
	}
}

// The unasked save carries the user's words as the evidence and is
// announced as a pending suggestion.
func TestFIX130_UnaskedSaveIsAnnouncedAsPending(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true)
	s := f.service()
	var saved *MemorySavedNotice
	s.toolExec.Execute(context.Background(), saveCall("แพ้กุ้ง", "user_requested"), ToolExecContext{
		TenantID: f.tenant, UserID: f.user, ConversationID: conv.ID, Sources: NewSourcesAccumulator(),
		UserMessage: "ผมแพ้กุ้งนะ ช่วยแนะนำเมนูเย็นนี้หน่อย",
		Emit: func(u StreamUpdate) {
			if u.MemorySaved != nil {
				saved = u.MemorySaved
			}
		},
	})
	pending := f.rows(t, model.MemoryStatusPendingConfirmation)
	if len(pending) != 1 || !strings.Contains(pending[0].EvidenceExcerpt, "ผมแพ้กุ้ง") {
		t.Fatalf("want one pending suggestion quoting the user, got %+v", pending)
	}
	if saved == nil || saved.Items[0].Status != model.MemoryStatusPendingConfirmation {
		t.Fatalf("memory_saved must say pending: %+v", saved)
	}
}

// memory_used is built from the compile step's saved_memory rows only.
func TestFIX130_MemoryUsedFrameComesFromInjectedMemories(t *testing.T) {
	long := strings.Repeat("ก", 120)
	msg := uuid.New()
	n := memoryUsedFromSources(msg, []ContextSource{
		{SourceType: model.SourceTypeSavedMemory, SourceID: "a", DisplayLabel: "ชื่อเล่นว่าบีท"},
		{SourceType: model.SourceTypeConversationState, SourceID: "c"},
		{SourceType: model.SourceTypeSavedMemory, SourceID: "b", DisplayLabel: long},
		{SourceType: model.SourceTypeSavedMemory, DisplayLabel: "a search hit has no id"},
	})
	if n == nil || n.Count != 2 || n.MessageID != msg.String() || n.Items[0].ID != "a" {
		t.Fatalf("memory_used = %+v", n)
	}
	if r := []rune(n.Items[1].Label); len(r) != memoryNoticeLabelRunes {
		t.Fatalf("label not capped to %d runes: %d", memoryNoticeLabelRunes, len(r))
	}
	if memoryUsedFromSources(msg, []ContextSource{{SourceType: model.SourceTypeConversationState}}) != nil {
		t.Fatal("no saved memory, no frame")
	}
	ch := make(chan StreamUpdate, 1)
	acc := NewSourcesAccumulator()
	emitMemoryUsed(ch, msg, acc)
	if len(ch) != 0 {
		t.Fatal("an empty accumulator must not emit")
	}
	acc.Add(ContextSource{SourceType: model.SourceTypeSavedMemory, SourceID: "a", DisplayLabel: "x"})
	emitMemoryUsed(ch, msg, acc)
	if u := <-ch; u.MemoryUsed == nil || u.MemoryUsed.Count != 1 {
		t.Fatalf("emitted %+v", u)
	}
}

// A reattaching client gets both frames back from the broker snapshot.
func TestFIX130_BrokerSnapshotKeepsMemoryFrames(t *testing.T) {
	b := NewStreamBroker()
	id := uuid.New()
	in := make(chan StreamUpdate, 8)
	out := b.Tee(id, in)
	in <- StreamUpdate{MemoryUsed: &MemoryUsedNotice{MessageID: id.String(), Count: 1, Items: []MemoryNoticeItem{{ID: "m1", Label: "x"}}}}
	in <- StreamUpdate{MemorySaved: &MemorySavedNotice{MessageID: id.String(), Items: []MemoryNoticeItem{{ID: "s1", Label: "a", Status: "pending_confirmation"}}}}
	in <- StreamUpdate{MemorySaved: &MemorySavedNotice{MessageID: id.String(), Items: []MemoryNoticeItem{{ID: "s2", Label: "b", Status: "active"}}}}
	in <- StreamUpdate{Delta: "hi"}
	for i := 0; i < 4; i++ {
		<-out
	}
	snap, _, cancel, _, ok := b.Subscribe(id)
	defer cancel()
	if !ok {
		t.Fatal("no broker entry")
	}
	if snap.MemoryUsed == nil || snap.MemoryUsed.Count != 1 {
		t.Fatalf("snapshot memory_used = %+v", snap.MemoryUsed)
	}
	if snap.MemorySaved == nil || len(snap.MemorySaved.Items) != 2 || snap.MemorySaved.Items[1].ID != "s2" {
		t.Fatalf("snapshot memory_saved = %+v", snap.MemorySaved)
	}
	close(in)
}

// The do-not-repeat list carries what the user rejected, under its own
// heading.
func TestFIX130_ExtractorPromptListsRejectedSuggestions(t *testing.T) {
	f := newFix130Fixture(t)
	ctx := context.Background()
	conv := f.chat(t, false, true, eightTurns...)
	cid := conv.ID
	m, err := f.memSvc.CreateMemory(ctx, CreateMemoryInput{
		TenantID: f.tenant, UserID: f.user, ScopeType: model.MemoryScopeGlobalUser,
		MemoryType: model.MemoryTypeInferredPreference, Content: "ชอบอีโมจิในคำตอบ",
		SourceType: model.MemorySourceAssistantInferred, SourceConversationID: &cid,
		StatusOverride: model.MemoryStatusPendingConfirmation, EvidenceExcerpt: "ใส่อีโมจิด้วย", ActorType: "system",
	})
	if err != nil {
		t.Fatalf("suggest: %v", err)
	}
	if _, err := f.memSvc.SetConfirmed(ctx, m.ID, f.user, f.tenant, false); err != nil {
		t.Fatalf("reject via confirm=false: %v", err)
	}
	got, err := f.mem.GetByID(ctx, m.ID, f.user, f.tenant)
	if err != nil || got.Status != model.MemoryStatusRejected || got.EvidenceExcerpt != "" {
		t.Fatalf("rejected row: %+v err=%v (status rejected, evidence dropped)", got, err)
	}
	gw := newExtractGateway(t, func() string { return completion("[]", "stop", 3, 0) })
	if _, err := f.extractor(gw).ExtractPreferences(ctx, conv.ID, f.user, f.tenant); err != nil {
		t.Fatalf("extract: %v", err)
	}
	prompt := fmt.Sprint(gw.calls()[0]["messages"])
	if !strings.Contains(prompt, "REJECTED") || !strings.Contains(prompt, "ชอบอีโมจิในคำตอบ") {
		t.Fatal("the rejected suggestion is not in the do-not-repeat list")
	}
	// The explicit path is not second-guessed: the user may still ask.
	if _, err := f.memSvc.CreateMemory(ctx, CreateMemoryInput{
		TenantID: f.tenant, UserID: f.user, ScopeType: model.MemoryScopeGlobalUser,
		MemoryType: model.MemoryTypeResponsePreference, Content: "ชอบอีโมจิในคำตอบ",
		SourceType: model.MemorySourceUserExplicit, ActorType: "user",
	}); err != nil {
		t.Fatalf("an explicit save of a once-rejected text was refused: %v", err)
	}
}

func TestFIX130_ParseTakesTheFirstArrayAndClassifies(t *testing.T) {
	cases := []struct {
		raw     string
		ok      bool
		n       int
		outcome string
		finish  string
	}{
		{`[{"type":"fact","content":"a","confidence":0.9,"evidence":"a"}]`, true, 1, extractOK, "stop"},
		{"note [not json] then\n```json\n[{\"content\":\"b\",\"confidence\":0.7}]\n```", true, 1, extractOK, "stop"},
		{`{"memories":[{"content":"c","confidence":0.8}]}`, true, 1, extractOK, "stop"},
		{`[]`, true, 0, extractNone, "stop"},
		{`[{"content":"low","confidence":0.3}]`, true, 0, extractNone, "stop"},
		{`[{"content":"no number"}]`, true, 1, extractOK, "stop"},
		{"I could not find anything.", false, 0, extractParseFailed, "stop"},
		{"", false, 0, extractEmpty, "stop"},
		{`[{"content":"cut`, false, 0, extractBudgetExhausted, "length"},
	}
	for _, c := range cases {
		got, ok := parsePreferenceResponse(c.raw)
		if ok != c.ok || len(got) != c.n {
			t.Errorf("parse(%q) = %d entries ok=%v, want %d ok=%v", c.raw, len(got), ok, c.n, c.ok)
		}
		if o := classifyExtraction(llmCall{content: c.raw, finishReason: c.finish}, ok, len(got)); o != c.outcome {
			t.Errorf("classify(%q) = %s, want %s", c.raw, o, c.outcome)
		}
	}
	if o := classifyExtraction(llmCall{err: errors.New("502")}, false, 0); o != extractGatewayError {
		t.Errorf("gateway failure classified %s", o)
	}
}

func TestFIX130_FactsAndPreferencesMapToRenderedTypes(t *testing.T) {
	cases := map[string]string{
		`{"type":"fact","category":"identity"}`:       model.MemoryTypeExplicitFact,
		`{"type":"Fact","category":"project"}`:        model.MemoryTypeOngoingInterest,
		`{"type":"preference","category":"language"}`: model.MemoryTypeInferredPreference,
		`{"category":"response_style"}`:               model.MemoryTypeInferredPreference,
	}
	for raw, want := range cases {
		got, _ := firstJSONArray("[" + raw[:len(raw)-1] + `,"content":"x"}]`)
		if len(got) != 1 {
			t.Fatalf("parse %s", raw)
		}
		if mt := extractedMemoryType(&got[0]); mt != want {
			t.Errorf("%s → %s, want %s", raw, mt, want)
		}
	}
	// And each renders where it belongs.
	block := renderMemoryBlock([]model.Memory{
		{MemoryType: model.MemoryTypeExplicitFact, Content: "FACT"},
		{MemoryType: model.MemoryTypeOngoingInterest, Content: "PROJECT"},
		{MemoryType: model.MemoryTypeInferredPreference, Content: "PREF"},
	})
	facts := block[strings.Index(block, "<known_facts>"):]
	instr := block[:strings.Index(block, "<known_facts>")]
	if !strings.Contains(facts, "FACT") || !strings.Contains(facts, "PROJECT") || !strings.Contains(instr, "PREF") {
		t.Fatalf("render:\n%s", block)
	}
}

// Only the user's own words: a "fact" whose evidence is not a quote of the
// user is dropped; the assistant's sentence is not a source.
func TestFIX130_FactWithoutAUserQuoteIsDropped(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true, "ช่วยแนะนำร้านกาแฟหน่อย", "คุณเป็นคนเชียงใหม่ใช่ไหมครับ ลองร้านนี้", "ขอบคุณ", "ยินดีครับ")
	hallucinated := `[{"type":"fact","content":"อยู่เชียงใหม่","category":"location","confidence":0.9,"evidence":"คุณเป็นคนเชียงใหม่ใช่ไหมครับ"}]`
	gw := newExtractGateway(t, func() string { return completion(hallucinated, "stop", 40, 0) })
	n, err := f.extractor(gw).ExtractPreferences(context.Background(), conv.ID, f.user, f.tenant)
	if err != nil || n != 0 {
		t.Fatalf("a fact quoted from the ASSISTANT was filed (created=%d err=%v)", n, err)
	}
	if !quotedFrom("ผม ชื่อเล่น…ภูผา", userCorpus([]model.Message{{Role: "user", ContentMasked: "ผมชื่อเล่นว่าภูผา"}})) {
		t.Error("an elided quote of the user's words must count")
	}
}

// The Memory page's "from which chat", resolved server-side, user-scoped.
func TestFIX130_ListNamesTheSourceChat(t *testing.T) {
	f := newFix130Fixture(t)
	ctx := context.Background()
	alive := f.chat(t, false, true)
	if err := f.cr.UpdateTitle(ctx, f.tenant, alive.ID, "วางแผนทริปเชียงใหม่"); err != nil {
		t.Fatalf("title: %v", err)
	}
	gone := f.chat(t, false, true)
	if err := f.cr.SoftDelete(ctx, f.tenant, gone.ID); err != nil {
		t.Fatalf("delete chat: %v", err)
	}
	// Another user's chat id planted on a row: never its title.
	otherConv, err := f.cr.Create(ctx, &model.Conversation{TenantID: f.tenant, UserID: uuid.New(), ModelID: "m"})
	if err != nil {
		t.Fatal(err)
	}
	_ = f.cr.UpdateTitle(ctx, f.tenant, otherConv.ID, "someone else's secret")
	mk := func(content string, conv *uuid.UUID) {
		if _, err := f.memSvc.CreateMemory(ctx, CreateMemoryInput{TenantID: f.tenant, UserID: f.user, ScopeType: model.MemoryScopeGlobalUser,
			MemoryType: model.MemoryTypeExplicitFact, Content: content, SourceType: model.MemorySourceUserExplicit,
			SourceConversationID: conv, ActorType: "user"}); err != nil {
			t.Fatalf("create %s: %v", content, err)
		}
	}
	mk("from alive", &alive.ID)
	mk("from gone", &gone.ID)
	mk("planted", &otherConv.ID)
	mk("typed in", nil)
	rows, _, err := f.memSvc.ListByUser(ctx, f.user, f.tenant, repository.MemoryListFilters{})
	if err != nil {
		t.Fatal(err)
	}
	by := map[string]model.Memory{}
	for _, r := range rows {
		by[r.Content] = r
	}
	if r := by["from alive"]; r.SourceConversationTitle != "วางแผนทริปเชียงใหม่" || r.SourceConversationDeleted {
		t.Errorf("alive: %+v", r)
	}
	if r := by["from gone"]; r.SourceConversationTitle != "" || !r.SourceConversationDeleted {
		t.Errorf("deleted chat: %+v", r)
	}
	if r := by["planted"]; r.SourceConversationTitle != "" || !r.SourceConversationDeleted {
		t.Errorf("another user's chat leaked: %+v", r)
	}
	if r := by["typed in"]; r.SourceConversationTitle != "" || r.SourceConversationDeleted {
		t.Errorf("manual add: %+v", r)
	}
}

// A4c — IncrementUseCount no longer touches a row by id alone.
func TestFIX130_UseCountBumpIsScopedToTheOwner(t *testing.T) {
	f := newFix130Fixture(t)
	ctx := context.Background()
	m := f.savedMemory(t)
	if err := f.mem.IncrementUseCount(ctx, m.ID, uuid.New(), f.tenant); err != nil {
		t.Fatal(err)
	}
	if err := f.mem.IncrementUseCount(ctx, m.ID, f.user, uuid.New()); err != nil {
		t.Fatal(err)
	}
	got, _ := f.mem.GetByID(ctx, m.ID, f.user, f.tenant)
	if got.UseCount != 0 || got.LastUsedAt != nil {
		t.Fatalf("another account stamped this memory: use_count=%d", got.UseCount)
	}
	if err := f.mem.IncrementUseCount(ctx, m.ID, f.user, f.tenant); err != nil {
		t.Fatal(err)
	}
	got, _ = f.mem.GetByID(ctx, m.ID, f.user, f.tenant)
	if got.UseCount != 1 {
		t.Fatalf("owner bump: use_count=%d", got.UseCount)
	}
}

// A4c — two users in one tenant, and the same user id in a second tenant:
// list, get, confirm, edit, delete and injection never reach the other's rows.
func TestFIX130_MemoryNeverCrossesAccountsOrTenants(t *testing.T) {
	f := newFix130Fixture(t)
	ctx := context.Background()
	userB := uuid.New()
	tenant2 := uuid.New()
	type acct struct {
		name         string
		user, tenant uuid.UUID
	}
	a := acct{"A", f.user, f.tenant}
	others := []acct{{"B same tenant", userB, f.tenant}, {"A's id in tenant 2", f.user, tenant2}}

	active, err := f.memSvc.CreateMemory(ctx, CreateMemoryInput{TenantID: a.tenant, UserID: a.user, ScopeType: model.MemoryScopeGlobalUser,
		MemoryType: model.MemoryTypeExplicitFact, Content: "A's secret fact", SourceType: model.MemorySourceUserExplicit, ActorType: "user"})
	if err != nil {
		t.Fatal(err)
	}
	pending, err := f.memSvc.CreateMemory(ctx, CreateMemoryInput{TenantID: a.tenant, UserID: a.user, ScopeType: model.MemoryScopeGlobalUser,
		MemoryType: model.MemoryTypeInferredPreference, Content: "A's pending suggestion", SourceType: model.MemorySourceAssistantInferred,
		StatusOverride: model.MemoryStatusPendingConfirmation, ActorType: "system"})
	if err != nil {
		t.Fatal(err)
	}
	for _, o := range others {
		for _, st := range []string{model.MemoryStatusActive, model.MemoryStatusPendingConfirmation} {
			rows, total, err := f.memSvc.ListByUser(ctx, o.user, o.tenant, repository.MemoryListFilters{Status: st})
			if err != nil || len(rows) != 0 || total != 0 {
				t.Errorf("%s lists %d %s rows of A", o.name, len(rows), st)
			}
		}
		if _, err := f.memSvc.GetMemory(ctx, active.ID, o.user, o.tenant); !errors.Is(err, ErrMemoryNotFound) {
			t.Errorf("%s got A's memory: %v", o.name, err)
		}
		if _, err := f.memSvc.SetConfirmed(ctx, pending.ID, o.user, o.tenant, true); !errors.Is(err, ErrMemoryNotFound) {
			t.Errorf("%s confirmed A's suggestion: %v", o.name, err)
		}
		if _, err := f.memSvc.SetConfirmed(ctx, pending.ID, o.user, o.tenant, false); !errors.Is(err, ErrMemoryNotFound) {
			t.Errorf("%s rejected A's suggestion: %v", o.name, err)
		}
		c := "overwritten"
		if _, err := f.memSvc.UpdateMemory(ctx, active.ID, o.user, o.tenant, UpdateMemoryInput{Content: &c}); !errors.Is(err, ErrMemoryNotFound) {
			t.Errorf("%s edited A's memory: %v", o.name, err)
		}
		if err := f.memSvc.DeleteMemory(ctx, active.ID, o.user, o.tenant); !errors.Is(err, ErrMemoryNotFound) {
			t.Errorf("%s deleted A's memory: %v", o.name, err)
		}
		if err := f.memSvc.DeleteMemory(ctx, pending.ID, o.user, o.tenant); !errors.Is(err, ErrMemoryNotFound) {
			t.Errorf("%s rejected A's suggestion by delete: %v", o.name, err)
		}
		if block, srcs := f.memSvc.BuildMemoryContextWithSources(ctx, o.user, o.tenant, nil); block != "" || len(srcs) != 0 {
			t.Errorf("%s was injected with A's memory:\n%s", o.name, block)
		}
	}
	// A's rows are untouched.
	got, err := f.mem.GetByID(ctx, active.ID, a.user, a.tenant)
	if err != nil || got.Content != "A's secret fact" || got.Status != model.MemoryStatusActive {
		t.Fatalf("A's memory changed: %+v %v", got, err)
	}
	got, err = f.mem.GetByID(ctx, pending.ID, a.user, a.tenant)
	if err != nil || got.Status != model.MemoryStatusPendingConfirmation {
		t.Fatalf("A's suggestion changed: %+v %v", got, err)
	}
	// And A still sees its own.
	if block, _ := f.memSvc.BuildMemoryContextWithSources(ctx, a.user, a.tenant, nil); !strings.Contains(block, "A's secret fact") {
		t.Fatal("A lost its own memory")
	}
}

// A4b — an edit is what the next turn injects, a delete is gone from it:
// the read goes to the database every turn (no cache in chat-edge).
func TestFIX130_EditAndDeleteReachTheNextTurn(t *testing.T) {
	f := newFix130Fixture(t)
	ctx := context.Background()
	m := f.savedMemory(t)
	c := "ชื่อเล่นว่ามะพร้าว"
	if _, err := f.memSvc.UpdateMemory(ctx, m.ID, f.user, f.tenant, UpdateMemoryInput{Content: &c}); err != nil {
		t.Fatal(err)
	}
	block, _ := f.memSvc.BuildMemoryContextWithSources(ctx, f.user, f.tenant, nil)
	if !strings.Contains(block, "มะพร้าว") || strings.Contains(block, fix130Secret) {
		t.Fatalf("the next turn does not carry the edit:\n%s", block)
	}
	if err := f.memSvc.DeleteMemory(ctx, m.ID, f.user, f.tenant); err != nil {
		t.Fatal(err)
	}
	if block, srcs := f.memSvc.BuildMemoryContextWithSources(ctx, f.user, f.tenant, nil); block != "" || len(srcs) != 0 {
		t.Fatalf("a deleted memory is still injected:\n%s", block)
	}
	// Accept → injected next turn (A4a).
	cid := uuid.New()
	p, err := f.memSvc.CreateMemory(ctx, CreateMemoryInput{TenantID: f.tenant, UserID: f.user, ScopeType: model.MemoryScopeGlobalUser,
		MemoryType: model.MemoryTypeExplicitFact, Content: "ทำงานเป็นวิศวกร", SourceType: model.MemorySourceAssistantInferred,
		SourceConversationID: &cid, StatusOverride: model.MemoryStatusPendingConfirmation, ActorType: "system"})
	if err != nil {
		t.Fatal(err)
	}
	if block, _ := f.memSvc.BuildMemoryContextWithSources(ctx, f.user, f.tenant, nil); strings.Contains(block, "วิศวกร") {
		t.Fatal("a pending suggestion was injected before the user accepted it")
	}
	if _, err := f.memSvc.SetConfirmed(ctx, p.ID, f.user, f.tenant, true); err != nil {
		t.Fatal(err)
	}
	if block, _ := f.memSvc.BuildMemoryContextWithSources(ctx, f.user, f.tenant, nil); !strings.Contains(block, "วิศวกร") {
		t.Fatal("an accepted suggestion is not injected on the next turn")
	}
}

// The marks: in-process and (when the test Redis is reachable) Redis.
func TestFIX130_ExtractionMarks(t *testing.T) {
	check := func(t *testing.T, m extractionMarks) {
		ctx := context.Background()
		id := uuid.New()
		if m.Watermark(ctx, id) != 0 {
			t.Fatal("fresh watermark not 0")
		}
		m.SetWatermark(ctx, id, 6)
		if m.Watermark(ctx, id) != 6 {
			t.Fatal("watermark not kept")
		}
		if !m.TryClaim(ctx, id) || m.TryClaim(ctx, id) {
			t.Fatal("claim must be exclusive")
		}
		m.Release(ctx, id)
		if !m.TryClaim(ctx, id) {
			t.Fatal("claim not released")
		}
		m.Release(ctx, id)
	}
	t.Run("in-process", func(t *testing.T) { check(t, newMemoryExtractionMarks()) })
	t.Run("redis", func(t *testing.T) {
		rdb := testRedis(t)
		m := newRedisExtractionMarks(rdb)
		check(t, m)
		// Watermarks expire: a key without a TTL would grow forever.
		id := uuid.New()
		m.SetWatermark(context.Background(), id, 2)
		t.Cleanup(func() { rdb.Del(context.Background(), extractionWatermarkKey(id)) })
		if ttl := rdb.TTL(context.Background(), extractionWatermarkKey(id)).Val(); ttl <= 0 || ttl > extractionWatermarkTTL+time.Minute {
			t.Fatalf("watermark TTL = %v", ttl)
		}
	})
}

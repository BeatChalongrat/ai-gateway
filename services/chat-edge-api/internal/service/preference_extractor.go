// PreferenceExtractor — the background memory learner (S8, reworked in
// FIX130 A1).
//
// Reads the newest stretch of a conversation and proposes durable things
// worth remembering about the user — facts they state about themselves
// (name, role, organisation, location, family, projects, dietary or health
// constraints they volunteer) and how they want to be answered
// (preferences). Every proposal lands in chat.user_memories as:
//
//	source_type = "assistant_inferred"
//	status      = "pending_confirmation"
//	memory_type = explicit_fact | ongoing_interest   (facts)
//	              inferred_preference                  (preferences)
//
// with the source chat and a verbatim quote of the user's words as the
// evidence. Pending rows stay OUT of the prompt (GetActiveForInjection reads
// status='active') until the user accepts them on the Memory page in one
// click; a rejected one is never proposed again (MemoryStatusRejected).
//
// FIX130 — NIGHT2 measured zero memories from 20-message chats: the call
// was capped at 800 output tokens, the background model (deepseek-v4-flash)
// spent ~1.8k of them thinking, the answer was empty, the parse returned
// nil and nothing was logged. Now: reasoning "off" plus a ceiling a model
// that thinks anyway still answers under, a tolerant parse, one structured
// log line per run, facts as well as preferences, content in the user's
// language, and a trigger that fires on a two-turn chat without ever
// re-reading the same messages (maybeExtractPreferences + extraction_marks).
//
// Same async-background pattern as S3's ConversationStateService: runs on
// context.Background() so the user's response isn't blocked, and a failed
// run never degrades the chat surface.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"
	"unicode"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// preferenceExtractionTimeout caps one gateway call. A slow extraction just
// delays the suggestion — not catastrophic to abandon.
const preferenceExtractionTimeout = 60 * time.Second

// The extraction call's budget.
//
// FIX130 A1a — 800 tokens left nothing for the answer once the model had
// thought. The request now says reasoning "off", but that is only honoured
// where the gateway has an off-switch for the model (the Qwen/vLLM
// enable_thinking family); deepseek-v4-flash — the default background
// model — is catalogued as a non-reasoning row, so "off" sends it nothing
// and it thinks anyway (649 and ~1.8k reasoning tokens measured). The
// ceiling is therefore the real guarantee: 4096 leaves ~2k for thinking and
// ~2k for an answer that is at most a few hundred tokens. Billing follows
// what is used, not the ceiling.
const (
	preferenceMaxTokens   = 4096
	preferenceTemperature = 0.1
)

// The trigger policy (FIX130 A1e) — replaces S8's floors (20 messages to
// trigger, 8 to run) and its "once per conversation" rule, under which a
// fact stated in a short chat was never read and a run that saved nothing
// ran again after every later turn.
//
//   - memoryExtractFirstAt: the first run happens once the chat holds two
//     user turns with their answers. A fact the user states in a short
//     chat is exactly what should be learned, and one exchange is usually
//     too little to tell a fact from a one-off request.
//   - memoryExtractEvery: after that, a run happens once this many NEW
//     messages (two more turns) have arrived since the last run. Each run
//     reads only those messages (the watermark), so nothing is read twice
//     and the cost is one small background call per two turns.
//   - memoryExtractWindow: at most this many of the newest messages are
//     read in one run.
//
// At most one run is in flight per conversation (extractionMarks), and
// only in a chat whose memory is active (on, and not a temporary chat).
const (
	memoryExtractFirstAt = 4
	memoryExtractEvery   = 4
	memoryExtractWindow  = 30
)

// promoteThreshold is the number of distinct conversations the
// same preference must surface in before auto-consolidation fires.
const promoteThreshold = 3

// PreferenceExtractor wires the dependencies it needs. Built once
// in main.go and registered on MessageService via SetPreferenceExtractor
// so the post-response trigger can fire it without circular wiring.
type PreferenceExtractor struct {
	memoryService *MemoryService
	memoryRepo    *repository.MemoryRepo
	messageRepo   *repository.MessageRepo
	conv          *repository.ConversationRepo
	gateway       *GatewayClient

	// extractionModel is the model the extraction call uses.
	// Empty → falls through to the conversation's own model.
	extractionModel string

	// marks — per-conversation watermark + in-flight claim (FIX130 A1e).
	// In-process by default; Redis when SetRedis is called (main.go), so
	// several chat-edge replicas agree.
	marks extractionMarks
}

func NewPreferenceExtractor(
	memoryService *MemoryService,
	memoryRepo *repository.MemoryRepo,
	messageRepo *repository.MessageRepo,
	conv *repository.ConversationRepo,
	gateway *GatewayClient,
) *PreferenceExtractor {
	return &PreferenceExtractor{
		memoryService: memoryService,
		memoryRepo:    memoryRepo,
		messageRepo:   messageRepo,
		conv:          conv,
		gateway:       gateway,
		marks:         newMemoryExtractionMarks(),
	}
}

// SetExtractionModel pins the extraction model regardless of which
// model the conversation chose (FIX126 O1: BACKGROUND_TASK_MODEL). The
// conversation's model is the fallback when it produces nothing.
func (e *PreferenceExtractor) SetExtractionModel(modelID string) {
	e.extractionModel = strings.TrimSpace(modelID)
}

// SetRedis keeps the watermark and the in-flight claim in Redis (FIX130).
func (e *PreferenceExtractor) SetRedis(rdb *redis.Client) {
	if rdb != nil {
		e.marks = newRedisExtractionMarks(rdb)
	}
}

// ---------------------------------------------------------------------------
// ExtractPreferences — main entry point
// ---------------------------------------------------------------------------

// ExtractPreferences runs one pass over the conversation's newest messages
// (up to memoryExtractWindow) and returns how many suggestions it filed.
// The background trigger uses extract with a watermark instead; this form
// reads the whole recent window.
func (e *PreferenceExtractor) ExtractPreferences(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (int, error) {
	res, err := e.extract(ctx, convID, userID, tenantID, 0)
	return res.created, err
}

// extractionOutcome values, one per run, in the memory_extraction log line.
const (
	extractOK              = "ok"               // parsed; >= 1 suggestion (created counts the new rows)
	extractNone            = "none"             // parsed; nothing worth remembering
	extractEmpty           = "empty"            // the model answered nothing
	extractParseFailed     = "parse_failed"     // text came back but held no JSON array
	extractGatewayError    = "gateway_error"    // every model failed at the gateway
	extractBudgetExhausted = "budget_exhausted" // finish_reason=length with no usable answer
	extractMemoryOff       = "memory_off"       // the chat's memory is not active (any more)
	extractNothingNew      = "nothing_new"      // no user message in the window
)

// extractResult is what one run did.
type extractResult struct {
	created int
	outcome string
	// seen is the message count the run covered — the next watermark.
	seen int
}

// advances reports whether the watermark may move past this run's
// messages. A gateway failure is retried at the next trigger; every other
// outcome was a real answer (or a real "no"), and re-reading the same
// messages would only cost money.
func (r extractResult) advances() bool {
	return r.outcome != extractGatewayError
}

// extract is one run over the messages after `since` (a message count).
func (e *PreferenceExtractor) extract(
	ctx context.Context, convID, userID, tenantID uuid.UUID, since int,
) (extractResult, error) {
	started := time.Now()
	conv, err := e.conv.GetByID(ctx, tenantID, convID)
	if err != nil {
		return extractResult{outcome: extractGatewayError}, fmt.Errorf("conv lookup: %w", err)
	}
	logRun := func(res extractResult, modelID string, call llmCall, parsed, skipped, dropped, window int) {
		attrs := []any{
			"conv", convID.String(),
			"model", modelID,
			"outcome", res.outcome,
			"messages", window,
			"suggestions", parsed,
			"created", res.created,
			"skipped", skipped,
			"dropped_unquoted", dropped,
			"finish_reason", call.finishReason,
			"prompt_tokens", call.promptTokens,
			"completion_tokens", call.completionTokens,
			"reasoning_tokens", call.reasoningTokens,
			"attempts", call.attempts,
			"elapsed_ms", time.Since(started).Milliseconds(),
		}
		if res.outcome == extractGatewayError {
			slog.Warn("memory_extraction", attrs...)
			return
		}
		slog.Info("memory_extraction", attrs...)
	}
	// FIX130 A2 — defence in depth: the trigger checks this too, but the
	// switch can be flipped while the run waits for the turn to end.
	if !conv.MemoryActive() {
		res := extractResult{outcome: extractMemoryOff}
		logRun(res, "", llmCall{}, 0, 0, 0, 0)
		return res, nil
	}

	total, err := e.messageRepo.CountMessages(ctx, tenantID, convID)
	if err != nil {
		return extractResult{outcome: extractGatewayError}, fmt.Errorf("count messages: %w", err)
	}
	msgs, err := e.messageRepo.ListContextByConversation(ctx, tenantID, convID, 50)
	if err != nil {
		return extractResult{outcome: extractGatewayError}, fmt.Errorf("list messages: %w", err)
	}
	window := extractionWindow(msgs, total-since)
	res := extractResult{seen: total}
	if !hasUserText(window) {
		res.outcome = extractNothingNew
		logRun(res, "", llmCall{}, 0, 0, 0, len(window))
		return res, nil
	}

	known, rejected := e.knownContents(ctx, userID, tenantID)
	prompt := buildExtractionPrompt(window, known, rejected)

	// FIX126 O1 — the background model (BACKGROUND_TASK_MODEL) first; the
	// conversation's model when it produced nothing usable (not served,
	// not usable by the tenant, failed, or answered nothing).
	var call llmCall
	var suggestions []inferredPreference
	usedModel := ""
	attempts := 0
	for _, modelID := range backgroundModelOrder(e.extractionModel, conv.ModelID) {
		attempts++
		usedModel = modelID
		call = e.callLLM(ctx, modelID, prompt, GatewayCallContext{
			TenantID:       tenantID,
			UserID:         userID,
			ConversationID: convID,
		})
		call.attempts = attempts
		var ok bool
		suggestions, ok = parsePreferenceResponse(call.content)
		res.outcome = classifyExtraction(call, ok, len(suggestions))
		if res.outcome == extractOK || res.outcome == extractNone || res.outcome == extractParseFailed {
			break
		}
	}
	if res.outcome != extractOK {
		logRun(res, usedModel, call, 0, 0, 0, len(window))
		return res, nil
	}

	userText := userCorpus(window)
	skipped, dropped := 0, 0
	for i := range suggestions {
		sg := &suggestions[i]
		memType := extractedMemoryType(sg)
		evidence := strings.TrimSpace(sg.evidence())
		// Only what the USER said: the evidence must be the user's own
		// words. A fact without such a quote is dropped (it may have come
		// from the assistant's side of the chat); a preference keeps its
		// suggestion but loses a "quote" the user never wrote, since the
		// Memory page shows the evidence as the user's words.
		if !quotedFrom(evidence, userText) {
			if memType != model.MemoryTypeInferredPreference {
				dropped++
				continue
			}
			evidence = ""
		}
		convPtr := convID
		_, cerr := e.memoryService.CreateMemory(ctx, CreateMemoryInput{
			TenantID:                tenantID,
			UserID:                  userID,
			ScopeType:               model.MemoryScopeGlobalUser,
			MemoryType:              memType,
			Category:                sg.Category,
			Content:                 sg.Content,
			SourceType:              model.MemorySourceAssistantInferred,
			SourceConversationID:    &convPtr,
			EvidenceExcerpt:         evidence,
			ActorType:               "system",
			StatusOverride:          model.MemoryStatusPendingConfirmation,
			ConfidenceScoreOverride: sg.confidence(),
		})
		if cerr != nil {
			// Duplicate / previously rejected / DLP / masked / cap — all
			// expected. Counted, never logged with the text.
			skipped++
			if !errors.Is(cerr, ErrMemoryDuplicate) && !errors.Is(cerr, ErrMemoryRejectedBefore) {
				slog.Debug("memory_extraction: suggestion not filed", "conv", convID.String(), "reason", cerr.Error())
			}
			continue
		}
		res.created++
		// Repeated observations of the same preference across chats are
		// consolidated (still pending) — preferences only.
		if memType == model.MemoryTypeInferredPreference {
			e.maybeAutoPromote(ctx, userID, tenantID, sg.Content)
		}
	}
	logRun(res, usedModel, call, len(suggestions), skipped, dropped, len(window))
	return res, nil
}

// classifyExtraction names a model answer's outcome.
func classifyExtraction(c llmCall, parsed bool, n int) string {
	switch {
	case c.err != nil:
		return extractGatewayError
	case parsed && n > 0:
		return extractOK
	case parsed:
		return extractNone
	case c.finishReason == "length":
		// The cap was hit before a complete array came out — the NIGHT2
		// shape (all of it spent thinking, or an answer cut in half).
		return extractBudgetExhausted
	case strings.TrimSpace(c.content) == "":
		return extractEmpty
	default:
		return extractParseFailed
	}
}

// extractionWindow is the newest `fresh` messages (at most
// memoryExtractWindow) — the part of the chat no earlier run has read.
func extractionWindow(msgs []model.Message, fresh int) []model.Message {
	n := len(msgs)
	if fresh < n {
		n = fresh
	}
	if n > memoryExtractWindow {
		n = memoryExtractWindow
	}
	if n <= 0 {
		return nil
	}
	return msgs[len(msgs)-n:]
}

func hasUserText(msgs []model.Message) bool {
	for i := range msgs {
		if msgs[i].Role == "user" && strings.TrimSpace(msgs[i].ContentMasked) != "" {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// Auto-promotion
// ---------------------------------------------------------------------------

// maybeAutoPromote checks whether the just-saved suggestion now
// has 3+ observations across distinct conversations. If so the
// newest pending row stays pending (activation is the user's call on
// the Memory page) and older duplicates are superseded so the page
// shows one canonical row.
//
// Best-effort: any error along the way just leaves the
// suggestion as plain pending — no auto-promote, no damage.
func (e *PreferenceExtractor) maybeAutoPromote(
	ctx context.Context, userID, tenantID uuid.UUID, content string,
) {
	key := normalizeKey(content)
	if key == "" {
		return
	}
	similar, err := e.memoryRepo.FindSimilarPending(ctx, userID, tenantID, key)
	if err != nil || len(similar) < promoteThreshold {
		return
	}
	// Count DISTINCT source conversations — three suggestions in
	// the same conversation aren't three observations of a habit.
	seen := make(map[uuid.UUID]struct{}, len(similar))
	for i := range similar {
		if similar[i].SourceConversationID != nil {
			seen[*similar[i].SourceConversationID] = struct{}{}
		}
	}
	if len(seen) < promoteThreshold {
		return
	}

	// Sort by created_at DESC; the newest becomes the canonical row.
	// It STAYS pending_confirmation — repeated observation makes the
	// suggestion stronger, but activation is the user's call on the
	// Memory page (ChatGPT-style review). Older duplicates are
	// superseded so the Settings page shows one canonical row.
	sort.Slice(similar, func(i, j int) bool {
		return similar[i].CreatedAt.After(similar[j].CreatedAt)
	})
	newest := &similar[0]
	for i := 1; i < len(similar); i++ {
		older := &similar[i]
		if older.Status == model.MemoryStatusSuperseded {
			continue
		}
		if err := e.memoryRepo.SetStatus(ctx, older.ID, userID, tenantID, model.MemoryStatusSuperseded); err != nil {
			slog.Warn("preference auto-consolidate: supersede failed",
				"id", older.ID.String(), "err", err)
		}
	}
	// Audit log — drives the "why is this suggested?" surface in the
	// memory event timeline.
	_ = e.memoryRepo.LogEvent(ctx, &model.MemoryEvent{
		MemoryID:  &newest.ID,
		TenantID:  tenantID,
		UserID:    userID,
		Action:    "auto_consolidated",
		ActorType: "system",
		Reason:    fmt.Sprintf("Observed in %d conversations — awaiting user confirmation", len(seen)),
		Metadata: map[string]any{
			"observations": len(seen),
			"superseded":   len(similar) - 1,
		},
	})
	slog.Info("preference auto-consolidated (kept pending)",
		"id", newest.ID.String(),
		"observations", len(seen),
		"superseded", len(similar)-1)
}

// ---------------------------------------------------------------------------
// LLM prompt + call + parse
// ---------------------------------------------------------------------------

// inferredPreference is one entry of the JSON array the extractor asks for.
// Type is "fact" or "preference" (FIX130; absent in the S8 shape, which
// meant a preference). Confidence and Evidence are pointers so the model can
// legitimately omit them.
type inferredPreference struct {
	Type       string   `json:"type"`
	Content    string   `json:"content"`
	Category   string   `json:"category"`
	Confidence *float64 `json:"confidence"`
	Evidence   *string  `json:"evidence,omitempty"`
}

func (p *inferredPreference) evidence() string {
	if p.Evidence == nil {
		return ""
	}
	r := []rune(strings.TrimSpace(*p.Evidence))
	if len(r) > 200 {
		return string(r[:200])
	}
	return string(r)
}

// confidence is the model's number, or 0.8 when it gave none (an explicit
// self-description rarely comes with a number attached).
func (p *inferredPreference) confidence() float64 {
	if p.Confidence == nil {
		return 0.8
	}
	return *p.Confidence
}

// extractedMemoryType maps an entry to the memory_type that renders it
// right in the injection block (renderMemoryBlock): facts go under
// <known_facts> ("use when relevant"), preferences under
// <standing_instructions>. A current project or interest is an
// ongoing_interest; every other fact an explicit_fact.
func extractedMemoryType(p *inferredPreference) string {
	if strings.EqualFold(strings.TrimSpace(p.Type), "fact") {
		switch strings.ToLower(strings.TrimSpace(p.Category)) {
		case "project", "interest", "hobby":
			return model.MemoryTypeOngoingInterest
		}
		return model.MemoryTypeExplicitFact
	}
	return model.MemoryTypeInferredPreference
}

const preferenceSystemPrompt = "You extract durable memories about a user from their conversation with an assistant. Output ONLY a JSON array. No markdown fencing. No commentary."

// buildExtractionPrompt assembles the user-message body the gateway sees.
// Kept long-form (with literal examples) because models do better on this
// task when given the exact shape they should emit.
func buildExtractionPrompt(messages []model.Message, known, rejected []string) string {
	var sb strings.Builder
	sb.WriteString(`Read the conversation below and list what is worth remembering about the USER for future conversations. Two kinds:

1. "fact" — something durable the user says about THEMSELVES: their name or nickname, role or job, organisation, where they live or work, family, an ongoing project, a dietary or health constraint they volunteer, a stable like or dislike.
2. "preference" — how the user wants the assistant to answer: language, length, format, tone, level of detail, programming languages or frameworks, way of working. Only when they state it or it clearly repeats.

RULES
- Use ONLY what the USER wrote (the [user] lines). Never take anything from [assistant] lines, and never guess beyond what the user said.
- A one-off request ("write an essay about X", "translate this") is not a memory.
- Never include passwords, keys, ID/account/card numbers, salary figures, or anything the user asks to keep private. Skip any value that is a placeholder such as <<PERSON_A>>.
- Write "content" and "evidence" in the same language the user writes in: a Thai conversation gets Thai content. "content" is one short statement about the user, under 100 characters, e.g. "ชื่อเล่นว่าภูผา" or "Prefers short answers".
- "evidence" is a short VERBATIM quote of the user's own words that shows it (copy it exactly, under 150 characters).
- "category" is one of: identity, work, location, family, project, health, diet, interest, language, response_style, coding, workflow.
- "confidence" between 0 and 1; only output entries with confidence >= 0.6.
- Output [] when there is nothing new worth remembering.

`)
	if len(known) > 0 {
		sb.WriteString("Already saved or waiting for the user's review (do NOT repeat these, even reworded):\n")
		for _, k := range known {
			sb.WriteString("- ")
			sb.WriteString(k)
			sb.WriteString("\n")
		}
		sb.WriteString("\n")
	}
	if len(rejected) > 0 {
		sb.WriteString("The user REJECTED these suggestions — never suggest them, or anything that means the same:\n")
		for _, k := range rejected {
			sb.WriteString("- ")
			sb.WriteString(k)
			sb.WriteString("\n")
		}
		sb.WriteString("\n")
	}

	sb.WriteString(`Output a JSON array, for example:
[
  {"type": "fact", "content": "ชื่อเล่นว่าภูผา ทำงานเป็นวิศวกรโยธา", "category": "identity", "confidence": 0.9, "evidence": "ผมชื่อเล่นว่าภูผา ทำงานเป็นวิศวกรโยธา"},
  {"type": "preference", "content": "Prefers short answers", "category": "response_style", "confidence": 0.8, "evidence": "keep it short please"}
]

--- CONVERSATION ---
`)
	for i := range messages {
		msg := &messages[i]
		limit := 400
		if msg.Role != "user" {
			limit = 200 // context only; nothing is taken from these lines
		}
		sb.WriteString(fmt.Sprintf("[%s] %s\n", msg.Role, truncateRunesMarked(msg.ContentMasked, limit)))
	}
	return sb.String()
}

// truncateRunesMarked caps s at n runes (never splitting a Thai glyph, as
// the old byte slice did) and marks the cut.
func truncateRunesMarked(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "... [truncated]"
}

// knownContents pulls the do-not-repeat lists: active + pending contents
// (capped at 100) and the suggestions the user rejected (capped at 50).
func (e *PreferenceExtractor) knownContents(
	ctx context.Context, userID, tenantID uuid.UUID,
) (known, rejected []string) {
	known = make([]string, 0, 50)
	for _, status := range []string{model.MemoryStatusActive, model.MemoryStatusPendingConfirmation} {
		rows, err := e.memoryRepo.ListByUser(ctx, userID, tenantID, repository.MemoryListFilters{
			Status: status,
			Limit:  50,
		})
		if err != nil {
			continue
		}
		for i := range rows {
			known = append(known, rows[i].Content)
		}
	}
	if len(known) > 100 {
		known = known[:100]
	}
	rows, err := e.memoryRepo.ListByUser(ctx, userID, tenantID, repository.MemoryListFilters{
		Status: model.MemoryStatusRejected,
		Limit:  50,
	})
	if err == nil {
		for i := range rows {
			rejected = append(rejected, rows[i].Content)
		}
	}
	return known, rejected
}

// llmCall is one extraction call as the log line reports it. Counts only.
type llmCall struct {
	content          string
	finishReason     string
	promptTokens     int
	completionTokens int
	reasoningTokens  int
	attempts         int
	err              error
}

// callLLM issues the non-streaming gateway call.
func (e *PreferenceExtractor) callLLM(
	parent context.Context, modelID, userPrompt string, callCtx GatewayCallContext,
) llmCall {
	ctx, cancel := context.WithTimeout(parent, preferenceExtractionTimeout)
	defer cancel()

	req := GatewayChatRequest{
		Model: modelID,
		Messages: []GatewayMessage{
			{Role: "system", Content: preferenceSystemPrompt},
			{Role: "user", Content: userPrompt},
		},
		MaxTokens:   preferenceMaxTokens,
		Temperature: preferenceTemperature,
		// FIX130 A1a — no thinking, copied from the state extractor
		// (FIX102 Part D). It also stops the orchestrator's adaptive
		// reasoning from switching thinking ON for a reasoning-capable
		// fallback model (it only does that when a request names no mode).
		Reasoning: &GatewayReasoningConfig{Mode: "off"},
	}
	resp, err := e.gateway.SendChatCompletion(ctx, callCtx, req)
	if err != nil {
		return llmCall{err: err}
	}
	out := llmCall{}
	if resp == nil || len(resp.Choices) == 0 {
		return out
	}
	if resp.Usage != nil {
		out.promptTokens = resp.Usage.PromptTokens
		out.completionTokens = resp.Usage.CompletionTokens
		out.reasoningTokens = resp.Usage.ReasoningTokens
	}
	out.finishReason = resp.Choices[0].FinishReason
	if s, ok := resp.Choices[0].Message.Content.(string); ok {
		out.content = s
	}
	return out
}

// parsePreferenceResponse is the tolerant JSON parser. It takes the FIRST
// JSON array of entries anywhere in the text — a model that wraps it in
// prose or a markdown fence still counts — caps each content at 100 runes,
// drops entries under 0.6 confidence, and caps the slice at 5 so a single
// bad answer cannot drown the user in suggestions. ok=false means no array
// was found at all (parse_failed / budget_exhausted in the log).
func parsePreferenceResponse(raw string) ([]inferredPreference, bool) {
	raws, ok := firstJSONArray(raw)
	if !ok {
		return nil, false
	}
	out := make([]inferredPreference, 0, len(raws))
	for i := range raws {
		p := raws[i]
		p.Content = strings.TrimSpace(p.Content)
		if p.Content == "" {
			continue
		}
		if c := p.confidence(); c < 0.6 || c > 1.0 {
			continue
		}
		// Trim long content. 100 rune cap (not byte) so Thai / CJK
		// content stays intact.
		if runes := []rune(p.Content); len(runes) > 100 {
			p.Content = string(runes[:100])
		}
		p.Category = strings.ToLower(strings.TrimSpace(p.Category))
		if runes := []rune(p.Category); len(runes) > 50 {
			p.Category = string(runes[:50])
		}
		out = append(out, p)
		if len(out) >= 5 {
			break
		}
	}
	return out, true
}

// firstJSONArray decodes the first '[' in s that starts a valid JSON array
// of entries; trailing text after it is ignored.
func firstJSONArray(s string) ([]inferredPreference, bool) {
	for i := strings.IndexByte(s, '['); i >= 0; {
		var raws []inferredPreference
		if err := json.NewDecoder(strings.NewReader(s[i:])).Decode(&raws); err == nil {
			return raws, true
		}
		next := strings.IndexByte(s[i+1:], '[')
		if next < 0 {
			break
		}
		i += 1 + next
	}
	return nil, false
}

// userCorpus is the user's words in the run's window, normalised for the
// quote check.
func userCorpus(msgs []model.Message) string {
	var sb strings.Builder
	for i := range msgs {
		if msgs[i].Role == "user" {
			sb.WriteString(quoteKey(msgs[i].ContentMasked))
			sb.WriteString("\x00")
		}
	}
	return sb.String()
}

// quotedFrom reports whether evidence is (close to) a verbatim quote of
// the user's words: after dropping case, spaces and punctuation, every
// piece of it between ellipses must appear in what the user wrote.
func quotedFrom(evidence, corpus string) bool {
	if strings.TrimSpace(evidence) == "" {
		return false
	}
	pieces := strings.FieldsFunc(evidence, func(r rune) bool { return r == '…' })
	found := 0
	for _, piece := range pieces {
		for _, p := range strings.Split(piece, "...") {
			k := quoteKey(p)
			if len([]rune(k)) < 2 {
				continue
			}
			if !strings.Contains(corpus, k) {
				return false
			}
			found++
		}
	}
	return found > 0
}

func quoteKey(s string) string {
	var sb strings.Builder
	for _, r := range strings.ToLower(s) {
		if unicode.IsSpace(r) || unicode.IsPunct(r) || unicode.IsSymbol(r) {
			continue
		}
		sb.WriteRune(r)
	}
	return sb.String()
}

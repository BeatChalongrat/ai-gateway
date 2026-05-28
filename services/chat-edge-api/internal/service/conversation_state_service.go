// ConversationStateService — S3 Structured Conversation State.
//
// Solves the "AI forgets after 80 messages" problem: as a
// conversation grows, this service periodically extracts a
// structured summary (goal, decisions, artifacts, tasks, rejected
// approaches, unresolved questions) via an LLM call and persists
// it to chat.conversation_states. The message flow then injects
// the rendered state into the system prompt so the model has
// durable context independent of the rolling message window.
//
// Two entry points feed the state:
//
//   GenerateOrUpdateState — full incremental extraction. Triggered
//     async after every Nth message (see maybeUpdateState in
//     message_service.go). Calls the gateway with a low-temperature
//     JSON-only prompt and merges the result with the existing
//     state.
//
//   ApplyToolUpdate — surgical incremental update. Driven by the
//     update_conversation_state AI tool so the model can record a
//     decision the instant it lands, without waiting for the next
//     extraction window.
//
// BuildStateContext is the render path: it shapes the persisted
// state into the <conversation_state> system-prompt block the
// MessageService.buildSystemPrefix concatenates onto every turn.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrStateNotFound = errors.New("conversation state: not found")
)

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

// ConversationStateService owns the lifecycle of
// chat.conversation_states. The fields are private so test wiring
// uses the typed constructor.
type ConversationStateService struct {
	states    *repository.ConversationStateRepo
	messages  *repository.MessageRepo
	conv      *repository.ConversationRepo
	gateway   *GatewayClient
	// Model used for extraction. When empty, GenerateOrUpdateState
	// falls back to the conversation's own model_id (so the bill
	// stays inside the conversation's allocated budget). Future
	// work could pin this to a cheap "summarisation" model.
	extractionModel string
}

func NewConversationStateService(
	states *repository.ConversationStateRepo,
	messages *repository.MessageRepo,
	conv *repository.ConversationRepo,
	gateway *GatewayClient,
) *ConversationStateService {
	return &ConversationStateService{
		states:   states,
		messages: messages,
		conv:     conv,
		gateway:  gateway,
	}
}

// SetExtractionModel pins the model the extractor uses regardless
// of which model the conversation chose. Optional; main.go can wire
// this from a config knob later.
func (s *ConversationStateService) SetExtractionModel(modelID string) {
	s.extractionModel = modelID
}

// ---------------------------------------------------------------------------
// GenerateOrUpdateState — extract structured state from messages
// ---------------------------------------------------------------------------

// extractionTimeout caps the gateway call that the extractor makes
// — runs in a background goroutine, so a hung provider can't pile
// up.
const extractionTimeout = 90 * time.Second

// extractionMaxTokens bounds the JSON envelope size. ~1500 tokens
// is enough for a 50-message conversation with rich state.
const extractionMaxTokens = 1500

// extractionTemperature — low so the JSON envelope stays
// deterministic. 0.1 has been good enough for OpenAI / Anthropic
// to consistently emit parseable JSON.
const extractionTemperature = 0.1

// GenerateOrUpdateState is the main extraction entry point. Reads
// the existing state (if any), fetches the messages since the last
// summary (or all messages on first run), shells out to the gateway
// for structured JSON, merges, and upserts.
//
// Safe to call from a background goroutine: every step uses its
// own bounded ctx, and a failure at any stage is logged but never
// panics. Returns the post-write state when extraction succeeded,
// the existing state when nothing new was available, or an error
// only on terminal infrastructure failures.
func (s *ConversationStateService) GenerateOrUpdateState(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (*model.ConversationState, error) {
	// 1. Existing state (may be nil for first-time extraction).
	existing, err := s.states.Get(ctx, convID, userID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("state lookup: %w", err)
	}

	// 2. Fetch messages to summarise. Incremental when state exists;
	//    falls back to the full ListContextByConversation slice
	//    (capped at 200) on first run.
	var newMessages []model.Message
	if existing != nil && existing.SummarizedUpToMessageID != nil {
		msgs, err := s.messages.ListAfter(
			ctx, tenantID, convID, *existing.SummarizedUpToMessageID, 200,
		)
		if err != nil {
			return nil, fmt.Errorf("list after: %w", err)
		}
		newMessages = msgs
	} else {
		msgs, err := s.messages.ListContextByConversation(ctx, tenantID, convID, 200)
		if err != nil {
			return nil, fmt.Errorf("list context: %w", err)
		}
		newMessages = msgs
	}
	if len(newMessages) == 0 {
		return existing, nil
	}

	// 3. Build the extraction prompt + call the gateway.
	conv, err := s.conv.GetByID(ctx, tenantID, convID)
	if err != nil {
		return nil, fmt.Errorf("conv lookup: %w", err)
	}
	modelID := s.extractionModel
	if modelID == "" {
		modelID = conv.ModelID
	}

	prompt := s.buildExtractionPrompt(existing, newMessages)
	rawJSON := s.callLLMForExtraction(ctx, modelID, prompt, GatewayCallContext{
		TenantID:       tenantID,
		UserID:         userID,
		ConversationID: convID,
	})
	if strings.TrimSpace(rawJSON) == "" {
		// Gateway failed or returned empty — keep existing state
		// rather than wiping it. Logged inside the LLM caller.
		return existing, nil
	}

	// 4. Parse + merge with existing.
	parsed := parseExtractionResponse(rawJSON)
	merged := mergeExtractionIntoState(existing, parsed, convID, userID, tenantID)

	// 5. Stamp compaction tracking.
	lastMsg := newMessages[len(newMessages)-1]
	mid := lastMsg.ID
	merged.SummarizedUpToMessageID = &mid
	count, cerr := s.messages.CountMessages(ctx, tenantID, convID)
	if cerr == nil {
		merged.MessageCountAtSummary = count
	}
	merged.SummaryTokenCount = estimateTokenCount(merged)

	// 6. Persist.
	if err := s.states.Upsert(ctx, merged); err != nil {
		return nil, fmt.Errorf("upsert state: %w", err)
	}
	return merged, nil
}

// ---------------------------------------------------------------------------
// LLM extraction prompt + call + parse
// ---------------------------------------------------------------------------

// buildExtractionPrompt assembles the user-message body sent to the
// gateway. The system message is a separate const so the prompt
// here stays focused on "what to analyse"; the system message
// owns "what shape to emit". Existing state is embedded so the
// model can merge rather than re-derive on every pass.
func (s *ConversationStateService) buildExtractionPrompt(
	existing *model.ConversationState, newMessages []model.Message,
) string {
	var sb strings.Builder
	sb.WriteString(`Analyse the following conversation messages and extract a structured state summary.

Output ONLY valid JSON with this exact shape (no markdown, no backticks):
{
  "current_goal": "One sentence describing what the user is trying to accomplish in this conversation",
  "structured_summary": {
    "key_context": "Important background for understanding this conversation",
    "progress": "What has been accomplished so far",
    "technical_details": "Key technical specifics (optional)"
  },
  "confirmed_decisions": [
    {"decision": "A decision explicitly agreed upon"}
  ],
  "active_artifacts": [
    {"name": "filename.ext", "status": "in_progress|completed|under_review", "version": 1}
  ],
  "open_tasks": [
    {"task": "Something still to be done", "status": "pending|in_progress"}
  ],
  "rejected_approaches": [
    {"approach": "Something considered but rejected"}
  ],
  "unresolved_questions": [
    {"question": "A genuine open question"}
  ]
}

Rules:
- current_goal: ONE sentence. If the goal shifted, use the latest.
- confirmed_decisions: only things explicitly agreed upon, not suggestions.
- active_artifacts: only files/documents actually created or worked on.
- open_tasks: TODOs identified but not yet completed.
- rejected_approaches: only if something was explicitly ruled out.
- unresolved_questions: only genuine open questions, not rhetorical.
- Keep each entry concise (under 100 chars when possible).
- Empty category → empty array [].
- Preserve the user's language (Thai or English) in entries.
`)

	// Embed existing state as merge context. The model is told to
	// "update/merge" so it doesn't have to re-derive every fact.
	if existing != nil && (existing.CurrentGoal != nil ||
		!isEmptyJSON(existing.ConfirmedDecisions) ||
		!isEmptyJSON(existing.OpenTasks)) {
		sb.WriteString("\n--- EXISTING STATE (update/merge with new information) ---\n")
		blob := map[string]any{
			"current_goal":         derefStr(existing.CurrentGoal),
			"structured_summary":   rawOrEmptyObject(existing.StructuredSummary),
			"confirmed_decisions":  rawOrEmptyArray(existing.ConfirmedDecisions),
			"active_artifacts":     rawOrEmptyArray(existing.ActiveArtifacts),
			"open_tasks":           rawOrEmptyArray(existing.OpenTasks),
			"rejected_approaches":  rawOrEmptyArray(existing.RejectedApproaches),
			"unresolved_questions": rawOrEmptyArray(existing.UnresolvedQuestions),
		}
		if b, err := json.Marshal(blob); err == nil {
			sb.Write(b)
			sb.WriteString("\n")
		}
	}

	sb.WriteString("\n--- NEW MESSAGES TO ANALYSE ---\n")
	for i := range newMessages {
		msg := &newMessages[i]
		content := msg.ContentMasked
		if len(content) > 500 {
			content = content[:500] + "... [truncated]"
		}
		sb.WriteString(fmt.Sprintf("[%s] %s\n", msg.Role, content))
	}
	return sb.String()
}

const extractionSystemPrompt = "You are a conversation analyst. Output ONLY valid JSON. No markdown fencing. No commentary."

// callLLMForExtraction issues the non-streaming gateway call.
// Returns "" on any failure so the caller falls back to keeping the
// existing state rather than crashing the extraction trigger.
func (s *ConversationStateService) callLLMForExtraction(
	parent context.Context, modelID, userPrompt string, callCtx GatewayCallContext,
) string {
	ctx, cancel := context.WithTimeout(parent, extractionTimeout)
	defer cancel()

	req := GatewayChatRequest{
		Model: modelID,
		Messages: []GatewayMessage{
			{Role: "system", Content: extractionSystemPrompt},
			{Role: "user", Content: userPrompt},
		},
		MaxTokens:   extractionMaxTokens,
		Temperature: extractionTemperature,
	}
	resp, err := s.gateway.SendChatCompletion(ctx, callCtx, req)
	if err != nil {
		slog.Warn("conversation state: extraction call failed",
			"conv", callCtx.ConversationID.String(), "err", err)
		return ""
	}
	if resp == nil || len(resp.Choices) == 0 {
		return ""
	}
	if s, ok := resp.Choices[0].Message.Content.(string); ok {
		return s
	}
	return ""
}

// parseExtractionResponse decodes the gateway's JSON envelope.
// Tolerant of markdown fences (some providers add them even when
// asked not to). Returns a zero-valued extractedState on any error
// so the merge step can fall through to "keep existing".
type extractedState struct {
	CurrentGoal         string          `json:"current_goal"`
	StructuredSummary   json.RawMessage `json:"structured_summary"`
	ConfirmedDecisions  json.RawMessage `json:"confirmed_decisions"`
	ActiveArtifacts     json.RawMessage `json:"active_artifacts"`
	OpenTasks           json.RawMessage `json:"open_tasks"`
	RejectedApproaches  json.RawMessage `json:"rejected_approaches"`
	UnresolvedQuestions json.RawMessage `json:"unresolved_questions"`
	parsed              bool
}

func parseExtractionResponse(raw string) extractedState {
	cleaned := strings.TrimSpace(raw)
	cleaned = strings.TrimPrefix(cleaned, "```json")
	cleaned = strings.TrimPrefix(cleaned, "```")
	cleaned = strings.TrimSuffix(cleaned, "```")
	cleaned = strings.TrimSpace(cleaned)

	var out extractedState
	if err := json.Unmarshal([]byte(cleaned), &out); err != nil {
		return out
	}
	out.parsed = true
	return out
}

// mergeExtractionIntoState resolves the (existing, fresh) → state
// merge. Today we trust the extractor's output verbatim (it was
// given the existing state as context and told to merge) — that
// keeps the merge logic linear and lets future prompt tuning shift
// behaviour without re-coding the Go side.
//
// On a parse failure we keep the existing state's content but
// still return a state object so the caller can stamp compaction
// fields and re-upsert (handy for keeping the "last attempted"
// timestamp fresh even when extraction failed).
func mergeExtractionIntoState(
	existing *model.ConversationState,
	fresh extractedState,
	convID, userID, tenantID uuid.UUID,
) *model.ConversationState {
	out := &model.ConversationState{
		ConversationID: convID,
		UserID:         userID,
		TenantID:       tenantID,
	}
	if existing != nil {
		out.CurrentGoal = existing.CurrentGoal
		out.StructuredSummary = existing.StructuredSummary
		out.ConfirmedDecisions = existing.ConfirmedDecisions
		out.ActiveArtifacts = existing.ActiveArtifacts
		out.OpenTasks = existing.OpenTasks
		out.RejectedApproaches = existing.RejectedApproaches
		out.UnresolvedQuestions = existing.UnresolvedQuestions
	}
	if !fresh.parsed {
		return out
	}
	if fresh.CurrentGoal != "" {
		g := fresh.CurrentGoal
		out.CurrentGoal = &g
	}
	if len(fresh.StructuredSummary) > 0 {
		out.StructuredSummary = fresh.StructuredSummary
	}
	if len(fresh.ConfirmedDecisions) > 0 {
		out.ConfirmedDecisions = fresh.ConfirmedDecisions
	}
	if len(fresh.ActiveArtifacts) > 0 {
		out.ActiveArtifacts = fresh.ActiveArtifacts
	}
	if len(fresh.OpenTasks) > 0 {
		out.OpenTasks = fresh.OpenTasks
	}
	if len(fresh.RejectedApproaches) > 0 {
		out.RejectedApproaches = fresh.RejectedApproaches
	}
	if len(fresh.UnresolvedQuestions) > 0 {
		out.UnresolvedQuestions = fresh.UnresolvedQuestions
	}
	return out
}

// ---------------------------------------------------------------------------
// Tool-driven incremental updates
// ---------------------------------------------------------------------------

// ToolStateUpdate carries the field-level deltas the
// update_conversation_state AI tool emits. All fields are
// optional; empty strings are treated as "skip this field".
type ToolStateUpdate struct {
	CurrentGoal     string
	AddDecision     string
	AddTask         string
	CompleteTask    string
	AddArtifact     string
	RejectApproach  string
	AddQuestion     string
	ResolveQuestion string
}

// ApplyToolUpdate fetches the current state (creating a zero-row
// in memory when none exists), applies each non-empty delta, and
// upserts. Returns a human-readable list of changes so the tool
// dispatcher can echo them back to the AI as the tool result.
func (s *ConversationStateService) ApplyToolUpdate(
	ctx context.Context, convID, userID, tenantID uuid.UUID, in ToolStateUpdate,
) ([]string, error) {
	state, err := s.states.Get(ctx, convID, userID, tenantID)
	if err != nil {
		return nil, err
	}
	if state == nil {
		state = &model.ConversationState{
			ConversationID: convID,
			UserID:         userID,
			TenantID:       tenantID,
		}
	}

	changes := make([]string, 0, 8)
	if strings.TrimSpace(in.CurrentGoal) != "" {
		g := strings.TrimSpace(in.CurrentGoal)
		state.CurrentGoal = &g
		changes = append(changes, "Goal set: "+g)
	}
	if v := strings.TrimSpace(in.AddDecision); v != "" {
		state.ConfirmedDecisions = appendToJSONArray(state.ConfirmedDecisions, "decision", v)
		changes = append(changes, "Decision recorded: "+v)
	}
	if v := strings.TrimSpace(in.AddTask); v != "" {
		state.OpenTasks = appendToJSONArray(state.OpenTasks, "task", v)
		changes = append(changes, "Task added: "+v)
	}
	if v := strings.TrimSpace(in.CompleteTask); v != "" {
		state.OpenTasks = removeFromJSONArray(state.OpenTasks, "task", v)
		changes = append(changes, "Task completed: "+v)
	}
	if v := strings.TrimSpace(in.AddArtifact); v != "" {
		state.ActiveArtifacts = appendToJSONArray(state.ActiveArtifacts, "name", v)
		changes = append(changes, "Artifact tracked: "+v)
	}
	if v := strings.TrimSpace(in.RejectApproach); v != "" {
		state.RejectedApproaches = appendToJSONArray(state.RejectedApproaches, "approach", v)
		changes = append(changes, "Approach rejected: "+v)
	}
	if v := strings.TrimSpace(in.AddQuestion); v != "" {
		state.UnresolvedQuestions = appendToJSONArray(state.UnresolvedQuestions, "question", v)
		changes = append(changes, "Question noted: "+v)
	}
	if v := strings.TrimSpace(in.ResolveQuestion); v != "" {
		state.UnresolvedQuestions = removeFromJSONArray(state.UnresolvedQuestions, "question", v)
		changes = append(changes, "Question resolved: "+v)
	}

	if len(changes) == 0 {
		return changes, nil
	}
	state.SummaryTokenCount = estimateTokenCount(state)
	if err := s.states.Upsert(ctx, state); err != nil {
		return nil, err
	}
	return changes, nil
}

// Get exposes the repo's read path through the service so handlers
// can fetch a conversation's state without taking a direct repo
// dependency.
func (s *ConversationStateService) Get(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (*model.ConversationState, error) {
	return s.states.Get(ctx, convID, userID, tenantID)
}

// ---------------------------------------------------------------------------
// BuildStateContext — render <conversation_state> system-prompt block
// ---------------------------------------------------------------------------

// BuildStateContext is the read path called by MessageService.
// buildSystemPrefix on every turn. Returns "" when no state is
// persisted yet — the caller treats that as "no block, skip".
//
// We DO NOT require a non-null current_goal anymore — a state row
// with only tool-driven updates (e.g. "added one task") should
// still render. The injection block opens with the goal when
// present and falls back to a generic header otherwise.
func (s *ConversationStateService) BuildStateContext(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) string {
	state, err := s.states.Get(ctx, convID, userID, tenantID)
	if err != nil || state == nil {
		return ""
	}
	if isStateEmpty(state) {
		return ""
	}

	var sb strings.Builder
	sb.WriteString("<conversation_state>\n")
	sb.WriteString("Summary of this conversation so far (older messages have been compacted):\n")

	if state.CurrentGoal != nil && *state.CurrentGoal != "" {
		sb.WriteString("\nGoal: ")
		sb.WriteString(*state.CurrentGoal)
		sb.WriteString("\n")
	}

	// structured_summary is a map[string]string in practice — render
	// each non-empty entry as "Key: value".
	if len(state.StructuredSummary) > 2 { // not "{}"
		var summary map[string]string
		if err := json.Unmarshal(state.StructuredSummary, &summary); err == nil {
			for _, key := range []string{"key_context", "progress", "technical_details"} {
				if val := strings.TrimSpace(summary[key]); val != "" {
					sb.WriteString("\n")
					sb.WriteString(titleCase(strings.ReplaceAll(key, "_", " ")))
					sb.WriteString(": ")
					sb.WriteString(val)
					sb.WriteString("\n")
				}
			}
		}
	}

	writeJSONArray(&sb, "Confirmed decisions", state.ConfirmedDecisions, "decision")
	writeArtifacts(&sb, state.ActiveArtifacts)
	writeJSONArray(&sb, "Open tasks", state.OpenTasks, "task")
	writeJSONArray(&sb, "Rejected approaches", state.RejectedApproaches, "approach")
	writeJSONArray(&sb, "Unresolved questions", state.UnresolvedQuestions, "question")

	sb.WriteString("\n</conversation_state>\n")
	return sb.String()
}

// writeJSONArray renders one "Label:\n- item\n- item\n" block.
// `field` is the single load-bearing key inside each object entry
// (e.g. "decision" / "task" / "question"). Silently skips when the
// array is empty or the unmarshal fails — these are best-effort
// blocks and a single malformed entry shouldn't fail the turn.
func writeJSONArray(sb *strings.Builder, label string, data json.RawMessage, field string) {
	if len(data) <= 2 { // "[]"
		return
	}
	var items []map[string]any
	if err := json.Unmarshal(data, &items); err != nil || len(items) == 0 {
		return
	}
	wrote := false
	for _, item := range items {
		val, _ := item[field].(string)
		val = strings.TrimSpace(val)
		if val == "" {
			continue
		}
		if !wrote {
			sb.WriteString("\n")
			sb.WriteString(label)
			sb.WriteString(":\n")
			wrote = true
		}
		sb.WriteString("- ")
		sb.WriteString(val)
		sb.WriteString("\n")
	}
}

// writeArtifacts is the artifact-specific renderer — each entry has
// name + status (+ optional version). Status renders inline so the
// model gets "report.docx (in_progress)" on one line.
func writeArtifacts(sb *strings.Builder, data json.RawMessage) {
	if len(data) <= 2 {
		return
	}
	var items []map[string]any
	if err := json.Unmarshal(data, &items); err != nil || len(items) == 0 {
		return
	}
	wrote := false
	for _, item := range items {
		name, _ := item["name"].(string)
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		if !wrote {
			sb.WriteString("\nActive artifacts:\n")
			wrote = true
		}
		status, _ := item["status"].(string)
		if status != "" {
			sb.WriteString("- ")
			sb.WriteString(name)
			sb.WriteString(" (")
			sb.WriteString(status)
			sb.WriteString(")\n")
		} else {
			sb.WriteString("- ")
			sb.WriteString(name)
			sb.WriteString("\n")
		}
	}
}

// ---------------------------------------------------------------------------
// JSON-array helpers (used by tool dispatcher + service)
// ---------------------------------------------------------------------------

// appendToJSONArray adds {key: value} to the array stored in `data`.
// Dedups (case-insensitive equality) against existing entries.
// Returns a fresh json.RawMessage. On marshal failure returns the
// original blob unchanged — same defensive pattern as the rest of
// the service.
func appendToJSONArray(data json.RawMessage, key, value string) json.RawMessage {
	var items []map[string]any
	if len(data) > 2 {
		_ = json.Unmarshal(data, &items)
	}
	for _, item := range items {
		if v, ok := item[key].(string); ok && strings.EqualFold(strings.TrimSpace(v), strings.TrimSpace(value)) {
			return data
		}
	}
	items = append(items, map[string]any{key: value})
	out, err := json.Marshal(items)
	if err != nil {
		return data
	}
	return out
}

// removeFromJSONArray drops any entry whose `key` value contains
// `value` (case-insensitive substring match). The fuzzy match is
// intentional — the AI's "complete this task" call rarely
// reproduces the exact stored wording, so we want close-enough
// matches to delete cleanly.
func removeFromJSONArray(data json.RawMessage, key, value string) json.RawMessage {
	if len(data) <= 2 {
		return data
	}
	var items []map[string]any
	if err := json.Unmarshal(data, &items); err != nil {
		return data
	}
	target := strings.ToLower(strings.TrimSpace(value))
	if target == "" {
		return data
	}
	filtered := make([]map[string]any, 0, len(items))
	for _, item := range items {
		v, _ := item[key].(string)
		if v != "" && strings.Contains(strings.ToLower(v), target) {
			continue
		}
		filtered = append(filtered, item)
	}
	out, err := json.Marshal(filtered)
	if err != nil {
		return data
	}
	return out
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

func isStateEmpty(s *model.ConversationState) bool {
	if s == nil {
		return true
	}
	if s.CurrentGoal != nil && strings.TrimSpace(*s.CurrentGoal) != "" {
		return false
	}
	if !isEmptyJSON(s.StructuredSummary) {
		return false
	}
	for _, blob := range []json.RawMessage{
		s.ConfirmedDecisions, s.ActiveArtifacts, s.OpenTasks,
		s.RejectedApproaches, s.UnresolvedQuestions,
	} {
		if !isEmptyJSON(blob) {
			return false
		}
	}
	return true
}

func isEmptyJSON(b json.RawMessage) bool {
	t := strings.TrimSpace(string(b))
	return t == "" || t == "{}" || t == "[]" || t == "null"
}

func rawOrEmptyObject(b json.RawMessage) json.RawMessage {
	if len(b) == 0 {
		return json.RawMessage(`{}`)
	}
	return b
}

func rawOrEmptyArray(b json.RawMessage) json.RawMessage {
	if len(b) == 0 {
		return json.RawMessage(`[]`)
	}
	return b
}

func derefStr(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

// estimateTokenCount — rough 4-chars-per-token heuristic across all
// state fields. Good enough for the future "is the block too big?"
// decision; not accurate enough to bill against.
func estimateTokenCount(s *model.ConversationState) int {
	if s == nil {
		return 0
	}
	total := 0
	if s.CurrentGoal != nil {
		total += len(*s.CurrentGoal)
	}
	for _, b := range []json.RawMessage{
		s.StructuredSummary, s.ConfirmedDecisions, s.ActiveArtifacts,
		s.OpenTasks, s.RejectedApproaches, s.UnresolvedQuestions,
	} {
		total += len(b)
	}
	return total / 4
}

// titleCase is a stdlib-free uppercase-first-letter for "key context"
// → "Key context". Avoids the deprecated strings.Title.
func titleCase(s string) string {
	if s == "" {
		return s
	}
	r := []rune(s)
	if r[0] >= 'a' && r[0] <= 'z' {
		r[0] = r[0] - 32
	}
	return string(r)
}

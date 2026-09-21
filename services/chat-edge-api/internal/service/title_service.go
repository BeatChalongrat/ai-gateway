// TitleService names a conversation once, after its first turn, by asking
// the model that ANSWERED that turn for a one-line summary.
//
// FIX122 — what this used to do, and why it stopped. The request went to a
// hard-coded "gpt-4o-mini" whatever the person had picked. When that id
// failed at the primary, the gateway's fallback chain carried the first
// user message (and a snippet of the answer) to whichever external
// provider answered — measured: gpt-5.6-luna, deepseek-v4-flash,
// glm-5.3-flash — for a chat the person had opened on a self-hosted model.
// When the walk ran dry the page got a 502. And the result was written
// with an unconditional rename, so a title the person had typed in the
// meantime was replaced.
//
// The rules now:
//   - the model is the one on the first assistant row — the SERVED model,
//     so an Auto chat that fell back is named by whoever really answered;
//   - the call may not leave that model: ``allowed_providers`` names no
//     provider, which puts every fallback hop outside the boundary;
//   - the request is small: two short messages, no tools, thinking off, a
//     low output cap;
//   - no answer on the first turn, a failed call, an empty completion → the
//     first words of the person's own message. Never an error to the page;
//   - only a title a machine put there is replaced ("New conversation" or
//     the first-message truncation), only on the first turn, and the write
//     is compare-and-set against the title that was read;
//   - a temporary chat (memory off) is never sent anywhere for a title.
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

// defaultConversationTitle is the placeholder a new row carries.
const defaultConversationTitle = "New conversation"

// titleSystemPrompt is the whole instruction. The language clause is
// load-bearing: without it small models title Thai chats in English.
const titleSystemPrompt = "Write a title of 3 to 6 words for this conversation, in the same language the user wrote in. Reply with the title only: no quotes, no explanation, no punctuation at the end."

// titleMaxTokens caps the completion. Six words of Thai are ~30 tokens;
// the rest is slack for a model that leaks a little preamble.
const titleMaxTokens = 64

// titleTimeout bounds the call. A title that is not back by then is not
// worth waiting for — the first words of the message are already there.
const titleTimeout = 20 * time.Second

// titleUserMaxRunes / assistantSnippetMaxRunes cap what is sent. The topic
// is in the opening lines; a pasted document is not title material.
const titleUserMaxRunes = 500
const assistantSnippetMaxRunes = 200

// titleNoFallbackBoundary is sent as ``metadata.allowed_providers``. The
// gateway never applies that list to the model a request names, only to
// the candidates of a fallback walk (dispatcher hops, the orchestrator's
// chain, the streaming pre-check) — so a list that names no provider
// means "this model or nothing".
var titleNoFallbackBoundary = []string{"none:title-generation"}

// titleMaxRunes is the final cap applied to whatever the model
// returns. The DB column is TEXT; this cap is a UX guard so the
// sidebar's single-line ellipsis doesn't kick in for normal titles.
const titleMaxRunes = 80

var (
	// ErrTitleConversationNotFound is returned when the conversation
	// id doesn't resolve under the caller's tenant. Maps to 404.
	ErrTitleConversationNotFound = errors.New("title: conversation not found")
	// ErrTitleNoMessages is returned when the conversation exists but
	// has no messages yet — there is nothing to summarize. Maps to
	// 409/422 at the handler boundary so the caller can retry once
	// the first turn lands.
	ErrTitleNoMessages = errors.New("title: conversation has no messages")
)

// Title sources, reported to the caller and logged.
const (
	TitleSourceModel    = "model"    // the serving model wrote it
	TitleSourceFallback = "fallback" // first words of the user's message
	TitleSourceKept     = "kept"     // nothing was changed
)

// TitleResult is what Generate decided. Title is always the title the
// conversation carries when Generate returns.
type TitleResult struct {
	Title  string
	Source string
	// Reason says why a title was kept or fell back; "" for a model title.
	Reason string
	// Model is the model that was asked; "" when none was.
	Model string
}

// TitleService is the title-generation entry point, driven by the
// generate-title HTTP endpoint via the ConversationHandler.
type TitleService struct {
	gateway       *GatewayClient
	conversations *repository.ConversationRepo
	messages      *repository.MessageRepo
}

func NewTitleService(g *GatewayClient) *TitleService {
	return &TitleService{gateway: g}
}

// SetRepos wires in the persistence layer. Done after construction
// so main.go can keep the existing TitleService instance reference
// (held by MessageService) without churning constructor signatures.
func (s *TitleService) SetRepos(conv *repository.ConversationRepo, msg *repository.MessageRepo) {
	s.conversations = conv
	s.messages = msg
}

// titlePlan is the decision, separate from the I/O so it can be tested
// as a table.
type titlePlan struct {
	// action: "keep" (touch nothing), "fallback" (first words, no model
	// call) or "ask" (call model, fall back to first words if it fails).
	action string
	reason string
	model  string
}

// planTitle decides what to do for a conversation and the head of its
// history (oldest first).
func planTitle(conv *model.Conversation, history []model.Message) titlePlan {
	firstUser, firstAsst := firstUserAndAssistant(history)
	if firstUser == nil {
		return titlePlan{action: "keep", reason: "no_messages"}
	}
	if !isMachineTitle(conv.Title, firstUser.ContentMasked) {
		return titlePlan{action: "keep", reason: "title_already_set"}
	}
	users := 0
	for i := range history {
		if history[i].Role == "user" {
			users++
		}
	}
	if users > 1 {
		return titlePlan{action: "keep", reason: "not_first_turn"}
	}
	if !conv.MemoryEnabled {
		return titlePlan{action: "keep", reason: "temporary_chat"}
	}
	if firstAsst == nil || firstAsst.Status != "completed" {
		return titlePlan{action: "fallback", reason: "first_turn_unanswered"}
	}
	if firstAsst.ModelID == nil || strings.TrimSpace(*firstAsst.ModelID) == "" {
		return titlePlan{action: "fallback", reason: "served_model_unknown"}
	}
	return titlePlan{action: "ask", model: strings.TrimSpace(*firstAsst.ModelID)}
}

// isMachineTitle reports whether nobody chose this title: the creation
// placeholder, or one of the truncations of the first message that the
// server (finalizeConversation, this file's fallback) writes.
func isMachineTitle(title, firstUserMessage string) bool {
	t := strings.TrimSpace(title)
	if t == "" || t == defaultConversationTitle {
		return true
	}
	return t == makeTitleFrom(firstUserMessage) ||
		t == cleanTitle(fallbackTitleFromMessage(firstUserMessage))
}

// buildTitleRequest is the whole request: two short messages and the
// switches that keep it cheap and keep it home.
func buildTitleRequest(modelID, userMsg, asstSnippet string) GatewayChatRequest {
	return GatewayChatRequest{
		Model: modelID,
		Messages: []GatewayMessage{
			{Role: "system", Content: titleSystemPrompt},
			{Role: "user", Content: buildTitlePrompt(userMsg, asstSnippet)},
		},
		MaxTokens: titleMaxTokens,
		// Thinking shares the output cap on a reasoning model: with it on,
		// 64 tokens are all thought and the title comes back empty.
		Reasoning: &GatewayReasoningConfig{Mode: "off"},
		Metadata:  map[string]any{"allowed_providers": titleNoFallbackBoundary},
	}
}

// Generate names the conversation if it is still unnamed, and reports
// what it did. A model failure is not an error: the result is then the
// first words of the user's message. Errors are reserved for "not yours",
// "nothing to name" and the database.
func (s *TitleService) Generate(
	ctx context.Context,
	tenantID, userID, convID uuid.UUID,
) (TitleResult, error) {
	if s.conversations == nil || s.messages == nil {
		return TitleResult{}, errors.New("title: service not wired (repos missing)")
	}
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return TitleResult{}, ErrTitleConversationNotFound
		}
		return TitleResult{}, err
	}
	if conv.UserID != userID {
		// Don't leak existence to other users of the same tenant.
		return TitleResult{}, ErrTitleConversationNotFound
	}

	history, err := s.messages.ListContextByConversation(ctx, tenantID, convID, 20)
	if err != nil {
		return TitleResult{}, fmt.Errorf("title: load history: %w", err)
	}
	plan := planTitle(conv, history)
	if plan.action == "keep" {
		if plan.reason == "no_messages" {
			return TitleResult{}, ErrTitleNoMessages
		}
		slog.Info("title: kept", "conv", convID.String(), "reason", plan.reason)
		return TitleResult{Title: conv.Title, Source: TitleSourceKept, Reason: plan.reason}, nil
	}

	firstUser, firstAsst := firstUserAndAssistant(history)
	chosen, source, reason := cleanTitle(fallbackTitleFromMessage(firstUser.ContentMasked)), TitleSourceFallback, plan.reason
	if plan.action == "ask" {
		if t, why := s.askModel(ctx, tenantID, userID, convID, plan.model, firstUser, firstAsst); t != "" {
			chosen, source, reason = t, TitleSourceModel, ""
		} else {
			reason = why
		}
	}
	if chosen == "" || chosen == conv.Title {
		if chosen == "" {
			source, reason = TitleSourceKept, "nothing_to_title_with"
		}
		return TitleResult{Title: conv.Title, Source: source, Reason: reason, Model: plan.model}, nil
	}
	written, err := s.conversations.UpdateTitleIfCurrent(ctx, tenantID, convID, conv.Title, chosen)
	if err != nil {
		return TitleResult{}, err
	}
	if !written {
		// Renamed while the model was writing. Theirs stands.
		current := conv.Title
		if fresh, ferr := s.conversations.GetByID(ctx, tenantID, convID); ferr == nil {
			current = fresh.Title
		}
		slog.Info("title: kept", "conv", convID.String(), "reason", "renamed_meanwhile")
		return TitleResult{Title: current, Source: TitleSourceKept, Reason: "renamed_meanwhile", Model: plan.model}, nil
	}
	slog.Info("title: written", "conv", convID.String(), "source", source, "reason", reason, "model", plan.model)
	return TitleResult{Title: chosen, Source: source, Reason: reason, Model: plan.model}, nil
}

// askModel makes the one small call. Returns the cleaned title, or "" and
// why there is none. Logs what the call cost either way.
func (s *TitleService) askModel(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
	modelID string, firstUser, firstAsst *model.Message,
) (string, string) {
	started := time.Now()
	callCtx, cancel := context.WithTimeout(ctx, titleTimeout)
	defer cancel()
	resp, err := s.gateway.SendChatCompletion(callCtx, GatewayCallContext{
		TenantID:       tenantID,
		UserID:         userID,
		ConversationID: convID,
	}, buildTitleRequest(modelID, truncateRunes(strings.TrimSpace(firstUser.ContentMasked), titleUserMaxRunes), assistantSnippet(firstAsst)))
	elapsed := time.Since(started).Milliseconds()
	if err != nil {
		slog.Warn("title: model call failed, using the first words",
			"conv", convID.String(), "model", modelID, "ms", elapsed, "err", err)
		return "", "model_call_failed"
	}
	title := cleanTitle(parseTitleCompletion(extractContent(resp)))
	prompt, completion, thinking, served := 0, 0, 0, ""
	if resp != nil {
		served = resp.Model
		if resp.Usage != nil {
			prompt, completion, thinking = resp.Usage.PromptTokens, resp.Usage.CompletionTokens, resp.Usage.ReasoningTokens
		}
	}
	slog.Info("title: model answered", "conv", convID.String(), "model", modelID, "served", served, "ms", elapsed,
		"prompt_tokens", prompt, "completion_tokens", completion, "reasoning_tokens", thinking, "empty", title == "")
	if title == "" {
		return "", "model_returned_nothing"
	}
	return title, ""
}

// resolveTitle turns a title-model completion into the final sidebar title.
// It parses the strict {"title": string} envelope; when the completion is
// unusable — empty, an empty title field, or a broken/truncated envelope like
// `{"title":` that a reasoning model can emit when its structured output is cut
// off — it falls back to the user's own first message rather than leaking JSON
// braces into the sidebar (FIX21 §3). Returns "" only when there is nothing at
// all to title with, which the caller maps to the "empty completion" error.
func resolveTitle(rawCompletion, firstUserMessage string) string {
	if t := cleanTitle(parseTitleCompletion(rawCompletion)); t != "" {
		return t
	}
	return cleanTitle(fallbackTitleFromMessage(firstUserMessage))
}

// fallbackTitleFromMessage derives a plain-text title from the user's first
// message: its first line, trimmed to a sidebar-sized prefix. cleanTitle runs
// on the result at the call site.
func fallbackTitleFromMessage(userMessage string) string {
	s := strings.TrimSpace(userMessage)
	if s == "" {
		return ""
	}
	if i := strings.IndexAny(s, "\r\n"); i >= 0 {
		s = s[:i]
	}
	runes := []rune(strings.TrimSpace(s))
	const fallbackRunes = 48
	if len(runes) > fallbackRunes {
		return string(runes[:fallbackRunes])
	}
	return string(runes)
}

// parseTitleCompletion pulls the title out of the model's completion.
// With the json_schema response_format the completion is a JSON object
// {"title": "..."}; we decode it and return the field verbatim (still
// pre-clean — cleanTitle runs afterwards). If the provider ignored the
// schema and returned free text (or malformed JSON), we gracefully
// fall back to treating the raw completion as the title so a title
// still lands. Non-ASCII (Thai/CJK) is preserved either way.
func parseTitleCompletion(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return ""
	}
	var parsed struct {
		Title string `json:"title"`
	}
	if err := json.Unmarshal([]byte(s), &parsed); err == nil {
		// Valid JSON: trust the structured path. Return the title
		// field (possibly empty — the caller maps empty to a
		// "gateway returned empty completion" error rather than
		// leaking the raw "{...}" braces into the sidebar).
		return strings.TrimSpace(parsed.Title)
	}
	// Not valid JSON. Two very different cases hide here:
	//   1. The provider ignored the schema and returned genuine FREE TEXT
	//      ("Debugging API rate limits") — use it as the title.
	//   2. The structured-output envelope was BROKEN or TRUNCATED
	//      (`{"title":`, `{"title": "half`) — a reasoning model whose JSON
	//      got cut off. Returning THAT raw leaks `{"title":` into the
	//      sidebar (FIX21 §3, seen live). Signal "no usable title" so the
	//      caller falls back to the user's own message.
	if looksLikeTitleEnvelope(s) {
		return ""
	}
	return s
}

// looksLikeTitleEnvelope reports whether a non-JSON-parseable string is a
// broken attempt at the {"title": ...} envelope (so it must NOT be used as a
// title verbatim), rather than genuine free text. Free-text titles never begin
// with "{" nor contain the quoted key `"title"`.
func looksLikeTitleEnvelope(s string) bool {
	t := strings.TrimSpace(s)
	return strings.HasPrefix(t, "{") || strings.Contains(t, `"title"`)
}

// firstUserAndAssistant returns the earliest user message and the
// earliest assistant message in chronological order. Either may be
// nil. The slice argument is assumed to be oldest-first.
func firstUserAndAssistant(history []model.Message) (*model.Message, *model.Message) {
	var u, a *model.Message
	for i := range history {
		m := &history[i]
		if u == nil && m.Role == "user" {
			u = m
		}
		if a == nil && m.Role == "assistant" && strings.TrimSpace(m.ContentMasked) != "" {
			a = m
		}
		if u != nil && a != nil {
			break
		}
	}
	return u, a
}

// assistantSnippet returns the assistant text trimmed to the first
// ~200 runes. Empty string when no assistant reply exists yet.
func assistantSnippet(m *model.Message) string {
	if m == nil {
		return ""
	}
	s := strings.TrimSpace(m.ContentMasked)
	if s == "" {
		return ""
	}
	runes := []rune(s)
	if len(runes) <= assistantSnippetMaxRunes {
		return s
	}
	return string(runes[:assistantSnippetMaxRunes]) + "…"
}

func buildTitlePrompt(userMsg, asstSnippet string) string {
	user := strings.TrimSpace(userMsg)
	if asstSnippet == "" {
		return "User: " + user
	}
	return "User: " + user + "\nAssistant: " + asstSnippet
}

// cleanTitle normalizes the model's freeform completion into a clean
// sidebar string: strip surrounding quotes the model often emits
// despite the system prompt asking otherwise, drop trailing
// punctuation, collapse whitespace, cap to titleMaxRunes.
func cleanTitle(raw string) string {
	s := strings.TrimSpace(raw)
	// Models love to wrap titles in quotes regardless of instructions.
	s = strings.Trim(s, "\"'“”‘’`")
	// Collapse any internal whitespace runs to single spaces so a
	// completion like "Foo   Bar\nBaz" doesn't render with awkward
	// gaps in the sidebar.
	s = strings.Join(strings.Fields(s), " ")
	// Strip trailing punctuation that would look weird in a sidebar
	// list — periods, commas, semicolons, exclamation/question
	// marks. Leaves intra-title punctuation alone (e.g. "API rate
	// limits: how to debug").
	s = strings.TrimRight(s, ".,;!?。、！？")
	// `"Title".` — the full stop sat outside the quotes, so they are only
	// reachable now.
	s = strings.TrimSpace(strings.Trim(s, "\"'“”‘’`"))
	runes := []rune(s)
	if len(runes) > titleMaxRunes {
		s = string(runes[:titleMaxRunes]) + "…"
	}
	return s
}

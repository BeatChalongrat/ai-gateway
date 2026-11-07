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
// The rules now (FIX122, FIX124):
//   - a chat is named after the first turn that was ANSWERED, whichever turn
//     that is; until then it carries the first words of its first message,
//     never "New conversation";
//   - the model that answered is asked first (the SERVED model, so an Auto
//     chat that fell back is named by whoever really answered); when it
//     cannot, any model that works is — the gateway's router and its own
//     fallback list, the same a chat turn gets — inside 20 s in total;
//   - the request is small: two short messages, no tools, thinking off;
//   - when no model can, the first words stand. Never an error to the page;
//   - only a title a machine put there is replaced, and the write is
//     compare-and-set against the title that was read: a rename is never
//     overwritten, and a chat is named once;
//   - a chat the user STARTED as temporary (is_temporary — FIX127 B1) is never sent anywhere for a title; memory off alone does not stop a title.
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
// load-bearing, and so is spelling Thai out: with only "the same language
// the user wrote in", qwen titled a Thai question "Why the sky is blue"
// (measured) — the instruction itself is English and a short message gives
// the model little to outweigh it.
const titleSystemPrompt = "Write a title of 3 to 6 words for this conversation. The title MUST be in the same language as the user's message: if the user writes in Thai, the title must be in Thai; if in English, in English. Reply with the title only: no quotes, no explanation, no punctuation at the end."

// titleMaxTokens caps the completion, thinking included. A model whose
// thinking can be switched off (qwen) stops after the six words — measured 6
// completion tokens — so the cap costs it nothing. A model with no off-switch
// thinks first: at 64, deepseek-v4-flash spent the whole cap thinking and the
// gateway answered output_limit_reached (measured). The cap is what such a
// model may spend before the first words of the message are used instead.
const titleMaxTokens = 512

// titleTimeout bounds the call. A title that is not back by then is not
// worth waiting for — the first words of the message are already there.
const titleTimeout = 20 * time.Second

// titleFirstAttempt is the share of titleTimeout the model that answered the
// turn gets before any model that works is asked instead. A live model
// titles in about a second (measured 0.8–1.9 s); the rest is the second
// request's.
const titleFirstAttempt = 8 * time.Second

// titleUserMaxRunes / assistantSnippetMaxRunes cap what is sent. The topic
// is in the opening lines; a pasted document is not title material.
const titleUserMaxRunes = 500
const assistantSnippetMaxRunes = 200

// titlePurpose rides metadata.purpose so the call can be told from a
// chat turn in the gateway's logs. The gateway ignores keys it does not know.
const titlePurpose = "title-generation"

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
	// Model is the model that wrote the title, or the last one asked; ""
	// when none was.
	Model string
	// FallbackUsed: the model that answered the turn could not name it and
	// another one was asked.
	FallbackUsed bool
}

// titleConvStore / titleMsgStore are the two reads and one write naming
// needs. Interfaces so the whole decision — not just its pure half — can be
// tested without a database.
type titleConvStore interface {
	GetByID(ctx context.Context, tenantID, convID uuid.UUID) (*model.Conversation, error)
	UpdateTitleIfCurrent(ctx context.Context, tenantID, convID uuid.UUID, current, title string) (bool, error)
}

type titleMsgStore interface {
	GetTitleSeed(ctx context.Context, tenantID, convID uuid.UUID) (*repository.TitleSeed, error)
}

// TitleService is the title-generation entry point, driven by the
// generate-title HTTP endpoint via the ConversationHandler.
type TitleService struct {
	gateway       *GatewayClient
	conversations titleConvStore
	messages      titleMsgStore
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
	// call) or "ask" (call a model, first words only if none can).
	action string
	reason string
	// model is who answered the first successful turn — asked first. ""
	// when the row does not say: the router's pick is asked straight away.
	model string
	// requested is the conversation's own model when it is the same model
	// under its catalogue id: the row stores the name the provider served
	// under ("deepseek-flash" for deepseek-v4-flash, measured), which the
	// catalogue may not list. Only when the turn did not fall back and the
	// person picked a model — after a fallback, or on Auto, the conversation's
	// model is not who answered.
	requested string
}

// planTitle decides what to do for a conversation.
//
// FIX124 — the rule is the STATE of the title, not the number of the turn. A
// chat is named after the first turn that was answered, whichever turn that
// is: FIX122 refused anything after turn one ("not_first_turn"), so a chat
// whose first answer failed stayed "New conversation" however many answers
// followed (the owner's 66b0122a: two failed turns, a third answered, no
// name). What stops a second name is the title itself — once it is not a
// machine title (a model wrote it, or the person did), nothing here runs.
func planTitle(conv *model.Conversation, seed *repository.TitleSeed) titlePlan {
	if seed == nil || seed.FirstUser == nil {
		return titlePlan{action: "keep", reason: "no_messages"}
	}
	if !isMachineTitle(conv.Title, seed.FirstUser.ContentMasked) {
		return titlePlan{action: "keep", reason: "title_already_set"}
	}
	// FIX127 B1 — only a chat the user STARTED as temporary goes unnamed.
	// Memory off is a separate switch: until FIX127 it was read as
	// "temporary" here, and an ordinary chat whose owner had turned memory
	// off stayed "New conversation" for good.
	if conv.IsTemporary {
		return titlePlan{action: "keep", reason: "temporary_chat"}
	}
	if seed.Answer == nil {
		// Nothing has been answered yet. The first words are the name until
		// something is (and they are normally already there: the message
		// paths write them when they take the first message).
		return titlePlan{action: "fallback", reason: "no_turn_answered"}
	}
	plan := titlePlan{action: "ask"}
	if seed.Answer.ModelID != nil {
		plan.model = strings.TrimSpace(*seed.Answer.ModelID)
	}
	if requested := strings.TrimSpace(conv.ModelID); seed.Answer.FallbackFrom == nil && requested != "" && !IsAutoModel(requested) && requested != plan.model {
		plan.requested = requested
	}
	return plan
}

// PlaceholderTitle is the name a chat carries until a model has named it:
// the first words of its first message. "" when the message has no text (a
// files-only send) — the creation placeholder then stands.
func PlaceholderTitle(firstUserMessage string) string {
	return makeTitleFrom(firstUserMessage)
}

// writePlaceholderTitle gives a conversation that still carries the creation
// placeholder the first words of its FIRST user message — not of the message
// being taken now: naming later recognises a machine title by comparing it
// with the first message's words, and a placeholder cut from a later message
// would read as a name somebody chose and never be replaced. Compare-and-set
// on the creation placeholder, so a rename is safe; best-effort, a failure
// leaves the placeholder for the next message.
func writePlaceholderTitle(ctx context.Context, convs *repository.ConversationRepo, msgs *repository.MessageRepo, tenantID, convID uuid.UUID) {
	text, err := msgs.FirstUserText(ctx, tenantID, convID)
	if err != nil {
		return
	}
	if placeholder := PlaceholderTitle(text); placeholder != "" {
		_, _ = convs.UpdateTitleIfCurrent(ctx, tenantID, convID, defaultConversationTitle, placeholder)
	}
}

// isMachineTitle reports whether nobody chose this title: the creation
// placeholder, or one of the truncations of the first message that the
// server writes.
func isMachineTitle(title, firstUserMessage string) bool {
	t := strings.TrimSpace(title)
	if t == "" || t == defaultConversationTitle {
		return true
	}
	return t == makeTitleFrom(firstUserMessage) ||
		t == cleanTitle(fallbackTitleFromMessage(firstUserMessage))
}

// buildTitleRequest is the whole request: two short messages and the
// switches that keep it cheap. No provider boundary of its own any more
// (FIX124, owner's policy: any model that works) — a tenant's or project's
// allowed_providers is applied by the gateway from its own settings, to this
// call as to a chat turn.
func buildTitleRequest(modelID, userMsg, asstSnippet string) GatewayChatRequest {
	return buildTitleRequestNear(modelID, "", userMsg, asstSnippet)
}

// buildTitleRequestNear is buildTitleRequest for the router's pick: closestTo
// names the model that answered the turn, and the gateway orders "any model
// that works" by closeness to it (its rescue order — tier and price first),
// so a chat answered by a small model is not named by a large one.
func buildTitleRequestNear(modelID, closestTo, userMsg, asstSnippet string) GatewayChatRequest {
	meta := map[string]any{"purpose": titlePurpose}
	if closestTo != "" {
		meta["closest_to"] = closestTo
	}
	return GatewayChatRequest{
		Model: modelID,
		Messages: []GatewayMessage{
			{Role: "system", Content: titleSystemPrompt},
			{Role: "user", Content: buildTitlePrompt(userMsg, asstSnippet)},
		},
		MaxTokens: titleMaxTokens,
		// Thinking shares the output cap on a reasoning model: with it on,
		// the cap is all thought and the title comes back empty.
		Reasoning: &GatewayReasoningConfig{Mode: "off"},
		Metadata:  meta,
	}
}

// Generate names the conversation if it is still unnamed, and reports
// what it did. A model failure is not an error: another model is asked, and
// when none can the result is the first words of the user's message. Errors
// are reserved for "not yours", "nothing to name" and the database.
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

	seed, err := s.messages.GetTitleSeed(ctx, tenantID, convID)
	if err != nil {
		return TitleResult{}, fmt.Errorf("title: load seed: %w", err)
	}
	plan := planTitle(conv, seed)
	if plan.action == "keep" {
		if plan.reason == "no_messages" {
			return TitleResult{}, ErrTitleNoMessages
		}
		slog.Info("title: kept", "conv", convID.String(), "reason", plan.reason)
		return TitleResult{Title: conv.Title, Source: TitleSourceKept, Reason: plan.reason}, nil
	}

	res := TitleResult{Title: PlaceholderTitle(seed.FirstUser.ContentMasked), Source: TitleSourceFallback, Reason: plan.reason, Model: plan.model}
	if plan.action == "ask" {
		asked := s.askModels(ctx, tenantID, userID, convID, plan, seed.Question, seed.Answer)
		res.Model, res.FallbackUsed = asked.model, asked.fallbackUsed
		if asked.title != "" {
			res.Title, res.Source, res.Reason = asked.title, TitleSourceModel, ""
		} else {
			res.Reason = asked.why
		}
	}
	if res.Title == "" || res.Title == conv.Title {
		if res.Title == "" {
			res.Source, res.Reason = TitleSourceKept, "nothing_to_title_with"
		}
		res.Title = conv.Title
		return res, nil
	}
	written, err := s.conversations.UpdateTitleIfCurrent(ctx, tenantID, convID, conv.Title, res.Title)
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
		return TitleResult{Title: current, Source: TitleSourceKept, Reason: "renamed_meanwhile", Model: res.Model, FallbackUsed: res.FallbackUsed}, nil
	}
	slog.Info("title: written", "conv", convID.String(), "source", res.Source, "reason", res.Reason, "model", res.Model, "fallback_used", res.FallbackUsed)
	return res, nil
}

// titleAsked is what askModels came back with.
type titleAsked struct {
	title        string
	why          string // why there is no title; "" when there is one
	model        string // the model that wrote it, or the last one asked
	fallbackUsed bool   // the model that answered the turn did not write it
}

// askModels asks the model that answered the turn, and when it cannot, any
// model that works — inside titleTimeout in total.
//
// "Any model that works" is not a list kept here: the second request names
// the Auto sentinel, so the gateway's router picks (skipping what its health
// markers call dead, inside the tenant's allowed providers) and walks its
// own alternatives — the list a chat turn gets. The first request is cut at
// titleFirstAttempt rather than left to the provider's own timeout: a dead
// box holds a request for 30 s (measured on qwen), which alone is longer
// than the whole allowance.
func (s *TitleService) askModels(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
	plan titlePlan, question, answer *model.Message,
) titleAsked {
	overall, cancel := context.WithTimeout(ctx, titleTimeout)
	defer cancel()
	userMsg := truncateRunes(strings.TrimSpace(question.ContentMasked), titleUserMaxRunes)
	snippet := assistantSnippet(answer)
	call := GatewayCallContext{TenantID: tenantID, UserID: userID, ConversationID: convID}

	out := titleAsked{why: "served_model_not_in_catalogue"}
	if first, _ := s.catalogueEntry(overall, plan.model, plan.requested); first != "" {
		attempt, cancelFirst := context.WithTimeout(overall, titleFirstAttempt)
		out.model = first
		out.title, out.why, _ = s.ask(attempt, call, first, userMsg, snippet, "")
		cancelFirst()
		if out.title != "" {
			return out
		}
	}
	if overall.Err() != nil {
		return out
	}
	out.fallbackUsed = true
	out.model = AutoModelID
	title, why, served := s.ask(overall, call, AutoModelID, userMsg, snippet, firstNonEmpty(plan.requested, plan.model))
	if served != "" {
		out.model = served
	}
	out.title, out.why = title, why
	return out
}

// ask makes one small call. Returns the cleaned title, or "" and why there
// is none, and the model the gateway says served it. Logs what the call cost
// either way.
func (s *TitleService) ask(ctx context.Context, call GatewayCallContext, modelID, userMsg, snippet, closestTo string) (title, why, served string) {
	started := time.Now()
	resp, err := s.gateway.SendChatCompletion(ctx, call, buildTitleRequestNear(modelID, closestTo, userMsg, snippet))
	elapsed := time.Since(started).Milliseconds()
	if err != nil {
		slog.Warn("title: model call failed", "conv", call.ConversationID.String(), "model", modelID, "ms", elapsed, "err", err)
		return "", "model_call_failed", ""
	}
	title = cleanTitle(parseTitleCompletion(extractContent(resp)))
	prompt, completion, thinking := 0, 0, 0
	if resp != nil {
		served = resp.Model
		if resp.Usage != nil {
			prompt, completion, thinking = resp.Usage.PromptTokens, resp.Usage.CompletionTokens, resp.Usage.ReasoningTokens
		}
	}
	slog.Info("title: model answered", "conv", call.ConversationID.String(), "model", modelID, "served", served, "ms", elapsed,
		"prompt_tokens", prompt, "completion_tokens", completion, "reasoning_tokens", thinking, "empty", title == "")
	if title == "" {
		return "", "model_returned_nothing", served
	}
	return title, "", served
}

// catalogueEntry returns the first of the candidate ids the gateway's
// catalogue lists, with its provider; "", "" when the catalogue cannot be
// read or lists none of them.
func (s *TitleService) catalogueEntry(ctx context.Context, candidates ...string) (string, string) {
	models, err := s.gateway.ListModels(ctx)
	if err != nil {
		return "", ""
	}
	for _, id := range candidates {
		if id == "" {
			continue
		}
		for _, m := range models {
			if m.ID == id {
				return id, strings.TrimSpace(m.Provider)
			}
		}
	}
	return "", ""
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

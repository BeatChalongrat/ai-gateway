// TitleService computes a short, descriptive conversation title from
// the first user/assistant turn by asking the Gateway for a one-line
// summary. The result is stripped of quotes/trailing punctuation and
// capped so it fits the sidebar without truncation.
//
// Why use the LLM: the local heuristic (truncate the first user
// message at 50 chars) produces terrible titles for short prompts
// ("ไง", "hi", "?") and for prompts that are themselves a long
// question. A 3-6 word summary captures intent without the prompt's
// exact wording leaking into the sidebar.
//
// Failure mode: title generation is fire-and-forget from the
// frontend's perspective. When the gateway is down or returns an
// empty completion, the conversation keeps whatever title it already
// has (typically the default "New conversation" placeholder) and the
// API returns the unchanged title — no error surfaced to the user.
package service

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// titleModelID is the model used for title generation. Hard-coded to
// a small/fast model so title generation doesn't burn premium tokens
// on a 6-word completion. Always available in the default gateway
// model list; if it's ever de-listed we fall back to the
// conversation's own model.
const titleModelID = "gpt-4o-mini"

// titleSystemPrompt frames the request so the model returns just a
// title, not a sentence-style answer. The language-mirror clause is
// load-bearing: without it gpt-4o-mini reliably defaults to English
// even when the user prompt is Thai/Japanese/etc., which produces
// sidebar titles in the wrong script.
const titleSystemPrompt = "Generate a short conversation title (3-6 words) based on this exchange. The title MUST be in the same language as the user's message. If the user writes in Thai, the title must be in Thai. If in English, title in English. Reply with just the title, no quotes, no punctuation at the end."

// assistantSnippetMaxRunes caps the assistant text we feed the title
// model. The full reply is irrelevant for naming — the first sentence
// or two carries the topic; longer payloads just waste tokens.
const assistantSnippetMaxRunes = 200

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

// TitleService is the title-generation entry point. The MessageService
// holds a reference too but only ever uses TitleService for future
// auto-title work; the generate-title HTTP endpoint drives it
// directly via the ConversationHandler.
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

// Generate produces a fresh title for the conversation by asking the
// gateway to summarize the first user/assistant turn. Returns the
// new title; the caller is responsible for persisting it (typically
// via ConversationService.Rename so the same length/whitespace rules
// apply as on user-driven renames).
//
// When the conversation has only a user message (assistant hasn't
// replied yet), the user prompt alone is fed to the title model —
// the user message is the dominant signal anyway.
func (s *TitleService) Generate(
	ctx context.Context,
	tenantID, userID, convID uuid.UUID,
) (string, error) {
	if s.conversations == nil || s.messages == nil {
		return "", errors.New("title: service not wired (repos missing)")
	}
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return "", ErrTitleConversationNotFound
		}
		return "", err
	}
	if conv.UserID != userID {
		// Don't leak existence to other users of the same tenant.
		return "", ErrTitleConversationNotFound
	}

	// Pull the chronological history. The repo caps at 200 already;
	// we only need the first user + first assistant entry, so a
	// small limit keeps the round trip cheap.
	history, err := s.messages.ListContextByConversation(ctx, tenantID, convID, 20)
	if err != nil {
		return "", fmt.Errorf("title: load history: %w", err)
	}
	firstUser, firstAsst := firstUserAndAssistant(history)
	if firstUser == nil {
		return "", ErrTitleNoMessages
	}

	prompt := buildTitlePrompt(firstUser.ContentMasked, assistantSnippet(firstAsst))
	chosenModel := titleModelID
	gwReq := GatewayChatRequest{
		Model: chosenModel,
		Messages: []GatewayMessage{
			{Role: "system", Content: titleSystemPrompt},
			{Role: "user", Content: prompt},
		},
	}
	resp, err := s.gateway.SendChatCompletion(ctx, GatewayCallContext{
		TenantID:       tenantID,
		UserID:         userID,
		ConversationID: convID,
	}, gwReq)
	if err != nil {
		// Retry once with the conversation's own model — covers the
		// edge case where the deployment has removed gpt-4o-mini from
		// the allow-list. If that also fails, bubble the gateway
		// error so the handler maps to 502.
		gwReq.Model = conv.ModelID
		resp, err = s.gateway.SendChatCompletion(ctx, GatewayCallContext{
			TenantID:       tenantID,
			UserID:         userID,
			ConversationID: convID,
		}, gwReq)
		if err != nil {
			return "", err
		}
	}
	raw := extractContent(resp)
	cleaned := cleanTitle(raw)
	if cleaned == "" {
		return "", errors.New("title: gateway returned empty completion")
	}
	return cleaned, nil
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
	runes := []rune(s)
	if len(runes) > titleMaxRunes {
		s = string(runes[:titleMaxRunes]) + "…"
	}
	return s
}

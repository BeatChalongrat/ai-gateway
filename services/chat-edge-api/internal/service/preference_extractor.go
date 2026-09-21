// PreferenceExtractor — S8 background pattern learner.
//
// Analyses a finished conversation for repeated user-behaviour
// patterns (language preferences, response-style preferences,
// coding-language preferences, workflow preferences) and creates
// chat.user_memories rows with:
//
//	memory_type = "inferred_preference"
//	source_type = "assistant_inferred"
//	status      = "pending_confirmation"
//
// The pending status keeps the row OUT of the system-prompt
// injection block (GetActiveForInjection filters status='active')
// until the user explicitly confirms via the Settings → Memory
// surface. The same conversation never triggers extraction twice
// — caller checks via the source_conversation_id filter before
// calling ExtractPreferences.
//
// Same async-background pattern as S3's ConversationStateService:
// runs on context.Background() so the user's response isn't
// blocked, swallows errors with slog.Warn so a single failed
// extraction doesn't degrade the chat surface.
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// extractionTimeout caps the per-extraction gateway call. Slightly
// shorter than the state extractor (90s) because preferences run
// LESS often (once per conversation) and a slow extraction just
// delays the suggestion — not catastrophic to abandon.
const preferenceExtractionTimeout = 60 * time.Second

// Bounds on the LLM call's response. 800 tokens is enough for
// 3-5 suggestions with evidence excerpts; temperature 0.1 keeps
// the JSON envelope deterministic. Mirrors the S3 extractor's
// conservative posture.
const (
	preferenceMaxTokens   = 800
	preferenceTemperature = 0.1
)

// preferenceMinMessages is the floor before extraction runs.
// Patterns need a few exchanges to surface; under this count the
// LLM tends to hallucinate "patterns" from one-off requests.
const preferenceMinMessages = 8

// promoteThreshold is the number of distinct conversations the
// same preference must surface in before auto-promotion fires.
// Three is the smallest signal that smells like a habit; lower
// would activate one-off coincidences.
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
	}
}

// SetExtractionModel pins the extraction model regardless of which
// model the conversation chose (FIX126 O1: BACKGROUND_TASK_MODEL). The
// conversation's model is the fallback when it produces nothing.
func (e *PreferenceExtractor) SetExtractionModel(modelID string) {
	e.extractionModel = strings.TrimSpace(modelID)
}

// ---------------------------------------------------------------------------
// ExtractPreferences — main entry point
// ---------------------------------------------------------------------------

// ExtractPreferences runs the analysis pass against one
// conversation. Safe to call from a background goroutine: every
// step uses its own bounded ctx and a failure at any stage logs
// + returns nil rather than panicking.
//
// Returns the count of pending suggestions actually persisted —
// 0 on a no-pattern conversation, > 0 when at least one
// suggestion was created. Auto-promotion of duplicates happens
// inline; the count includes preferences that got
// auto-promoted in this call.
func (e *PreferenceExtractor) ExtractPreferences(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (int, error) {
	conv, err := e.conv.GetByID(ctx, tenantID, convID)
	if err != nil {
		return 0, fmt.Errorf("conv lookup: %w", err)
	}

	// Pull the last ~50 messages — enough signal without blowing
	// the extraction budget. The cap inside ListContextByConversation
	// is 200; 50 keeps the prompt tidy.
	msgs, err := e.messageRepo.ListContextByConversation(ctx, tenantID, convID, 50)
	if err != nil {
		return 0, fmt.Errorf("list messages: %w", err)
	}
	if len(msgs) < preferenceMinMessages {
		return 0, nil
	}

	// Build the "already known" list so the LLM can avoid
	// re-suggesting things the user has already confirmed OR
	// rejected (still pending). Pull both statuses, cap at 100
	// to keep the prompt bounded.
	known := e.knownContents(ctx, userID, tenantID)

	prompt := e.buildExtractionPrompt(msgs, known)
	// FIX126 O1 — the background model (BACKGROUND_TASK_MODEL) first; the
	// conversation's model when it produced nothing (not served, not usable
	// by the tenant, failed).
	raw := ""
	for _, modelID := range backgroundModelOrder(e.extractionModel, conv.ModelID) {
		raw = e.callLLM(ctx, modelID, prompt, GatewayCallContext{
			TenantID:       tenantID,
			UserID:         userID,
			ConversationID: convID,
		})
		if strings.TrimSpace(raw) != "" {
			break
		}
	}
	if raw == "" {
		return 0, nil
	}
	suggestions := parsePreferenceResponse(raw)
	if len(suggestions) == 0 {
		return 0, nil
	}

	created := 0
	for i := range suggestions {
		sg := &suggestions[i]
		evidence := ""
		if sg.Evidence != nil {
			evidence = *sg.Evidence
		}
		convPtr := convID
		_, cerr := e.memoryService.CreateMemory(ctx, CreateMemoryInput{
			TenantID:                tenantID,
			UserID:                  userID,
			ScopeType:               model.MemoryScopeGlobalUser,
			MemoryType:              model.MemoryTypeInferredPreference,
			Category:                sg.Category,
			Content:                 sg.Content,
			SourceType:              model.MemorySourceAssistantInferred,
			SourceConversationID:    &convPtr,
			EvidenceExcerpt:         evidence,
			ActorType:               "system",
			StatusOverride:          model.MemoryStatusPendingConfirmation,
			ConfidenceScoreOverride: sg.Confidence,
		})
		if cerr != nil {
			// Dedup / DLP / validation — all expected paths. Log
			// at debug volume; not worth warning per suggestion.
			slog.Debug("preference_extractor: create skipped",
				"conv", convID.String(), "reason", cerr.Error())
			continue
		}
		created++
		// Check if this suggestion has now hit the auto-promote
		// threshold across distinct conversations.
		e.maybeAutoPromote(ctx, userID, tenantID, sg.Content)
	}

	if created > 0 {
		slog.Info("preference_extractor: created pending preferences",
			"conv", convID.String(),
			"user", userID.String(),
			"created", created)
	}
	return created, nil
}

// ---------------------------------------------------------------------------
// Auto-promotion
// ---------------------------------------------------------------------------

// maybeAutoPromote checks whether the just-saved suggestion now
// has 3+ observations across distinct conversations. If so:
//   - the newest pending row gets promoted to active +
//     is_user_confirmed=false (user still sees it, but it now
//     ships into the prompt). Confidence stays at the extractor's
//     value so the UI can show "auto-confirmed by repetition" if
//     it wants to.
//   - older pending duplicates get superseded so the Settings
//     surface doesn't show a stack of identical suggestions.
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

// inferredPreference is the JSON envelope the extractor expects.
// Evidence is a pointer so the LLM can legitimately omit it.
type inferredPreference struct {
	Content    string  `json:"content"`
	Category   string  `json:"category"`
	Confidence float64 `json:"confidence"`
	Evidence   *string `json:"evidence,omitempty"`
}

const preferenceSystemPrompt = "You are a behavior analyst. Identify user communication preferences from conversation patterns. Output ONLY valid JSON. No markdown fencing. No commentary."

// buildExtractionPrompt assembles the user-message body the
// gateway sees. Kept long-form (with literal examples) because
// LLMs do better on this task when given the exact shape they
// should emit.
func (e *PreferenceExtractor) buildExtractionPrompt(
	messages []model.Message, known []string,
) string {
	var sb strings.Builder
	sb.WriteString(`Analyse the following conversation and identify BEHAVIOR PATTERNS that suggest user preferences.

IMPORTANT RULES:
- Only identify patterns that appear MULTIPLE TIMES in this conversation or are explicitly stated.
- Do NOT guess or fabricate preferences.
- Do NOT include one-time requests as preferences.
- Do NOT include facts that are better stored as explicit_fact (names, locations, etc.)
- Focus on HOW the user wants to be communicated with, not WHAT they asked about.
- Keep each preference under 100 characters.
- Confidence must be between 0 and 1; only output entries with confidence >= 0.6.
- Output ONLY valid JSON (an array of objects). No markdown fencing.

Categories to look for:
- language          — what language they use, mix of languages, formality level
- response_style    — length preference, structure preference, level of detail
- coding            — preferred languages, frameworks, patterns
- workflow          — step-by-step vs big-picture, phased rollout, etc.

`)
	if len(known) > 0 {
		sb.WriteString("Already known (do NOT repeat these):\n")
		for _, k := range known {
			sb.WriteString("- ")
			sb.WriteString(k)
			sb.WriteString("\n")
		}
		sb.WriteString("\n")
	}

	sb.WriteString(`Output a JSON array (empty [] if no new patterns detected):
[
  {
    "content": "Prefers concise Thai responses with English technical terms",
    "category": "language",
    "confidence": 0.8,
    "evidence": "Consistently writes in Thai but uses English for code and technical terms"
  }
]

--- CONVERSATION ---
`)

	start := 0
	if len(messages) > 30 {
		start = len(messages) - 30
	}
	for i := start; i < len(messages); i++ {
		msg := &messages[i]
		content := msg.ContentMasked
		if len(content) > 300 {
			content = content[:300] + "... [truncated]"
		}
		sb.WriteString(fmt.Sprintf("[%s] %s\n", msg.Role, content))
	}
	return sb.String()
}

// knownContents pulls active + pending memory contents to feed
// into the LLM's "do not repeat" list. Capped at 100 so the
// prompt stays bounded.
func (e *PreferenceExtractor) knownContents(
	ctx context.Context, userID, tenantID uuid.UUID,
) []string {
	out := make([]string, 0, 50)
	active, err := e.memoryRepo.ListByUser(ctx, userID, tenantID, repository.MemoryListFilters{
		Status: model.MemoryStatusActive,
		Limit:  50,
	})
	if err == nil {
		for i := range active {
			out = append(out, active[i].Content)
		}
	}
	pending, err := e.memoryRepo.ListByUser(ctx, userID, tenantID, repository.MemoryListFilters{
		Status: model.MemoryStatusPendingConfirmation,
		Limit:  50,
	})
	if err == nil {
		for i := range pending {
			out = append(out, pending[i].Content)
		}
	}
	if len(out) > 100 {
		out = out[:100]
	}
	return out
}

// callLLM issues the non-streaming gateway call. Returns "" on
// any failure — caller treats that as "no extraction this round".
func (e *PreferenceExtractor) callLLM(
	parent context.Context, modelID, userPrompt string, callCtx GatewayCallContext,
) string {
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
	}
	resp, err := e.gateway.SendChatCompletion(ctx, callCtx, req)
	if err != nil {
		slog.Warn("preference_extractor: gateway call failed",
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

// parsePreferenceResponse is the tolerant JSON parser — strips
// markdown fences if the model insists on emitting them, caps the
// per-suggestion content at 100 chars, drops anything under 0.6
// confidence, and caps the slice at 5 suggestions per extraction
// so a single bad prompt can't drown the user in noise.
func parsePreferenceResponse(raw string) []inferredPreference {
	cleaned := strings.TrimSpace(raw)
	cleaned = strings.TrimPrefix(cleaned, "```json")
	cleaned = strings.TrimPrefix(cleaned, "```")
	cleaned = strings.TrimSuffix(cleaned, "```")
	cleaned = strings.TrimSpace(cleaned)

	var raws []inferredPreference
	if err := json.Unmarshal([]byte(cleaned), &raws); err != nil {
		return nil
	}
	out := make([]inferredPreference, 0, len(raws))
	for i := range raws {
		p := raws[i]
		p.Content = strings.TrimSpace(p.Content)
		if p.Content == "" {
			continue
		}
		if p.Confidence < 0.6 || p.Confidence > 1.0 {
			continue
		}
		// Trim long content. 100 rune cap (not byte) so Thai / CJK
		// content stays intact.
		runes := []rune(p.Content)
		if len(runes) > 100 {
			p.Content = string(runes[:100])
		}
		out = append(out, p)
		if len(out) >= 5 {
			break
		}
	}
	return out
}

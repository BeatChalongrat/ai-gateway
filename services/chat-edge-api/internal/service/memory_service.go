// MemoryService — the durable cross-conversation memory layer
// (Phase S1). Owns DLP / dedup / classification + audit logging
// around chat.user_memories so the handler + AI-tool layer stay
// thin shells over typed inputs.
//
// Contract notes for downstream callers:
//   - Every mutation path also emits a chat.memory_events row via
//     the same transaction-free flow the rest of chat-edge uses
//     (best-effort; event-log failure logs a warning but doesn't
//     fail the user-visible operation — the actual data change is
//     the load-bearing side effect).
//   - BuildMemoryContext is read-only and must never block the
//     message turn — failures return "" so the AI still gets a
//     reply, just without memory injection.
package service

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrMemoryEmpty             = errors.New("memory: content is required")
	ErrMemoryProhibitedContent = errors.New("memory: content contains prohibited data")
	ErrMemoryDuplicate         = errors.New("memory: identical memory already exists")
	ErrMemoryInvalidScope      = errors.New("memory: invalid scope_type")
	ErrMemoryInvalidType       = errors.New("memory: invalid memory_type")
	ErrMemoryInvalidSource     = errors.New("memory: invalid source_type")
	ErrMemoryNotFound          = errors.New("memory: not found")
	// S9 — enforced when the user is at or above the tenant's
	// max_memories_per_user setting. Message includes the
	// observed/limit pair so the UI can render a clear hint.
	ErrMemoryLimitReached = errors.New("memory: per-user limit reached")
)

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

type MemoryService struct {
	repo *repository.MemoryRepo
	// Optional. When wired (S9), CreateMemory enforces the
	// tenant's max_memories_per_user setting before persisting.
	// Nil-safe — without it the cap defaults effectively to
	// "unlimited" (tests + legacy boot paths).
	settings *repository.TenantMemorySettingsRepo
}

func NewMemoryService(repo *repository.MemoryRepo) *MemoryService {
	return &MemoryService{repo: repo}
}

// SetTenantSettings wires the S9 per-tenant settings repo so the
// CreateMemory path can enforce the max-memories-per-user cap.
func (s *MemoryService) SetTenantSettings(r *repository.TenantMemorySettingsRepo) {
	s.settings = r
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

// CreateMemoryInput is the typed payload from handler + AI tool.
type CreateMemoryInput struct {
	TenantID  uuid.UUID
	UserID    uuid.UUID
	ScopeType string
	ScopeID   *uuid.UUID

	MemoryType string
	Category   string
	Content    string

	SourceType            string
	SourceConversationID  *uuid.UUID
	SourceMessageID       *uuid.UUID
	EvidenceExcerpt       string

	// ActorType is the audit-log actor: "user" (manual API), "assistant"
	// (AI tool call), or "system" (background extractor — S8).
	ActorType string

	// S8 overrides — used by the PreferenceExtractor so background-
	// inferred preferences land as pending_confirmation rather than
	// the active default. Empty / zero values fall through to the
	// trust defaults (user-explicit → active+confirmed+0.95,
	// everything else → active+unconfirmed+0.7).
	//
	// Status: only `pending_confirmation` is accepted as an override
	// AND only when SourceType is assistant_inferred. Any other
	// value falls back to active.
	//
	// ConfidenceScoreOverride: when > 0 replaces the trust-default
	// confidence — extractor passes its per-suggestion confidence.
	StatusOverride          string
	ConfidenceScoreOverride float64
}

// CreateMemory validates + DLP-gates + dedup-checks + persists a new
// memory. Returns ErrMemoryProhibitedContent for DLP rejections,
// ErrMemoryDuplicate for dedup hits, ErrMemoryInvalid* for shape
// errors. Successful creates emit a 'created' audit event.
func (s *MemoryService) CreateMemory(
	ctx context.Context, in CreateMemoryInput,
) (*model.Memory, error) {
	content := strings.TrimSpace(in.Content)
	if content == "" {
		return nil, ErrMemoryEmpty
	}

	if !validScopeType(in.ScopeType) {
		return nil, ErrMemoryInvalidScope
	}
	if !validMemoryType(in.MemoryType) {
		return nil, ErrMemoryInvalidType
	}
	if !validSourceType(in.SourceType) {
		return nil, ErrMemoryInvalidSource
	}

	// DLP — refuse to persist anything that looks like a credential,
	// raw PII number, or financial detail. The check is intentionally
	// conservative; better to refuse a borderline benign string than
	// to silently store a leaked key.
	if containsProhibitedData(content) {
		return nil, ErrMemoryProhibitedContent
	}

	// S9 — per-tenant max-memories cap. Skipped when the settings
	// repo isn't wired (legacy boot path) so tests don't have to
	// stand up the table. Limit counts active + pending — a
	// pending suggestion still occupies a slot from the user's POV.
	if s.settings != nil {
		ts, sErr := s.settings.Get(ctx, in.TenantID)
		if sErr == nil && ts != nil && ts.MaxMemoriesPerUser > 0 {
			activeN, _ := s.repo.CountByStatus(ctx, in.UserID, in.TenantID, model.MemoryStatusActive)
			pendingN, _ := s.repo.CountByStatus(ctx, in.UserID, in.TenantID, model.MemoryStatusPendingConfirmation)
			if activeN+pendingN >= ts.MaxMemoriesPerUser {
				return nil, fmt.Errorf("%w: %d/%d slots used",
					ErrMemoryLimitReached, activeN+pendingN, ts.MaxMemoriesPerUser)
			}
		}
	}

	normKey := normalizeKey(content)

	// Dedup. Background-inferred preferences also dedup against
	// pending rows so the extractor doesn't re-suggest the same
	// pattern on every conversation. User-explicit + assistant tool
	// saves use the narrower active-only check so soft-deleted
	// memories can be re-added.
	var dup bool
	var derr error
	if in.SourceType == model.MemorySourceAssistantInferred {
		dup, derr = s.repo.ExistsByNormalizedKeyOrPending(ctx, in.UserID, in.TenantID, normKey)
	} else {
		dup, derr = s.repo.ExistsByNormalizedKey(ctx, in.UserID, in.TenantID, normKey)
	}
	if derr != nil {
		return nil, derr
	}
	if dup {
		return nil, ErrMemoryDuplicate
	}

	// Trust defaults. User-explicit memories are confirmed + high
	// confidence; inferred ones (S8) need explicit confirmation
	// and start at 0.7 — but the extractor can override with its
	// per-suggestion confidence via ConfidenceScoreOverride.
	confidence := 0.7
	confirmed := false
	if in.SourceType == model.MemorySourceUserExplicit {
		confidence = 0.95
		confirmed = true
	}
	if in.ConfidenceScoreOverride > 0 {
		confidence = in.ConfidenceScoreOverride
	}

	// S8 — pending status is permitted ONLY for assistant_inferred
	// rows. Any other status override falls back to active so the
	// public API can't sneak rows past the user's review surface.
	status := model.MemoryStatusActive
	if in.StatusOverride == model.MemoryStatusPendingConfirmation &&
		in.SourceType == model.MemorySourceAssistantInferred {
		status = model.MemoryStatusPendingConfirmation
		confirmed = false
	}

	scopeType := in.ScopeType
	if scopeType == "" {
		scopeType = model.MemoryScopeGlobalUser
	}

	row := &model.Memory{
		TenantID:             in.TenantID,
		UserID:               in.UserID,
		ScopeType:            scopeType,
		ScopeID:              in.ScopeID,
		MemoryType:           in.MemoryType,
		Category:             strings.TrimSpace(in.Category),
		Content:              content,
		NormalizedKey:        normKey,
		SourceType:           in.SourceType,
		SourceConversationID: in.SourceConversationID,
		SourceMessageID:      in.SourceMessageID,
		EvidenceExcerpt:      strings.TrimSpace(in.EvidenceExcerpt),
		Status:               status,
		ConfidenceScore:      confidence,
		ImportanceScore:      0.5,
		IsUserConfirmed:      confirmed,
		SensitivityLevel:     model.MemorySensitivityNormal,
	}

	out, err := s.repo.Create(ctx, row)
	if err != nil {
		return nil, err
	}

	s.logEvent(ctx, &model.MemoryEvent{
		MemoryID:  &out.ID,
		TenantID:  in.TenantID,
		UserID:    in.UserID,
		Action:    "created",
		ActorType: orDefault(in.ActorType, "user"),
		Reason:    fmt.Sprintf("%s memory via %s", in.MemoryType, in.SourceType),
	})
	return out, nil
}

// ---------------------------------------------------------------------------
// Update / Pin / Confirm
// ---------------------------------------------------------------------------

// UpdateMemoryInput is the typed payload for PUT /memories/:id.
// Nil fields skip that field (no implicit clear).
type UpdateMemoryInput struct {
	Content         *string
	Category        *string
	MemoryType      *string
	ImportanceScore *float64
}

func (s *MemoryService) UpdateMemory(
	ctx context.Context, id, userID, tenantID uuid.UUID, in UpdateMemoryInput,
) (*model.Memory, error) {
	existing, err := s.repo.GetByID(ctx, id, userID, tenantID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMemoryNotFound
		}
		return nil, err
	}
	if in.Content != nil {
		c := strings.TrimSpace(*in.Content)
		if c == "" {
			return nil, ErrMemoryEmpty
		}
		if containsProhibitedData(c) {
			return nil, ErrMemoryProhibitedContent
		}
		existing.Content = c
		existing.NormalizedKey = normalizeKey(c)
	}
	if in.Category != nil {
		existing.Category = strings.TrimSpace(*in.Category)
	}
	if in.MemoryType != nil {
		if !validMemoryType(*in.MemoryType) {
			return nil, ErrMemoryInvalidType
		}
		existing.MemoryType = *in.MemoryType
	}
	if in.ImportanceScore != nil {
		v := *in.ImportanceScore
		if v < 0 {
			v = 0
		} else if v > 1 {
			v = 1
		}
		existing.ImportanceScore = v
	}
	if err := s.repo.Update(ctx, existing); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMemoryNotFound
		}
		return nil, err
	}
	s.logEvent(ctx, &model.MemoryEvent{
		MemoryID:  &existing.ID,
		TenantID:  tenantID,
		UserID:    userID,
		Action:    "updated",
		ActorType: "user",
	})
	// Re-read so the response carries the trigger-updated timestamp.
	return s.repo.GetByID(ctx, id, userID, tenantID)
}

func (s *MemoryService) SetPinned(
	ctx context.Context, id, userID, tenantID uuid.UUID, pinned bool,
) (*model.Memory, error) {
	if err := s.repo.SetPinned(ctx, id, userID, tenantID, pinned); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMemoryNotFound
		}
		return nil, err
	}
	action := "pinned"
	if !pinned {
		action = "unpinned"
	}
	s.logEvent(ctx, &model.MemoryEvent{
		MemoryID:  &id,
		TenantID:  tenantID,
		UserID:    userID,
		Action:    action,
		ActorType: "user",
	})
	return s.repo.GetByID(ctx, id, userID, tenantID)
}

func (s *MemoryService) SetConfirmed(
	ctx context.Context, id, userID, tenantID uuid.UUID, confirmed bool,
) (*model.Memory, error) {
	if err := s.repo.SetConfirmed(ctx, id, userID, tenantID, confirmed); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMemoryNotFound
		}
		return nil, err
	}

	// S8 — when the user confirms a pending suggestion, the SQL in
	// SetConfirmed already promotes status from pending_confirmation
	// to active. Bump the confidence score too so the now-active
	// row sits at the same trust level as a user-explicit save
	// (otherwise the inferred 0.7 default would underweight a
	// human-confirmed preference).
	if confirmed {
		if updated, err := s.repo.GetByID(ctx, id, userID, tenantID); err == nil && updated != nil {
			if updated.ConfidenceScore < 0.95 {
				updated.ConfidenceScore = 0.95
				_ = s.repo.Update(ctx, updated)
			}
		}
	}

	action := "confirmed"
	if !confirmed {
		action = "rejected"
	}
	s.logEvent(ctx, &model.MemoryEvent{
		MemoryID:  &id,
		TenantID:  tenantID,
		UserID:    userID,
		Action:    action,
		ActorType: "user",
	})
	return s.repo.GetByID(ctx, id, userID, tenantID)
}

// ---------------------------------------------------------------------------
// Delete / bulk clear
// ---------------------------------------------------------------------------

func (s *MemoryService) DeleteMemory(
	ctx context.Context, id, userID, tenantID uuid.UUID,
) error {
	// Ownership is enforced by the repo's user_id + tenant_id WHERE
	// clause; a missing row returns ErrNotFound which we surface as
	// ErrMemoryNotFound regardless of whether the row truly doesn't
	// exist or belongs to another user.
	if err := s.repo.SoftDelete(ctx, id, userID, tenantID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrMemoryNotFound
		}
		return err
	}
	s.logEvent(ctx, &model.MemoryEvent{
		MemoryID:  &id,
		TenantID:  tenantID,
		UserID:    userID,
		Action:    "deleted",
		ActorType: "user",
	})
	return nil
}

func (s *MemoryService) ClearAll(
	ctx context.Context, userID, tenantID uuid.UUID, scopeType *string,
) (int, error) {
	n, err := s.repo.ClearAll(ctx, userID, tenantID, scopeType)
	if err != nil {
		return 0, err
	}
	scopeLabel := "all"
	if scopeType != nil && *scopeType != "" {
		scopeLabel = *scopeType
	}
	s.logEvent(ctx, &model.MemoryEvent{
		TenantID:  tenantID,
		UserID:    userID,
		Action:    "deleted",
		ActorType: "user",
		Reason:    fmt.Sprintf("bulk clear scope=%s count=%d", scopeLabel, n),
		Metadata:  map[string]any{"count": n, "scope": scopeLabel},
	})
	return n, nil
}

// ---------------------------------------------------------------------------
// List / Get / Events
// ---------------------------------------------------------------------------

func (s *MemoryService) ListByUser(
	ctx context.Context, userID, tenantID uuid.UUID, filters repository.MemoryListFilters,
) ([]model.Memory, int, error) {
	rows, err := s.repo.ListByUser(ctx, userID, tenantID, filters)
	if err != nil {
		return nil, 0, err
	}
	total, err := s.repo.CountByUser(ctx, userID, tenantID, filters)
	if err != nil {
		return nil, 0, err
	}
	return rows, total, nil
}

func (s *MemoryService) GetMemory(
	ctx context.Context, id, userID, tenantID uuid.UUID,
) (*model.Memory, error) {
	m, err := s.repo.GetByID(ctx, id, userID, tenantID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMemoryNotFound
		}
		return nil, err
	}
	return m, nil
}

func (s *MemoryService) GetEvents(
	ctx context.Context, memoryID, userID, tenantID uuid.UUID, limit int,
) ([]model.MemoryEvent, error) {
	return s.repo.GetEvents(ctx, memoryID, userID, tenantID, limit)
}

// CountByStatus is a thin pass-through used by the S8 pending-
// count endpoint. Kept on the service so the handler doesn't have
// to depend on the repo directly.
func (s *MemoryService) CountByStatus(
	ctx context.Context, userID, tenantID uuid.UUID, status string,
) (int, error) {
	return s.repo.CountByStatus(ctx, userID, tenantID, status)
}

// ---------------------------------------------------------------------------
// BuildMemoryContext — system-prompt injection block
// ---------------------------------------------------------------------------
//
// NOTE: the proactive-save policy that used to live here as
// MemoryPolicyBlock now lives in the PromptCompiler's Tool Policy layer
// (prompt_compiler.go, Layer 7) — the save_memory tool's paragraph
// carries the "save naturally, don't wait for 'remember'" directive so
// the policy is gated on the tool actually being active.

// BuildMemoryContext is the legacy wrapper retained for callers
// that don't need source attribution (tests, the
// MemoryReader interface). New code should prefer
// BuildMemoryContextWithSources so the source attribution table
// stays populated.
func (s *MemoryService) BuildMemoryContext(
	ctx context.Context, userID, tenantID uuid.UUID,
) string {
	text, _ := s.BuildMemoryContextWithSources(ctx, userID, tenantID)
	return text
}

// BuildMemoryContextWithSources assembles the <saved_user_memory>
// block AND returns one ContextSource per memory injected. The
// message-flow's compile step records these so the S7 sources
// card can show "Memory: Preferred name is Beat" under the
// assistant response.
//
// Returns ("", nil) on any failure or when the user has no
// active memories — the message service treats "" as "skip block"
// and nil sources contribute zero rows to the attribution table.
//
// Side effect: bumps use_count + last_used_at for every recalled
// memory in the background, so the management UI can show the
// most-referenced facts. Background writes use a fresh ctx so a
// client disconnect doesn't kill the accounting.
func (s *MemoryService) BuildMemoryContextWithSources(
	ctx context.Context, userID, tenantID uuid.UUID,
) (string, []ContextSource) {
	memories, err := s.repo.GetActiveForInjection(ctx, userID, tenantID)
	if err != nil || len(memories) == 0 {
		return "", nil
	}

	var sb strings.Builder
	sb.WriteString("<saved_user_memory>\n")
	sb.WriteString("Facts and preferences you have saved about this user:\n")
	for i := range memories {
		sb.WriteString("- ")
		sb.WriteString(memories[i].Content)
		sb.WriteString("\n")
	}
	sb.WriteString("\nUse this information naturally when relevant — do not force it into every response.\n")
	sb.WriteString("Do not say 'according to my memory' or mention the memory system.\n")
	sb.WriteString("</saved_user_memory>\n")

	// S7 — one source per injected memory. Metadata carries the
	// memory_type + category so the SourcesCard can render the
	// secondary line ("saved memory · identity") without a second
	// lookup.
	sources := make([]ContextSource, 0, len(memories))
	for i := range memories {
		m := &memories[i]
		meta := map[string]any{}
		if m.MemoryType != "" {
			meta["memory_type"] = m.MemoryType
		}
		if m.Category != "" {
			meta["category"] = m.Category
		}
		sources = append(sources, ContextSource{
			SourceType:   model.SourceTypeSavedMemory,
			SourceID:     m.ID.String(),
			DisplayLabel: TruncateLabel(m.Content),
			Metadata:     MarshalMeta(meta),
		})
	}

	// Async use_count bump. Each goroutine carries its own background
	// ctx so the message turn doesn't block on these writes — and so
	// a client disconnect doesn't cancel them mid-flight.
	ids := make([]uuid.UUID, len(memories))
	for i := range memories {
		ids[i] = memories[i].ID
	}
	safeGo("memory-use-count-bump", func() {
		bg := context.Background()
		for _, id := range ids {
			if err := s.repo.IncrementUseCount(bg, id); err != nil {
				slog.Warn("memory: increment use_count failed",
					"memory_id", id.String(), "err", err)
			}
		}
	})

	return sb.String(), sources
}

// ---------------------------------------------------------------------------
// Audit log helper
// ---------------------------------------------------------------------------

// logEvent best-effort writes a chat.memory_events row. A failure
// logs a warning but never bubbles up to the caller — losing an
// audit row is bad but losing the user's create/delete is worse.
func (s *MemoryService) logEvent(ctx context.Context, e *model.MemoryEvent) {
	if err := s.repo.LogEvent(ctx, e); err != nil {
		slog.Warn("memory: log event failed",
			"action", e.Action, "actor", e.ActorType, "err", err)
	}
}

// ---------------------------------------------------------------------------
// DLP — prohibited content patterns
// ---------------------------------------------------------------------------

var (
	// Thai national ID — 13 consecutive digits.
	reThaiNID = regexp.MustCompile(`\b\d{13}\b`)
	// "salary = 123,456" / "เงินเดือน: 50000" — refuses to persist
	// concrete pay figures even if the user types them.
	reSalary = regexp.MustCompile(`(?i)(salary|เงินเดือน)\s*[=:]\s*[\d,]+`)
)

// Lowered substrings that mark prohibited-content secrets. Lower-
// cased once at module load via the const list below + lowercased
// scan input so we don't compile a regex per check.
var prohibitedSubstrings = []string{
	// API keys / tokens — common SaaS prefixes.
	"sk-", "sk_live_", "sk_test_",
	"api_key=", "apikey:", "secret_key",
	"access_token", "bearer ",
	"ghp_", "gho_", "glpat-", "xoxb-", "xoxp-",
	// Credential field labels.
	"password:", "password=", "passwd:", "pwd=",
	// Financial.
	"credit card", "card number", "cvv",
	"bank account", "routing number",
	"เลขบัตรเครดิต", "เลขบัญชี",
}

// containsProhibitedData returns true when the supplied content
// looks like something we MUST not persist into memory. Conservative
// on purpose — false positives ask the user to rephrase; false
// negatives leak a secret. Reviewed-against-OWASP-defaults rather
// than provider-specific.
func containsProhibitedData(content string) bool {
	lower := strings.ToLower(content)
	for _, p := range prohibitedSubstrings {
		if strings.Contains(lower, p) {
			return true
		}
	}
	if reThaiNID.MatchString(content) {
		return true
	}
	if reSalary.MatchString(content) {
		return true
	}
	return false
}

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

func validScopeType(s string) bool {
	switch s {
	case "", // empty allowed; service defaults to global_user
		model.MemoryScopeGlobalUser,
		model.MemoryScopeConversation,
		model.MemoryScopeArtifact,
		model.MemoryScopeTemporary:
		return true
	}
	return false
}

func validMemoryType(s string) bool {
	switch s {
	case model.MemoryTypeExplicitFact,
		model.MemoryTypeResponsePreference,
		model.MemoryTypeOngoingInterest,
		model.MemoryTypeConfirmedInstruction,
		model.MemoryTypeInferredPreference,
		model.MemoryTypeArtifactReference:
		return true
	}
	return false
}

func validSourceType(s string) bool {
	switch s {
	case model.MemorySourceUserExplicit,
		model.MemorySourceAssistantInferred,
		model.MemorySourceConversationSummary,
		model.MemorySourceArtifactEvent:
		return true
	}
	return false
}

// normalizeKey produces the dedup key: lowercased + trimmed +
// capped at 200 chars. Truncation uses byte slice because the cap
// is dedup-collision tolerance, not user-visible.
func normalizeKey(content string) string {
	k := strings.ToLower(strings.TrimSpace(content))
	if len(k) > 200 {
		k = k[:200]
	}
	return k
}

func orDefault(s, def string) string {
	if s == "" {
		return def
	}
	return s
}

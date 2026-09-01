// MessageService — orchestrates the per-message lifecycle.
//
// Two entry points:
//
//	SendNonStream — synchronous: save user msg → call gateway →
//	                save assistant msg → return final response.
//	SendStream    — async: save user msg → open stream → yield
//	                events to the caller (handler forwards to
//	                browser SSE) → save assistant msg on completion.
//
// The Web Chat product stores REAL content, NOT pseudonymized text
// — that's a deliberate Phase 1 choice (DLP-on-save is an opt-in
// tenant policy, off by default). The column name in
// “chat.messages_masked“ keeps the schema forward-compatible.
package service

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrMessageConversationNotFound = errors.New("message: conversation not found")
	ErrMessageEmptyContent         = errors.New("message: content is required")
	ErrMessageGatewayUnavailable   = errors.New("message: gateway unavailable")
	ErrMessageNotFound             = errors.New("message: not found")
	ErrMessageNotAssistant         = errors.New("message: not an assistant message")
	ErrMessageModelNotAllowed      = errors.New("message: model not allowed")
)

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

type MessageService struct {
	conversations *repository.ConversationRepo
	messages      *repository.MessageRepo
	gateway       *GatewayClient
	titles        *TitleService
	models        ModelChecker
	// Optional. When set, the message service:
	//   - attaches file_ids passed on Send to the user message
	//   - injects a markdown context block of extracted file text
	//     into the gateway request's system prompt
	// Left nil for unit tests that don't exercise the file flow.
	fileSvc FileLinker
	// Optional. Pulls the caller's custom instructions and prepends
	// them to the system prompt.
	prefsSvc PreferencesReader
	// Optional. Queries the knowledge base bound to the conversation
	// and returns a retrieved-context block for the system prompt.
	ragSvc RAGReader
	// projectReader — Phase 4. Optional; nil keeps every conversation on
	// the pre-Phase-4 prompt path.
	projectReader ProjectReader
	// skillReader — FIX12 Item 3. Optional; when wired, buildSystemPrefix
	// loads the user's enabled skills, matches their triggers against the
	// latest user message, and preloads ActiveSkills + SkillCatalog into
	// the CompileContext. nil keeps the prompt byte-identical to the
	// pre-skills behaviour.
	skillReader SkillReader
	// Optional. Reads conversation files (with their cached
	// inspection_json) so the Phase 4 Excel context block can
	// inject the workbook structure + action catalog into the
	// system prompt. Wired via SetExcelLookup.
	excelLookup ExcelLookup
	// Optional. Builds the "## Canvas Artifacts" system-prompt block
	// (Phase 8 Task 3). Wired via SetCanvasLookup.
	canvasLookup CanvasLookup
	// Optional. Builds the <saved_user_memory> system-prompt block
	// (S1 — Personal Memory Core). Gated by conv.MemoryEnabled.
	memorySvc MemoryReader
	// Optional. Dispatches AI tool_calls (save_memory, list_memories
	// today; future tools register here). Same MemoryEnabled gate
	// hides the tools from the gateway request when the toggle is
	// off so the model doesn't try to call them.
	toolExec *ToolExecutor
	// Phase D — optional model-id -> tool-capable lookup, backed by the
	// gateway model catalog (handler.ModelHandler). Used by SendStream's
	// image routing to decide whether to override a non-tool model for an
	// image-generation turn. nil => no override (capability unknown).
	toolCapable func(ctx context.Context, modelID string) bool
	// Optional model-id -> "known to lack tool support" lookup
	// (handler.ModelHandler.KnownWithoutTools). attachTools skips the
	// tools block for these models so a mid-conversation switch to a
	// non-tool model (e.g. after an image was generated) doesn't send
	// a ``tools`` param the provider would reject. nil / unknown id /
	// the Auto sentinel => tools stay attached.
	nonToolModel func(ctx context.Context, modelID string) bool
	// Optional model-id -> reasoning-capable lookup backed by the live
	// gateway catalog (handler.ModelHandler.ReasoningCapable) — models-
	// catalog step 3. attachReasoning's trivial-turn downgrade gate uses
	// it so a DB-onboarded reasoning model is recognised without a code
	// change. nil => the static isReasoningCapableID fallback.
	reasoningCapable func(ctx context.Context, modelID string) bool
	// Optional model-id -> vision-capable lookup
	// (handler.ModelHandler.SupportsVision). Gates Approach B: only a
	// model the catalog says can SEE images gets generated images
	// replayed into history as image_url content; everything else
	// (nil checker, unknown id, the Auto sentinel) falls back to the
	// Approach-A text annotation.
	visionCapable func(ctx context.Context, modelID string) bool
	// Web-search attach-time gate. ``searchConfig`` queries the gateway
	// for the (shared-key) tenant's search-enabled state; the result is
	// cached behind ``searchCfgMu`` for ~60s. nil => web_search is never
	// offered (feature unwired).
	searchConfig     searchConfigChecker
	searchCfgMu      sync.Mutex
	searchCfgEnabled bool
	searchCfgExpiry  time.Time
	// Optional. Builds the <conversation_state> system-prompt block
	// (S3 — Structured Conversation State) AND backs the
	// update_conversation_state AI tool. Auto-extraction kicks in
	// from finalizeConversation when message count crosses the
	// thresholds defined below.
	stateSvc *ConversationStateService
	// Optional. Token-budgeted context assembly (S4). When wired,
	// every send path runs the loaded history + freshly-built
	// system prefix through Compile to trim to the model's working
	// context window before shipping to the gateway. Falls back to
	// the legacy "send everything prepareSend loaded" path when nil.
	compiler *ContextCompiler
	// Optional. Persists per-response context sources (S7). When
	// wired, every send path collects sources during compile + tool
	// execution and flushes them to chat.response_context_sources
	// in the finish callback. Nil-safe — no source rows when
	// unwired (tests, downgrade path).
	sourceRepo *repository.ResponseContextSourceRepo
	// Optional. Background pattern learner (S8). Fires from
	// finalizeConversation alongside the S3 state extractor — once
	// per qualifying conversation. Nil-safe.
	prefExtractor *PreferenceExtractor

	maxContext int // max history messages threaded to the gateway
	// Dedicated timeout for image generation. Kept separate from text
	// streaming so slow image providers don't change chat behavior.
	imageGenerationTimeout time.Duration

	// Phase 4A.2.2 — DLP canonical history. When ``canonicalHistoryEnabled``
	// the provider-history builder uses ``content_canonical`` (status
	// complete) ONLY and excludes every other assistant row; the consumer
	// persists the canonical artifact. ``canonicalHistoryShadow`` validates
	// + counts without persisting or changing provider history. Defaults
	// false/false (legacy) until wired from config.
	canonicalHistoryEnabled bool
	canonicalHistoryShadow  bool

	// Phase 4A.2.4 — DLP display lifecycle. When ``displayLifecycleEnabled``
	// new assistant writes persist a provider-safe representation (tokenized
	// canonical or redacted placeholder) instead of restored raw display, and
	// reads go through ResolveRepresentation. ``legacyDisplayStrict`` redacts
	// pre-feature legacy rows on owner reload. ``displayRestorer`` (nil
	// default) is the Vault-V2-backed restore-on-read bridge. See
	// display_lifecycle.go. Defaults keep legacy behavior.
	displayLifecycleEnabled bool
	displayLifecycleShadow  bool
	legacyDisplayStrict     bool
	displayRestorer         DisplayRestorer

	// activeStreams tracks in-flight streaming generations so the
	// POST /cancel endpoint can interrupt them. Keyed by
	// conversation_id (one in-flight generation per conversation in
	// Phase 1; the second concurrent send would race the first).
	activeStreams sync.Map // map[uuid.UUID]*streamRegistration

	// Phase 5 — stream resume. Mirrors every in-flight generation's
	// events (keyed by assistant message id) so a disconnected client
	// can reattach via GET .../messages/{id}/stream. In-process only;
	// see stream_broker.go for the single-instance caveat. Nil in unit
	// tests that construct MessageService directly — teeStream and
	// ResumeAttach are nil-safe.
	broker *StreamBroker
}

// FileLinker is the slice of FileService the message flow needs.
// Decouples message_service from the concrete file service so unit
// tests can pass a tiny fake.
//
// ImageDataURLsForMessage resolves the image attachments on a single
// message to a list of "data:image/...;base64,..." strings, ready
// to drop into a vision request. Returns an empty slice when the
// message has no image attachments, or when image processing is
// unavailable — the caller treats nil as "no images" and ships a
// plain text turn.
type FileLinker interface {
	AttachToMessage(ctx context.Context, tenantID, convID, messageID uuid.UUID, fileIDs []uuid.UUID) (int64, error)
	BuildContextBlock(ctx context.Context, tenantID, convID uuid.UUID) (string, error)
	ImageDataURLsForMessage(ctx context.Context, tenantID, messageID uuid.UUID) []string
	// ImageDataURLsForMessageChecked is ImageDataURLsForMessage plus the
	// per-image failure list, so the streaming send paths can tell the
	// user an attachment couldn't be read instead of silently dropping it.
	ImageDataURLsForMessageChecked(ctx context.Context, tenantID, messageID uuid.UUID) ([]string, []ImageAttachFailure)
	// GeneratedImageNotes returns a per-message annotation describing
	// the AI-generated image attachments on each given message (one
	// batched query). The request builder appends it to the assistant
	// message COPY shipped to the provider so a model switched mid-
	// conversation knows a generated image really exists. nil when no
	// message carries one.
	GeneratedImageNotes(ctx context.Context, tenantID uuid.UUID, messageIDs []uuid.UUID) map[uuid.UUID]string
	// GeneratedImageVisionURLs returns per-message base64 data URLs
	// for the generated images on those messages (resized + capped at
	// the file-service layer) so the builder can replay them as vision
	// content when the target model supports it (Approach B). nil when
	// none qualify — callers then rely on the text annotation alone.
	GeneratedImageVisionURLs(ctx context.Context, tenantID uuid.UUID, messageIDs []uuid.UUID) map[uuid.UUID][]string
}

// PreferencesReader pulls the user's custom instructions for system-
// prompt injection. Same decoupling trick as FileLinker — interface
// in this package, concrete impl wired in main.go.
type PreferencesReader interface {
	BuildInstructionsBlock(ctx context.Context, tenantID, userID uuid.UUID) string
}

// RAGReader queries the bound knowledge base for context relevant
// to the user's prompt. The message service hands it the most-recent
// user content; the returned string (possibly empty) is folded into
// the system prompt prefix.
type RAGReader interface {
	BuildContext(ctx context.Context, tenantID, userID, kbID uuid.UUID, query string) string
}

// MemoryReader is the slice of MemoryService the message flow needs.
// BuildMemoryContext returns the <saved_user_memory> block (or "")
// for the system prompt; the message service handles the
// conv.MemoryEnabled gate before calling.
type MemoryReader interface {
	BuildMemoryContext(ctx context.Context, userID, tenantID uuid.UUID) string
	// S7 — same shape but also returns the per-memory source
	// attribution list. Concrete MemoryService implements both;
	// fakes can implement either depending on what they exercise.
	BuildMemoryContextWithSources(ctx context.Context, userID, tenantID uuid.UUID) (string, []ContextSource)
}

// ExcelLookup is the slice of the file repo the Phase 4 Excel
// system-prompt injector needs. Interface so unit tests don't have
// to wire the full pgx pool.
type ExcelLookup interface {
	ListByConversation(ctx context.Context, tenantID, convID uuid.UUID) ([]model.ConversationFile, error)
}

// streamRegistration is what's stored in MessageService.activeStreams.
// Holds the cancel func + a flag the cancel path flips so the
// stream's “finish“ callback knows whether the termination was
// user-initiated (status="cancelled") vs upstream error
// (status="error") vs natural completion (status="completed").
type streamRegistration struct {
	cancel        context.CancelFunc
	mu            sync.Mutex
	userCancelled bool
}

func (r *streamRegistration) markCancelled() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.userCancelled = true
}

func (r *streamRegistration) isUserCancelled() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.userCancelled
}

func NewMessageService(
	conversations *repository.ConversationRepo,
	messages *repository.MessageRepo,
	gateway *GatewayClient,
	titles *TitleService,
	models ModelChecker,
	maxContext int,
) *MessageService {
	if maxContext <= 0 {
		maxContext = 50
	}
	return &MessageService{
		conversations:          conversations,
		messages:               messages,
		gateway:                gateway,
		titles:                 titles,
		models:                 models,
		maxContext:             maxContext,
		imageGenerationTimeout: 180 * time.Second,
		broker:                 NewStreamBroker(),
	}
}

// teeStream mirrors the pump channel into the resume broker keyed by
// the assistant message id. Nil broker (direct-constructed tests) ⇒
// pass-through unchanged.
func (s *MessageService) teeStream(msgID uuid.UUID, runID string, ch chan StreamUpdate) (<-chan StreamUpdate, string) {
	if s.broker == nil {
		return ch, runID
	}
	// FIX41 — every generation gets an id of its own. Not the message id:
	// a regenerate reuses neither the row nor the client's cursor, and a
	// client that cannot tell one run from the next will happily drop the
	// new run's frames for failing to advance the old run's seq.
	if runID == "" {
		runID = uuid.NewString()
	}
	return s.broker.TeeRun(msgID, runID, ch), runID
}

// ConfigureRunLedger applies the deployment's ledger bounds and attaches
// the durable mirror. A nil redis client leaves the ledger in-memory only,
// which is a degradation (no resume across a restart) and not a failure —
// refusing to stream because a resume convenience is unavailable would be
// the worse trade.
func (s *MessageService) ConfigureRunLedger(ttl time.Duration, maxEvents int, rdb *redis.Client) {
	if s.broker == nil {
		return
	}
	s.broker.SetLedgerBounds(ttl, maxEvents)
	if rdb != nil {
		s.broker.SetRunStore(NewRedisRunStore(rdb, ttl, maxEvents))
	}
}

// OpenRun lets the handler register a generation with the resume broker
// before the pump exists, so frames written during the early-open pause
// are numbered by the same counter as the rest of the run.
func (s *MessageService) OpenRun(msgID uuid.UUID, runID string) {
	if s.broker == nil {
		return
	}
	s.broker.OpenRun(msgID, runID)
}

// RecordFrame stamps one outgoing SSE frame into the run's ledger and
// returns its seq. “build“ receives the assigned seq plus final_seq
// (non-zero only for a terminal frame) and must return the exact bytes
// the writer is about to send — the ledger and the wire must never
// disagree, because a resume replays the ledger verbatim.
func (s *MessageService) RecordFrame(
	msgID uuid.UUID, event string, build func(seq, finalSeq int64) []byte,
) (int64, bool) {
	if s.broker == nil {
		return 0, false
	}
	return s.broker.Record(msgID, event, build)
}

func (s *MessageService) SetImageGenerationTimeout(timeout time.Duration) {
	if timeout <= 0 {
		return
	}
	s.imageGenerationTimeout = timeout
}

// SetCanonicalHistory wires the Phase 4A.2.2 canonical-history rollout
// flags after construction (mirrors the other Set* wiring). Defaults stay
// false (legacy) when never called.
func (s *MessageService) SetCanonicalHistory(enabled, shadow bool) {
	s.canonicalHistoryEnabled = enabled
	s.canonicalHistoryShadow = shadow
}

// persistFinishContent writes the display content + (when canonical
// history is ENABLED) the canonical artifact metadata, atomically. When
// disabled or shadow, only the display content is written (legacy
// behaviour) so a non-complete row is later EXCLUDED from provider
// history rather than leaking display. “streamCompleted“ distinguishes
// a normal terminal from cancellation/error for the status derivation.
func (s *MessageService) persistFinishContent(
	ctx context.Context, msgID uuid.UUID, content, status string,
	tokInput, tokOutput *int, canon *CanonicalStreamState, streamCompleted bool,
) {
	if !s.canonicalHistoryEnabled || canon == nil {
		_ = s.messages.UpdateContent(ctx, msgID, content, status, tokInput, tokOutput)
		return
	}
	cstatus, art, errCode := canon.DeriveStatus(streamCompleted)
	w := repository.CanonicalWrite{Status: cstatus}
	var canonPtr *string
	if cstatus == CanonicalStatusComplete && art != nil {
		sv, gen := art.SchemaVersion, art.GenerationID
		sha, ln, cc := art.CanonicalSHA256, art.CanonicalLength, art.CanonicalContent
		w.Content, w.SchemaVersion, w.GenerationID = &cc, &sv, &gen
		w.SHA256, w.Length = &sha, &ln
		canonPtr = &cc
	} else if errCode != "" {
		w.ErrorCode = &errCode
	}
	// Phase 4A.2.4 — choose the PERSISTED representation. Flag off → restored
	// display (legacy). Flag on → tokenized canonical (complete) or a redacted
	// placeholder; never restored raw display.
	storedDisplay, dispState := s.persistedDisplay(content, canonPtr)
	w.DisplayState = dispState
	if err := s.messages.UpdateContentAndCanonical(
		ctx, msgID, storedDisplay, status, tokInput, tokOutput, w,
	); err != nil {
		// H3/§5 — a failed canonical write must leave the row NON-replayable.
		// Invalidate any prior canonical atomically (NULL canonical +
		// non-complete status) rather than a plain write that could preserve
		// a stale ``complete``. No raw/error content logged.
		sd, ds := s.persistedDisplay(content, nil)
		_ = s.messages.UpdateContentInvalidateCanonical(
			ctx, msgID, sd, status, CanonicalStatusUnavailable,
			"canonical_write_failed", ds, tokInput, tokOutput,
		)
	}
}

// canonicalHistoryEligible is the MANAGED-CHANNEL enable gate. Canonical
// history may be persisted/used ONLY for a managed WebChat request with a
// complete authenticated owner scope (tenant + user + conversation) AND the
// feature enabled. chat-edge only ever serves managed WebChat, so the public
// Direct API (which talks to the Python gateway directly and never reaches
// this service) is structurally never canonical-history-eligible — it must
// not be treated as having provider-safe restored history.
func (s *MessageService) canonicalHistoryEligible(in SendInput, conv *model.Conversation) bool {
	if !s.canonicalHistoryEnabled {
		return false
	}
	if conv == nil {
		return false
	}
	return in.TenantID != uuid.Nil && in.UserID != uuid.Nil && conv.ID != uuid.Nil
}

// persistNonStreamCanonical consumes the internal “x_sran_dlp“ artifact on
// a non-stream gateway response and persists canonical metadata atomically.
// Complete + valid → “content_canonical“ + status=complete; missing /
// unavailable / invalid → display kept but canonical INVALIDATED (NULL +
// non-complete) — never a display fallback. No-op unless the caller has
// already confirmed managed-channel eligibility.
func (s *MessageService) persistNonStreamCanonical(
	ctx context.Context, msgID uuid.UUID, content string,
	tokIn, tokOut *int, art *CanonicalArtifact,
) {
	cstatus, valid, errCode := validateNonStreamArtifact(art, art != nil)
	if cstatus == CanonicalStatusComplete && valid != nil {
		sv, gen := valid.SchemaVersion, valid.GenerationID
		sha, ln, cc := valid.CanonicalSHA256, valid.CanonicalLength, valid.CanonicalContent
		storedDisplay, dispState := s.persistedDisplay(content, &cc)
		_ = s.messages.UpdateContentAndCanonical(
			ctx, msgID, storedDisplay, "completed", tokIn, tokOut,
			repository.CanonicalWrite{
				Status: CanonicalStatusComplete, Content: &cc,
				SchemaVersion: &sv, GenerationID: &gen, SHA256: &sha, Length: &ln,
				DisplayState: dispState,
			},
		)
		return
	}
	if errCode == "" {
		errCode = "non_stream_no_canonical"
	}
	sd, ds := s.persistedDisplay(content, nil)
	_ = s.messages.UpdateContentInvalidateCanonical(
		ctx, msgID, sd, "completed", cstatus, errCode, ds, tokIn, tokOut,
	)
}

// persistDisplayOnly writes display content for a finish/update path that
// has NO canonical artifact (image, edit, non-stream-without-envelope,
// error/cancel) and, when canonical history is enabled, atomically
// INVALIDATES any prior canonical (H3) so a stale “complete“ can never be
// replayed. When disabled it uses the legacy plain write (no canonical
// columns referenced — H4 pre-migration safe).
func (s *MessageService) persistDisplayOnly(
	ctx context.Context, msgID uuid.UUID, content, status string,
	tokInput, tokOutput *int,
) {
	if !s.canonicalHistoryEnabled {
		_ = s.messages.UpdateContent(ctx, msgID, content, status, tokInput, tokOutput)
		return
	}
	// No canonical artifact on this path → redacted under display lifecycle
	// (never persist partial raw display), tokenized/legacy otherwise.
	sd, ds := s.persistedDisplay(content, nil)
	_ = s.messages.UpdateContentInvalidateCanonical(
		ctx, msgID, sd, status, CanonicalStatusUnavailable,
		"display_only_update", ds, tokInput, tokOutput,
	)
}

// SetFileLinker wires in the file service after construction. Done
// in main.go so the dependency order (file_service needs the file
// repo, message_service needs the file_service) stays linear and
// the constructor signatures don't churn.
func (s *MessageService) SetFileLinker(fl FileLinker) {
	s.fileSvc = fl
}

// SetPreferencesReader hooks up the custom-instructions injector.
// Same lazy-wire pattern as SetFileLinker.
func (s *MessageService) SetPreferencesReader(p PreferencesReader) {
	s.prefsSvc = p
}

// SetExcelLookup wires the file lookup the Excel system-prompt
// injector needs. Optional — without it the Excel block simply
// never renders and the AI gets the legacy CSV body via
// FileLinker.BuildContextBlock.
func (s *MessageService) SetExcelLookup(e ExcelLookup) {
	s.excelLookup = e
}

// CanvasLookup returns the latest version of every canvas artifact in a
// conversation (Phase 8 Task 3). Satisfied by
// *repository.CanvasArtifactRepo — the repo, not the service, because
// ownership of the conversation is already established by the message
// flow before the system prompt is built.
type CanvasLookup interface {
	ListLatest(ctx context.Context, tenantID, convID uuid.UUID) ([]model.CanvasArtifact, error)
}

// SetCanvasLookup wires the canvas system-prompt block. Optional —
// without it the block never renders and the model keeps the C1–C3
// behaviour of emitting a fresh code block per revision.
func (s *MessageService) SetCanvasLookup(c CanvasLookup) {
	s.canvasLookup = c
}

// ProjectReader supplies the project context (instructions + KB) a
// conversation inherits (Phase 4). Satisfied by *repository.ProjectRepo.
type ProjectReader interface {
	GetForConversation(ctx context.Context, tenantID, convID uuid.UUID) (*model.Project, error)
}

// SetProjectReader wires the Phase-4 project-context lookup. Optional —
// nil keeps every conversation on the pre-Phase-4 prompt path.
func (s *MessageService) SetProjectReader(r ProjectReader) {
	s.projectReader = r
}

// SkillReader supplies the user's enabled skills for turn-time trigger
// matching + the use_skill catalog (FIX12 Item 3). Satisfied by
// *repository.SkillRepo. The compiler stays repo-free — the message
// service preloads via this reader and hands the result in as strings.
type SkillReader interface {
	ListEnabledByUser(ctx context.Context, tenantID, userID uuid.UUID) ([]model.Skill, error)
}

// SetSkillReader wires the FIX12 skills lookup. Optional — nil keeps the
// prompt byte-identical to the pre-skills behaviour.
func (s *MessageService) SetSkillReader(r SkillReader) {
	s.skillReader = r
}

// resolveSkillsForTurn loads the user's enabled skills and splits them
// into (active, catalog): active = skills whose triggers case-
// insensitively substring-match the latest user message; catalog = ALL
// enabled skills (for the use_skill list). A load failure degrades to
// (nil, nil) — skills never block a reply. The compiler treats empty
// slices as a no-op, so the prompt stays byte-identical.
func (s *MessageService) resolveSkillsForTurn(
	ctx context.Context, tenantID, userID uuid.UUID, userText string,
) (active, catalog []model.Skill) {
	if s.skillReader == nil {
		return nil, nil
	}
	all, err := s.skillReader.ListEnabledByUser(ctx, tenantID, userID)
	if err != nil || len(all) == 0 {
		return nil, nil
	}
	lowText := strings.ToLower(userText)
	for i := range all {
		sk := all[i]
		if skillTriggersMatch(sk.Triggers, lowText) {
			active = append(active, sk)
		}
	}
	return active, all
}

// skillTriggersMatch reports whether any trigger is a case-insensitive
// substring of the (already-lowercased) message text. Empty/blank
// triggers never match, and empty text can't match anything.
func skillTriggersMatch(triggers []string, lowText string) bool {
	if lowText == "" {
		return false
	}
	for _, t := range triggers {
		t = strings.ToLower(strings.TrimSpace(t))
		if t == "" {
			continue
		}
		if strings.Contains(lowText, t) {
			return true
		}
	}
	return false
}

// SetRAGReader wires the knowledge-base retrieval service. Same
// lazy-wire pattern — kept out of the constructor so the service
// boots fine when RAG isn't configured.
func (s *MessageService) SetRAGReader(r RAGReader) {
	s.ragSvc = r
}

// SetMemoryService wires the S1 memory layer. When set:
//   - buildSystemPrefix injects the <saved_user_memory> block
//     (gated by conv.MemoryEnabled)
//   - Send/Regenerate/Edit include MemoryToolDefs() in the gateway
//     request's tool list (same gate)
//   - tool_call responses from the gateway dispatch through a
//     ToolExecutor that wraps this service
//
// Optional — without it the message flow falls back to the legacy
// memory-free behaviour.
func (s *MessageService) SetMemoryService(m *MemoryService) {
	s.memorySvc = m
	if m != nil {
		s.toolExec = NewToolExecutor(m)
		// If the state service was wired BEFORE memory, re-attach it
		// here so the executor exposes the S3 tool too.
		if s.stateSvc != nil {
			s.toolExec.SetConversationStateService(s.stateSvc)
		}
	}
}

// SetConversationStateService wires the S3 conversation-state
// layer. When set:
//   - buildSystemPrefix injects the <conversation_state> block
//     between the user-memory block and the file/RAG blocks
//   - the ToolExecutor exposes update_conversation_state alongside
//     save_memory / list_memories
//   - finalizeConversation triggers async state extraction on
//     thresholds (10 / +20 messages)
func (s *MessageService) SetConversationStateService(svc *ConversationStateService) {
	s.stateSvc = svc
	if s.toolExec != nil {
		s.toolExec.SetConversationStateService(svc)
	}
}

// SetImageGeneration wires the Phase-B generate_image tool onto the
// ToolExecutor (gen = GatewayClient, files = FileService, passed as
// interfaces). Optional — when unset, generate_image is hidden. Must run
// after SetMemoryService because the executor is created there.
func (s *MessageService) SetImageGeneration(gen imageGenerator, files generatedImageSaver, model string) {
	if s.toolExec != nil {
		s.toolExec.SetImageGeneration(gen, files, model)
	}
}

// SetVideoGeneration wires the Sprint-2A generate_video tool onto the
// ToolExecutor. Same optional passthrough pattern as SetImageGeneration.
func (s *MessageService) SetVideoGeneration(gen videoGenerator, files generatedVideoSaver, model string) {
	if s.toolExec != nil {
		s.toolExec.SetVideoGeneration(gen, files, model)
	}
}

// SetWebSearch wires the web_search tool onto the ToolExecutor (gateway
// search client + the message service that persists citations). Same
// passthrough pattern as SetImageGeneration. Pair with
// SetSearchConfigChecker, which gates the tool per tenant.
func (s *MessageService) SetWebSearch(searcher webSearcher, saver searchSourcesSaver) {
	if s.toolExec != nil {
		s.toolExec.SetWebSearch(searcher, saver)
	}
}

// SetWebFetch wires the Phase-3 web_fetch tool (SSRF-guarded page
// reader). Shares the citation saver with web_search so fetched pages
// render as the same source chips.
func (s *MessageService) SetWebFetch(fetcher webPageFetcher, saver searchSourcesSaver) {
	if s.toolExec != nil {
		s.toolExec.SetWebFetch(fetcher, saver)
	}
}

// SetCodeInterpreter wires the Phase-13-B run_python tool onto the
// ToolExecutor: the sandbox-backed interpreter, the ownership-checked file
// resolver, and the artifact saver. Optional — when unset, run_python is
// hidden. Callers wire this ONLY when the sandbox is available
// (SANDBOX_ENABLED=1); run_python never runs model code in-process.
func (s *MessageService) SetCodeInterpreter(ci *CodeInterpreter, files codeFileResolver, saver generatedFileSaver) {
	if s.toolExec != nil {
		s.toolExec.SetCodeInterpreter(ci, files, saver)
	}
}

// SetCanvasArtifactTool exposes update_canvas_artifact to the model
// (Phase 8 Task 3). Optional — without it the tool is hidden and canvas
// artifacts stay user-edited only, exactly as in C3.
func (s *MessageService) SetCanvasArtifactTool(c canvasArtifactUpdater) {
	if s.toolExec != nil {
		s.toolExec.SetCanvasArtifacts(c)
	}
}

// SetSkillTool exposes the FIX12 use_skill tool to the model. Optional —
// without it the tool is hidden. Forwards to the ToolExecutor (created
// inside SetMemoryService), so call this AFTER SetMemoryService.
func (s *MessageService) SetSkillTool(r *repository.SkillRepo) {
	if s.toolExec != nil {
		s.toolExec.SetSkillRepo(r)
	}
}

// SetToolCapabilityChecker wires a model-id -> tool-capable lookup used by
// Phase D image routing to decide whether to override a non-tool model for
// an image-generation turn. Optional — when unset, no override happens.
// Backed by handler.ModelHandler's cached catalog.
func (s *MessageService) SetToolCapabilityChecker(fn func(ctx context.Context, modelID string) bool) {
	s.toolCapable = fn
}

// SetNonToolModelChecker wires a model-id -> "known to lack tool support"
// lookup (handler.ModelHandler.KnownWithoutTools). Optional — when unset,
// tools are attached for every model (pre-existing behaviour).
func (s *MessageService) SetNonToolModelChecker(fn func(ctx context.Context, modelID string) bool) {
	s.nonToolModel = fn
}

// SetReasoningCapabilityChecker wires a model-id -> reasoning-capable
// lookup (handler.ModelHandler.ReasoningCapable) backed by the live
// gateway catalog. Optional — when unset, attachReasoning falls back to
// the static isReasoningCapableID set (pre-existing behaviour).
// SetServedModel records, on the assistant row, the model that actually
// answered (FIX35 Part B). An "auto" conversation's rows used to carry the
// literal "auto"; now each reply names the model the gateway's router
// picked, so the per-message meta line and any later audit read the truth.
// Best-effort: a failure here must not fail the turn.
func (s *MessageService) SetServedModel(ctx context.Context, msgID uuid.UUID, modelID string) error {
	modelID = strings.TrimSpace(modelID)
	if modelID == "" || msgID == uuid.Nil {
		return nil
	}
	return s.messages.SetModelID(ctx, msgID, modelID)
}

func (s *MessageService) SetReasoningCapabilityChecker(fn func(ctx context.Context, modelID string) bool) {
	s.reasoningCapable = fn
}

// reasoningCheckerFor adapts the injected checker to the per-turn shape
// attachReasoning takes; nil when no checker is wired so the callee
// falls back to the static set.
func (s *MessageService) reasoningCheckerFor(ctx context.Context) func(string) bool {
	if s.reasoningCapable == nil {
		return nil
	}
	return func(id string) bool { return s.reasoningCapable(ctx, id) }
}

// SetVisionCapabilityChecker wires a model-id -> vision-capable lookup
// (handler.ModelHandler.SupportsVision). Optional — when unset,
// generated images are never replayed as vision content and history
// keeps the text-annotation behaviour.
func (s *MessageService) SetVisionCapabilityChecker(fn func(ctx context.Context, modelID string) bool) {
	s.visionCapable = fn
}

// searchConfigChecker fetches the gateway's per-tenant web-search
// config (whether the tool should be offered). Backed by *GatewayClient.
type searchConfigChecker interface {
	SearchConfig(ctx context.Context, callCtx GatewayCallContext) (*GatewaySearchConfig, error)
}

// SetSearchConfigChecker wires the attach-time web_search gate.
// Optional — when unset, web_search is never offered.
func (s *MessageService) SetSearchConfigChecker(c searchConfigChecker) {
	s.searchConfig = c
}

// SetContextCompiler wires the S4 token-budgeted compiler. Without
// it the send paths fall through to the legacy "ship everything
// prepareSend loaded" behaviour — useful for tests + a graceful
// downgrade if the compiler ever needs to be disabled.
func (s *MessageService) SetContextCompiler(c *ContextCompiler) {
	s.compiler = c
}

// SetSearchService wires the S6 read-only search layer onto the
// ToolExecutor. Optional — when nil, search_past_conversations is
// hidden from the tool list. Must run after SetMemoryService
// because the executor is created inside SetMemoryService.
func (s *MessageService) SetSearchService(svc *SearchService) {
	if s.toolExec != nil {
		s.toolExec.SetSearchService(svc)
	}
}

// SetResponseSourceRepo wires the S7 attribution persist path.
// Without it, sources collected during compile + tool execution
// are simply dropped at the end of the turn (no errors, no rows).
func (s *MessageService) SetResponseSourceRepo(r *repository.ResponseContextSourceRepo) {
	s.sourceRepo = r
}

// SetPreferenceExtractor wires the S8 background pattern learner.
// Without it, finalizeConversation skips the
// maybeExtractPreferences hook entirely (no extraction goroutines,
// no pending suggestions).
func (s *MessageService) SetPreferenceExtractor(e *PreferenceExtractor) {
	s.prefExtractor = e
}

// persistSources flushes an accumulator's snapshot to
// chat.response_context_sources. Best-effort: a failed insert
// logs a warning but never blocks the turn. Uses background ctx
// so a client disconnect during the very last instant doesn't
// strand the rows.
func (s *MessageService) persistSources(
	parent context.Context,
	asstMsgID, userID, tenantID, convID uuid.UUID,
	acc *SourcesAccumulator,
) {
	if s.sourceRepo == nil || acc == nil {
		return
	}
	snapshot := acc.Snapshot()
	if len(snapshot) == 0 {
		return
	}
	rows := toModelSources(snapshot, asstMsgID.String(), userID.String(), tenantID.String(), convID.String())
	if len(rows) == 0 {
		return
	}
	// Detach: persist runs on whichever goroutine called finish,
	// but the request ctx may already be cancelled. Use background.
	bg := context.Background()
	if err := s.sourceRepo.CreateBatch(bg, rows); err != nil {
		slog.Warn("context sources: persist failed",
			"asst_msg", asstMsgID.String(), "count", len(rows), "err", err)
	}
	_ = parent // kept for signature symmetry with other persist helpers
}

// ---------------------------------------------------------------------------
// ListMessages — handler reads conversation history
// ---------------------------------------------------------------------------

func (s *MessageService) ListMessages(
	ctx context.Context,
	tenantID, userID, convID uuid.UUID,
	limit int,
	cursor uuid.UUID,
) ([]model.Message, bool, error) {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, false, ErrMessageConversationNotFound
		}
		return nil, false, err
	}
	if conv.UserID != userID {
		// Other tenant member's conversation — same tenant, but the
		// chat product is single-user-per-conversation in Phase 1.
		return nil, false, ErrMessageConversationNotFound
	}
	if limit <= 0 {
		limit = 50
	}
	if limit > 100 {
		limit = 100
	}
	return s.messages.ListByConversation(ctx, tenantID, convID, limit, cursor)
}

// ---------------------------------------------------------------------------
// SendNonStream — request → blocking gateway call → response
// ---------------------------------------------------------------------------

// SendInput is the typed payload from the handler.
type SendInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	Content        string
	RequestID      string
	// FileIDs is the (optional) list of conversation_files row ids
	// the user attached to this send. Each id is patched onto the
	// new user message + their extracted text is folded into the
	// gateway request's system context.
	FileIDs []uuid.UUID
	// ImageMode (Phase C) — the web-chat 🎨 toggle. When set, a
	// generate_image directive is appended to the system prompt so a
	// tool-capable model produces an image rather than a text reply.
	ImageMode bool
	// CloudToolAllowlist (FIX16 §1.4) restricts the tools offered on this
	// turn to the named set. nil = unrestricted (every interactive turn).
	// A cloud scheduled run passes model.CloudAllowedToolNames() so an
	// unattended turn can never reach a spend/write/connector tool. When
	// set, executeToolCalls also REFUSES any call outside the set and marks
	// ToolExecContext.WithheldToolRequested so the run's audit shows the wall.
	CloudToolAllowlist []string
	// OnCompactionStart (FIX33 A-W9) — set by the streaming handler.
	// Fires once, before the synchronous pre-trim state extraction,
	// with the ids the SSE message_start frame needs, so the handler
	// can open the response and show "organising the conversation…"
	// during the pause instead of nothing. Nil on non-stream sends.
	OnCompactionStart func(assistantID, userMessageID uuid.UUID)
	// RunID (FIX41) — the id the HANDLER already minted for this
	// generation. The handler needs it before SendStream returns, because
	// the OnCompactionStart early-open writes frames first; passing it in
	// keeps one run id across both. Empty ⇒ the service mints one.
	RunID string
}

// SendResult is what the non-stream handler returns to the client.
type SendResult struct {
	UserMessage      *model.Message
	AssistantMessage *model.Message
	// PolicyAction (FIX16 §1.5) is the DLP verdict the gateway reported for
	// this turn ("allow"|"mask"|"block"|…), surfaced from the x_sran_dlp
	// envelope so an unattended run's audit row can record what the policy
	// did. Empty when the gateway returned no verdict (public/Direct paths).
	PolicyAction string
	// WithheldToolRequested (FIX16 §1.4/§2) is true when a cloud run's model
	// asked for a tool outside the allowlist; the runner records a
	// tool_withheld skip note so the wall is visible in run history.
	WithheldToolRequested bool
}

func (s *MessageService) SendNonStream(ctx context.Context, in SendInput) (*SendResult, error) {
	conv, userMsg, history, err := s.prepareSend(ctx, in)
	if err != nil {
		return nil, err
	}

	imgs := s.resolveUserImages(ctx, in.TenantID, history)
	sourcesAcc := NewSourcesAccumulator()
	gwReq := s.compileAndBuildRequest(ctx, conv, in.UserID, in.TenantID, in.Content, history, imgs, sourcesAcc, s.omittedOlder(ctx, in.TenantID, conv.ID, len(history)), nil)
	toolsAttached := s.attachTools(ctx, &gwReq, conv, in.CloudToolAllowlist)
	// FIX16 §1.4 — on a cloud run, build the allow-map the executor guard
	// uses to refuse (and flag) any out-of-allowlist call.
	var cloudAllow map[string]bool
	var withheldToolRequested bool
	if in.CloudToolAllowlist != nil {
		cloudAllow = make(map[string]bool, len(in.CloudToolAllowlist))
		for _, n := range in.CloudToolAllowlist {
			cloudAllow[n] = true
		}
	}
	gwResp, err := s.gateway.SendChatCompletion(
		ctx, gatewayCallContext(in, conv), gwReq,
	)
	if err != nil {
		// Persist a placeholder assistant row with status=error so the
		// conversation timeline reflects the failed turn — the
		// front-end can show a "Couldn't reach the AI" bubble.
		_ = s.persistError(ctx, in, conv, "")
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
		}
		return nil, err
	}

	// Tool-execution loop. When the model emits tool_calls instead of
	// (or alongside) content, we dispatch them through the executor,
	// append the results as role="tool" messages, and re-call the
	// gateway. Bounded by maxToolIterations.
	if toolsAttached {
		iter := 0
		for iter < maxToolIterations && hasToolCalls(gwResp) {
			tcMsg, results := s.executeToolCalls(ctx, toolCallsOf(gwResp), ToolExecContext{
				TenantID:       in.TenantID,
				UserID:         in.UserID,
				ConversationID: conv.ID,
				Sources:        sourcesAcc,
				CloudToolAllow: cloudAllow,             // FIX16 §1.4 — nil on interactive turns
				WithheldHit:    &withheldToolRequested, // set if a withheld tool is asked for
			})
			gwReq.Messages = append(gwReq.Messages, tcMsg)
			gwReq.Messages = append(gwReq.Messages, results...)
			next, err := s.gateway.SendChatCompletion(ctx, gatewayCallContext(in, conv), gwReq)
			if err != nil {
				_ = s.persistError(ctx, in, conv, "")
				var ue *GatewayUnavailableError
				if errors.As(err, &ue) {
					return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
				}
				return nil, err
			}
			gwResp = next
			iter++
		}
	}

	content := extractContent(gwResp)
	var tokInput, tokOutput *int
	if gwResp.Usage != nil {
		ti, to := gwResp.Usage.PromptTokens, gwResp.Usage.CompletionTokens
		tokInput, tokOutput = &ti, &to
	}

	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID:   conv.ID,
		TenantID:         in.TenantID,
		UserID:           in.UserID,
		Role:             "assistant",
		ContentMasked:    content,
		Status:           "completed",
		ModelID:          &conv.ModelID,
		TokenUsageInput:  tokInput,
		TokenUsageOutput: tokOutput,
	})
	if err != nil {
		return nil, err
	}

	// Phase 4A.2.x — non-stream canonical consumer. Managed-channel gated;
	// persists complete canonical from the internal ``x_sran_dlp`` envelope
	// or invalidates (never a display fallback). Disabled / public Direct
	// API never reach this (gate false → row stays legacy → excluded).
	if s.canonicalHistoryEligible(in, conv) {
		s.persistNonStreamCanonical(ctx, asstMsg.ID, content, tokInput, tokOutput, gwResp.XSranDLP)
	}

	s.finalizeConversation(ctx, conv, userMsg, asstMsg)
	// S7 — persist the per-turn source attribution. Best-effort;
	// nil-safe when the source repo isn't wired.
	s.persistSources(ctx, asstMsg.ID, in.UserID, in.TenantID, conv.ID, sourcesAcc)

	return &SendResult{
		UserMessage:           userMsg,
		AssistantMessage:      asstMsg,
		PolicyAction:          gwResp.PolicyAction, // FIX16 §1.5 — audit the DLP verdict
		WithheldToolRequested: withheldToolRequested,
	}, nil
}

// ---------------------------------------------------------------------------
// SendStream — request → open gateway stream → yield events
// ---------------------------------------------------------------------------

// StreamHandle is what SendStream returns. The caller (the HTTP
// handler) ranges over Events, forwards each to the browser via
// SSE, then calls Finish to persist the accumulated content.
type StreamHandle struct {
	UserMessage    *model.Message
	AssistantID    uuid.UUID // pre-allocated; sent on message_start
	ConversationID uuid.UUID
	// RunID (FIX41) identifies THIS generation. Announced on
	// message_start and repeated on every frame, including replays.
	RunID string

	Events <-chan StreamUpdate
	finish func(content string, reasoning string, finishReason string, usage *GatewayChatUsage)
	cancel func()
	// cancelled (FIX41) reports whether POST /cancel fired for this run.
	// The handler needs it to pick the right terminal event: a person
	// pressing Stop must not be reported to the other viewers of the same
	// generation as a crash, and then as a success (docs/fix41 P3).
	cancelled func() bool
}

// UserCancelled reports whether this generation was stopped by a person
// rather than by an error or by finishing. Safe on a handle whose path
// has no cancel registration (returns false).
func (h *StreamHandle) UserCancelled() bool {
	if h.cancelled == nil {
		return false
	}
	return h.cancelled()
}

// StreamUpdate is the per-event surface — one delta plus optional
// terminal metadata. The handler emits a “content_delta“ SSE
// event for every non-empty Delta and a “message_complete“ SSE
// event when Done is true.
//
// T4 — Thinking Mode adds two pass-through channels:
//   - ReasoningDelta: emitted by the Gateway as
//     “reasoning_summary_delta“ frames; the handler forwards
//     them to the frontend as “reasoning_delta“ SSE events.
//   - AnalysisStatus: “started“ or “completed“ markers that
//     bracket the reasoning stream; the handler forwards them as
//     “analysis_status“ SSE events so the client can show a
//     "thinking…" indicator without parsing every reasoning
//     fragment.
//
// Each frame populates ONE of (Delta / ReasoningDelta /
// AnalysisStatus) on a given update — the pump never combines
// them so the handler can route to the right event verb cleanly.
type StreamUpdate struct {
	Delta        string
	Done         bool
	FinishReason string
	Usage        *GatewayChatUsage
	Err          error

	ReasoningDelta string
	AnalysisStatus string

	// Image-generation phase marker, "started" only. Emitted just
	// before the generate_image tool executes (tool path) or the
	// direct /images/generations call begins (direct path) so the
	// frontend can swap the typing dots for an image placeholder.
	// There is no "completed" — the message_files SSE event (or a
	// terminal error / message_complete) ends the phase client-side.
	ImageStatus string

	// Video-generation phase marker, "started" only. Emitted just before
	// the generate_video tool executes so the frontend can show a video
	// placeholder. Like ImageStatus there is no "completed" — message_files
	// (or a terminal error / message_complete) ends the phase client-side.
	VideoStatus string

	// Web-search phase marker, "searching" only. Emitted just before
	// the web_search tool executes so the frontend can show a "searching
	// the web" indicator. Cleared client-side by the first content_delta
	// (the answer is streaming) or message_sources / message_complete.
	SearchStatus string

	// FIX33 A-W9 — compaction phase marker, "summarizing" only. Emitted
	// before the pre-trim state extraction so the user sees "organising
	// the conversation…" instead of an unexplained pause (claude.ai's
	// "organizing its thoughts"). Cleared client-side by the first
	// content_delta / message_complete / error.
	CompactionStatus string

	// Phase 8 Task 2 — run_python progress. Emitted twice per call:
	// once with Status="running" the moment the sandbox job starts (so
	// the user sees the code and a spinner instead of a silent 60s
	// pause), once with the terminal status + stdout/stderr. Nil on
	// every other event.
	CodeExecution *CodeExecutionUpdate

	// Generic per-tool activity marker for tools that have NO dedicated
	// progress event (save_memory, list_memories, update_conversation_state,
	// search_past_conversations, update_canvas_artifact — previously fully
	// invisible to the user). Emitted twice per call: "started" just before
	// the executor runs, "finished" right after. Ephemeral like
	// CodeExecution — never persisted; survives a mid-stream reconnect via
	// the broker snapshot only.
	ToolActivity *ToolActivityUpdate

	// B1 — a provider error frame forwarded from the Gateway. The
	// handler emits it as an ``error`` SSE event (distinct styling on
	// the frontend) instead of letting the stream end blank.
	ProviderError *StreamProviderError
	// B2 — a fallback notice forwarded from the Gateway. The handler
	// emits it as a ``fallback_info`` SSE event.
	FallbackInfo map[string]any
	// FIX35 Part B — the model that actually answered, as the gateway
	// names it (a router route resolved, or the chunk's own ``model``).
	// Forwarded once when it becomes known; the handler emits it as a
	// ``model_resolved`` SSE event, persists it on the assistant row and
	// repeats it on ``message_complete`` as ``model_id``.
	ServedModel string
	// Reasoning downgrade notice forwarded from the Gateway (the
	// answer was produced without reasoning although it was
	// requested). The handler emits it as a ``reasoning_notice`` SSE
	// event — informational, never an error.
	ReasoningNotice *ReasoningNotice

	// Provider-stall state. chat-edge holds the upstream connection, so
	// chat-edge is the only layer that can tell "the provider has gone
	// quiet" apart from "the server is busy working" — to a client both
	// look identical (see the heartbeat comment in handler/message.go).
	// Emitted once when the wait crosses the threshold and once more
	// with Stalled=false when output resumes.
	ProviderStall *ProviderStallNotice

	// One or more attached images could not be turned into vision
	// content (unreachable source, resize failure, unsupported format).
	// Emitted ONCE at the very start of the turn — before content — so
	// the person who attached them learns they didn't attach rather than
	// the turn silently degrading to text-only. Informational, never an
	// error: the turn still proceeds with whatever images did resolve.
	ImageNotice *ImageAttachNotice
}

// ImageAttachNotice is the payload of the “image_notice“ client event:
// the list of attachments that fell out of the vision request this turn.
type ImageAttachNotice struct {
	Failures []ImageAttachFailure `json:"failures"`
}

// ProviderStallNotice tells the client the turn is alive but the model
// has produced nothing for an unusual length of time. Informational —
// the turn is NOT cancelled and may still complete on its own.
type ProviderStallNotice struct {
	Stalled bool `json:"stalled"`
	// WaitingSeconds is how long the provider had been silent when this
	// frame was emitted (0 on the recovery frame).
	WaitingSeconds int `json:"waiting_seconds,omitempty"`
}

// ToolActivityUpdate is one generic "the assistant is using a tool"
// frame. ID is the gateway tool_call id so the client can pair the
// "started" and "finished" frames into one indicator/card.
type ToolActivityUpdate struct {
	ID    string `json:"id"`
	Tool  string `json:"tool"`
	Phase string `json:"phase"` // "started" | "finished"
}

// dedicatedEventTools are the tools whose progress already has a
// dedicated SSE event. The generic tool_activity is suppressed for
// them — emitting both would double-render client-side (the phase
// placeholder AND a generic activity row for the same call).
var dedicatedEventTools = map[string]bool{
	"generate_image": true, // image_generating
	"generate_video": true, // video_generating
	"web_search":     true, // search_status
	"web_fetch":      true, // search_status (reused, see pump)
	"run_python":     true, // code_execution
}

// silentByDesignTools are internal-bookkeeping tools that deliberately
// get NO tool_activity either: memory/state writes are housekeeping the
// model already acknowledges in its reply text ("จำไว้แล้วครับ…"), so a
// "saving a memory…" badge double-reports the same fact — ChatGPT and
// Claude both keep these silent. User-visible ACTIONS stay announced:
// search_past_conversations (reads the user's other chats) and
// update_canvas_artifact (mutates a document the user is looking at).
var silentByDesignTools = map[string]bool{
	"save_memory":               true,
	"list_memories":             true,
	"update_conversation_state": true,
}

// CodeExecutionUpdate is one run_python invocation as the UI sees it
// (Phase 8 Task 2). It carries the SOURCE the model wrote plus the
// captured output, so the client can render a ChatGPT-style
// code+output card instead of only the produced files.
//
// Field notes:
//   - ID is the gateway tool_call id. The client keys on it, so the
//     "running" frame and the terminal frame collapse into ONE card.
//   - Stdout/Stderr are TRUNCATED for display (truncateForDisplay);
//     the model still receives the full text in the tool result.
//   - Nothing here is persisted — see the SSE handler comment. A
//     reload after the turn completes keeps the produced files but
//     drops the card.
type CodeExecutionUpdate struct {
	ID         string   `json:"id"`
	Status     string   `json:"status"` // "running" | "completed" | "error"
	Code       string   `json:"code"`
	Stdout     string   `json:"stdout,omitempty"`
	Stderr     string   `json:"stderr,omitempty"`
	ExitCode   int      `json:"exit_code"`
	TimedOut   bool     `json:"timed_out,omitempty"`
	DurationMS int64    `json:"duration_ms,omitempty"`
	Artifacts  []string `json:"artifacts,omitempty"`
	// Truncated reports that Stdout/Stderr were clipped for display so
	// the card can say so instead of quietly showing a partial result.
	Truncated bool `json:"truncated,omitempty"`
}

// Cancel terminates the upstream gateway connection. Used by the
// HTTP handler when the client disconnects before completion.
func (h *StreamHandle) Cancel() { h.cancel() }

// Finish persists the final assistant message. MUST be called
// exactly once after the Events channel is fully drained, even on
// error (the handler still persists partial content with
// status=error so the conversation timeline survives).
//
// T4: “reasoning“ is the accumulated reasoning summary text the
// handler captured from StreamUpdate.ReasoningDelta. Empty string
// when reasoning was off / no reasoning frames arrived — the
// persist path skips the reasoning_content write in that case.
func (h *StreamHandle) Finish(content, reasoning, finishReason string, usage *GatewayChatUsage) {
	h.finish(content, reasoning, finishReason, usage)
}

func (s *MessageService) SendStream(ctx context.Context, in SendInput) (*StreamHandle, error) {
	conv, userMsg, history, err := s.prepareSend(ctx, in)
	if err != nil {
		return nil, err
	}
	// Create the assistant placeholder row. Inherit branch info
	// from the just-saved user message so the placeholder lives
	// inside the same fork group.
	asstStatus := "streaming"
	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "assistant",
		ContentMasked:  "",
		Status:         asstStatus,
		ModelID:        &conv.ModelID,
		ParentID:       userMsg.ParentID,
		BranchIndex:    userMsg.BranchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, err
	}

	// Open the upstream stream. cancelCtx propagates the
	// handler-driven cancel through to the HTTP transport.
	// We use context.Background as the parent (not ctx) so the
	// HTTP request finishing doesn't immediately tear down the
	// streaming connection — the handler explicitly invokes
	// StreamHandle.Cancel() when the client disconnects, and the
	// external POST /cancel endpoint invokes CancelStream(convID).
	streamCtx, cancel := context.WithCancel(context.Background())
	imgs := s.resolveUserImages(ctx, in.TenantID, history)
	sourcesAcc := NewSourcesAccumulator()
	// FIX33 A-W9 — let the handler open the SSE response and show
	// "organising the conversation…" the moment the pre-trim state
	// extraction starts (it can take seconds).
	var onExtractionStart func()
	if in.OnCompactionStart != nil {
		asstID, userID := asstMsg.ID, userMsg.ID
		cb := in.OnCompactionStart
		onExtractionStart = func() { cb(asstID, userID) }
	}
	gwReq := s.compileAndBuildRequest(ctx, conv, in.UserID, in.TenantID, in.Content, history, imgs, sourcesAcc, s.omittedOlder(ctx, in.TenantID, conv.ID, len(history)), onExtractionStart)
	toolsAttached := s.attachTools(ctx, &gwReq, conv, nil)
	intent := DetectImageIntent(in.Content)
	if in.ImageMode || intent {
		imageModel := ""
		if s.toolExec != nil {
			imageModel = s.toolExec.imageModel
		}
		imageCtx, imageCancel := context.WithTimeout(context.Background(), s.imageGenerationTimeout)
		reg := &streamRegistration{cancel: imageCancel}
		s.activeStreams.Store(conv.ID, reg)
		slog.Info("image_generation_route_resolved",
			"request_id", in.RequestID,
			"tenant_id", in.TenantID.String(),
			"user_id", in.UserID.String(),
			"conversation_id", conv.ID.String(),
			"message_id", asstMsg.ID.String(),
			"selected_model", conv.ModelID,
			"resolved_chat_model", gwReq.Model,
			"resolved_capability", "image_generation",
			"resolved_endpoint", "/v1/images/generations",
			"image_model", imageModel,
			"timeout_ms", s.imageGenerationTimeout.Milliseconds(),
			"image_mode", in.ImageMode,
			"intent_detected", intent,
			"tools_attached", toolsAttached,
		)
		cancel()
		return s.directImageStream(imageCtx, imageCancel, reg, conv, userMsg, asstMsg, in), nil
	}
	// Phase C + D — image routing. When the turn wants an image (the 🎨
	// toggle OR a detected "generate an image" intent): if the selected model
	// can call tools, apply the generate_image hint and proceed; if it
	// CANNOT, reject with a helpful "pick a 🎨 model" message — no gateway
	// call, no silent model swap — so the user keeps control of their model.
	if intent := DetectImageIntent(in.Content); (in.ImageMode || intent) && toolsAttached {
		// Unknown capability (checker not wired) -> treat as capable so we
		// never wrongly reject; only reject when we KNOW the model can't.
		toolCapable := s.toolCapable == nil || s.toolCapable(ctx, gwReq.Model)
		if shouldRejectImageTurn(toolCapable, intent, in.ImageMode) {
			slog.Info("phase-d: image request rejected (model lacks tools)",
				"conv_model", conv.ModelID, "intent", intent, "image_mode", in.ImageMode)
			return s.rejectImageStream(conv, userMsg, asstMsg, cancel), nil
		}
		applyImageModeHint(&gwReq)
	}
	stream, err := s.gateway.SendChatCompletionStream(
		streamCtx, gatewayCallContext(in, conv), gwReq,
	)
	if err != nil {
		cancel()
		// Persist as error so the timeline reflects the failed turn.
		_ = s.messages.UpdateContent(ctx, asstMsg.ID, "", "error", nil, nil)
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
		}
		return nil, err
	}

	// Register the in-flight stream so the POST /cancel endpoint
	// can interrupt it. The registration is removed inside the
	// ``finish`` callback below regardless of how the stream ends.
	reg := &streamRegistration{cancel: cancel}
	s.activeStreams.Store(conv.ID, reg)

	// Phase 4A.2.6.2 — wire the canonical stream state so the PRIMARY
	// streaming send path persists ``content_canonical`` + display lifecycle
	// (previously only Regenerate/Edit did; SendStream used legacy UpdateContent
	// and therefore persisted the restored display raw + canonical_status=legacy).
	canonState := &CanonicalStreamState{}
	ch := make(chan StreamUpdate, 16)
	// Surface any image that couldn't be read BEFORE content streams, so
	// the user learns their attachment didn't attach. Buffered channel +
	// pre-pump enqueue guarantees it's the first frame the client sees.
	emitImageNotice(ch, imgs.failures)
	go s.pumpStreamWithTools(streamPumpInput{
		initialStream: stream,
		initialReq:    gwReq,
		callCtx:       gatewayCallContext(in, conv),
		streamCtx:     streamCtx,
		toolsAttached: toolsAttached,
		toolExecCtx: ToolExecContext{
			TenantID:       in.TenantID,
			UserID:         in.UserID,
			ConversationID: conv.ID,
			MessageID:      asstMsg.ID,
			Sources:        sourcesAcc,
		},
		out:   ch,
		canon: canonState,
	})

	// finish closes the message lifecycle. Caller invokes exactly
	// once after consuming the events channel.
	finish := func(content, reasoning, finishReason string, usage *GatewayChatUsage) {
		// Always free the active-stream slot so a future POST /cancel
		// for the same conversation doesn't dangle.
		s.activeStreams.Delete(conv.ID)

		// Use background ctx (not streamCtx, which is cancelled on
		// disconnect) so the persist write completes even if the
		// browser dropped the connection or external cancel fired.
		persistCtx := context.Background()

		// Status precedence:
		//   userCancelled  → "cancelled"  (POST /cancel was hit)
		//   finishReason=cancelled → "cancelled"  (defensive; gateway-side)
		//   finishReason=error → "error"  (handler bubbled an error)
		//   default        → "completed"
		status := "completed"
		switch {
		case reg.isUserCancelled():
			status = "cancelled"
		case finishReason == "cancelled":
			status = "cancelled"
		case finishReason == "error":
			status = "error"
		}
		var tokInput, tokOutput *int
		if usage != nil {
			ti, to := usage.PromptTokens, usage.CompletionTokens
			tokInput, tokOutput = &ti, &to
		}
		// Phase 4A.2.6.2 — persist via the canonical-aware path (mirrors
		// Regenerate/Edit). When canonical history is on this writes
		// content_canonical + display_state; when off it falls back to the
		// legacy UpdateContent internally. Fixes the SendStream gap where
		// streaming turns (incl. the secure-calc <CALC_n> result) persisted
		// the restored display raw with canonical_status=legacy.
		s.persistFinishContent(
			persistCtx, asstMsg.ID, content, status, tokInput, tokOutput,
			canonState, status == "completed",
		)
		// T4: persist reasoning summary if the model produced one.
		// Skipped for empty / cancelled / error turns — the message
		// has no useful reasoning to surface and we'd just clutter
		// the row with the partial fragment.
		if reasoning != "" && status == "completed" {
			_ = s.messages.SetReasoningContent(persistCtx, asstMsg.ID, reasoning)
		}
		assistantFinal := *asstMsg
		assistantFinal.ContentMasked = content
		assistantFinal.Status = status
		assistantFinal.TokenUsageInput = tokInput
		assistantFinal.TokenUsageOutput = tokOutput
		s.finalizeConversation(persistCtx, conv, userMsg, &assistantFinal)
		// S7 — persist the per-turn source attribution after the
		// assistant message has been finalised. Best-effort; does
		// nothing when sourceRepo isn't wired.
		s.persistSources(persistCtx, asstMsg.ID, in.UserID, in.TenantID, conv.ID, sourcesAcc)
	}

	events, runID := s.teeStream(asstMsg.ID, in.RunID, ch)
	return &StreamHandle{
		UserMessage:    userMsg,
		AssistantID:    asstMsg.ID,
		ConversationID: conv.ID,
		RunID:          runID,
		Events:         events,
		finish:         finish,
		cancel:         cancel,
		cancelled:      reg.isUserCancelled,
	}, nil
}

// ---------------------------------------------------------------------------
// Cancel
// ---------------------------------------------------------------------------

// AssertConversationAccess verifies the caller owns the named
// conversation. Used by the Cancel handler before invoking
// “CancelStream“ so an attacker can't terminate streams for
// conversations they don't own by guessing IDs.
func (s *MessageService) AssertConversationAccess(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) error {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrMessageConversationNotFound
		}
		return err
	}
	if conv.UserID != userID {
		return ErrMessageConversationNotFound
	}
	return nil
}

// CancelStream interrupts the in-flight streaming generation for a
// conversation, if any. Returns true iff a stream existed and was
// cancelled. Idempotent: calling on a quiet conversation returns
// false; calling twice is harmless.
//
// The actual partial-content persist happens inside the stream's
// “finish“ callback — by the time CancelStream returns the
// stream goroutine has been notified but may not have flushed yet.
// Callers that need the partial-row guarantee should re-read via
// the message repo after a short delay (the handler does this).
func (s *MessageService) CancelStream(convID uuid.UUID) bool {
	v, ok := s.activeStreams.LoadAndDelete(convID)
	if !ok {
		return false
	}
	reg := v.(*streamRegistration)
	reg.markCancelled()
	reg.cancel()
	return true
}

// ---------------------------------------------------------------------------
// Regenerate
// ---------------------------------------------------------------------------

// RegenerateInput is the typed payload for the regenerate flow.
// “MessageID“ is the assistant message the user wants redone.
type RegenerateInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	MessageID      uuid.UUID
	RequestID      string
}

// Regenerate marks the named assistant message as “regenerated“
// and opens a fresh streaming generation off the same context that
// produced it (i.e. all messages BEFORE the target). Returns a
// StreamHandle that the handler consumes identically to SendStream.
func (s *MessageService) Regenerate(ctx context.Context, in RegenerateInput) (*StreamHandle, error) {
	conv, err := s.conversations.GetByID(ctx, in.TenantID, in.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageConversationNotFound
		}
		return nil, err
	}
	if conv.UserID != in.UserID {
		return nil, ErrMessageConversationNotFound
	}
	if !s.models.ModelAllowed(conv.ModelID) {
		return nil, ErrMessageModelNotAllowed
	}

	target, err := s.messages.GetByID(ctx, in.TenantID, in.MessageID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageNotFound
		}
		return nil, err
	}
	if target.ConversationID != conv.ID {
		return nil, ErrMessageNotFound
	}
	if target.Role != "assistant" {
		return nil, ErrMessageNotAssistant
	}

	// Mark the old assistant message superseded. Status flip lives
	// in its own UPDATE so the rest of the row (token usage,
	// timestamps) survives for audit.
	if err := s.messages.UpdateStatus(ctx, target.ID, "regenerated"); err != nil {
		return nil, err
	}

	// History = every message strictly BEFORE the target. The
	// final entry in the slice is the user prompt that produced the
	// original assistant reply.
	history, err := s.messages.ListContextBeforeMessage(
		ctx, in.TenantID, conv.ID, target.CreatedAt, s.maxContext,
	)
	if err != nil {
		return nil, err
	}

	// Fabricate a SendInput-style userMsg pointer for
	// finalizeConversation's auto-title branch. Auto-title only
	// fires on conversations with the default title, so for
	// already-titled conversations this is inert.
	var userMsg *model.Message
	if len(history) > 0 {
		last := history[len(history)-1]
		userMsg = &last
	}

	// FIX33.1 Part 6.1 — rows beyond the load window BEFORE the target are
	// counted so a regenerate in a >1000-message conversation still shows
	// the compaction marker (A-W1's rule: nothing goes missing silently).
	omitted := s.omittedBefore(ctx, in.TenantID, conv.ID, target.CreatedAt, len(history))
	return s.openAssistantStream(ctx, conv, userMsg, history, omitted, in.RequestID, in.UserID)
}

// openAssistantStream is the shared streaming-open helper used by
// SendStream's primary path and by Regenerate. Pre-creates the
// assistant placeholder row, opens the upstream stream, wires the
// registration + finish callback. Extracted so Regenerate doesn't
// duplicate SendStream's stream-plumbing code.
func (s *MessageService) openAssistantStream(
	ctx context.Context,
	conv *model.Conversation,
	userMsg *model.Message,
	history []model.Message,
	omittedOlder int,
	requestID string,
	userID uuid.UUID,
) (*StreamHandle, error) {
	// Regenerate inherits the predecessor user message's branch
	// metadata so the new assistant turn lives in the same group.
	var parentID *uuid.UUID
	branchIndex := 0
	if userMsg != nil {
		parentID = userMsg.ParentID
		branchIndex = userMsg.BranchIndex
	}
	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       conv.TenantID,
		UserID:         userID,
		Role:           "assistant",
		ContentMasked:  "",
		Status:         "streaming",
		ModelID:        &conv.ModelID,
		ParentID:       parentID,
		BranchIndex:    branchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, err
	}

	streamCtx, cancel := context.WithCancel(context.Background())
	// Regenerate: the query for RAG is the last user message in the
	// history (the prompt the original assistant turn was answering).
	ragQuery := lastUserContent(history)
	imgs := s.resolveUserImages(ctx, conv.TenantID, history)
	sourcesAcc := NewSourcesAccumulator()
	gwReq := s.compileAndBuildRequest(ctx, conv, userID, conv.TenantID, ragQuery, history, imgs, sourcesAcc, omittedOlder, nil)
	toolsAttached := s.attachTools(ctx, &gwReq, conv, nil)
	callCtx := GatewayCallContext{
		TenantID:       conv.TenantID,
		UserID:         userID,
		ConversationID: conv.ID,
		RequestID:      requestID,
	}
	stream, err := s.gateway.SendChatCompletionStream(streamCtx, callCtx, gwReq)
	if err != nil {
		cancel()
		_ = s.messages.UpdateContent(ctx, asstMsg.ID, "", "error", nil, nil)
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
		}
		return nil, err
	}

	reg := &streamRegistration{cancel: cancel}
	s.activeStreams.Store(conv.ID, reg)

	canonState := &CanonicalStreamState{}
	ch := make(chan StreamUpdate, 16)
	emitImageNotice(ch, imgs.failures)
	go s.pumpStreamWithTools(streamPumpInput{
		initialStream: stream,
		initialReq:    gwReq,
		callCtx:       callCtx,
		streamCtx:     streamCtx,
		toolsAttached: toolsAttached,
		toolExecCtx: ToolExecContext{
			TenantID:       conv.TenantID,
			UserID:         userID,
			ConversationID: conv.ID,
			MessageID:      asstMsg.ID,
			Sources:        sourcesAcc,
		},
		out:   ch,
		canon: canonState,
	})

	finish := func(content, reasoning, finishReason string, usage *GatewayChatUsage) {
		s.activeStreams.Delete(conv.ID)
		persistCtx := context.Background()
		status := "completed"
		switch {
		case reg.isUserCancelled():
			status = "cancelled"
		case finishReason == "cancelled":
			status = "cancelled"
		case finishReason == "error":
			status = "error"
		}
		var tokInput, tokOutput *int
		if usage != nil {
			ti, to := usage.PromptTokens, usage.CompletionTokens
			tokInput, tokOutput = &ti, &to
		}
		s.persistFinishContent(persistCtx, asstMsg.ID, content, status, tokInput, tokOutput, canonState, status == "completed")
		// T4: persist reasoning summary (regenerate path).
		if reasoning != "" && status == "completed" {
			_ = s.messages.SetReasoningContent(persistCtx, asstMsg.ID, reasoning)
		}
		assistantFinal := *asstMsg
		assistantFinal.ContentMasked = content
		assistantFinal.Status = status
		assistantFinal.TokenUsageInput = tokInput
		assistantFinal.TokenUsageOutput = tokOutput
		if userMsg != nil {
			s.finalizeConversation(persistCtx, conv, userMsg, &assistantFinal)
		} else {
			_ = s.conversations.UpdateTimestamp(persistCtx, conv.ID)
		}
		// S7 — persist per-turn source attribution. Same pattern
		// as SendStream's finish callback.
		s.persistSources(persistCtx, asstMsg.ID, userID, conv.TenantID, conv.ID, sourcesAcc)
	}

	events, runID := s.teeStream(asstMsg.ID, "", ch)
	return &StreamHandle{
		UserMessage:    userMsg,
		AssistantID:    asstMsg.ID,
		ConversationID: conv.ID,
		RunID:          runID,
		Events:         events,
		finish:         finish,
		cancel:         cancel,
		cancelled:      reg.isUserCancelled,
	}, nil
}

// ---------------------------------------------------------------------------
// Edit + switch-branch
// ---------------------------------------------------------------------------

var (
	ErrEditWrongRole       = errors.New("edit: only user messages can be edited")
	ErrSwitchBranchNoFork  = errors.New("switch-branch: message is not part of a fork group")
	ErrSwitchBranchMissing = errors.New("switch-branch: target branch_index does not exist")
)

// EditInput is the typed payload for an edit.
type EditInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	MessageID      uuid.UUID
	NewContent     string
	RequestID      string
}

// EditMessage replaces a user message with a new edit, recording
// the old branch via parent_id + branch_index. The new user
// message + its streamed assistant response together form a new
// branch; the old branch's messages stay in the DB with
// is_active=false so the user can switch back.
//
// Returns a StreamHandle the HTTP handler consumes identically to
// SendStream (same SSE event format).
func (s *MessageService) EditMessage(ctx context.Context, in EditInput) (*StreamHandle, error) {
	if strings.TrimSpace(in.NewContent) == "" {
		return nil, ErrMessageEmptyContent
	}

	conv, err := s.conversations.GetByID(ctx, in.TenantID, in.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageConversationNotFound
		}
		return nil, err
	}
	if conv.UserID != in.UserID {
		return nil, ErrMessageConversationNotFound
	}
	if !s.models.ModelAllowed(conv.ModelID) {
		return nil, ErrMessageModelNotAllowed
	}

	target, err := s.messages.GetByID(ctx, in.TenantID, in.MessageID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageNotFound
		}
		return nil, err
	}
	if target.ConversationID != conv.ID {
		return nil, ErrMessageNotFound
	}
	if target.Role != "user" {
		return nil, ErrEditWrongRole
	}

	// fork_root: the marker id every sibling + descendant shares.
	// If this is the first edit at this point, the target becomes
	// the fork root (self-pointed). Otherwise, the target already
	// has parent_id set and we reuse that.
	forkRoot := target.ID
	if target.ParentID != nil {
		forkRoot = *target.ParentID
	} else {
		// First-time edit: stamp parent_id on the original AND on
		// every message that came after it so they all belong to
		// "branch 0" of the new fork group.
		if err := s.messages.SetParent(ctx, target.ID, forkRoot); err != nil {
			return nil, err
		}
		if err := s.messages.StampForkLineage(
			ctx, in.TenantID, conv.ID, forkRoot, target.CreatedAt,
		); err != nil {
			return nil, err
		}
	}

	// Compute the next branch index inside this group.
	maxIdx, err := s.messages.MaxBranchIndex(ctx, in.TenantID, forkRoot)
	if err != nil {
		return nil, err
	}
	newBranchIndex := maxIdx + 1

	// Kill the tail of the currently-active branch starting at the
	// target message. Both target and every downstream active
	// message go is_active=false; the new branch becomes the live
	// one once the next inserts land.
	if err := s.messages.DeactivateChain(
		ctx, in.TenantID, conv.ID, target.CreatedAt,
	); err != nil {
		return nil, err
	}

	// Insert the new user message into the new branch.
	newUser, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "user",
		ContentMasked:  in.NewContent,
		Status:         "completed",
		ModelID:        &conv.ModelID,
		ParentID:       &forkRoot,
		BranchIndex:    newBranchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, err
	}

	// Rebuild the context for the gateway from the *active* branch
	// up through the new user message.
	history, err := s.messages.ListContextByConversation(
		ctx, in.TenantID, conv.ID, s.maxContext,
	)
	if err != nil {
		return nil, err
	}

	return s.openAssistantStreamWithBranch(
		ctx, conv, newUser, history, in.RequestID, in.UserID,
		&forkRoot, newBranchIndex,
	)
}

// openAssistantStreamWithBranch is openAssistantStream's branched
// twin — same plumbing, but the assistant placeholder gets the
// new branch's parent_id + branch_index so the active flag stays
// consistent across the whole branch.
func (s *MessageService) openAssistantStreamWithBranch(
	ctx context.Context,
	conv *model.Conversation,
	userMsg *model.Message,
	history []model.Message,
	requestID string,
	userID uuid.UUID,
	parentID *uuid.UUID,
	branchIndex int,
) (*StreamHandle, error) {
	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       conv.TenantID,
		UserID:         userID,
		Role:           "assistant",
		ContentMasked:  "",
		Status:         "streaming",
		ModelID:        &conv.ModelID,
		ParentID:       parentID,
		BranchIndex:    branchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, err
	}

	streamCtx, cancel := context.WithCancel(context.Background())
	// Edit-branch RAG query: prefer the new user message's content
	// (which is the prompt that produced this branch); fall back to
	// the tail of the history if the caller didn't pass one.
	ragQuery := ""
	if userMsg != nil {
		ragQuery = userMsg.ContentMasked
	}
	if ragQuery == "" {
		ragQuery = lastUserContent(history)
	}
	imgs := s.resolveUserImages(ctx, conv.TenantID, history)
	sourcesAcc := NewSourcesAccumulator()
	gwReq := s.compileAndBuildRequest(ctx, conv, userID, conv.TenantID, ragQuery, history, imgs, sourcesAcc, s.omittedOlder(ctx, conv.TenantID, conv.ID, len(history)), nil)
	toolsAttached := s.attachTools(ctx, &gwReq, conv, nil)
	callCtx := GatewayCallContext{
		TenantID:       conv.TenantID,
		UserID:         userID,
		ConversationID: conv.ID,
		RequestID:      requestID,
	}
	stream, err := s.gateway.SendChatCompletionStream(streamCtx, callCtx, gwReq)
	if err != nil {
		cancel()
		_ = s.messages.UpdateContent(ctx, asstMsg.ID, "", "error", nil, nil)
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
		}
		return nil, err
	}

	reg := &streamRegistration{cancel: cancel}
	s.activeStreams.Store(conv.ID, reg)

	canonState := &CanonicalStreamState{}
	ch := make(chan StreamUpdate, 16)
	emitImageNotice(ch, imgs.failures)
	go s.pumpStreamWithTools(streamPumpInput{
		initialStream: stream,
		initialReq:    gwReq,
		callCtx:       callCtx,
		streamCtx:     streamCtx,
		toolsAttached: toolsAttached,
		toolExecCtx: ToolExecContext{
			TenantID:       conv.TenantID,
			UserID:         userID,
			ConversationID: conv.ID,
			MessageID:      asstMsg.ID,
			Sources:        sourcesAcc,
		},
		out:   ch,
		canon: canonState,
	})

	finish := func(content, reasoning, finishReason string, usage *GatewayChatUsage) {
		s.activeStreams.Delete(conv.ID)
		persistCtx := context.Background()
		status := "completed"
		switch {
		case reg.isUserCancelled():
			status = "cancelled"
		case finishReason == "cancelled":
			status = "cancelled"
		case finishReason == "error":
			status = "error"
		}
		var tokInput, tokOutput *int
		if usage != nil {
			ti, to := usage.PromptTokens, usage.CompletionTokens
			tokInput, tokOutput = &ti, &to
		}
		s.persistFinishContent(persistCtx, asstMsg.ID, content, status, tokInput, tokOutput, canonState, status == "completed")
		// T4: persist reasoning summary (edit-message path).
		if reasoning != "" && status == "completed" {
			_ = s.messages.SetReasoningContent(persistCtx, asstMsg.ID, reasoning)
		}
		// Bump message_count + updated_at so the sidebar refreshes.
		_ = s.conversations.IncrementMessageCount(persistCtx, conv.ID, 2)
		// S7 — persist per-turn source attribution.
		s.persistSources(persistCtx, asstMsg.ID, userID, conv.TenantID, conv.ID, sourcesAcc)
	}

	events, runID := s.teeStream(asstMsg.ID, "", ch)
	return &StreamHandle{
		UserMessage:    userMsg,
		AssistantID:    asstMsg.ID,
		ConversationID: conv.ID,
		RunID:          runID,
		Events:         events,
		finish:         finish,
		cancel:         cancel,
		cancelled:      reg.isUserCancelled,
	}, nil
}

// BranchCountsByParents is a thin pass-through used by the list
// handler to populate branch_count per message.
func (s *MessageService) BranchCountsByParents(
	ctx context.Context, tenantID uuid.UUID, parentIDs []uuid.UUID,
) (map[uuid.UUID]int, error) {
	return s.messages.BranchCountByParent(ctx, tenantID, parentIDs)
}

// SwitchBranch flips the active branch within a fork group. The
// caller addresses the fork by ANY message id inside it; the
// service resolves the fork-root by reading its parent_id.
func (s *MessageService) SwitchBranch(
	ctx context.Context,
	tenantID, userID, convID, messageID uuid.UUID,
	branchIndex int,
) error {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrMessageConversationNotFound
		}
		return err
	}
	if conv.UserID != userID {
		return ErrMessageConversationNotFound
	}

	m, err := s.messages.GetByID(ctx, tenantID, messageID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrMessageNotFound
		}
		return err
	}
	if m.ConversationID != conv.ID {
		return ErrMessageNotFound
	}
	if m.ParentID == nil {
		return ErrSwitchBranchNoFork
	}

	// Validate the target branch actually exists for this group.
	maxIdx, err := s.messages.MaxBranchIndex(ctx, tenantID, *m.ParentID)
	if err != nil {
		return err
	}
	if branchIndex < 0 || branchIndex > maxIdx {
		return ErrSwitchBranchMissing
	}

	if err := s.messages.SetActiveBranch(ctx, tenantID, *m.ParentID, branchIndex); err != nil {
		return err
	}
	_ = s.conversations.UpdateTimestamp(ctx, conv.ID)
	return nil
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

// fileContext is a tiny adapter around FileLinker.BuildContextBlock
// that swallows errors. Failing to assemble the context block must
// not block the message — the user still gets an AI reply, just
// without document context.
func (s *MessageService) fileContext(ctx context.Context, tenantID, convID uuid.UUID) string {
	if s.fileSvc == nil {
		return ""
	}
	block, err := s.fileSvc.BuildContextBlock(ctx, tenantID, convID)
	if err != nil {
		return ""
	}
	return block
}

// ragContext queries the conversation's bound KB (if any) for the
// most-recent user content. Returns "" when no KB is bound, RAG
// isn't configured, or retrieval came back empty. Errors are
// swallowed for the same reason file_context does — never block the
// reply because retrieval stuttered.
// fallbackKB (Phase 4) is the project-inherited KB, consulted only when
// the conversation has no explicit binding of its own — an explicit
// per-conversation KB always wins over the inherited one.
func (s *MessageService) ragContext(ctx context.Context, userID uuid.UUID, conv *model.Conversation, query string, fallbackKB *uuid.UUID) string {
	if s.ragSvc == nil || conv == nil {
		return ""
	}
	kb := conv.KnowledgeBaseID
	if kb == nil {
		kb = fallbackKB
	}
	if kb == nil {
		return ""
	}
	q := strings.TrimSpace(query)
	if q == "" {
		return ""
	}
	// FIX 2 — forward the end-user so the query-embedding egress (through
	// the gateway) is attributed/audited to them.
	return s.ragSvc.BuildContext(ctx, conv.TenantID, userID, *kb, q)
}

// buildSystemPrefix concatenates the user's custom instructions, the
// RAG-retrieved KB context, and the file-context block into a single
// system-prompt prefix. The order is deliberate:
//
//	persona  — shapes tone (must come first so RAG / file content
//	           doesn't accidentally override the "respond as X" rule)
//	RAG      — authoritative facts the model should cite
//	files    — supplementary attached documents
//	excel    — Phase 4 workbook structure + action catalog so the
//	           model knows how to emit :::excel-plan blocks
//
// Any subset may be empty. “ragQuery“ is the most-recent user
// message content used as the retrieval query; pass "" to skip RAG.
func (s *MessageService) buildSystemPrefix(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	conv *model.Conversation,
	ragQuery string,
	resolvedModel string,
) string {
	text, _ := s.buildSystemPrefixWithSources(ctx, tenantID, userID, conv, ragQuery, resolvedModel)
	return text
}

// buildSystemPrefixWithSources assembles the system prompt through the
// 13-layer PromptCompiler and returns the ContextSource list the memory
// + state layers contributed (S7 attribution: "how did the AI know X?").
//
// The compiler owns the persona / identity / behaviour / tool-policy /
// per-model layers (1–12). The file / RAG / Excel / Doc / Pptx context
// (layer 13) is pre-rendered HERE because it needs the file + KB repos
// the compiler deliberately doesn't hold, then handed in as strings.
//
// resolvedModel is the concrete model id chosen for this turn (after
// auto-resolution); it drives the per-model adapter layer.
func (s *MessageService) buildSystemPrefixWithSources(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	conv *model.Conversation,
	ragQuery string,
	resolvedModel string,
) (string, []ContextSource) {
	in := CompileContext{
		Model:       resolvedModel,
		Conv:        conv,
		TenantID:    tenantID,
		UserID:      userID,
		CurrentTime: time.Now(),
		ActiveTools: s.activeToolNames(conv),
	}
	// Phase 4 — project context. Loaded once per compile when the
	// conversation belongs to a project; feeds BOTH the Layer-10
	// instructions block and the RAG KB fallback below. A load failure
	// degrades to no-project behavior — never blocks the reply. When
	// conv.ProjectID is nil this branch is skipped entirely, so the
	// non-project prompt stays byte-identical to pre-Phase-4.
	var projectKB *uuid.UUID
	if conv != nil && conv.ProjectID != nil && s.projectReader != nil {
		if p, err := s.projectReader.GetForConversation(ctx, tenantID, conv.ID); err == nil && p != nil {
			in.ProjectInstructions = p.Instructions
			projectKB = p.KnowledgeBaseID
		}
	}
	// FIX12 Item 3 — user skills. Load enabled skills, match triggers
	// against this turn's user message (ragQuery IS the latest user
	// content), and preload the compiler's ActiveSkills + SkillCatalog.
	// nil reader / no skills ⇒ both empty ⇒ prompt byte-identical.
	in.ActiveSkills, in.SkillCatalog = s.resolveSkillsForTurn(ctx, tenantID, userID, ragQuery)
	// Layer 13 inputs — built with the file/KB repos and gated exactly
	// as before (each returns "" when its precondition isn't met).
	if conv != nil {
		in.RAGContext = s.ragContext(ctx, userID, conv, ragQuery, projectKB)
		in.FileContext = s.fileContext(ctx, tenantID, conv.ID)
		in.ExcelContext = s.excelContext(ctx, tenantID, conv.ID)
		in.DocContext = s.docContext(ctx, tenantID, conv.ID)
		in.PptxContext = s.pptxContext(ctx, tenantID, conv.ID)
		in.CanvasContext = s.canvasContext(ctx, tenantID, conv.ID)
	}
	pc := newPromptCompiler(s.prefsSvc, s.memorySvc, s.stateSvc)
	return pc.Build(ctx, in)
}

// activeToolNames returns the names of the tools active for this
// conversation — the same set ToolDefs attaches to the gateway request
// — so the Tool Policy layer advertises only tools the model can
// actually call (e.g. no save_memory when memory is disabled).
func (s *MessageService) activeToolNames(conv *model.Conversation) []string {
	if s.toolExec == nil {
		return nil
	}
	defs := s.toolExec.ToolDefs(conv)
	names := make([]string, 0, len(defs))
	for i := range defs {
		names = append(names, defs[i].Function.Name)
	}
	return names
}

// excelContext renders the Phase-4 Excel system block when the
// conversation has at least one xlsx file with a cached inspection.
// The block teaches the model the workbook structure + the
// :::excel-plan output contract; the frontend intercepts those
// fenced blocks and renders the plan card.
//
// To keep the system prompt bounded we only embed the MOST RECENT
// xlsx's inspection_json. When users iterate on a workbook the
// generated versions land as new files (is_generated=true) and the
// newest version is what the model needs to plan further edits
// against.
func (s *MessageService) excelContext(
	ctx context.Context, tenantID, convID uuid.UUID,
) string {
	if s.excelLookup == nil {
		return ""
	}
	files, err := s.excelLookup.ListByConversation(ctx, tenantID, convID)
	if err != nil || len(files) == 0 {
		// Phase 8 Task 1: no attachments no longer suppresses the
		// contract. ExcelService.ExecutePlan now runs create-from-scratch
		// when source_file_id is omitted, so "สร้างตาราง Excel สรุปยอดขาย"
		// in a brand-new chat yields a plan card that actually executes.
		return excelCapabilitiesPromptHeaderless
	}
	// Newest-first — pick the first xlsx with a cached inspection.
	var latest *model.ConversationFile
	for i := len(files) - 1; i >= 0; i-- {
		f := &files[i]
		if !IsExcelContentType(f.ContentType) {
			continue
		}
		if f.InspectionJSON == nil || *f.InspectionJSON == "" {
			continue
		}
		latest = f
		break
	}
	if latest == nil {
		// Files exist but none is an inspected workbook — same as the
		// zero-file case from the model's point of view.
		return excelCapabilitiesPromptHeaderless
	}
	return renderExcelSystemBlock(latest)
}

// renderExcelSystemBlock builds the literal text injected at the
// end of the system prompt. Kept as a free function so unit tests
// can assert on the snapshot without booting a full MessageService.
func renderExcelSystemBlock(f *model.ConversationFile) string {
	var b strings.Builder
	b.WriteString("## Excel File Context\n\n")
	b.WriteString("You have access to an Excel workbook. Here is its complete structure:\n\n")
	b.WriteString("file_id: ")
	b.WriteString(f.ID.String())
	b.WriteString("\nfilename: ")
	b.WriteString(f.OriginalFilename)
	b.WriteString("\n\n```json\n")
	b.WriteString(*f.InspectionJSON)
	b.WriteString("\n```\n\n")
	b.WriteString(excelCapabilitiesPrompt)
	return b.String()
}

// excelCapabilitiesPrompt is the static portion of the Excel system
// block — the contract the model must follow when the user asks to
// edit or generate a workbook. Kept as a package-level constant so
// adjusting the prompt doesn't require a rebuild of the message
// service tests' fixtures.
const excelCapabilitiesPrompt = `## Your Capabilities

You can perform three types of operations:

### 1. ANALYZE — Answer questions about the data
Just respond in chat with your analysis. Reference specific sheets, cells, and formulas.

### 2. EDIT — Modify the existing workbook
When the user asks you to edit, add, modify, generate, summarize, format, sort, filter, or otherwise change the Excel file, you MUST output an edit plan in this EXACT format (the triple-colon fences are required). Output the plan immediately — do NOT ask for confirmation, do NOT say "let me know if this is okay", do NOT say "let's proceed". A plan block is your action; the user runs it by clicking Execute on the rendered card.

:::excel-plan
{
  "operation": "modify_workbook",
  "source_file_id": "<file_id from above>",
  "output_filename": "<original>_edited.xlsx",
  "description": "Brief description of what will change",
  "actions": [
    {"type": "create_sheet", "sheet_name": "Summary", "position": 0},
    {"type": "write_cells", "sheet_name": "Summary", "cells": [
      {"cell": "A1", "value": "Total Revenue", "bold": true, "font_size": 14},
      {"cell": "B1", "value_formula": "=SUM(Sales_2025!D2:D13)", "number_format": "#,##0"}
    ]},
    {"type": "format_range", "sheet_name": "Summary", "range": "A1:B1",
     "fill_color": "1F4E79", "font_color": "FFFFFF", "bold": true}
  ],
  "preserve": {
    "existing_formulas": true,
    "existing_styles": true,
    "existing_charts": true,
    "hidden_sheets": true
  }
}
:::

CRITICAL: every action MUST be a flat object with a "type" field — NEVER nest the body under the type name. Use {"type": "create_sheet", "sheet_name": "X"}, NOT {"create_sheet": {"sheet_name": "X"}}.

### 3. GENERATE — Create a new Excel file from scratch
Same format as EDIT but with "operation": "create_workbook".

## Rules — MUST follow
- For ANY edit / generate / modify / format request: output exactly one :::excel-plan ... ::: block. No exceptions, no hedging, no asking permission first.
- BEFORE the plan, write 1-2 short sentences in the user's language describing what you'll change (this is the explanation rendered above the plan card).
- AFTER the closing ::: fence, write nothing further. The plan IS the deliverable.
- NEVER output raw CSV or text tables when the user wants an Excel file.
- ALWAYS preserve existing formulas, styles, and charts unless explicitly asked to remove them.
- Use ` + "`value_formula`" + ` instead of hardcoded values when the data should be dynamic.
- Use source_file_id = the file_id printed above the workbook structure.

## Action Types Available
- create_sheet { sheet_name, position }
- delete_sheet { sheet_name }
- rename_sheet { sheet_name, new_name }
- write_cells { sheet_name, cells: [ { cell, value | value_formula, bold, font_size, number_format } ] }
- write_table { sheet_name, start_cell, headers: [...], data: [[...]] }
- create_chart { sheet_name, chart_type (bar|line|pie|column|area|scatter), title, anchor_cell, data_range, category_range, width, height }
- format_range { sheet_name, range, fill_color, font_color, bold, font_size }
- set_column_width { sheet_name, columns: { "A": 20, "B": 16 } }
- merge_cells { sheet_name, range }
- auto_filter { sheet_name, range }
- freeze_panes { sheet_name, cell }
`

// excelCapabilitiesPromptHeaderless is what we ship when the
// conversation has no inspected workbook: there is no structure block
// and no file_id, so the model is told explicitly to OMIT
// source_file_id and use "create_workbook". Mirrors the doc / pptx
// headerless prompts (Phase 1) — Phase 8 Task 1 unblocked it by adding
// create-mode to ExcelService.ExecutePlan.
const excelCapabilitiesPromptHeaderless = "## Excel File Context\n\n" +
	"No workbook is currently uploaded, but if the user asks you to create a spreadsheet " +
	"you should still emit an :::excel-plan block: use \"operation\": \"create_workbook\", " +
	"OMIT source_file_id entirely, and build every sheet with create_sheet / write_table / " +
	"write_cells actions. The executor runs in create mode and returns a real .xlsx file.\n\n" +
	excelCapabilitiesPrompt

// docContext renders the Phase-4 Session 2 doc system block when the
// conversation has at least one docx or pdf file with a cached
// inspection. Same shape as excelContext: the model gets the
// inspection JSON + a contract to emit :::doc-plan blocks that the
// frontend turns into the DocPlanCard.
func (s *MessageService) docContext(
	ctx context.Context, tenantID, convID uuid.UUID,
) string {
	if s.excelLookup == nil {
		return ""
	}
	files, err := s.excelLookup.ListByConversation(ctx, tenantID, convID)
	if err != nil || len(files) == 0 {
		// Zero attachments (or a failed lookup) must NOT suppress the
		// contract: "create a PDF report about X" in a brand-new
		// conversation should still yield a :::doc-plan card. The
		// generator runs in create mode when source_file_id is omitted,
		// so the headerless prompt is safe without any uploaded file.
		return docCapabilitiesPromptHeaderless
	}
	// docx + pdf only — pptx is owned by pptxContext below so the
	// two capabilities prompts stay orthogonal (the AI gets a
	// separate :::doc-plan and :::pptx-plan contract).
	var latest *model.ConversationFile
	for i := len(files) - 1; i >= 0; i-- {
		f := &files[i]
		if !(IsDocxContentType(f.ContentType) || IsPdfContentType(f.ContentType)) {
			continue
		}
		if f.InspectionJSON == nil || *f.InspectionJSON == "" {
			continue
		}
		latest = f
		break
	}
	if latest == nil {
		// No uploaded doc — still inject the capabilities prompt so
		// the model can generate from-scratch reports when the user
		// asks ("create a PDF report about X"). Without the catalog
		// it would just write prose.
		return docCapabilitiesPromptHeaderless
	}
	return renderDocSystemBlock(latest)
}

func renderDocSystemBlock(f *model.ConversationFile) string {
	var b strings.Builder
	b.WriteString("## Document Context\n\n")
	b.WriteString("You have access to a document file. Here is its structure:\n\n")
	b.WriteString("file_id: ")
	b.WriteString(f.ID.String())
	b.WriteString("\nfilename: ")
	b.WriteString(f.OriginalFilename)
	b.WriteString("\ncontent_type: ")
	b.WriteString(f.ContentType)
	b.WriteString("\n\n```json\n")
	b.WriteString(*f.InspectionJSON)
	b.WriteString("\n```\n\n")
	b.WriteString(docCapabilitiesPrompt)
	return b.String()
}

// docCapabilitiesPrompt is the static contract injected when an
// uploaded doc is present. The headerless variant goes out when no
// doc is uploaded but the user might still ask for one.
const docCapabilitiesPrompt = `## Document Report Capabilities

When the user asks you to create, edit, or summarize a Word document (.docx) or PDF report, output a JSON plan in this EXACT format. The frontend renders the plan as a card; the user clicks Generate to execute.

:::doc-plan
{
  "format": "docx",
  "source_file_id": "<file_id from above; omit for create-from-scratch>",
  "output_filename": "report.docx",
  "document_settings": {
    "page_size": "A4",
    "orientation": "portrait",
    "margins": {"top": 1.0, "bottom": 1.0, "left": 1.0, "right": 1.0},
    "font_family": "Sarabun",
    "base_font_size": 16,
    "language": "th",
    "header_text": "SRAN AI Report",
    "footer_text": "หน้า {page} จาก {total}",
    "title_page": {
      "title": "รายงานสรุปผลประจำเดือน",
      "subtitle": "พฤษภาคม 2569",
      "author": "SRAN AI",
      "date": "27 พ.ค. 2569"
    }
  },
  "actions": [
    {"type": "add_title_page"},
    {"type": "add_heading", "level": 1, "text": "บทสรุปผู้บริหาร"},
    {"type": "add_paragraph", "text": "เนื้อหา..."},
    {"type": "add_bullet_list", "items": ["ข้อหนึ่ง", "ข้อสอง"]},
    {"type": "add_table", "headers": ["หัวข้อ", "ค่า"], "rows": [["A", "1"]], "style": "professional"}
  ]
}
:::

## Rules — MUST follow
- For ANY create/edit/generate request: output exactly one :::doc-plan ... ::: block. No hedging.
- Output the explanation in 1-2 short sentences BEFORE the fence; nothing after it.
- Detect the user's language from the prompt and the inspection metadata:
  * Thai input → language: "th", font_family: "Sarabun", base_font_size: 16.
  * English input → language: "en", font_family: "Arial", base_font_size: 12.
- "format" is "docx" or "pdf" depending on the user's request. If unclear, ask.
- When source_file_id is set and format is "docx", the generator preserves the source layout; for pdf the source is treated as reference content.
- Use professional table style (corporate navy headers) for any reporting table.

## Action Types
CREATE: add_title_page, add_heading {level, text}, add_paragraph {text, bold, italic, alignment}, add_bullet_list {items}, add_numbered_list {items}, add_table {headers, rows, style}, add_page_break, add_spacer {height}, add_horizontal_rule, add_blockquote {text}, add_code_block {code, language}
EDIT (docx only): replace_text {find, replace}, set_header {text}, set_footer {text}
`

// docCapabilitiesPromptHeaderless is what we ship when no
// document is uploaded. Skips the "Here is its structure" preamble
// since there's nothing to describe, but still gives the model the
// :::doc-plan contract so create-from-scratch works.
const docCapabilitiesPromptHeaderless = "## Document Report Capabilities\n\nNo document is currently uploaded, but if the user asks you to create a Word or PDF report you should still emit a :::doc-plan block (omit source_file_id; the generator runs in create mode).\n\n" + docCapabilitiesPrompt

// pptxContext is the Phase-4 Session-3 sibling of docContext.
// Picks the most-recent pptx with cached inspection, or injects the
// headerless capabilities prompt when no pptx is uploaded but the
// user might still ask for one.
func (s *MessageService) pptxContext(
	ctx context.Context, tenantID, convID uuid.UUID,
) string {
	if s.excelLookup == nil {
		return ""
	}
	files, err := s.excelLookup.ListByConversation(ctx, tenantID, convID)
	if err != nil || len(files) == 0 {
		// Same rationale as docContext: a brand-new conversation with
		// no files must still get the :::pptx-plan contract so
		// create-from-scratch deck requests produce a plan card.
		return pptxCapabilitiesPromptHeaderless
	}
	var latest *model.ConversationFile
	for i := len(files) - 1; i >= 0; i-- {
		f := &files[i]
		if !IsPptxContentType(f.ContentType) {
			continue
		}
		if f.InspectionJSON == nil || *f.InspectionJSON == "" {
			continue
		}
		latest = f
		break
	}
	if latest == nil {
		return pptxCapabilitiesPromptHeaderless
	}
	return renderPptxSystemBlock(latest)
}

func renderPptxSystemBlock(f *model.ConversationFile) string {
	var b strings.Builder
	b.WriteString("## Presentation Context\n\n")
	b.WriteString("You have access to a PowerPoint file. Here is its structure:\n\n")
	b.WriteString("file_id: ")
	b.WriteString(f.ID.String())
	b.WriteString("\nfilename: ")
	b.WriteString(f.OriginalFilename)
	b.WriteString("\ncontent_type: ")
	b.WriteString(f.ContentType)
	b.WriteString("\n\n```json\n")
	b.WriteString(*f.InspectionJSON)
	b.WriteString("\n```\n\n")
	b.WriteString(pptxCapabilitiesPrompt)
	return b.String()
}

// pptxCapabilitiesPrompt teaches the AI to emit :::pptx-plan blocks
// against the 9-slide-type generator catalog. Same MUST/structure
// rules as the doc prompt so the LLM's behaviour stays consistent
// across the three formats.
const pptxCapabilitiesPrompt = `## Presentation Generation Capabilities

When the user asks you to create, edit, or summarize a PowerPoint presentation / slide deck, output a JSON plan in this EXACT format. The frontend renders the plan as a card; the user clicks Generate to execute.

:::pptx-plan
{
  "format": "pptx",
  "source_file_id": "<file_id from above; omit for create-from-scratch>",
  "output_filename": "presentation.pptx",
  "presentation_settings": {
    "title": "Monthly Report",
    "author": "SRAN AI",
    "layout": "16:9",
    "language": "th",
    "color_scheme": {
      "primary": "0A1230", "secondary": "0F1835", "accent": "2E75B6",
      "light_bg": "F6F7F9", "text_dark": "1A1A2E", "text_light": "FFFFFF"
    },
    "font": {"heading": "Sarabun", "body": "Sarabun"}
  },
  "slides": [
    {"type": "title", "title": "รายงานสรุปผลประจำเดือน", "subtitle": "พฤษภาคม 2569", "author": "SRAN AI"},
    {"type": "section_divider", "title": "บทสรุปผู้บริหาร"},
    {"type": "content", "title": "ภาพรวม", "body": "...", "bullets": ["...","..."], "speaker_notes": "..."},
    {"type": "two_column", "title": "เปรียบเทียบ", "left": {"heading": "Q1", "bullets": ["..."]}, "right": {"heading": "Q2", "bullets": ["..."]}},
    {"type": "stats", "title": "ตัวเลขสำคัญ", "stats": [{"value": "5,200", "label": "รายการ", "color": "2E75B6"}, {"value": "98.5%", "label": "พึงพอใจ", "color": "10B981"}]},
    {"type": "table", "title": "ตารางสรุป", "headers": ["เดือน","ยอดขาย"], "rows": [["ม.ค.","1,200"]]},
    {"type": "chart", "title": "แนวโน้ม", "chart_type": "bar", "data": {"categories": ["ม.ค.","ก.พ."], "series": [{"name": "ยอดขาย", "values": [1200, 1350]}]}},
    {"type": "closing", "title": "ขอบคุณครับ", "subtitle": "team@sran.ai"}
  ]
}
:::

## Rules — MUST follow
- For ANY presentation request: output exactly one :::pptx-plan ... ::: block. No hedging.
- Output the explanation in 1-2 short sentences BEFORE the fence; nothing after it.
- Mix slide types — never just a stack of content/bullet slides. Use stats for KPIs, chart for trends, table for data, two_column for comparisons, section_divider between major topics.
- Always start with a "title" slide and end with a "closing" slide.
- Detect language from the prompt:
  * Thai input → language: "th", font Sarabun.
  * English input → language: "en", font Calibri.
- Keep bullets short (under 10 words each); 6 bullets max per slide.
- 8-12 slides is the sweet spot for most topics.
- "chart_type" is one of: bar, column, line, pie, doughnut, area.

## Slide Types
- title { title, subtitle, author }          — dark cover slide
- section_divider { title }                  — dark section header
- content { title, body, bullets, speaker_notes } — standard content slide
- two_column { title, left:{heading,bullets}, right:{heading,bullets} }
- stats { title, stats:[{value,label,color}] } — 2-4 KPI cards
- table { title, headers, rows }             — professional data table
- chart { title, chart_type, data:{categories, series:[{name,values}]} }
- content_with_image { title, body, bullets, image_placeholder } — content + image note
- closing { title, subtitle }                — dark thank-you slide
`

const pptxCapabilitiesPromptHeaderless = "## Presentation Generation Capabilities\n\nNo presentation is currently uploaded, but if the user asks you to create a slide deck you should still emit a :::pptx-plan block (omit source_file_id; the generator runs in create mode).\n\n" + pptxCapabilitiesPrompt

// ---------------------------------------------------------------------------
// Canvas artifacts (Phase 8 Task 3)
// ---------------------------------------------------------------------------

const (
	// maxCanvasPromptArtifacts bounds how many artifacts are listed. A
	// long conversation can accumulate many; the model only ever needs
	// the recent ones to answer "change the button colour".
	maxCanvasPromptArtifacts = 5
	// maxCanvasPromptSource bounds the SOURCE embedded for the newest
	// artifact. Beyond this the model is told the source is too large to
	// inline — better than silently truncating it into a half file the
	// model would then "complete" with invented markup.
	maxCanvasPromptSource = 12000
)

// canvasContext renders the "## Canvas Artifacts" block: the artifacts
// this conversation has, and — for the most recent one — its current
// source, so the model can revise it with update_canvas_artifact rather
// than emitting a brand-new code block the user has to re-open.
//
// Only artifacts that EXIST server-side appear. A code block the user
// never opened in the canvas has no lineage yet and is invisible here;
// opening it adopts version 1 (client side), which is what makes it
// updatable. That keeps the model from trying to "update" blocks that
// were never adopted.
func (s *MessageService) canvasContext(
	ctx context.Context, tenantID, convID uuid.UUID,
) string {
	if s.canvasLookup == nil {
		return ""
	}
	artifacts, err := s.canvasLookup.ListLatest(ctx, tenantID, convID)
	if err != nil || len(artifacts) == 0 {
		// No artifacts ⇒ no block at all. Advertising the tool with an
		// empty list would only invite invented keys.
		return ""
	}
	return renderCanvasSystemBlock(artifacts)
}

// renderCanvasSystemBlock builds the literal injected text. Free
// function so the snapshot is unit-testable without a MessageService.
// Input is newest-first (ListLatest orders by created_at DESC).
func renderCanvasSystemBlock(artifacts []model.CanvasArtifact) string {
	if len(artifacts) > maxCanvasPromptArtifacts {
		artifacts = artifacts[:maxCanvasPromptArtifacts]
	}
	var b strings.Builder
	b.WriteString("## Canvas Artifacts\n\n")
	b.WriteString("These artifacts are open to this conversation's canvas panel. " +
		"When the user asks to CHANGE one of them, call update_canvas_artifact with its " +
		"artifact_key and the complete new source — do NOT write a new code block, which " +
		"would create a second, unrelated artifact.\n\n")
	for i := range artifacts {
		a := &artifacts[i]
		title := ""
		if a.Title != nil {
			title = *a.Title
		}
		fmt.Fprintf(&b, "- artifact_key: %s | type: %s | version: %d",
			a.ArtifactKey, a.Type, a.Version)
		if title != "" {
			fmt.Fprintf(&b, " | title: %s", title)
		}
		b.WriteString("\n")
	}
	// Current source of the NEWEST artifact only — the one the user is
	// almost certainly looking at. Same "most recent only" budget rule
	// the Excel/Doc blocks use.
	newest := &artifacts[0]
	b.WriteString("\nCurrent source of ")
	b.WriteString(newest.ArtifactKey)
	b.WriteString(":\n\n")
	if len(newest.Content) > maxCanvasPromptSource {
		b.WriteString("(too large to include here — ask the user to paste the part they " +
			"want changed, or to describe the change precisely; do not guess the full source)\n")
		return b.String()
	}
	b.WriteString("```")
	b.WriteString(newest.Type)
	b.WriteString("\n")
	b.WriteString(newest.Content)
	b.WriteString("\n```\n")
	return b.String()
}

// keep “time“ import referenced even when no other code in this
// file uses it directly (the GatewayChatUsage struct references
// it transitively through the gateway client).
var _ time.Duration

// prepareSend runs the gating checks shared by stream + non-stream:
// validate input, verify conversation ownership, persist the user
// message, build the gateway-context history.
func (s *MessageService) prepareSend(ctx context.Context, in SendInput) (
	*model.Conversation, *model.Message, []model.Message, error,
) {
	// "Empty content" is only invalid when the user ALSO didn't
	// attach any files. A file-only send (e.g. drag a PDF in, hit
	// Send without typing anything) is a legitimate flow — Claude
	// and ChatGPT both accept it. buildGatewayRequestWithImages
	// substitutes a "please analyze the attached files" prompt at
	// request time so the upstream provider gets a non-empty user
	// turn even though the persisted message bubble stays empty.
	if strings.TrimSpace(in.Content) == "" && len(in.FileIDs) == 0 {
		return nil, nil, nil, ErrMessageEmptyContent
	}
	conv, err := s.conversations.GetByID(ctx, in.TenantID, in.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, nil, nil, ErrMessageConversationNotFound
		}
		return nil, nil, nil, err
	}
	if conv.UserID != in.UserID {
		return nil, nil, nil, ErrMessageConversationNotFound
	}
	// Model-allowed gate. The conversation's model_id could
	// have been recorded BEFORE an admin removed that model from
	// the allow-list; we treat a now-disallowed model as a
	// hard reject so the user can't keep using it.
	if !s.models.ModelAllowed(conv.ModelID) {
		return nil, nil, nil, ErrMessageModelNotAllowed
	}

	// If the conversation already has a fork group, new messages
	// inherit the currently-active branch so they stay on the same
	// side of the tree.
	var parentID *uuid.UUID
	branchIndex := 0
	if recent, err := s.messages.ListContextByConversation(ctx, in.TenantID, conv.ID, 1); err == nil && len(recent) > 0 {
		latest := recent[len(recent)-1]
		if latest.ParentID != nil {
			parentID = latest.ParentID
			branchIndex = latest.BranchIndex
		}
	}

	userMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "user",
		ContentMasked:  in.Content,
		Status:         "completed",
		ModelID:        &conv.ModelID,
		ParentID:       parentID,
		BranchIndex:    branchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, nil, nil, err
	}

	// Link any attached files to the new user message. Non-fatal:
	// if the linker isn't wired (tests) or the SQL fails, the user
	// message persists and the file just doesn't show up in the
	// history response. Logged so an operator can chase it down.
	if s.fileSvc != nil && len(in.FileIDs) > 0 {
		if _, err := s.fileSvc.AttachToMessage(
			ctx, in.TenantID, conv.ID, userMsg.ID, in.FileIDs,
		); err != nil {
			// Don't return — the user's prompt is already saved and
			// failing the whole send because the attach query
			// stuttered would be a worse outcome than missing
			// metadata.
			_ = err
		}
	}

	// History includes the just-saved user message — that becomes
	// the final "user" turn in the gateway request.
	history, err := s.messages.ListContextByConversation(
		ctx, in.TenantID, conv.ID, s.maxContext,
	)
	if err != nil {
		return nil, nil, nil, err
	}

	return conv, userMsg, history, nil
}

// finalizeConversation runs the post-message housekeeping:
//   - bump message_count by 2 (user + assistant)
//   - auto-title from the first user message if the conversation
//     is still on its default title
//   - S3: trigger async conversation-state extraction when the
//     message count crosses one of the thresholds (10 / +20)
func (s *MessageService) finalizeConversation(
	ctx context.Context,
	conv *model.Conversation,
	userMsg, asstMsg *model.Message,
) {
	_ = s.conversations.IncrementMessageCount(ctx, conv.ID, 2)

	// Auto-title: triggered on the FIRST user/assistant pair.
	// message_count before this call was 0 → 2 after.
	if conv.MessageCount == 0 && conv.Title == "New conversation" {
		title := makeTitleFrom(userMsg.ContentMasked)
		_ = s.conversations.UpdateTitle(ctx, conv.TenantID, conv.ID, title)
	}

	// S3 — kick the state-extraction trigger. Runs async so the
	// user's response isn't blocked on the gateway extraction call.
	s.maybeUpdateConversationState(ctx, conv)
	// S8 — kick the preference-pattern extractor on the same path.
	// Skipped when memory is off (temporary chats) and capped to
	// once per conversation lifetime.
	s.maybeExtractPreferences(ctx, conv)
}

// State-extraction triggers.
//
// firstExtractionAtMessages: conversations under this size still
// fit in the rolling window; no need to spend a gateway call yet.
//
// extractEveryNMessages: once a state row exists, re-extract every
// time this many new messages accumulate. Bigger = cheaper but
// staler; smaller = fresher but more $$.
const (
	firstExtractionAtMessages = 10
	extractEveryNMessages     = 20
)

// maybeUpdateConversationState decides whether the conversation
// has accumulated enough new messages to warrant another
// extraction pass and, if so, fires it on a background goroutine.
//
// Disabled when:
//   - the state service isn't wired (tests, legacy builds)
//   - conv.MemoryEnabled is false (temporary chat — the user
//     opted out of persisted state)
//
// Read uses background ctx so the trigger survives the request
// ctx cancellation that fires on stream completion.
func (s *MessageService) maybeUpdateConversationState(
	parent context.Context, conv *model.Conversation,
) {
	if s.stateSvc == nil || conv == nil || !conv.MemoryEnabled {
		return
	}

	// Use the parent ctx for the cheap "should we run?" checks so a
	// shutdown still cancels us cleanly. The actual extraction goroutine
	// gets context.Background() because we want it to complete after
	// the request ends.
	count, err := s.messages.CountMessages(parent, conv.TenantID, conv.ID)
	if err != nil {
		return
	}
	existing, _ := s.stateSvc.Get(parent, conv.ID, conv.UserID, conv.TenantID)

	run := false
	if existing == nil && count >= firstExtractionAtMessages {
		run = true
	} else if existing != nil && count-existing.MessageCountAtSummary >= extractEveryNMessages {
		run = true
	}
	if !run {
		return
	}

	convID, userID, tenantID := conv.ID, conv.UserID, conv.TenantID
	safeGo("conversation-state-extraction", func() {
		bg := context.Background()
		if _, err := s.stateSvc.GenerateOrUpdateState(bg, convID, userID, tenantID); err != nil {
			slog.Warn("conversation state: auto-extraction failed",
				"conv", convID.String(), "err", err)
		}
	})
}

// preferenceExtractAtMessages — minimum conversation size before
// the S8 background pattern learner runs. Higher than the S3
// state trigger (10) because preference patterns need MORE signal
// to surface reliably; one short conversation rarely shows a
// behaviour.
const preferenceExtractAtMessages = 20

// maybeExtractPreferences fires the S8 PreferenceExtractor at most
// ONCE per conversation, on a background goroutine, when:
//   - the extractor is wired
//   - the conv has memory enabled (temp chats opt out — they
//     mustn't surface suggestions either)
//   - the conv has crossed preferenceExtractAtMessages messages
//   - no inferred_preference row from THIS conv exists yet (dedup
//     check via the source_conversation_id filter)
//
// Same async-on-background-ctx pattern as
// maybeUpdateConversationState so the user's response is never
// blocked on the extraction call.
func (s *MessageService) maybeExtractPreferences(
	parent context.Context, conv *model.Conversation,
) {
	if s.prefExtractor == nil || conv == nil || !conv.MemoryEnabled {
		return
	}

	count, err := s.messages.CountMessages(parent, conv.TenantID, conv.ID)
	if err != nil {
		return
	}
	if count < preferenceExtractAtMessages {
		return
	}

	// Only extract once per conversation. We use the memory service's
	// list path with the source_conversation_id field on a single
	// scan — cheaper than a dedicated count query for a row that
	// realistically has at most a handful of suggestions.
	existing, err := s.prefExtractor.memoryRepo.ListByUser(
		parent, conv.UserID, conv.TenantID, repository.MemoryListFilters{
			MemoryType: model.MemoryTypeInferredPreference,
			Status:     model.MemoryStatusPendingConfirmation,
			Limit:      100,
		},
	)
	if err == nil {
		for i := range existing {
			if existing[i].SourceConversationID != nil && *existing[i].SourceConversationID == conv.ID {
				return // already extracted from this conv
			}
		}
	}

	convID, userID, tenantID := conv.ID, conv.UserID, conv.TenantID
	safeGo("preference-extraction", func() {
		bg := context.Background()
		if _, err := s.prefExtractor.ExtractPreferences(bg, convID, userID, tenantID); err != nil {
			slog.Warn("preference extraction failed",
				"conv", convID.String(), "err", err)
		}
	})
}

// persistError saves a placeholder assistant row with status=error
// so the conversation timeline reflects the failed turn. The
// non-stream path uses this when the gateway call itself fails
// (before any content arrived).
func (s *MessageService) persistError(
	ctx context.Context,
	in SendInput,
	conv *model.Conversation,
	content string,
) error {
	_, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "assistant",
		ContentMasked:  content,
		Status:         "error",
		ModelID:        &conv.ModelID,
		IsActive:       true,
	})
	return err
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

// compileAndBuildRequest is the S4 integration shim. Every send
// path (Send / SendStream / Regenerate / Edit) used to call
// buildSystemPrefix + buildGatewayRequestWithImages back-to-back;
// they now call this instead so the compiler can trim the history
// to the model's working budget before the request goes out.
//
// When the compiler isn't wired (legacy / test setup), falls back
// to the original behaviour: prefix + history shipped verbatim.
// numTools is the count of GatewayTool entries the caller plans
// to attach AFTER this call — used by the compiler's budget math
// to reserve room for tool-definition payload.
func (s *MessageService) compileAndBuildRequest(
	ctx context.Context,
	conv *model.Conversation,
	userID uuid.UUID,
	tenantID uuid.UUID,
	ragQuery string,
	history []model.Message,
	imgs userImageSet,
	sources *SourcesAccumulator,
	omittedOlder int,
	onExtractionStart func(),
) GatewayChatRequest {
	// current-turn images drive routing + the compiler budget; the
	// per-message map drives inline placement in the built request.
	imageURLs := imgs.current
	numTools := 0
	if s.toolExec != nil {
		numTools = len(s.toolExec.ToolDefs(conv))
	}
	// Auto-model resolution. The conversation row stores the user's
	// preference verbatim (so "auto" persists across turns); the
	// concrete model id only materialises at request time. This
	// keeps the heuristic responsive to the CURRENT turn's signals
	// (images / files / message length / code markers) rather than
	// being locked in at conversation create.
	modelID, routingHints := s.resolveAutoIfNeeded(ctx, conv, tenantID, ragQuery, imageURLs, len(history))

	// Annotate (a copy of) assistant turns that produced an AI image so
	// the model — possibly a different one than generated it — knows the
	// image exists. Computed once from the full history; the builder
	// matches by message id so compaction truncating the history later
	// just drops unused entries.
	genNotes := s.generatedImageNotes(ctx, tenantID, history)

	// Approach B — when the target model can SEE images, also replay the
	// generated images themselves as vision content (synthetic user
	// message after each producing assistant turn). Only messages that
	// already earned a note are queried; non-vision / unknown / Auto
	// models keep the text-annotation-only behaviour.
	var genVision map[uuid.UUID][]string
	if len(genNotes) > 0 {
		vision := s.visionCapable != nil && s.visionCapable(ctx, modelID)
		if vision && s.fileSvc != nil {
			ids := make([]uuid.UUID, 0, len(genNotes))
			for id := range genNotes {
				ids = append(ids, id)
			}
			genVision = s.fileSvc.GeneratedImageVisionURLs(ctx, tenantID, ids)
		}
		slog.Info("generated_image_history_mode",
			"model", modelID,
			"vision_capable", vision,
			"messages_with_images", len(genNotes),
			"messages_with_vision", len(genVision))
	}

	// Source-aware closure: the same call site can run multiple
	// times inside Compile (initial + post-extraction rebuild),
	// so the closure returns both the prefix text AND the
	// ContextSource list each block contributed. Compile uses the
	// MOST RECENT call's sources via CompiledContext.Sources.
	build := func() (string, []ContextSource) {
		return s.buildSystemPrefixWithSources(ctx, tenantID, userID, conv, ragQuery, modelID)
	}

	if s.compiler == nil {
		// Legacy fallback: no compiler wired. Still collect S7
		// sources from the single prefix build so attribution
		// works in test setups.
		prefix, srcs := build()
		sources.Add(srcs...)
		req := buildGatewayRequestWithImages(modelID, history, prefix, imgs.byMsg, genNotes, genVision, s.canonicalHistoryEnabled)
		req.RoutingHints = routingHints
		attachReasoning(&req, conv, modelID, ragQuery, s.reasoningCheckerFor(ctx))
		return req
	}

	compiled, err := s.compiler.Compile(ctx, CompileInputs{
		Conv:              conv,
		UserID:            userID,
		TenantID:          tenantID,
		History:           history,
		ImageURLs:         imageURLs,
		BuildPrefix:       build,
		NumTools:          numTools,
		OmittedOlder:      omittedOlder,
		OnExtractionStart: onExtractionStart,
		BuildState:        s.stateBlockBuilder(ctx, conv, userID, tenantID),
	})
	if err != nil || compiled == nil {
		// Defensive fallback — should never happen since Compile
		// doesn't return an error today, but the contract is
		// "always send something rather than fail the turn".
		slog.Warn("context_compiler: fell back to legacy assembly",
			"conv", conv.ID.String(), "err", err)
		prefix, srcs := build()
		sources.Add(srcs...)
		req := buildGatewayRequestWithImages(modelID, history, prefix, imgs.byMsg, genNotes, genVision, s.canonicalHistoryEnabled)
		req.RoutingHints = routingHints
		attachReasoning(&req, conv, modelID, ragQuery, s.reasoningCheckerFor(ctx))
		return req
	}
	// S7 — flush the compiler's sources (memory + state +
	// compaction marker) into the per-turn accumulator so the
	// finish() callback can persist them alongside any search-
	// tool hits the model adds during the turn.
	sources.Add(compiled.Sources...)
	req := buildGatewayRequestWithImages(modelID, compiled.History, compiled.SystemPrefix, imgs.byMsg, genNotes, genVision, s.canonicalHistoryEnabled)
	req.RoutingHints = routingHints
	attachReasoning(&req, conv, modelID, ragQuery, s.reasoningCheckerFor(ctx))
	return req
}

// attachReasoning forwards the conversation's reasoning preferences to
// the Gateway. Called from compileAndBuildRequest after the request is
// built so every send path (Send, Regenerate, EditMessage) honors the
// same settings consistently.
//
// P1 — the per-conversation settings are now honored instead of being
// hardcoded. The composer's ReasoningToggle PATCHes these onto the
// conversation row; this is where they take effect on the wire:
//
//   - ReasoningEnabled == true  -> forward the user's chosen effort
//     (ReasoningMode: auto / low / medium / high). This is an explicit
//     "think at this depth" request.
//   - ReasoningEnabled == false -> forward mode="auto" (Auto Thinking
//     Mode, the established default). The Gateway's per-adapter gate +
//     reasoning-aware router still engage native thinking ONLY on a
//     model that supports it, so this never alters a non-reasoning turn.
//     We deliberately do NOT send "off" here: "toggle off" returns to
//     automatic behaviour, it does not suppress thinking on a model the
//     router already picked for its reasoning. (A literal off would
//     require the composer to persist ReasoningMode="off".)
//   - ShowReasoningSummary controls whether the reasoning channel is
//     surfaced to the browser; honored as stored (default true).
//
// conv == nil (defensive) falls back to the prior unconditional
// auto / show_summary=true default.
func attachReasoning(req *GatewayChatRequest, conv *model.Conversation, modelID, message string, reasoningCapable func(string) bool) {
	// Models-catalog step 3: the caller may inject a live-catalog-backed
	// capability checker; nil keeps the static fallback set.
	capable := reasoningCapable
	if capable == nil {
		capable = isReasoningCapableID
	}
	mode := "auto"
	showSummary := true
	if conv != nil {
		if conv.ReasoningEnabled && conv.ReasoningMode != "" {
			// Explicit user choice (low/medium/high/auto) — always respected.
			mode = conv.ReasoningMode
		} else if conv.ReasoningMode == "off" {
			// Explicit persisted opt-out (not currently set by the
			// composer, but honor it if a future client does).
			mode = "off"
		} else if !IsAutoModel(conv.ModelID) && capable(modelID) && isTrivialQuery(message) {
			// P1-4 — the user PINNED a concrete reasoning-capable model (not
			// Auto) and this turn is trivial (e.g. "ไง"). Suppress thinking so
			// a greeting doesn't burn a full thinking budget. The model is
			// unchanged; only the effort drops to "off". Non-trivial / complex
			// turns keep "auto". Auto-model turns are unaffected — the router
			// already routes trivial turns to a non-reasoning model where the
			// gateway omits thinking anyway. The gateway respects mode="off"
			// (it never force-re-enables an explicit opt-out).
			mode = "off"
		}
		showSummary = conv.ShowReasoningSummary
	}
	req.Reasoning = &GatewayReasoningConfig{
		Mode:        mode,
		ShowSummary: showSummary,
	}
}

// resolveAutoIfNeeded decides what to ship to the gateway this turn and
// returns (modelID, hints):
//
//   - Explicit pick (user chose a concrete model): returns
//     (conv.ModelID, nil). Unchanged behaviour — no hints, gateway's
//     explicit path serves the exact model. Still honours conv.LockedModel
//     if a prior turn locked one (legacy convs), but never writes a new
//     lock here.
//   - Auto ("auto" sentinel or empty): returns ("auto", hints). chat-edge
//     no longer resolves a concrete model client-side (that bypassed the
//     gateway's provider-agnostic smart router and pinned every turn to one
//     provider, so a dead key meant an empty reply). Instead it forwards
//     capability hints and lets the gateway pick the best model among
//     providers that hold a valid key RIGHT NOW. No model lock on the auto
//     path — every turn re-hints so routing adapts to the current turn
//     AND so a key rotation immediately changes the served model.
//
// Per-turn signals (images / files / length / turn count / code / language)
// are folded into the hints by BuildRoutingHints. The decision is logged
// once per turn so an operator can reconstruct what was requested.
func (s *MessageService) resolveAutoIfNeeded(
	ctx context.Context,
	conv *model.Conversation,
	tenantID uuid.UUID,
	message string,
	imageURLs []string,
	historyLen int,
) (string, *RoutingHints) {
	if !IsAutoModel(conv.ModelID) {
		return conv.ModelID, nil
	}
	hasFiles := strings.TrimSpace(s.fileContext(ctx, tenantID, conv.ID)) != ""
	hasImages := len(imageURLs) > 0
	// Same count the context budgeter reserves for (InspectContext does
	// this too) — the gateway needs it to keep a tool-bearing turn off a
	// model that can't call tools.
	numTools := 0
	if s.toolExec != nil {
		numTools = len(s.toolExec.ToolDefs(conv))
	}
	hints := BuildRoutingHints(message, hasFiles, hasImages, historyLen, conv.ReasoningEnabled, numTools)
	// FIX35 Part C — the conversation's cost ceiling rides along; "" lets the
	// gateway apply the tenant / deployment default.
	hints.Tier = conv.RoutingTier
	slog.Info("auto_routing_hints",
		"tier", hints.Tier,
		"conversation_id", conv.ID.String(),
		"needs_vision", hints.NeedsVision,
		"needs_reasoning", hints.NeedsReasoning,
		"has_tools", hints.HasTools,
		"tool_count", hints.ToolCount,
		"has_code", hints.HasCode,
		"has_files", hints.HasFiles,
		"complexity", hints.Complexity,
		"conv_turn_count", hints.ConvTurnCount,
		"language", hints.Language,
	)
	return AutoModelID, &hints
}

// InspectContext is the public dry-run entry point — runs the
// compiler against the current state of a conversation without
// actually sending a request. Backs GET /conversations/{id}/context
// -info so the frontend (or a curl from the operator) can see
// the budget breakdown before chatting.
//
// Returns nil + ErrMessageConversationNotFound when the caller
// doesn't own the conversation; tenant + user are pulled from
// the supplied IDs so the handler does ownership verification
// before calling this.
func (s *MessageService) InspectContext(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (*CompiledContext, error) {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageConversationNotFound
		}
		return nil, err
	}
	if conv.UserID != userID {
		return nil, ErrMessageConversationNotFound
	}
	history, err := s.messages.ListContextByConversation(ctx, tenantID, convID, s.maxContext)
	if err != nil {
		return nil, err
	}
	imageURLs := s.lastUserImageURLs(ctx, tenantID, history)
	ragQuery := lastUserContent(history)
	numTools := 0
	if s.toolExec != nil {
		numTools = len(s.toolExec.ToolDefs(conv))
	}
	if s.compiler == nil {
		// Synthesize a CompiledContext so callers see the same
		// shape they'd get with the compiler wired — just with no
		// compaction ever applied.
		prefix := s.buildSystemPrefix(ctx, tenantID, userID, conv, ragQuery, conv.ModelID)
		budget := CalculateBudget(conv.ModelID, EstimateTokens(prefix)+4, 0, 0, 0, numTools*perToolTokens)
		return &CompiledContext{
			SystemPrefix:  prefix,
			History:       history,
			Budget:        budget,
			MessagesTotal: len(history),
			MessagesSent:  len(history),
		}, nil
	}
	return s.compiler.Compile(ctx, CompileInputs{
		Conv:      conv,
		UserID:    userID,
		TenantID:  tenantID,
		History:   history,
		ImageURLs: imageURLs,
		BuildPrefix: func() (string, []ContextSource) {
			return s.buildSystemPrefixWithSources(ctx, tenantID, userID, conv, ragQuery, conv.ModelID)
		},
		NumTools:     numTools,
		OmittedOlder: s.omittedOlder(ctx, tenantID, convID, len(history)),
		DryRun:       true,
		BuildState:   s.stateBlockBuilder(ctx, conv, userID, tenantID),
	})
}

// stateBlockBuilder (FIX33.1 Part 2) — the compiler's source for the
// <conversation_state> block, injected as a user/assistant pair at the
// head of the sent history when compaction happens. nil-safe.
func (s *MessageService) stateBlockBuilder(ctx context.Context, conv *model.Conversation, userID, tenantID uuid.UUID) func() (string, []ContextSource) {
	if s.stateSvc == nil || conv == nil || !conv.MemoryEnabled {
		return nil
	}
	return func() (string, []ContextSource) {
		return s.stateSvc.BuildStateContextWithSources(ctx, conv.ID, userID, tenantID)
	}
}

// omittedBefore (FIX33.1 Part 6.1) — the regenerate path loads the
// history strictly BEFORE the target message; the rows beyond its load
// window are the ones created before the oldest loaded row, so the count
// must be bounded by the target's timestamp — CountMessages (whole
// conversation) would inflate the marker with rows that come after it.
func (s *MessageService) omittedBefore(ctx context.Context, tenantID, convID uuid.UUID, before time.Time, loaded int) int {
	if loaded < s.maxContext {
		return 0
	}
	total, err := s.messages.CountMessagesBefore(ctx, tenantID, convID, before)
	if err != nil {
		slog.Warn("context: could not count messages before the regenerate target", "conv", convID.String(), "err", err)
		return 0
	}
	if total > loaded {
		return total - loaded
	}
	return 0
}

// omittedOlder (FIX33 A-W1) — how many active messages exist beyond the
// loaded window. Only asks the DB when the load actually hit the
// ceiling (len == maxContext), so the common case costs nothing. The
// compiler counts the answer into Compacted + the marker and folds
// those rows into the state before trimming — the old 200-cap's
// silent drop has no successor.
func (s *MessageService) omittedOlder(ctx context.Context, tenantID, convID uuid.UUID, loaded int) int {
	if loaded < s.maxContext {
		return 0
	}
	total, err := s.messages.CountMessages(ctx, tenantID, convID)
	if err != nil {
		slog.Warn("context: could not count messages beyond the load window", "conv", convID.String(), "err", err)
		return 0
	}
	if total > loaded {
		return total - loaded
	}
	return 0
}

// ManualCompactResult is what POST /conversations/{id}/compact returns.
type ManualCompactResult struct {
	State           *model.ConversationState `json:"state"`
	BoundaryID      uuid.UUID                `json:"boundary_message_id"`
	MessagesTotal   int                      `json:"messages_total"`
	CoverageReached bool                     `json:"coverage_reached"`
}

// ManualCompact (FIX33 A-W7 — Claude's /compact): summarise every
// message of the conversation into <conversation_state> NOW, honouring
// the user's instructions, and — only if that succeeded — record the
// newest message as the manual boundary so the next turn drops
// everything at or before it (the first exchange still survives).
// Nothing is dropped when the summary could not be produced: the
// boundary is set after coverage, never before.
func (s *MessageService) ManualCompact(
	ctx context.Context, convID, userID, tenantID uuid.UUID, instructions string,
) (*ManualCompactResult, error) {
	if s.stateSvc == nil {
		return nil, errors.New("conversation state service not configured")
	}
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageConversationNotFound
		}
		return nil, err
	}
	if conv.UserID != userID {
		return nil, ErrMessageConversationNotFound
	}
	if !conv.MemoryEnabled {
		return nil, ErrMessageMemoryOff
	}
	latest, err := s.messages.ListContextByConversation(ctx, tenantID, convID, 1)
	if err != nil {
		return nil, err
	}
	if len(latest) == 0 {
		return nil, ErrMessageEmptyContent
	}
	last := latest[len(latest)-1].ID
	st, reached, err := s.stateSvc.EnsureStateCoversUpTo(ctx, convID, userID, tenantID, &last, ExtractionOptions{
		Instructions: instructions, Trigger: "manual",
	})
	if err != nil {
		return nil, err
	}
	if st == nil || isStateEmpty(st) {
		// The extractor produced nothing (gateway down, model chatted):
		// refuse to set a boundary — dropping without a summary is the
		// thing this whole feature exists to prevent.
		return nil, ErrCompactNoSummary
	}
	st, err = s.stateSvc.SetManualCompactBoundary(ctx, convID, userID, tenantID, last, instructions)
	if err != nil {
		return nil, err
	}
	total, _ := s.messages.CountMessages(ctx, tenantID, convID)
	slog.Info("manual_compact", "conv", convID.String(), "boundary", last.String(), "reached", reached, "messages_total", total)
	return &ManualCompactResult{State: st, BoundaryID: last, MessagesTotal: total, CoverageReached: reached}, nil
}

// ErrMessageMemoryOff — manual compact needs the state store, which a
// temporary (memory-off) chat has opted out of.
var ErrMessageMemoryOff = errors.New("memory is off for this conversation")

// ErrCompactNoSummary — the extractor produced no usable summary, so
// nothing was dropped.
var ErrCompactNoSummary = errors.New("the summary could not be produced; nothing was compacted")

func buildGatewayRequest(model string, history []model.Message, systemPrefix string) GatewayChatRequest {
	return buildGatewayRequestWithImages(model, history, systemPrefix, nil, nil, nil, false)
}

// buildGatewayRequestWithImages is the vision-aware variant.
// `imagesByMsg` maps a user message id to its image data URLs; EACH
// such user message is rendered as a multimodal content array — image
// blocks FIRST (Anthropic guidance) then one text block carrying that
// message's prompt. All other messages (system, assistant replies, and
// user turns without images) ship as plain strings.
//
// Every image-bearing user turn keeps its OWN images inline — not just
// the latest — so a picture attached three turns ago is still visible to
// the model when a later text turn asks about it. The caller (resolveUserImages)
// bounds the replayed history with the existing vision caps so re-sending
// old images doesn't balloon the bill; the current turn stays uncapped.
//
// generatedNotes (optional, keyed by message id) carries the per-
// message annotation for assistant turns that produced an AI image.
// It is appended to the OUTBOUND copy of the assistant content only —
// stored rows and the chat UI never see it — so a model switched
// mid-conversation knows the image exists instead of contradicting
// the bare "image created" text.
//
// generatedVision (optional, keyed by message id; Approach B) carries
// resized data URLs of those generated images for a vision-capable
// model. Each producing assistant turn is followed by a SYNTHETIC
// user message holding the image_url blocks — providers (OpenAI
// included) accept image_url only in user-role messages, so the image
// cannot ride the assistant message itself. The text block labels it
// as a reference so the model doesn't mistake it for a real user turn.
// Outbound-only, same as the notes: nothing is stored or shown in the
// chat UI.
func buildGatewayRequestWithImages(
	model string, history []model.Message, systemPrefix string, imagesByMsg map[uuid.UUID][]string,
	generatedNotes map[uuid.UUID]string,
	generatedVision map[uuid.UUID][]string,
	canonicalHistoryEnabled bool,
) GatewayChatRequest {
	msgs := make([]GatewayMessage, 0, len(history)+1)
	if strings.TrimSpace(systemPrefix) != "" {
		msgs = append(msgs, GatewayMessage{
			Role:    "system",
			Content: systemPrefix,
		})
	}

	// Pre-compute the index of the last user-role entry. Used for
	// two things now: image-block decoration (existing) AND the
	// empty-content fallback below. Empty user content is reachable
	// whenever the user attached files without typing text — every
	// upstream provider rejects an empty user turn so we substitute
	// a prompt that engages whatever file context the system-prefix
	// builder already injected. Computed unconditionally now (no
	// more "only when images" gate) so document-only sends get the
	// same treatment.
	lastUserIdx := -1
	for i := len(history) - 1; i >= 0; i-- {
		if history[i].Role == "user" {
			lastUserIdx = i
			break
		}
	}

	for i, m := range history {
		var content any = m.ContentMasked
		if m.Role == "assistant" {
			// Phase 4A.2.2.1 (H1) — canonical-only provider history with
			// READ-TIME re-validation. When enabled, an assistant row
			// contributes its CANONICAL content ONLY if it RE-VALIDATES
			// (status complete + non-nil + schema/generation/sha/length/
			// utf8/size all check out); ANY failure EXCLUDES the row —
			// never a restored-display fallback.
			if canonicalHistoryEnabled {
				cc, ok, _ := ValidateStoredCanonicalMessage(m)
				if !ok {
					continue
				}
				content = cc
				if note, ok := generatedNotes[m.ID]; ok {
					content = strings.TrimSpace(cc + "\n\n" + note)
				}
			} else if note, ok := generatedNotes[m.ID]; ok {
				content = strings.TrimSpace(m.ContentMasked + "\n\n" + note)
			}
		}
		if m.Role == "user" {
			text := m.ContentMasked
			emptyText := strings.TrimSpace(text) == ""
			imgs := imagesByMsg[m.ID]
			if len(imgs) > 0 {
				// Image-attach path: build multimodal blocks (image
				// URLs FIRST — Anthropic guidance — then one text block).
				// Applied to EVERY user message that carries images so an
				// earlier turn's picture stays inline in history, not just
				// the latest turn. Empty text gets an image-specific prompt.
				blocks := make([]GatewayContentBlock, 0, len(imgs)+1)
				for _, url := range imgs {
					blocks = append(blocks, GatewayContentBlock{
						Type: "image_url",
						ImageURL: &GatewayImageURL{
							URL:    url,
							Detail: "auto",
						},
					})
				}
				if emptyText {
					text = "What's in this image?"
				}
				blocks = append(blocks, GatewayContentBlock{
					Type: "text",
					Text: text,
				})
				content = blocks
			} else if emptyText && i == lastUserIdx {
				// Non-image attach path: text-only schema, but the
				// stored content is empty. Substitute a generic
				// "analyze the files" prompt. The system prefix
				// already carries the file context (extracted text,
				// excel inspection, doc structure, etc.) so the
				// model has everything it needs to engage. Only the
				// CURRENT turn — an empty earlier user row is left as-is.
				content = "Please analyze the attached files."
			}
		}
		msgs = append(msgs, GatewayMessage{Role: m.Role, Content: content})

		// Approach B — replay this assistant turn's generated images as
		// a synthetic user message right after it (image blocks first,
		// one text block last — same shape as the upload-vision path).
		if m.Role == "assistant" {
			if urls := generatedVision[m.ID]; len(urls) > 0 {
				blocks := make([]GatewayContentBlock, 0, len(urls)+1)
				for _, url := range urls {
					blocks = append(blocks, GatewayContentBlock{
						Type: "image_url",
						ImageURL: &GatewayImageURL{
							URL: url,
							// "low" always: context continuity needs the
							// gist, not pixel-perfect detail (~85 tokens
							// vs 765+ per image).
							Detail: "low",
						},
					})
				}
				blocks = append(blocks, GatewayContentBlock{
					Type: "text",
					Text: "[Reference: the AI-generated image(s) from the assistant's reply above, attached so you can see what the user received. This is not a new user request.]",
				})
				msgs = append(msgs, GatewayMessage{Role: "user", Content: blocks})
			}
		}
	}
	if canonicalHistoryEnabled {
		// H2 — excluding non-complete assistant rows can leave a
		// provider-invalid role sequence (e.g. consecutive user turns) or
		// orphan tool messages. Normalize WITHOUT reintroducing any raw
		// display/legacy assistant content.
		msgs = normalizeRoleSequence(msgs)
	}
	return GatewayChatRequest{Model: model, Messages: msgs}
}

// normalizeRoleSequence repairs the provider message sequence after
// canonical exclusion (H2). It merges consecutive plain-string USER turns
// with a blank-line delimiter and drops orphan tool messages (a tool turn
// not immediately preceded by an assistant turn). It NEVER fabricates an
// assistant turn and NEVER reinserts raw display/legacy content — it only
// reshapes messages already admitted to the list. System/developer turns
// are preserved as-is; multimodal (non-string) user turns are left intact.
func normalizeRoleSequence(msgs []GatewayMessage) []GatewayMessage {
	out := make([]GatewayMessage, 0, len(msgs))
	for _, m := range msgs {
		if m.Role == "tool" {
			if len(out) == 0 || out[len(out)-1].Role != "assistant" {
				continue // orphan tool result → drop
			}
		}
		if len(out) > 0 && out[len(out)-1].Role == "user" && m.Role == "user" {
			prev, ok1 := out[len(out)-1].Content.(string)
			cur, ok2 := m.Content.(string)
			if ok1 && ok2 {
				out[len(out)-1].Content = prev + "\n\n" + cur
				continue
			}
		}
		out = append(out, m)
	}
	return out
}

func gatewayCallContext(in SendInput, conv *model.Conversation) GatewayCallContext {
	return GatewayCallContext{
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		ConversationID: conv.ID,
		RequestID:      in.RequestID,
	}
}

// lastUserContent walks the history slice tail-first looking for the
// last "user" turn. Used as the RAG query for regenerate / edit
// flows where the in-flight prompt isn't on a SendInput.
func lastUserContent(history []model.Message) string {
	for i := len(history) - 1; i >= 0; i-- {
		if history[i].Role == "user" {
			return history[i].ContentMasked
		}
	}
	return ""
}

// lastUserImageURLs resolves the image attachments on the latest
// user-role message in `history` to a slice of base64 data URLs.
// Returns nil (not an empty slice) when there are no images or
// the file linker isn't wired — callers treat nil as "ship text
// only" and skip the vision-format content array entirely.
func (s *MessageService) lastUserImageURLs(
	ctx context.Context, tenantID uuid.UUID, history []model.Message,
) []string {
	if s.fileSvc == nil {
		return nil
	}
	for i := len(history) - 1; i >= 0; i-- {
		if history[i].Role != "user" {
			continue
		}
		urls := s.fileSvc.ImageDataURLsForMessage(ctx, tenantID, history[i].ID)
		if len(urls) == 0 {
			return nil
		}
		return urls
	}
	return nil
}

// userImageSet is the resolved vision content for a whole turn:
//   - byMsg   : per user-message-id image data URLs, so EACH user message
//     that carries images gets its own inline image blocks (fixes images
//     attaching to the wrong message — earlier-turn images stay in history).
//   - current : the latest user message's images, used for auto-routing and
//     the compiler's token budget (the active turn's signals).
//   - failures: attachments on the CURRENT turn that could not be encoded,
//     surfaced to the user as an image_notice.
type userImageSet struct {
	byMsg    map[uuid.UUID][]string
	current  []string
	failures []ImageAttachFailure
}

// resolveUserImages walks every user message in history and resolves its
// image attachments, so the request builder can attach each message's
// images inline (image-before-text) rather than piling them all onto the
// last user turn. Existing vision caps bound the REPLAYED history so the
// payload stays in budget; the current (latest) user turn is exempt from
// the caps — it's the active request and keeps full fidelity. Only the
// current turn's encode failures are surfaced: older-turn failures were
// already reported when first sent, so re-notifying every later turn
// would just be noise.
func (s *MessageService) resolveUserImages(
	ctx context.Context, tenantID uuid.UUID, history []model.Message,
) userImageSet {
	var set userImageSet
	if s.fileSvc == nil {
		return set
	}
	lastUserIdx := -1
	for i := len(history) - 1; i >= 0; i-- {
		if history[i].Role == "user" {
			lastUserIdx = i
			break
		}
	}
	byMsg := make(map[uuid.UUID][]string)
	total := 0 // replayed (non-current) images admitted so far
	// Newest-first so the caps keep the most recent history images.
	for i := len(history) - 1; i >= 0; i-- {
		m := &history[i]
		if m.Role != "user" {
			continue
		}
		urls, fails := s.fileSvc.ImageDataURLsForMessageChecked(ctx, tenantID, m.ID)
		if i == lastUserIdx {
			set.failures = fails // surface current-turn failures only
			if len(urls) > 0 {
				set.current = urls
				byMsg[m.ID] = urls // current turn: uncapped
			}
			continue
		}
		if len(urls) == 0 {
			continue
		}
		if total >= visionHistoryMaxImages {
			continue // budget spent on newer images
		}
		if len(urls) > visionHistoryMaxPerMsg {
			urls = urls[:visionHistoryMaxPerMsg]
		}
		if total+len(urls) > visionHistoryMaxImages {
			urls = urls[:visionHistoryMaxImages-total]
		}
		byMsg[m.ID] = urls
		total += len(urls)
	}
	if len(byMsg) > 0 {
		set.byMsg = byMsg
	}
	return set
}

// emitImageNotice pushes a one-shot image_notice frame onto a stream's
// buffered event channel when the current turn had any unreadable
// attachments. No-op when there were none. The channel buffer (16)
// guarantees the send never blocks.
func emitImageNotice(ch chan<- StreamUpdate, failures []ImageAttachFailure) {
	if len(failures) == 0 {
		return
	}
	ch <- StreamUpdate{ImageNotice: &ImageAttachNotice{Failures: failures}}
}

// SaveSearchSources persists the web_search citations on an assistant
// message (implements the tool executor's searchSourcesSaver). Marshals
// the gateway result list to JSON so the repo writes the JSONB column;
// the handler reads it back to emit the message_sources SSE event and
// to render citation chips on reload.
func (s *MessageService) SaveSearchSources(
	ctx context.Context, messageID uuid.UUID, sources []GatewaySearchResult,
) error {
	if len(sources) == 0 {
		return nil
	}
	raw, err := json.Marshal(sources)
	if err != nil {
		return fmt.Errorf("marshal search sources: %w", err)
	}
	return s.messages.SetSearchSources(ctx, messageID, raw)
}

// GetSearchSources reads the persisted web_search citations for one
// message (tenant-scoped). Used by the handler to emit message_sources
// after a streaming turn and to hydrate citation chips on list/reload.
func (s *MessageService) GetSearchSources(
	ctx context.Context, tenantID, messageID uuid.UUID,
) (json.RawMessage, error) {
	return s.messages.GetSearchSources(ctx, tenantID, messageID)
}

// generatedImageNotes batches the generated-image annotations for
// every assistant turn in history (one query via the file service).
// Returns nil when the file flow isn't wired or no assistant message
// carries a generated image — the request builder then ships history
// unchanged.
func (s *MessageService) generatedImageNotes(
	ctx context.Context, tenantID uuid.UUID, history []model.Message,
) map[uuid.UUID]string {
	if s.fileSvc == nil {
		return nil
	}
	ids := make([]uuid.UUID, 0, len(history))
	for i := range history {
		if history[i].Role == "assistant" {
			ids = append(ids, history[i].ID)
		}
	}
	if len(ids) == 0 {
		return nil
	}
	return s.fileSvc.GeneratedImageNotes(ctx, tenantID, ids)
}

// extractContent pulls the assistant text from the non-stream
// gateway response. Defensive against empty / missing fields.
// Content is `any` to accommodate vision-format user messages,
// but assistant replies are always strings — assert and fall
// back to empty when the gateway ever surprises us.
func extractContent(r *GatewayChatResponse) string {
	if r == nil || len(r.Choices) == 0 {
		return ""
	}
	if s, ok := r.Choices[0].Message.Content.(string); ok {
		return s
	}
	return ""
}

// makeTitleFrom condenses the first user message into a 100-char
// title (rune-counted so we don't truncate mid-codepoint on Thai
// input). Appends "…" when truncation occurred.
func makeTitleFrom(content string) string {
	content = strings.TrimSpace(content)
	const maxRunes = 100
	runes := []rune(content)
	if len(runes) <= maxRunes {
		return content
	}
	return string(runes[:maxRunes]) + "…"
}

// ---------------------------------------------------------------------------
// Tool calling (S1 — memory)
// ---------------------------------------------------------------------------

// maxToolIterations caps the tool-call loop so a model that keeps
// asking for tools can't run away. After this many rounds the loop
// closes the stream and lets the model's last partial text (if any)
// stand as the final answer.
const maxToolIterations = 5

// attachTools mutates the request to expose the per-conversation
// tool set. The executor's ToolDefs(conv) already gates each tool
// by what's appropriate for THIS conv: memory-write tools require
// conv.MemoryEnabled, the S6 read-only search tool stays
// available in temporary chats too.
//
// Returns true when at least one tool was attached (caller uses
// this to decide whether to enter the tool-loop accumulator —
// no tools → no tool_calls to worry about).
//
// Skips attaching entirely when the request's resolved model is KNOWN
// to lack tool support (catalog entry without function_calling) — some
// providers 400 on an unexpected “tools“ param, which would break a
// conversation after the user switches to such a model. Unknown ids and
// the Auto sentinel keep tools (capability unknown → don't degrade).
func (s *MessageService) attachTools(ctx context.Context, req *GatewayChatRequest, conv *model.Conversation, cloudAllow []string) bool {
	if s.toolExec == nil {
		return false
	}
	if s.nonToolModel != nil && s.nonToolModel(ctx, req.Model) {
		slog.Info("tools_skipped_non_tool_model",
			"model", req.Model, "conversation_id", conv.ID.String())
		return false
	}
	defs := s.toolExec.ToolDefs(conv)
	// Per-tenant gate for web_search: the tool is wired globally, but
	// only offered when the gateway says the tenant enabled search AND
	// has a provider configured. Dropped otherwise so the model never
	// tries to search when the feature is off (config is cached, so this
	// is at most one cheap gateway call per minute).
	if s.toolExec.HasWebSearch() && !s.webSearchEnabled(ctx) {
		defs = dropToolByName(defs, "web_search")
	}
	// FIX16 §1.4 — cloud tool boundary. When cloudAllow is set (a cloud
	// scheduled run), offer ONLY those tools; spend/write/connector tools
	// are never even presented to an unattended model.
	if cloudAllow != nil {
		defs = keepOnlyTools(defs, cloudAllow)
	}
	if len(defs) == 0 {
		return false
	}
	req.Tools = append(req.Tools, defs...)
	// tool_choice="auto" lets the model decide whether to call a
	// tool — most turns won't, and forcing "required" would make
	// the model invent tool calls for vanilla chat.
	req.ToolChoice = "auto"
	return true
}

// dropToolByName returns defs without the named tool (web_search gate).
func dropToolByName(defs []GatewayTool, name string) []GatewayTool {
	out := defs[:0]
	for _, d := range defs {
		if d.Function.Name != name {
			out = append(out, d)
		}
	}
	return out
}

// keepOnlyTools returns defs restricted to the named allowlist (FIX16 §1.4 —
// the cloud tool boundary). A nil/empty allow means "keep none".
func keepOnlyTools(defs []GatewayTool, allow []string) []GatewayTool {
	allowed := make(map[string]bool, len(allow))
	for _, n := range allow {
		allowed[n] = true
	}
	out := defs[:0]
	for _, d := range defs {
		if allowed[d.Function.Name] {
			out = append(out, d)
		}
	}
	return out
}

// webSearchEnabled reports whether the gateway says web search is on for
// chat-edge's (shared-key) tenant. Cached ~60s; the gateway fetch runs
// OUTSIDE the lock with a short timeout so a slow/down gateway can't
// stall chat requests (it degrades to the last-known value, default
// off). All web-chat traffic shares one gateway key/tenant, so a single
// cached value is correct.
func (s *MessageService) webSearchEnabled(ctx context.Context) bool {
	if s.searchConfig == nil {
		return false
	}
	s.searchCfgMu.Lock()
	if time.Now().Before(s.searchCfgExpiry) {
		v := s.searchCfgEnabled
		s.searchCfgMu.Unlock()
		return v
	}
	// Push expiry forward to avoid a fetch stampede, then release before I/O.
	s.searchCfgExpiry = time.Now().Add(10 * time.Second)
	last := s.searchCfgEnabled
	s.searchCfgMu.Unlock()

	cctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	cfg, err := s.searchConfig.SearchConfig(cctx, GatewayCallContext{})
	if err != nil {
		return last
	}
	s.searchCfgMu.Lock()
	s.searchCfgEnabled = cfg.Enabled
	s.searchCfgExpiry = time.Now().Add(60 * time.Second)
	s.searchCfgMu.Unlock()
	return cfg.Enabled
}

// applyImageModeHint appends a generate_image directive to the request's
// system context when the user enabled image mode (the web-chat 🎨
// toggle). It biases a tool-capable model to call generate_image with an
// enhanced English prompt instead of replying in text. No-op for models
// without tools (the gateway ignores the hint + tools). Mutates the
// existing system message in place, or prepends one if absent.
func applyImageModeHint(req *GatewayChatRequest) {
	const hint = "\n\n[IMAGE MODE] The user wants an IMAGE. Treat their message as an image " +
		"description and call the generate_image tool with a detailed, vivid ENGLISH prompt " +
		"(translate + enhance as needed). Do not ask clarifying questions unless the description " +
		"is empty. After the tool runs, reply with one short friendly sentence and no URL."
	for i := range req.Messages {
		if req.Messages[i].Role == "system" {
			if c, ok := req.Messages[i].Content.(string); ok {
				req.Messages[i].Content = c + hint
				return
			}
		}
	}
	req.Messages = append(
		[]GatewayMessage{{Role: "system", Content: strings.TrimSpace(hint)}},
		req.Messages...,
	)
}

func (s *MessageService) directImageStream(
	ctx context.Context,
	cancel context.CancelFunc,
	reg *streamRegistration,
	conv *model.Conversation,
	userMsg, asstMsg *model.Message,
	in SendInput,
) *StreamHandle {
	ch := make(chan StreamUpdate, 4)
	go func() {
		defer close(ch)
		ch <- StreamUpdate{ImageStatus: "started"}
		res := s.generateImageForMessage(ctx, in, conv.ID, asstMsg.ID)
		if res.Err != nil {
			ch <- StreamUpdate{ProviderError: &StreamProviderError{
				Code:    res.Err.Code,
				Message: res.Err.Message,
			}}
			return
		}
		ch <- StreamUpdate{Delta: res.Message}
		ch <- StreamUpdate{Done: true}
	}()
	finish := func(content, reasoning, finishReason string, usage *GatewayChatUsage) {
		s.activeStreams.Delete(conv.ID)
		cancel()
		persistCtx := context.Background()
		status := "completed"
		switch {
		case reg != nil && reg.isUserCancelled():
			status = "cancelled"
		case finishReason == "cancelled":
			status = "cancelled"
		case finishReason == "error":
			status = "error"
		}
		s.persistDisplayOnly(persistCtx, asstMsg.ID, content, status, nil, nil)
		final := *asstMsg
		final.ContentMasked = content
		final.Status = status
		s.finalizeConversation(persistCtx, conv, userMsg, &final)
	}
	events, runID := s.teeStream(asstMsg.ID, "", ch)
	return &StreamHandle{
		UserMessage:    userMsg,
		AssistantID:    asstMsg.ID,
		ConversationID: conv.ID,
		RunID:          runID,
		Events:         events,
		finish:         finish,
		cancel:         cancel,
		cancelled:      reg.isUserCancelled,
	}
}

type imageGenerationResult struct {
	Message string
	Err     *ImageGenerationError
}

type ImageGenerationError struct {
	Code    string
	Message string
	Status  int
}

func (e *ImageGenerationError) Error() string {
	return e.Code + ": " + e.Message
}

func (s *MessageService) generateImageForMessage(
	ctx context.Context,
	in SendInput,
	convID uuid.UUID,
	asstMsgID uuid.UUID,
) imageGenerationResult {
	if s.toolExec == nil || s.toolExec.imageGen == nil || s.toolExec.imageFiles == nil {
		slog.Warn("image_generation_unavailable",
			"request_id", in.RequestID,
			"conversation_id", convID.String(),
			"resolved_endpoint", "/v1/images/generations",
		)
		return imageGenerationResult{Err: &ImageGenerationError{
			Code:    "image_service_not_configured",
			Message: "ระบบสร้างรูปยังไม่ได้ตั้งค่า กรุณาติดต่อผู้ดูแลระบบ",
			Status:  503,
		}}
	}
	imageModel := s.toolExec.imageModel
	if imageModel == "" {
		imageModel = "gpt-image-1"
	}
	start := time.Now()
	resp, err := s.toolExec.imageGen.GenerateImages(ctx, GatewayCallContext{
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		ConversationID: convID,
		RequestID:      in.RequestID,
	}, GatewayImagesRequest{
		Model:   imageModel,
		Prompt:  in.Content,
		Size:    "1024x1024",
		Quality: "auto",
	})
	latencyMs := time.Since(start).Milliseconds()
	if err != nil {
		if errors.Is(ctx.Err(), context.Canceled) {
			slog.Info("image_cancelled",
				"request_id", in.RequestID,
				"tenant_id", in.TenantID.String(),
				"user_id", in.UserID.String(),
				"conversation_id", convID.String(),
				"message_id", asstMsgID.String(),
				"model", imageModel,
				"endpoint", "/v1/images/generations",
				"elapsed_ms", latencyMs,
			)
			return imageGenerationResult{Err: &ImageGenerationError{
				Code:    "image_cancelled",
				Message: "ยกเลิกการสร้างรูปแล้ว",
				Status:  499,
			}}
		}
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			slog.Warn("image_timeout",
				"request_id", in.RequestID,
				"tenant_id", in.TenantID.String(),
				"user_id", in.UserID.String(),
				"conversation_id", convID.String(),
				"message_id", asstMsgID.String(),
				"model", imageModel,
				"endpoint", "/v1/images/generations",
				"elapsed_ms", latencyMs,
				"timeout_ms", s.imageGenerationTimeout.Milliseconds(),
			)
			return imageGenerationResult{Err: &ImageGenerationError{
				Code:    "image_timeout",
				Message: "ระบบสร้างรูปใช้เวลานานเกินไป กรุณาลองใหม่อีกครั้ง",
				Status:  504,
			}}
		}
		mapped := mapImageGenerationError(err)
		slog.Error("image_generation_direct_failed",
			"request_id", in.RequestID,
			"tenant_id", in.TenantID.String(),
			"user_id", in.UserID.String(),
			"conversation_id", convID.String(),
			"selected_model", imageModel,
			"resolved_endpoint", "/v1/images/generations",
			"status_code", mapped.Status,
			"error_code", mapped.Code,
			"latency_ms", latencyMs,
			"retry_count", 0,
			"fallback_result", "none",
		)
		return imageGenerationResult{Err: mapped}
	}
	if resp == nil || len(resp.Data) == 0 || resp.Data[0].B64JSON == "" {
		slog.Error("image_generation_direct_empty_response",
			"request_id", in.RequestID,
			"conversation_id", convID.String(),
			"selected_model", imageModel,
			"resolved_endpoint", "/v1/images/generations",
			"latency_ms", latencyMs,
		)
		return imageGenerationResult{Err: &ImageGenerationError{
			Code:    "image_empty_response",
			Message: "ระบบสร้างรูปไม่ส่งไฟล์รูปกลับมา กรุณาลองใหม่อีกครั้ง",
			Status:  502,
		}}
	}
	raw, err := base64.StdEncoding.DecodeString(resp.Data[0].B64JSON)
	if err != nil {
		slog.Error("image_generation_direct_decode_failed",
			"request_id", in.RequestID,
			"conversation_id", convID.String(),
			"selected_model", imageModel,
			"error", err.Error(),
		)
		return imageGenerationResult{Err: &ImageGenerationError{
			Code:    "image_bad_response",
			Message: "ไฟล์รูปที่ได้รับไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง",
			Status:  502,
		}}
	}
	slog.Info("attachment_save_started",
		"request_id", in.RequestID,
		"conversation_id", convID.String(),
		"message_id", asstMsgID.String(),
		"mime_type", "image/png",
		"file_size_bytes", len(raw),
	)
	if _, err := s.toolExec.imageFiles.SaveGeneratedImage(ctx, GeneratedImageInput{
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		ConversationID: convID,
		MessageID:      asstMsgID,
		RequestID:      in.RequestID,
		Data:           raw,
		ContentType:    "image/png",
		Prompt:         in.Content,
	}); err != nil {
		slog.Error("image_generation_direct_save_failed",
			"request_id", in.RequestID,
			"conversation_id", convID.String(),
			"selected_model", imageModel,
			"error", err.Error(),
		)
		return imageGenerationResult{Err: &ImageGenerationError{
			Code:    "image_attachment_save_failed",
			Message: "สร้างรูปแล้วแต่บันทึกไฟล์ไม่สำเร็จ กรุณาลองใหม่อีกครั้ง",
			Status:  500,
		}}
	}
	slog.Info("image_generation_direct_succeeded",
		"request_id", in.RequestID,
		"tenant_id", in.TenantID.String(),
		"user_id", in.UserID.String(),
		"conversation_id", convID.String(),
		"selected_model", imageModel,
		"resolved_endpoint", "/v1/images/generations",
		"latency_ms", latencyMs,
		"retry_count", 0,
		"fallback_result", "none",
	)
	return imageGenerationResult{Message: "สร้างรูปเรียบร้อยแล้วครับ"}
}

func mapImageGenerationError(err error) *ImageGenerationError {
	var ue *GatewayUnavailableError
	if errors.As(err, &ue) {
		return classifyGatewayImageError(ue.Status, ue.Body)
	}
	errText := strings.ToLower(err.Error())
	if strings.Contains(errText, "timeout") || strings.Contains(errText, "deadline exceeded") {
		return &ImageGenerationError{
			Code:    "image_timeout",
			Message: "ระบบสร้างรูปใช้เวลานานเกินไป กรุณาลองใหม่อีกครั้ง",
			Status:  504,
		}
	}
	return &ImageGenerationError{
		Code:    "image_provider_error",
		Message: "ไม่สามารถสร้างรูปได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
		Status:  502,
	}
}

type gatewayErrorEnvelope struct {
	Error struct {
		Code    string         `json:"code"`
		Message string         `json:"message"`
		Details map[string]any `json:"details"`
	} `json:"error"`
}

func classifyGatewayImageError(status int, body string) *ImageGenerationError {
	providerStatus := status
	code := ""
	providerCode := ""
	msg := ""
	var env gatewayErrorEnvelope
	if body != "" && json.Unmarshal([]byte(body), &env) == nil {
		code = env.Error.Code
		msg = env.Error.Message
		if env.Error.Details != nil {
			if v, ok := env.Error.Details["status"].(float64); ok && v > 0 {
				providerStatus = int(v)
			}
			if rawBody, ok := env.Error.Details["body"].(map[string]any); ok {
				if rawErr, ok := rawBody["error"].(map[string]any); ok {
					if v, ok := rawErr["code"].(string); ok {
						providerCode = v
					}
					if msg == "" {
						if v, ok := rawErr["message"].(string); ok {
							msg = v
						}
					}
				}
			}
		}
	}
	lower := strings.ToLower(msg + " " + code + " " + providerCode + " " + body)
	switch {
	case providerStatus == 401 || providerStatus == 403 ||
		strings.Contains(lower, "no api key") || strings.Contains(lower, "rejected credentials") ||
		strings.Contains(lower, "permission"):
		return &ImageGenerationError{
			Code:    "image_auth_error",
			Message: "ระบบยังไม่มีสิทธิ์ใช้บริการสร้างรูป กรุณาตรวจสอบ OpenAI API key หรือสิทธิ์ของโมเดล",
			Status:  providerStatusOr(providerStatus, 403),
		}
	case providerStatus == 400 || providerStatus == 404 ||
		strings.Contains(lower, "invalid_request") || strings.Contains(lower, "unknown_parameter") ||
		strings.Contains(lower, "invalid size") || strings.Contains(lower, "model_not_found"):
		return &ImageGenerationError{
			Code:    "image_invalid_request",
			Message: "คำขอสร้างรูปไม่ถูกต้อง หรือโมเดล/ขนาดรูปไม่รองรับ",
			Status:  providerStatusOr(providerStatus, 400),
		}
	case providerStatus == 429 || strings.Contains(lower, "rate_limit") ||
		strings.Contains(lower, "insufficient_quota") || strings.Contains(lower, "quota"):
		return &ImageGenerationError{
			Code:    "image_rate_limited",
			Message: "โควต้าหรือ rate limit สำหรับสร้างรูปเต็มแล้ว กรุณารอสักครู่แล้วลองใหม่",
			Status:  providerStatusOr(providerStatus, 429),
		}
	case status == 504 || code == "provider_timeout" || strings.Contains(lower, "timed out") ||
		strings.Contains(lower, "timeout"):
		return &ImageGenerationError{
			Code:    "image_timeout",
			Message: "ระบบสร้างรูปใช้เวลานานเกินไป กรุณาลองใหม่อีกครั้ง",
			Status:  providerStatusOr(providerStatus, 504),
		}
	case providerStatus >= 500 || status == 502 || status == 503:
		return &ImageGenerationError{
			Code:    "image_provider_unavailable",
			Message: "บริการสร้างรูปไม่พร้อมใช้งานชั่วคราว กรุณาลองใหม่อีกครั้ง",
			Status:  providerStatusOr(providerStatus, 502),
		}
	default:
		return &ImageGenerationError{
			Code:    "image_provider_error",
			Message: "ไม่สามารถสร้างรูปได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
			Status:  providerStatusOr(providerStatus, 502),
		}
	}
}

func providerStatusOr(status, fallback int) int {
	if status > 0 {
		return status
	}
	return fallback
}

// rejectImageStream (Phase D) returns a StreamHandle that emits a canned
// "this model can't generate images" message WITHOUT calling the gateway,
// and persists it as a normal assistant message so it lands in the
// conversation timeline. Used when an image turn (intent or 🎨 mode) hits a
// model that can't call tools. “cancel“ is the unused gateway stream
// context, released in finish.
func (s *MessageService) rejectImageStream(
	conv *model.Conversation, userMsg, asstMsg *model.Message, cancel context.CancelFunc,
) *StreamHandle {
	ch := make(chan StreamUpdate, 2)
	go func() {
		ch <- StreamUpdate{Delta: imageUnsupportedMessage()}
		close(ch) // channel close is the stream terminator the handler reads
	}()
	finish := func(content, reasoning, finishReason string, usage *GatewayChatUsage) {
		cancel() // release the unused gateway stream context
		persistCtx := context.Background()
		s.persistDisplayOnly(persistCtx, asstMsg.ID, content, "completed", nil, nil)
		final := *asstMsg
		final.ContentMasked = content
		final.Status = "completed"
		s.finalizeConversation(persistCtx, conv, userMsg, &final)
	}
	events, runID := s.teeStream(asstMsg.ID, "", ch)
	return &StreamHandle{
		UserMessage:    userMsg,
		AssistantID:    asstMsg.ID,
		ConversationID: conv.ID,
		RunID:          runID,
		Events:         events,
		finish:         finish,
		cancel:         cancel,
	}
}

// accumulateToolCallDeltas merges a partial tool_calls delta into
// the in-flight accumulator slice, growing as needed. OpenAI ships
// tool calls in stream as a sparse sequence of (index, partial)
// updates; this normalises them into a flat slice the executor can
// dispatch against.
func accumulateToolCallDeltas(acc []GatewayToolCall, deltas []StreamToolCallDelta) []GatewayToolCall {
	for _, d := range deltas {
		for len(acc) <= d.Index {
			acc = append(acc, GatewayToolCall{Type: "function"})
		}
		t := &acc[d.Index]
		if d.ID != "" {
			t.ID = d.ID
		}
		if d.Type != "" {
			t.Type = d.Type
		}
		if d.FunctionName != "" {
			t.Function.Name = d.FunctionName
		}
		if d.FunctionArgsFrag != "" {
			t.Function.Arguments += d.FunctionArgsFrag
		}
	}
	return acc
}

// streamPumpInput bundles the parameters pumpStreamWithTools needs.
// Keeping them in a struct keeps the call site at Send/Regenerate/
// Edit readable (each only differs in callCtx + toolExecCtx).
type streamPumpInput struct {
	initialStream *StreamReader
	initialReq    GatewayChatRequest
	callCtx       GatewayCallContext
	streamCtx     context.Context
	toolsAttached bool
	toolExecCtx   ToolExecContext
	out           chan<- StreamUpdate
	// Phase 4A.2.2 — request-local canonical artifact accumulator. The
	// pump feeds the dlp.canonical_* events here (never to ``out``); the
	// finish closure reads it after the channel drains (happens-before).
	canon *CanonicalStreamState
}

// pumpStreamWithTools reads chunks from the initial gateway stream,
// forwards content deltas to `out`, and on a tool_calls finish:
//
//  1. Accumulates the streamed tool_call deltas into a flat slice.
//  2. Executes each tool via the executor, capturing result text.
//  3. Appends (assistant tool_calls msg, tool result msgs) to the
//     running request's Messages slice.
//  4. Opens a follow-up gateway stream with the augmented request
//     and continues forwarding deltas.
//
// Loops until either a non-tool finish (forwards Done + exits),
// maxToolIterations is hit (emits Done + exits — the model's last
// partial text stands as the final answer), or an error / EOF.
//
// The caller's `finish` callback handles persistence — pump only
// feeds the events channel and closes it when no more streams.
// providerStallAfter is how long the pump may wait on PROVIDER OUTPUT
// before telling the client the turn looks stalled.
//
// Chosen against three numbers. Realistic time-to-first-token here is a
// few seconds (measured: 2-7s on gpt-4o/4o-mini) and tens of seconds at
// worst for a reasoning model that streams no summary frames, so 75s
// clears every legitimate wait we have observed with room to spare. It is
// also deliberately BELOW the clients' 90s byte-based idle watchdog, so
// the specific, informative signal ("the model has gone quiet") arrives
// before the generic one ("no bytes at all"). And it is well inside the
// minute-or-two a user will stare at a spinner before deciding the
// product is broken.
//
// A var, not a const, purely so tests can shorten it — same precedent as
// heartbeatInterval.
var providerStallAfter = 75 * time.Second

// providerStallPoll is how often the watcher re-checks; small enough that
// the reported WaitingSeconds is accurate, large enough to be free.
var providerStallPoll = time.Second

// stallWatch reports a provider that has stopped producing output.
//
// The critical property: it measures ONLY the windows in which the pump
// is genuinely awaiting the provider. Silence during tool execution is
// normal and must never raise a stall — a run_python call can hold the
// turn for a legitimate minute. The pump therefore drives the phase
// explicitly (BeginAwait / EndAwait) instead of the watcher inferring it
// from byte timing, which is precisely the inference that made the
// client-side watchdog blind to this failure.
type stallWatch struct {
	out       chan<- StreamUpdate
	threshold time.Duration

	mu       sync.Mutex
	since    time.Time // zero => not awaiting the provider
	fired    bool
	stopped  bool
	stopOnce sync.Once
	done     chan struct{}
	finished chan struct{}
}

func newStallWatch(out chan<- StreamUpdate, threshold time.Duration) *stallWatch {
	w := &stallWatch{
		out:       out,
		threshold: threshold,
		done:      make(chan struct{}),
		finished:  make(chan struct{}),
	}
	go w.run()
	return w
}

func (w *stallWatch) run() {
	defer close(w.finished)
	t := time.NewTicker(providerStallPoll)
	defer t.Stop()
	for {
		select {
		case <-w.done:
			return
		case <-t.C:
			w.mu.Lock()
			if w.stopped || w.fired || w.since.IsZero() {
				w.mu.Unlock()
				continue
			}
			waited := time.Since(w.since)
			if waited < w.threshold {
				w.mu.Unlock()
				continue
			}
			w.fired = true
			w.mu.Unlock()
			// Sent while holding no lock; Stop() waits for this
			// goroutine to finish before the pump closes `out`.
			w.emit(&ProviderStallNotice{
				Stalled:        true,
				WaitingSeconds: int(waited.Seconds()),
			})
		}
	}
}

func (w *stallWatch) emit(n *ProviderStallNotice) {
	select {
	case <-w.done:
	case w.out <- StreamUpdate{ProviderStall: n}:
	}
}

// BeginAwait starts (or restarts) the clock: the pump is now blocked on
// provider output.
func (w *stallWatch) BeginAwait() {
	w.mu.Lock()
	w.since = time.Now()
	w.mu.Unlock()
}

// EndAwait stops the clock — the pump is doing something else (running a
// tool). Does NOT clear a fired stall: recovery is declared by real
// output arriving, which is Activity().
func (w *stallWatch) EndAwait() {
	w.mu.Lock()
	w.since = time.Time{}
	w.mu.Unlock()
}

// Activity records provider output. Restarts the clock and, if a stall
// was announced, tells the client it is over so the notice clears itself
// exactly like the byte-based path does.
func (w *stallWatch) Activity() {
	w.mu.Lock()
	w.since = time.Now()
	recovered := w.fired
	w.fired = false
	w.mu.Unlock()
	if recovered {
		w.emit(&ProviderStallNotice{Stalled: false})
	}
}

// Stop ends the watcher and returns only once its goroutine can no longer
// send, so the pump may safely close the channel afterwards.
func (w *stallWatch) Stop() {
	w.stopOnce.Do(func() {
		w.mu.Lock()
		w.stopped = true
		w.mu.Unlock()
		close(w.done)
	})
	<-w.finished
}

func (s *MessageService) pumpStreamWithTools(in streamPumpInput) {
	defer close(in.out)
	// Registered AFTER close(in.out): defers run LIFO, so the watcher is
	// stopped (and proven silent) before the channel closes.
	stall := newStallWatch(in.out, providerStallAfter)
	defer stall.Stop()
	// The first round is already in flight when the pump starts — we are
	// awaiting its first frame right now.
	stall.BeginAwait()

	// Phase 8 Task 2 — let a long-running tool report progress on the
	// SAME channel the pump owns (run_python's code+output card). Wired
	// here rather than at the three SendStream call sites so the emitter
	// can never be bound to a different channel than `out`, and so it is
	// nil on the non-streaming path (where no SSE consumer exists).
	// Safe to send: `out` is only closed by the deferred close above,
	// which runs after every tool call has returned.
	in.toolExecCtx.Emit = func(u StreamUpdate) { in.out <- u }

	current := in.initialStream
	req := in.initialReq
	var lastUsage *GatewayChatUsage

	lastServedModel := "" // FIX35 Part B — dedupe the served-model forward
	for outerIter := 0; outerIter < maxToolIterations; outerIter++ {
		var acc []GatewayToolCall
		finishReason := ""
		isToolCallStream := false

		for {
			ev, err := current.Next()
			if errors.Is(err, io.EOF) {
				break
			}
			if err != nil {
				in.out <- StreamUpdate{Err: err}
				_ = current.Close()
				return
			}
			// Any frame from the provider — content, reasoning, tool-call
			// delta, even a bare usage frame — is proof it is alive, and
			// clears a stall notice if one went out.
			stall.Activity()
			if ev.Usage != nil {
				lastUsage = ev.Usage
			}
			// Phase 4A.2.2 — internal DLP canonical events: feed the
			// request-local state machine and DO NOT forward (never a
			// browser delta, never display content, never a token).
			if in.canon != nil {
				if ev.CanonicalCapability != nil {
					in.canon.OnCapability(ev.CanonicalCapability)
				}
				if ev.CanonicalFinal != nil {
					in.canon.OnFinal(ev.CanonicalFinal)
				}
				if ev.CanonicalUnavailable != nil {
					in.canon.OnUnavailable(ev.CanonicalUnavailable)
				}
			}
			if ev.CanonicalCapability != nil || ev.CanonicalFinal != nil ||
				ev.CanonicalUnavailable != nil {
				continue
			}
			if len(ev.ToolCallDeltas) > 0 {
				acc = accumulateToolCallDeltas(acc, ev.ToolCallDeltas)
			}
			if ev.FinishReason != "" {
				finishReason = ev.FinishReason
			}
			if ev.Delta != "" {
				in.out <- StreamUpdate{
					Delta:        ev.Delta,
					Usage:        ev.Usage,
					FinishReason: ev.FinishReason,
				}
			}
			// T4 reasoning frames — forwarded as separate updates so
			// the handler can emit dedicated SSE event verbs. The
			// Gateway never combines reasoning text with normal
			// content text in the same frame, so distinct updates
			// here preserve causal ordering on the wire.
			if ev.ReasoningDelta != "" {
				in.out <- StreamUpdate{ReasoningDelta: ev.ReasoningDelta}
			}
			if ev.AnalysisStatus != "" {
				in.out <- StreamUpdate{AnalysisStatus: ev.AnalysisStatus}
			}
			// B1 / B2 — forward provider-error + fallback frames straight
			// through. Never swallow: an error frame the pump drops is a
			// blank screen on the client.
			if ev.ProviderError != nil {
				in.out <- StreamUpdate{ProviderError: ev.ProviderError}
			}
			if ev.FallbackInfo != nil {
				in.out <- StreamUpdate{FallbackInfo: ev.FallbackInfo}
			}
			// FIX35 Part B — forward the served model the first time the
			// gateway names it (and again only if it changes, e.g. a
			// mid-turn fallback), never per chunk.
			if ev.ServedModel != "" && ev.ServedModel != lastServedModel {
				lastServedModel = ev.ServedModel
				in.out <- StreamUpdate{ServedModel: ev.ServedModel}
			}
			if ev.ReasoningNotice != nil {
				in.out <- StreamUpdate{ReasoningNotice: ev.ReasoningNotice}
			}
			if ev.Done {
				// finish_reason="tool_calls" means another round.
				// Anything else (stop / length / cancelled) ends.
				if finishReason == "tool_calls" && in.toolsAttached && len(acc) > 0 {
					isToolCallStream = true
					break
				}
				in.out <- StreamUpdate{Done: true, Usage: ev.Usage}
				_ = current.Close()
				return
			}
		}
		_ = current.Close()

		if !isToolCallStream {
			// Stream ended without a Done event (EOF path) — emit a
			// synthetic Done so the handler's terminator fires.
			in.out <- StreamUpdate{Done: true, Usage: lastUsage}
			return
		}

		// Dispatch tools + extend the request for the next round.
		// Emit phase markers for the tools the frontend shows progress
		// for, before they run.
		for _, tc := range acc {
			switch tc.Function.Name {
			case "generate_image":
				in.out <- StreamUpdate{ImageStatus: "started"}
			case "generate_video":
				in.out <- StreamUpdate{VideoStatus: "started"}
			case "web_search":
				in.out <- StreamUpdate{SearchStatus: "searching"}
			case "web_fetch":
				// Reuses the web-search indicator — same "reading the
				// web" affordance, no new SSE event type needed.
				in.out <- StreamUpdate{SearchStatus: "searching"}
			}
		}
		// Tool execution is legitimately silent — a sandboxed run_python
		// call may hold the turn for a minute. Stop the clock so the
		// watcher cannot mistake work for a stall; restart it once we are
		// waiting on the model again.
		stall.EndAwait()
		asstToolMsg, results := s.executeToolCalls(in.streamCtx, acc, in.toolExecCtx)
		req.Messages = append(req.Messages, asstToolMsg)
		req.Messages = append(req.Messages, results...)

		// Opening the next round IS waiting on the provider (this call
		// blocks until the upstream response headers arrive).
		stall.BeginAwait()
		next, err := s.gateway.SendChatCompletionStream(in.streamCtx, in.callCtx, req)
		if err != nil {
			in.out <- StreamUpdate{Err: err}
			return
		}
		current = next
	}

	// Hit the iteration cap — close the current stream and emit Done.
	_ = current.Close()
	in.out <- StreamUpdate{Done: true, Usage: lastUsage}
}

// hasToolCalls reports whether a non-stream gateway response wants
// us to dispatch tools. Defensive against nil + empty choices so a
// surprise upstream shape doesn't panic the message flow.
func hasToolCalls(r *GatewayChatResponse) bool {
	if r == nil || len(r.Choices) == 0 {
		return false
	}
	return len(r.Choices[0].Message.ToolCalls) > 0
}

// toolCallsOf is the matching extractor — returns nil-safe slice.
func toolCallsOf(r *GatewayChatResponse) []GatewayToolCall {
	if !hasToolCalls(r) {
		return nil
	}
	return r.Choices[0].Message.ToolCalls
}

// executeToolCalls runs every accumulated tool call against the
// executor and returns (assistantToolMsg, []toolResultMsgs).
//
// assistantToolMsg is the synthetic assistant message that carries
// the tool_calls — appended to the running history so the model's
// next turn has the call/response pair available.
//
// toolResultMsgs is one role="tool" message per tool call, in
// the order the model emitted them.
func (s *MessageService) executeToolCalls(
	ctx context.Context, toolCalls []GatewayToolCall, ec ToolExecContext,
) (GatewayMessage, []GatewayMessage) {
	asstMsg := GatewayMessage{
		Role:      "assistant",
		Content:   "",
		ToolCalls: toolCalls,
	}
	results := make([]GatewayMessage, 0, len(toolCalls))
	for _, tc := range toolCalls {
		// FIX16 §1.4 — cloud tool boundary. On a cloud run (CloudToolAllow
		// non-nil) a call to a tool outside the allowlist is REFUSED here:
		// the tool never executes, the model gets a plain result saying so,
		// and the run is flagged tool_withheld. Withheld tools aren't even
		// offered (attachTools filters them), so this is belt-and-braces for
		// a fabricated/leaked call — but it is the hard guarantee that an
		// unattended turn cannot reach a spend/write tool.
		if ec.CloudToolAllow != nil && !ec.CloudToolAllow[tc.Function.Name] {
			if ec.WithheldHit != nil {
				*ec.WithheldHit = true
			}
			slog.Warn("cloud run refused out-of-allowlist tool",
				"tool", tc.Function.Name, "conversation_id", ec.ConversationID.String())
			results = append(results, GatewayMessage{
				Role:       "tool",
				ToolCallID: tc.ID,
				Name:       tc.Function.Name,
				Content:    "This tool is not available in an unattended scheduled run. Answer without it, and note that it was unavailable.",
			})
			continue
		}
		// Generic activity frames for tools with no dedicated event —
		// the tool logic itself is untouched, this only brackets it.
		// Suppressed for tools with their own event AND for
		// silent-by-design bookkeeping tools (see the sets above).
		generic := !dedicatedEventTools[tc.Function.Name] &&
			!silentByDesignTools[tc.Function.Name]
		if generic {
			ec.emit(StreamUpdate{ToolActivity: &ToolActivityUpdate{
				ID: tc.ID, Tool: tc.Function.Name, Phase: "started",
			}})
		}
		out := s.toolExec.Execute(ctx, tc, ec)
		if generic {
			ec.emit(StreamUpdate{ToolActivity: &ToolActivityUpdate{
				ID: tc.ID, Tool: tc.Function.Name, Phase: "finished",
			}})
		}
		results = append(results, GatewayMessage{
			Role:       "tool",
			ToolCallID: tc.ID,
			Name:       tc.Function.Name,
			Content:    out,
		})
	}
	return asstMsg, results
}

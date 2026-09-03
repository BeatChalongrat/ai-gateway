// Message handlers — list history + send (stream + non-stream).
//
// The send path is the privacy-sensitive code in the service. The
// contract:
//   - Persist the user's real text via MessageService.SendStream /
//     SendNonStream BEFORE the gateway call.
//   - Forward each gateway content delta to the browser as an SSE
//     “content_delta“ event. The handler never logs the deltas.
//   - On completion, MessageService persists the accumulated text;
//     this handler never writes content to the DB directly.
package handler

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type MessageHandler struct {
	messages *service.MessageService
	// Optional. When set, the list endpoint joins file metadata
	// into each message's response. Construction in main.go wires
	// this immediately after the file repo is built.
	files *repository.FileRepo
	// Optional. When set, the list endpoint joins S7 context-source
	// attribution onto each assistant message via a single batched
	// query (no N+1).
	sources *repository.ResponseContextSourceRepo
}

func NewMessageHandler(m *service.MessageService) *MessageHandler {
	return &MessageHandler{messages: m}
}

// SetFileRepo wires in the file repo after construction so list
// responses can embed attachments. Optional — when nil, the
// "files" field is simply omitted from the wire response.
func (h *MessageHandler) SetFileRepo(fr *repository.FileRepo) {
	h.files = fr
}

// SetSourceRepo wires the S7 attribution repo so the list
// endpoint can render each assistant message's "Sources used"
// card without a per-message round-trip.
func (h *MessageHandler) SetSourceRepo(sr *repository.ResponseContextSourceRepo) {
	h.sources = sr
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type messageResponse struct {
	ID         string      `json:"id"`
	Role       string      `json:"role"`
	Content    string      `json:"content"`
	Status     string      `json:"status"`
	ModelID    *string     `json:"model_id,omitempty"`
	TokenUsage *tokenUsage `json:"token_usage,omitempty"`
	CreatedAt  string      `json:"created_at"`
	// Auto Thinking Mode — the model's thinking summary for this
	// assistant turn (masked, persisted in migration 019). nil for
	// user turns and assistant turns that didn't think, so the wire
	// stays unchanged for non-reasoning messages. Lets the
	// ThinkingBlock re-render after a page reload (the live SSE
	// reasoning_delta stream is gone once the turn completes).
	ReasoningContent *string         `json:"reasoning_content,omitempty"`
	SearchSources    json.RawMessage `json:"search_sources,omitempty"`
	// ClientID is the desktop's own message id for rows that arrived via
	// the desktop import endpoints (FIX 9). The desktop uses it to
	// correlate a server row back to its local copy; nil / omitted for
	// native chat-edge rows so the web wire is unchanged.
	ClientID    *string        `json:"client_id,omitempty"`
	Files       []fileResponse `json:"files,omitempty"`
	BranchIndex int            `json:"branch_index"`
	BranchCount int            `json:"branch_count"`
	HasBranches bool           `json:"has_branches"`
	// S7 — populated only on assistant messages that have at least
	// one context source recorded. Empty / nil for user messages
	// and for assistants that drew on no special context (memory,
	// state, search, compaction). omitempty keeps the wire size
	// honest.
	ContextSources []contextSourceResponse `json:"context_sources,omitempty"`
}

// contextSourceResponse mirrors model.ResponseContextSource on the
// wire. Defined here (handler package) rather than reused from
// model so the metadata JSON stays a typed map[string]any for the
// frontend's convenience.
type contextSourceResponse struct {
	ID           string         `json:"id"`
	SourceType   string         `json:"source_type"`
	SourceID     *string        `json:"source_id,omitempty"`
	DisplayLabel string         `json:"display_label"`
	Metadata     map[string]any `json:"metadata,omitempty"`
	CreatedAt    string         `json:"created_at"`
}

type tokenUsage struct {
	Input  int `json:"input"`
	Output int `json:"output"`
}

// sourceToWire formats one S7 source row for the wire response.
// Metadata is decoded into a generic map so the frontend gets
// typed access to category / conversation_title / hit_type without
// re-parsing JSON on the client.
func sourceToWire(s *model.ResponseContextSource) contextSourceResponse {
	out := contextSourceResponse{
		ID:           s.ID.String(),
		SourceType:   s.SourceType,
		DisplayLabel: s.DisplayLabel,
		CreatedAt:    s.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
	}
	if s.SourceID != nil {
		sid := s.SourceID.String()
		out.SourceID = &sid
	}
	if len(s.Metadata) > 0 {
		var meta map[string]any
		if err := json.Unmarshal(s.Metadata, &meta); err == nil {
			out.Metadata = meta
		}
	}
	return out
}

func messageToWire(m *model.Message) messageResponse {
	r := messageResponse{
		ID:          m.ID.String(),
		Role:        m.Role,
		Content:     m.ContentMasked,
		Status:      m.Status,
		ModelID:     m.ModelID,
		CreatedAt:   m.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
		BranchIndex: m.BranchIndex,
		BranchCount: 1,
		ClientID:    m.ClientID,
	}
	if m.ReasoningContent != nil && *m.ReasoningContent != "" {
		r.ReasoningContent = m.ReasoningContent
	}
	// Web-search citations — forward the stored JSON array verbatim so
	// the frontend renders citation chips on reload.
	if len(m.SearchSources) > 0 {
		r.SearchSources = json.RawMessage(m.SearchSources)
	}
	if m.TokenUsageInput != nil && m.TokenUsageOutput != nil {
		r.TokenUsage = &tokenUsage{
			Input:  *m.TokenUsageInput,
			Output: *m.TokenUsageOutput,
		}
	}
	return r
}

// ---------------------------------------------------------------------------
// List messages
// ---------------------------------------------------------------------------

func (h *MessageHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	cursor := parseUUID(r.URL.Query().Get("before"))

	msgs, hasMore, err := h.messages.ListMessages(
		r.Context(), caller.TenantUUID, caller.UserID, convID, limit, cursor,
	)
	if err != nil {
		if errors.Is(err, service.ErrMessageConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	items := make([]messageResponse, 0, len(msgs))
	for i := range msgs {
		items = append(items, messageToWire(&msgs[i]))
	}

	// Phase 4A.2.4 — display lifecycle. When enabled, the persisted
	// content_masked is no longer the restored raw display, so the reload read
	// goes through the owner restore-on-read policy: restored (Vault V2) or a
	// tokenized/redacted fallback. No-op when the feature is off (items keep
	// the legacy content_masked passthrough).
	if h.messages.DisplayLifecycleEnabled() {
		actor := service.DisplayActor{UserID: caller.UserID, TenantID: caller.TenantUUID}
		for i := range msgs {
			rep := h.messages.ResolveRepresentation(
				r.Context(), service.ConsumerBrowserReloadOwner, msgs[i], actor,
			)
			items[i].Content = rep.Content
		}
	}

	// Branch counts. One batched query per parent_id; messages
	// without parent_id stay at the default branch_count=1.
	parentIDs := make([]uuid.UUID, 0)
	seen := map[uuid.UUID]bool{}
	for i := range msgs {
		if msgs[i].ParentID != nil && !seen[*msgs[i].ParentID] {
			seen[*msgs[i].ParentID] = true
			parentIDs = append(parentIDs, *msgs[i].ParentID)
		}
	}
	if len(parentIDs) > 0 {
		counts, err := h.messages.BranchCountsByParents(r.Context(), caller.TenantUUID, parentIDs)
		if err == nil {
			for i := range msgs {
				if msgs[i].ParentID == nil {
					continue
				}
				if c, ok := counts[*msgs[i].ParentID]; ok && c > 1 {
					items[i].BranchCount = c
					items[i].HasBranches = true
				}
			}
		}
	}

	// S7 — Join context-source attribution for assistant messages.
	// One batched query for every assistant id; skipped silently
	// when the source repo isn't wired so test/legacy setups keep
	// working without the column.
	if h.sources != nil && len(msgs) > 0 {
		asstIDs := make([]uuid.UUID, 0, len(msgs))
		for i := range msgs {
			if msgs[i].Role == "assistant" {
				asstIDs = append(asstIDs, msgs[i].ID)
			}
		}
		if len(asstIDs) > 0 {
			byMsg, err := h.sources.ListByMessages(r.Context(), asstIDs, caller.UserID, caller.TenantUUID)
			if err == nil {
				for i := range items {
					if srcs, ok := byMsg[msgs[i].ID]; ok && len(srcs) > 0 {
						wire := make([]contextSourceResponse, 0, len(srcs))
						for j := range srcs {
							wire = append(wire, sourceToWire(&srcs[j]))
						}
						items[i].ContextSources = wire
					}
				}
			}
		}
	}

	// Join file attachments. Skipped silently if the file repo
	// isn't wired (unit tests / older deployments) — the wire
	// response just omits ``files`` per message.
	if h.files != nil && len(msgs) > 0 {
		ids := make([]uuid.UUID, 0, len(msgs))
		for i := range msgs {
			ids = append(ids, msgs[i].ID)
		}
		byMsg, err := h.files.ListByMessageIDs(r.Context(), caller.TenantUUID, ids)
		if err == nil {
			for i := range items {
				if attached, ok := byMsg[msgs[i].ID]; ok {
					out := make([]fileResponse, 0, len(attached))
					for j := range attached {
						out = append(out, FileToWire(&attached[j]))
					}
					items[i].Files = out
				}
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"messages": items,
		"has_more": hasMore,
	})
}

// ---------------------------------------------------------------------------
// Send (stream OR non-stream depending on the request body's `stream` flag)
// ---------------------------------------------------------------------------

type sendRequest struct {
	Content string   `json:"content"`
	Stream  bool     `json:"stream"`
	FileIDs []string `json:"file_ids,omitempty"`
	// ImageMode (Phase C) — set by the web-chat 🎨 composer toggle.
	// Biases a tool-capable model to call generate_image instead of
	// replying in text. Ignored when the model has no tools.
	ImageMode bool `json:"image_mode,omitempty"`
}

func (h *MessageHandler) Send(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req sendRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	in := service.SendInput{
		TenantID:       caller.TenantUUID,
		UserID:         caller.UserID,
		ConversationID: convID,
		Content:        req.Content,
		RequestID:      r.Header.Get("X-Request-Id"),
		ImageMode:      req.ImageMode,
	}
	if in.RequestID == "" {
		in.RequestID = uuid.NewString()
	}
	if len(req.FileIDs) > 0 {
		in.FileIDs = make([]uuid.UUID, 0, len(req.FileIDs))
		for _, s := range req.FileIDs {
			id, err := uuid.Parse(s)
			if err != nil {
				WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
				return
			}
			in.FileIDs = append(in.FileIDs, id)
		}
	}
	if req.Stream {
		h.sendStream(w, r, in)
		return
	}
	h.sendNonStream(w, r, in)
}

// ---------------------------------------------------------------------------
// Non-stream path
// ---------------------------------------------------------------------------

func (h *MessageHandler) sendNonStream(w http.ResponseWriter, r *http.Request, in service.SendInput) {
	res, err := h.messages.SendNonStream(r.Context(), in)
	if err != nil {
		h.writeSendError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"message": messageToWire(res.AssistantMessage),
	})
}

// ---------------------------------------------------------------------------
// Stream path (SSE)
// ---------------------------------------------------------------------------

// sendStream opens the gateway stream, forwards each delta to the
// browser, and persists the accumulated text on completion.
//
// SSE event format (per Phase 1 contract):
//
//	event: message_start
//	data: {"message_id":"uuid","conversation_id":"uuid"}
//
//	event: content_delta
//	data: {"delta":"text"}
//
//	event: message_complete
//	data: {"message_id":"uuid","token_usage":{"input":N,"output":N}}
//
//	event: error
//	data: {"code":"PROVIDER_UNAVAILABLE","message":"..."}
//
// The handler MUST flush after every event so the browser sees
// incremental output; chi's default ResponseWriter implements
// http.Flusher.

// streamDrainResult carries what drainStreamEvents accumulated so
// the caller can persist the turn (StreamHandle.Finish) and emit its
// message_complete terminator.
type streamDrainResult struct {
	content      string
	reasoning    string
	finishReason string
	usage        *service.GatewayChatUsage
	err          error
	// servedModel (FIX35 Part B) — the model that actually answered, as
	// the gateway named it; "" when the stream never said.
	servedModel string
	// FIX41 — what to put in the TERMINAL error frame. The drain no
	// longer writes its own error frame: a failed run has to END on
	// ``error``, and it cannot if the drain spends that event mid-stream
	// and the handler then appends message_complete after it (which is
	// what P4 measured: the last frame of a failed turn was the success
	// terminator).
	errCode     string
	errMessage  string
	errDetails  any
	errAttempts any
	// errRetryable is the gateway's retryable verdict, forwarded as-is.
	errRetryable *bool
}

// terminalErrorPayload builds the terminal “error“ frame's data from
// whatever the drain learned. Falls back to a generic internal error so a
// failed run always carries a code the client can localise.
func (res streamDrainResult) terminalErrorPayload() map[string]any {
	code, msg := res.errCode, res.errMessage
	if code == "" {
		code = CodeInternalError
	}
	if msg == "" {
		msg = "the stream ended without a result"
		if res.err != nil {
			msg = fmt.Sprintf("stream error: %v", res.err)
		}
	}
	out := map[string]any{"code": code, "message": msg}
	if res.errDetails != nil {
		out["details"] = res.errDetails
	}
	if res.errAttempts != nil {
		out["attempts"] = res.errAttempts
	}
	if res.errRetryable != nil {
		out["retryable"] = *res.errRetryable
	}
	return out
}

// completeWithModel adds the served model to a “message_complete“
// payload (FIX35 Part B) so the client's per-message meta line can name
// the model an "auto" turn was answered by without a reload.
func completeWithModel(completeData map[string]any, servedModel string) {
	if servedModel != "" {
		completeData["model_id"] = servedModel
	}
}

// drainStreamEvents forwards service stream updates to the browser
// as SSE frames until the events channel yields Done, an error, or
// closes. Shared by sendStream, Regenerate and Edit — the three
// loops were byte-identical.
//
// “done“ is the request context's Done channel. When it fires
// (client disconnected) the loop invokes “onDisconnect“ ONCE and
// then sets “done“ to nil: a cancelled context's Done channel is
// permanently ready in select, so continuing to select on it would
// busy-spin at 100% CPU until the service pump emits its terminal
// event. A nil channel blocks forever in select, so after the first
// firing only “events“ drives the drain. The drain always
// terminates: pumpStreamWithTools sends a terminal update (Err or
// Done) and defer-closes the channel on every exit path.
//
// Phase 5 — what “onDisconnect“ should be:
//   - primary streams (Send/Regenerate/Edit): “detachOnDisconnect“.
//     The generation KEEPS RUNNING server-side (writes to the dead
//     ResponseWriter fail silently), the drain still accumulates the
//     full content, and Finish persists it — so a refreshed client
//     can reattach via GET .../messages/{id}/stream and receive the
//     rest. The user-initiated Stop path is unaffected: it goes
//     through POST /cancel → CancelStream, never through this hook.
//   - resume streams: the broker unsubscribe — detaching a viewer
//     must never touch the generation itself.
//
// detachOnDisconnect is the Phase 5 disconnect hook for primary
// streams: do nothing — let the generation finish server-side so the
// client can resume. (Pre-Phase-5 behaviour was stream.Cancel here,
// which killed the generation on every refresh/network blip.)
func detachOnDisconnect() {}

// heartbeatInterval is how often an SSE comment frame (": ping") goes
// out while a generation is in flight with no real event to send
// (Batch 2 T2). Two jobs: intermediate proxies stop idle-killing
// quiet streams (long tool runs), and the clients' idle watchdogs get
// a liveness signal to reset on — so the watchdog fires only on a dead
// connection, a hung chat-edge, or a buffering proxy. A stalled
// UPSTREAM is NOT covered: the ticker fires during any quiet stretch,
// including one where the provider has gone silent, so the client
// clock keeps resetting and the stall stays invisible (verified live,
// Batch 2 A2: 130s provider pause → pings every 15s, no pill).
// Detecting that needs upstream-activity tracking in the pump —
// tracked as the provider-stall-detection follow-up.
// Comment frames are ignored by both clients' parsers (web
// lib/stream.ts parseFrame yields nothing without a data: line;
// desktop SseStreamReader skips lines starting with ':') and are
// written at the HTTP edge ONLY — they never enter the stream broker,
// so resume replays stay heartbeat-free by construction, and they
// never touch the persistence/orphan-settlement clocks, which read
// database timestamps.
// A var (not const) purely so the stream tests can shorten it.
var heartbeatInterval = 15 * time.Second

// writeSSEHeartbeat emits the comment frame. Failures are silently
// dropped like writeSSEEvent's — a dead connection is detected by the
// request context, not by write errors.
func writeSSEHeartbeat(w http.ResponseWriter, flusher http.Flusher) {
	_, _ = fmt.Fprint(w, ": ping\n\n")
	flusher.Flush()
}

func drainStreamEvents(
	w http.ResponseWriter,
	flusher http.Flusher,
	done <-chan struct{},
	events <-chan service.StreamUpdate,
	onDisconnect func(),
) streamDrainResult {
	var (
		content   strings.Builder
		reasoning strings.Builder
		res       streamDrainResult
	)
	// T2 — keepalive ticker. Fires only when neither `done` nor
	// `events` is ready, i.e. exactly the quiet stretches (slow
	// provider, long tool run) that need a heartbeat. Stops with the
	// drain; writes to an already-disconnected client fail silently.
	heartbeat := time.NewTicker(heartbeatInterval)
	defer heartbeat.Stop()
Loop:
	for {
		select {
		case <-heartbeat.C:
			writeSSEHeartbeat(w, flusher)
		case <-done:
			// Client disconnected — fire the disconnect hook once,
			// keep draining until the pump's terminal event (see doc
			// comment: nil channel = no busy-spin).
			onDisconnect()
			done = nil
		case ev, ok := <-events:
			if !ok {
				break Loop
			}
			if ev.Err != nil {
				// FIX41: record it, do NOT spend the ``error`` event
				// here. The caller emits exactly one terminal after the
				// files/sources read-back, and it may not be ``error`` at
				// all — a user pressing Stop arrives on this same branch
				// as "context canceled", and reporting that to every
				// other viewer as an internal error was P3's finding.
				res.err = ev.Err
				res.errMessage = fmt.Sprintf("stream error: %v", ev.Err)
				break Loop
			}
			if ev.Delta != "" {
				content.WriteString(ev.Delta)
				writeSSEEvent(w, flusher, "content_delta", map[string]any{
					"delta": ev.Delta,
				})
			}
			// T4: reasoning delta + analysis status forwarded as
			// dedicated SSE event verbs so the frontend can render
			// the ThinkingBlock without parsing inside content_delta.
			if ev.ReasoningDelta != "" {
				reasoning.WriteString(ev.ReasoningDelta)
				writeSSEEvent(w, flusher, "reasoning_delta", map[string]any{
					"delta": ev.ReasoningDelta,
				})
			}
			if ev.AnalysisStatus != "" {
				writeSSEEvent(w, flusher, "analysis_status", map[string]any{
					"status": ev.AnalysisStatus,
				})
			}
			// Image-generation phase marker — the frontend swaps the
			// typing dots for an image placeholder until message_files
			// (or a terminal frame) arrives.
			if ev.ImageStatus != "" {
				writeSSEEvent(w, flusher, "image_generating", map[string]any{
					"status": ev.ImageStatus,
				})
			}
			if ev.VideoStatus != "" {
				writeSSEEvent(w, flusher, "video_generating", map[string]any{
					"status": ev.VideoStatus,
				})
			}
			// Web-search phase marker — the frontend shows a "searching
			// the web" indicator until the answer streams / sources land.
			if ev.SearchStatus != "" {
				writeSSEEvent(w, flusher, "search_status", map[string]any{
					"status": ev.SearchStatus,
				})
			}
			// FIX33 A-W9 — compaction phase marker ("summarizing").
			if ev.CompactionStatus != "" {
				writeSSEEvent(w, flusher, "compaction_status", map[string]any{
					"status": ev.CompactionStatus,
				})
			}
			// Phase 8 Task 2 — run_python progress: the code the model
			// wrote, then its stdout/stderr + exit status. Rendered as a
			// code+output card. Deliberately NOT persisted on the message
			// row: chat-edge stores DLP-masked content only and has no
			// masking path of its own, so writing raw sandbox stdout at
			// rest would open a new plaintext surface. It survives a
			// mid-stream reconnect via the resume snapshot below; after
			// the turn completes only the produced files remain.
			if ev.CodeExecution != nil {
				writeSSEEvent(w, flusher, "code_execution", ev.CodeExecution)
			}
			// Generic per-tool activity (tools without a dedicated
			// event). Ephemeral like code_execution: not persisted,
			// survives mid-stream reconnects via the resume snapshot.
			if ev.ToolActivity != nil {
				writeSSEEvent(w, flusher, "tool_activity", ev.ToolActivity)
			}
			// B1: a provider error frame from the Gateway. Forward it as
			// an ``error`` event so the client renders an error bubble
			// rather than ending blank. Not a ``break`` — the Gateway
			// closes the stream after, which yields the synthetic Done +
			// message_complete terminator in the caller.
			if ev.ProviderError != nil {
				// FIX41: same as ev.Err — carried to the terminal frame
				// instead of being spent here, so the run ends on
				// ``error`` and the client never has to guess whether a
				// mid-stream error frame was the end or not. The cost is
				// that the error surfaces one read-back later (a DB round
				// trip on a turn that produced no files anyway); the
				// benefit is one unambiguous terminal.
				res.err = fmt.Errorf("provider error: %s", ev.ProviderError.Code)
				res.errCode = ev.ProviderError.Code
				res.errMessage = ev.ProviderError.Message
				if ev.ProviderError.Details != nil {
					res.errDetails = ev.ProviderError.Details
				}
				if len(ev.ProviderError.Attempts) > 0 {
					res.errAttempts = ev.ProviderError.Attempts
				}
				res.errRetryable = ev.ProviderError.Retryable
			}
			// B2: a fallback notice — which model actually answered.
			if ev.FallbackInfo != nil {
				writeSSEEvent(w, flusher, "fallback_info", ev.FallbackInfo)
			}
			// FIX50 P1-C: the question was partially masked before the
			// model read it — counts only, never values.
			if ev.DlpNotice != nil {
				writeSSEEvent(w, flusher, "dlp_notice", ev.DlpNotice)
			}
			// FIX35 Part B — which model IS answering, as soon as the
			// gateway names it (before content on an auto /
			// openrouter/auto route), not only at the end.
			if ev.ServedModel != "" {
				res.servedModel = ev.ServedModel
				writeSSEEvent(w, flusher, "model_resolved", map[string]any{"model": ev.ServedModel})
			}
			// Reasoning downgrade notice — informational, never an
			// error: the answer arrived without the requested thinking.
			if ev.ReasoningNotice != nil {
				writeSSEEvent(w, flusher, "reasoning_notice", ev.ReasoningNotice)
			}
			// Provider stall / recovery. Unlike the heartbeat (an HTTP-edge
			// comment frame), this is a real typed event: only chat-edge
			// can tell provider silence from server work, so it says so
			// explicitly instead of leaving the client to guess from bytes.
			if ev.ProviderStall != nil {
				writeSSEEvent(w, flusher, "provider_stall", ev.ProviderStall)
			}
			// One or more attached images couldn't be read — informational,
			// never an error: the turn proceeds text-only for those images.
			if ev.ImageNotice != nil {
				writeSSEEvent(w, flusher, "image_notice", ev.ImageNotice)
			}
			if ev.FinishReason != "" {
				res.finishReason = ev.FinishReason
			}
			if ev.Usage != nil {
				res.usage = ev.Usage
			}
			if ev.Done {
				break Loop
			}
		}
	}
	res.content = content.String()
	res.reasoning = reasoning.String()
	return res
}

func (h *MessageHandler) sendStream(w http.ResponseWriter, r *http.Request, in service.SendInput) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}

	// FIX41 — the run id is minted HERE, not in the service, because the
	// early-open path below writes frames before SendStream returns. One
	// id, one seq counter, one ledger for the whole run.
	runID := uuid.NewString()
	in.RunID = runID
	rw := newRunWriter(w, runID, nil)
	// Whatever happens below, the run ends on a terminal frame. A `return`
	// that leaves the pipe silent is not a valid ending (STREAM_CONTRACT
	// §3) — and there were two such returns before FIX41.
	defer rw.ensureTerminal(flusher)

	// SSE headers — must be set BEFORE any write, and BEFORE the
	// first flush. Factored so the FIX33 A-W9 early-open path below and
	// the normal path write them exactly once.
	started := false
	openStream := func(assistantID, userMessageID uuid.UUID) {
		if started {
			return
		}
		started = true
		rw.started = true
		// Register the run with the resume broker now, so the frames
		// written during the early-open pause are numbered by the same
		// counter as the ones the pump will produce.
		h.messages.OpenRun(assistantID, runID)
		rw.record = func(event string, build func(seq, final int64) []byte) (int64, bool) {
			return h.messages.RecordFrame(assistantID, event, build)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.Header().Set("X-Accel-Buffering", "no")
		w.WriteHeader(http.StatusOK)
		// FIX28 §2 (F3): the client renders the user turn under an optimistic
		// temp id; hand it the persisted row id here (Edit already did) so an
		// edit → fork can name a real message without a reload.
		startPayload := map[string]any{
			"message_id":      assistantID.String(),
			"conversation_id": in.ConversationID.String(),
		}
		if userMessageID != uuid.Nil {
			startPayload["user_message_id"] = userMessageID.String()
		}
		writeSSEEvent(rw, flusher, "message_start", startPayload)
	}
	// FIX33 A-W9 — when the compiler is about to run the synchronous
	// state extraction (a pause of several seconds), open the stream NOW
	// and tell the user the conversation is being organised. Without
	// this the first byte only arrives after the extraction, and the
	// pause looks like a hang.
	in.OnCompactionStart = func(assistantID, userMessageID uuid.UUID) {
		openStream(assistantID, userMessageID)
		writeSSEEvent(rw, flusher, "compaction_status", map[string]any{"status": "summarizing"})
	}

	stream, err := h.messages.SendStream(r.Context(), in)
	if err != nil {
		if started {
			// The response is already an open SSE stream: the error must
			// travel as a frame, not as a JSON status the client will never
			// parse.
			// Terminal: this run failed before it produced anything.
			writeSSEEvent(rw, flusher, "error", sendErrorFrame(err))
			return
		}
		h.writeSendError(w, err)
		return
	}

	userMessageID := uuid.Nil
	if stream.UserMessage != nil {
		userMessageID = stream.UserMessage.ID
	}
	openStream(stream.AssistantID, userMessageID)

	res := drainStreamEvents(rw, flusher, r.Context().Done(), stream.Events, detachOnDisconnect)

	// Persist via the handle's finish callback. Uses
	// context.Background() internally so the write completes even
	// after the request context is cancelled (client disconnected).
	persistFinishReason := res.finishReason
	if res.err != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.Finish(res.content, res.reasoning, persistFinishReason, res.usage)
	// FIX35 Part B — attribute the row to the model that answered.
	if res.servedModel != "" {
		_ = h.messages.SetServedModel(context.Background(), stream.AssistantID, res.servedModel)
	}
	h.finishStream(rw, flusher, r.Context(), in.TenantID, stream, res)
}

// finishStream writes the files/sources read-back and then the run's ONE
// terminal event (STREAM_CONTRACT §3). Shared by Send / Regenerate / Edit
// so all three answer the same question the same way: a run that a person
// stopped says “cancelled“, a run that failed ENDS on “error“, and
// only a run that produced its answer says “message_complete“.
//
// Before FIX41 each of the three ended unconditionally on
// message_complete, so the last frame of a failed turn was the success
// terminator and a cancel was indistinguishable from a normal finish
// (docs/fix41/A-baseline.md, P3 and P4).
func (h *MessageHandler) finishStream(
	rw *runWriter,
	flusher http.Flusher,
	ctx context.Context,
	tenantID uuid.UUID,
	stream *service.StreamHandle,
	res streamDrainResult,
) {
	h.writeMessageFiles(rw, flusher, ctx, tenantID, stream.AssistantID)
	h.writeMessageSources(rw, flusher, ctx, tenantID, stream.AssistantID)

	tokens := map[string]int(nil)
	if res.usage != nil {
		tokens = map[string]int{
			"input":  res.usage.PromptTokens,
			"output": res.usage.CompletionTokens,
		}
		// T4: surface reasoning_tokens when the Gateway broke it out
		// (OpenAI o-series / Anthropic estimate / Gemini thoughts).
		if res.usage.ReasoningTokens > 0 {
			tokens["reasoning"] = res.usage.ReasoningTokens
		}
	}

	switch {
	case stream.UserCancelled():
		// A person pressed Stop. Everything produced so far stands; the
		// answer is simply partial, and the client is told so rather than
		// being left to infer it from a stream that went quiet.
		data := map[string]any{
			"message_id": stream.AssistantID.String(),
			"partial":    true,
		}
		completeWithModel(data, res.servedModel)
		if tokens != nil {
			data["token_usage"] = tokens
		}
		writeSSEEvent(rw, flusher, "cancelled", data)
	case res.err != nil:
		writeSSEEvent(rw, flusher, "error", res.terminalErrorPayload())
	default:
		data := map[string]any{"message_id": stream.AssistantID.String()}
		completeWithModel(data, res.servedModel)
		if tokens != nil {
			data["token_usage"] = tokens
		}
		writeSSEEvent(rw, flusher, "message_complete", data)
	}
}

// writeSSEEvent serialises one SSE frame and flushes. “data“ is
// JSON-marshalled inline; failures are silently dropped because at
// this point in the request we've already committed to a 200
// response — the client will see a missing frame, the SSE clean-up
// loop on the browser handles the missing terminator.
func (h *MessageHandler) writeMessageFiles(
	w http.ResponseWriter,
	flusher http.Flusher,
	ctx context.Context,
	tenantID uuid.UUID,
	messageID uuid.UUID,
) {
	if h.files == nil {
		return
	}
	byMsg, err := h.files.ListByMessageIDs(ctx, tenantID, []uuid.UUID{messageID})
	if err != nil {
		return
	}
	files := byMsg[messageID]
	if len(files) == 0 {
		return
	}
	out := make([]fileResponse, 0, len(files))
	for i := range files {
		out = append(out, FileToWire(&files[i]))
	}
	writeSSEEvent(w, flusher, "message_files", map[string]any{
		"message_id": messageID.String(),
		"files":      out,
	})
	slog.Info("sse_message_files_sent",
		"tenant_id", tenantID.String(),
		"message_id", messageID.String(),
		"file_count", len(out),
	)
}

// writeMessageSources emits the web_search citations persisted on the
// assistant message (read-back pattern matching writeMessageFiles). The
// frontend renders these as citation chips and clears the searching
// indicator. No-op when the turn didn't search.
func (h *MessageHandler) writeMessageSources(
	w http.ResponseWriter,
	flusher http.Flusher,
	ctx context.Context,
	tenantID uuid.UUID,
	messageID uuid.UUID,
) {
	raw, err := h.messages.GetSearchSources(ctx, tenantID, messageID)
	if err != nil || len(raw) == 0 {
		return
	}
	// Forward the stored JSON array verbatim under "sources".
	writeSSEEvent(w, flusher, "message_sources", map[string]any{
		"message_id": messageID.String(),
		"sources":    json.RawMessage(raw),
	})
	slog.Info("sse_message_sources_sent",
		"tenant_id", tenantID.String(),
		"message_id", messageID.String(),
	)
}

func writeSSEEvent(w http.ResponseWriter, flusher http.Flusher, event string, data any) {
	buf, err := json.Marshal(data)
	if err != nil {
		return
	}
	// FIX41 — a stream that was opened for a run writes through the
	// envelope (run_id / seq / final_seq + the ledger). Everything else
	// keeps the pre-FIX41 shape, so a caller that never wraps w — a unit
	// test, a future non-run stream — is unaffected.
	if rw, ok := w.(*runWriter); ok {
		rw.emit(flusher, event, buf)
		return
	}
	_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, buf)
	flusher.Flush()
}

// writeSendError maps service errors to HTTP responses for the
// non-stream path AND for the pre-stream error path (when
// SendStream returns before any SSE frame goes out).
// sendErrorMessage is writeSendError's message half, for the FIX33 A-W9
// case where the SSE stream is already open and the error must travel
// as a frame.
// gatewayEnvelope finds the gateway's typed error envelope in a send
// error, when the gateway answered the request with one (a 4xx before
// the stream opened: no model can serve the request's tools, the prompt
// is over the context window, a policy block). The client localises on
// the code; the status is the gateway's own.
func gatewayEnvelope(err error) (status int, env service.GatewayErrorEnvelope, ok bool) {
	var ue *service.GatewayUnavailableError
	if !errors.As(err, &ue) {
		return 0, service.GatewayErrorEnvelope{}, false
	}
	env, ok = ue.Envelope()
	return ue.Status, env, ok
}

// sendErrorFrame is the code + message for an "error" frame on a send
// that failed before the stream produced anything.
func sendErrorFrame(err error) map[string]any {
	if _, env, ok := gatewayEnvelope(err); ok {
		out := map[string]any{"code": env.Code, "message": env.Message}
		if env.Retryable != nil {
			out["retryable"] = *env.Retryable
		}
		return out
	}
	return map[string]any{"code": "send_failed", "message": sendErrorMessage(err)}
}

// writeGatewayEnvelope answers a not-yet-streaming request with the
// gateway's own status, code and message. False when there was none.
func writeGatewayEnvelope(w http.ResponseWriter, err error) bool {
	status, env, ok := gatewayEnvelope(err)
	if !ok {
		return false
	}
	if status < 400 || status > 599 {
		status = http.StatusBadGateway
	}
	WriteError(w, status, env.Code, env.Message)
	return true
}

func sendErrorMessage(err error) string {
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		return "conversation not found"
	case errors.Is(err, service.ErrMessageEmptyContent):
		return "content is required"
	case strings.Contains(err.Error(), "insufficient_credits"):
		return "เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต"
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		return err.Error()
	default:
		return "internal error"
	}
}

func (h *MessageHandler) writeSendError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrMessageEmptyContent):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "content is required")
	case strings.Contains(err.Error(), "insufficient_credits"):
		// Gateway's portal credit gate fired (cost-based deduction).
		// 402 + a stable code so the frontend shows the Thai
		// credits-exhausted notice instead of a generic provider error.
		WriteError(w, http.StatusPaymentRequired, "INSUFFICIENT_CREDITS",
			"เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		if writeGatewayEnvelope(w, err) {
			return
		}
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", "the AI service is unavailable")
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// ---------------------------------------------------------------------------
// Cancel
// ---------------------------------------------------------------------------

// Cancel interrupts the in-flight streaming generation, if any.
// Idempotent: when no stream is active the response still returns
// 200 with “cancelled=false“ so the front-end can retry without
// branching on 404 vs success.
//
//	POST /api/v1/conversations/{id}/cancel
//	→ 200 {"cancelled": true|false}
func (h *MessageHandler) Cancel(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	// Tenant-scope check: verify caller owns the conversation before
	// invoking cancel. Without this an attacker with any valid JWT
	// could DoS another tenant's streams by guessing conversation IDs.
	if err := h.messages.AssertConversationAccess(
		r.Context(), caller.TenantUUID, caller.UserID, convID,
	); err != nil {
		if errors.Is(err, service.ErrMessageConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	cancelled := h.messages.CancelStream(convID)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"cancelled": cancelled})
}

// ---------------------------------------------------------------------------
// Resume (Phase 5 — stream resume)
// ---------------------------------------------------------------------------

// ResumeStream reattaches a client to an in-flight generation and
// replays what it missed. Emits the SAME SSE event vocabulary as Send
// (message_start / content_delta / … / message_complete) so the
// frontend consumes it with the identical pipeline — the prefix simply
// arrives as one large content_delta.
//
//	GET /api/v1/conversations/{id}/messages/{messageID}/stream
//	→ SSE stream
//	→ 409 RESUME_UNAVAILABLE when the generation isn't attachable yet
//	  (client retries with backoff)
//
// This endpoint NEVER calls StreamHandle.Finish — persistence belongs
// to the primary drain (which keeps running detached after the
// original client disconnected). Disconnecting from a resume stream
// only unsubscribes the viewer; the generation is untouched.
func (h *MessageHandler) ResumeStream(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	msgID, err := uuid.Parse(chi.URLParam(r, "messageID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message id")
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}

	// FIX41 / STREAM_CONTRACT section 4 - starting_after is the client's
	// cursor: replay only what it has not already rendered. Absent (or 0)
	// keeps the pre-FIX41 behaviour of replaying the whole run, so a
	// client that has not been updated is unaffected.
	startingAfter := int64(0)
	if raw := strings.TrimSpace(r.URL.Query().Get("starting_after")); raw != "" {
		n, convErr := strconv.ParseInt(raw, 10, 64)
		if convErr != nil || n < 0 {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "starting_after must be a non-negative integer")
			return
		}
		startingAfter = n
	}

	att, err := h.messages.ResumeAttachFrom(
		r.Context(), caller.TenantUUID, caller.UserID, convID, msgID, startingAfter,
	)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrResumeUnavailable):
			WriteError(w, http.StatusConflict, "RESUME_UNAVAILABLE",
				"generation not attachable yet")
		case errors.Is(err, service.ErrResumeTooOld):
			// The frames this client is missing are gone. Saying
			// "complete" here would show a truncated answer as if it were
			// the whole one; the client must reload the message instead.
			WriteError(w, http.StatusConflict, "RESUME_TOO_OLD",
				"the stream position requested is older than the retained ledger; reload the message")
		case errors.Is(err, service.ErrMessageConversationNotFound),
			errors.Is(err, service.ErrMessageNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
		case errors.Is(err, service.ErrMessageNotAssistant):
			WriteError(w, http.StatusBadRequest, CodeBadRequest,
				"only assistant messages can be resumed")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	defer att.Cancel()

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	// FIX41 - the ledger path. Frames are replayed BYTE FOR BYTE with the
	// seq they originally went out under, then the live tail follows off
	// the same ledger. Nothing is re-derived, so nothing can be renumbered
	// or reordered: replaying a folded snapshot is what turned eight
	// frames into three and put the tool cards ahead of the text
	// (docs/fix41/A-baseline.md, P1b).
	if att.Frames != nil {
		h.resumeFromLedger(w, flusher, r, msgID, att)
		return
	}

	rw := newReplayWriter(w, replayRunID(att))
	rw.started = true
	defer rw.ensureTerminal(flusher)

	writeSSEEvent(rw, flusher, "message_start", map[string]any{
		"message_id":      msgID.String(),
		"conversation_id": convID.String(),
	})
	h.writeResumeSnapshot(rw, flusher, att.Snapshot)

	if att.Events != nil {
		// Live tail — same drain as the primary stream; disconnect
		// only unsubscribes this viewer.
		res := drainStreamEvents(rw, flusher, r.Context().Done(), att.Events, att.Cancel)
		if att.Lagged() {
			// This subscriber fell behind and was cut loose mid-stream.
			// Tell the client to reattach fresh instead of pretending
			// the message completed. This IS this response's terminal:
			// the run is fine, but this view of it ends here, and the
			// client must be able to tell that from a finished answer.
			writeSSEEvent(rw, flusher, "error", map[string]any{
				"code":    "RESUME_LAGGED",
				"message": "resume stream fell behind; reattach",
			})
			return
		}
		// The primary drain persists files/sources in its Finish
		// callback, which runs just after the same terminal event we
		// saw — give it a beat before reading them back.
		if r.Context().Err() == nil {
			time.Sleep(250 * time.Millisecond)
		}
		h.writeMessageFiles(rw, flusher, r.Context(), caller.TenantUUID, msgID)
		h.writeMessageSources(rw, flusher, r.Context(), caller.TenantUUID, msgID)
		if res.err != nil {
			writeSSEEvent(rw, flusher, "error", res.terminalErrorPayload())
			return
		}
		completeData := map[string]any{"message_id": msgID.String()}
		completeWithModel(completeData, att.Snapshot.ServedModel) // FIX35 Part B
		if res.usage != nil {
			tokenUsage := map[string]int{
				"input":  res.usage.PromptTokens,
				"output": res.usage.CompletionTokens,
			}
			if res.usage.ReasoningTokens > 0 {
				tokenUsage["reasoning"] = res.usage.ReasoningTokens
			}
			completeData["token_usage"] = tokenUsage
		}
		writeSSEEvent(rw, flusher, "message_complete", completeData)
		return
	}

	// Terminal replay — the generation already ended; the snapshot
	// carries the persisted row.
	interrupted := false
	if t := att.Snapshot.Terminal; t != nil && t.Err != nil {
		interrupted = true
	}
	h.writeMessageFiles(rw, flusher, r.Context(), caller.TenantUUID, msgID)
	h.writeMessageSources(rw, flusher, r.Context(), caller.TenantUUID, msgID)
	if interrupted {
		// FIX41: an interrupted generation ENDS on error. Before, this
		// wrote error and then message_complete, so the last thing a
		// client learned about a lost turn was the success terminator.
		writeSSEEvent(rw, flusher, "error", map[string]any{
			"code":    "GENERATION_INTERRUPTED",
			"message": "generation was interrupted",
		})
		return
	}
	completeData := map[string]any{"message_id": msgID.String()}
	completeWithModel(completeData, att.Snapshot.ServedModel) // FIX35 Part B
	if t := att.Snapshot.Terminal; t != nil && t.Usage != nil {
		tokenUsage := map[string]int{
			"input":  t.Usage.PromptTokens,
			"output": t.Usage.CompletionTokens,
		}
		if t.Usage.ReasoningTokens > 0 {
			tokenUsage["reasoning"] = t.Usage.ReasoningTokens
		}
		completeData["token_usage"] = tokenUsage
	}
	writeSSEEvent(rw, flusher, "message_complete", completeData)
}

// replayRunID names a replay that has no live run behind it (the row is
// already terminal). A synthetic id, not the message id: the client keys
// its cursor on run_id, and a replay must not be mistaken for a
// continuation of the generation that produced the row.
func replayRunID(att *service.ResumeAttachment) string {
	if att.Frames != nil && att.Frames.RunID != "" {
		return att.Frames.RunID
	}
	return "replay-" + uuid.NewString()
}

// resumeFromLedger serves a reattach off the recorded frames.
//
// The replay set and the live subscription were taken under one lock in
// SubscribeFrames, so no frame can fall between them and none can arrive
// twice. Every write here is verbatim: this response never mints a seq,
// because the run already has one for every frame, and two writers
// numbering the same run independently is precisely the gap-and-duplicate
// problem the ledger exists to remove.
func (h *MessageHandler) resumeFromLedger(
	w http.ResponseWriter,
	flusher http.Flusher,
	r *http.Request,
	msgID uuid.UUID,
	att *service.ResumeAttachment,
) {
	fa := att.Frames
	// Any frame this response has to synthesise continues the run's own
	// numbering — see newReplayWriterFrom.
	after := fa.FinalSeq
	if n := len(fa.Replay); n > 0 && fa.Replay[n-1].Seq > after {
		after = fa.Replay[n-1].Seq
	}
	rw := newReplayWriterFrom(w, fa.RunID, after)
	rw.started = true
	defer rw.ensureTerminal(flusher)

	for _, le := range fa.Replay {
		rw.writeRecorded(flusher, le.Event, le.Data)
	}
	if fa.Live == nil {
		if rw.terminalWritten() {
			return
		}
		if fa.Interrupted {
			// FIX41 chunk 2 — these frames came from the durable mirror
			// and stop without a terminal, because the process producing
			// them went away. The user gets everything that was produced,
			// then the truth. Writing message_complete here would present
			// a truncated answer as a finished one, which is the exact
			// failure the whole change exists to remove.
			writeSSEEvent(rw, flusher, "error", map[string]any{
				"code":    "GENERATION_INTERRUPTED",
				"message": "generation was interrupted",
			})
			return
		}
		// The run already ended; its terminal was inside the replay. When
		// the client's cursor was already at or past the end the replay is
		// empty, so the ending is re-sent rather than leaving it on an
		// open stream it would have to time out on (STREAM_CONTRACT 4).
		writeSSEEvent(rw, flusher, "message_complete", map[string]any{
			"message_id": msgID.String(),
		})
		return
	}

	done := r.Context().Done()
	for {
		select {
		case <-done:
			// This viewer went away. The run is untouched; only this
			// subscription ends. Nothing is owed to a client that is no
			// longer there.
			return
		case le, ok := <-fa.Live:
			if !ok {
				if fa.Lagged() {
					writeSSEEvent(rw, flusher, "error", map[string]any{
						"code":    "RESUME_LAGGED",
						"message": "resume stream fell behind; reattach",
					})
				}
				return
			}
			rw.writeRecorded(flusher, le.Event, le.Data)
		}
	}
}

// writeResumeSnapshot replays the already-generated prefix in the same
// event order a live stream would have produced it: phase markers →
// reasoning → content, so the client's ThinkingBlock/placeholder logic
// behaves identically to a live stream.
func (h *MessageHandler) writeResumeSnapshot(
	w http.ResponseWriter, flusher http.Flusher, snap service.StreamSnapshot,
) {
	if snap.AnalysisStatus != "" {
		writeSSEEvent(w, flusher, "analysis_status", map[string]any{"status": "started"})
	}
	if snap.Reasoning != "" {
		writeSSEEvent(w, flusher, "reasoning_delta", map[string]any{"delta": snap.Reasoning})
	}
	if snap.AnalysisStatus == "completed" {
		writeSSEEvent(w, flusher, "analysis_status", map[string]any{"status": "completed"})
	}
	if snap.ImageStatus != "" {
		writeSSEEvent(w, flusher, "image_generating", map[string]any{"status": snap.ImageStatus})
	}
	if snap.VideoStatus != "" {
		writeSSEEvent(w, flusher, "video_generating", map[string]any{"status": snap.VideoStatus})
	}
	if snap.SearchStatus != "" {
		writeSSEEvent(w, flusher, "search_status", map[string]any{"status": snap.SearchStatus})
	}
	if snap.FallbackInfo != nil {
		writeSSEEvent(w, flusher, "fallback_info", snap.FallbackInfo)
	}
	if snap.DlpNotice != nil { // FIX50 P1-C — sticky, like fallback_info
		writeSSEEvent(w, flusher, "dlp_notice", snap.DlpNotice)
	}
	if snap.ServedModel != "" { // FIX35 Part B — sticky, like fallback_info
		writeSSEEvent(w, flusher, "model_resolved", map[string]any{"model": snap.ServedModel})
	}
	if snap.ReasoningNotice != nil {
		writeSSEEvent(w, flusher, "reasoning_notice", snap.ReasoningNotice)
	}
	// Latest stall state — a client reattaching mid-stall is told the
	// turn is stuck; one reattaching after recovery gets Stalled=false.
	if snap.ProviderStall != nil {
		writeSSEEvent(w, flusher, "provider_stall", snap.ProviderStall)
	}
	if snap.ImageNotice != nil {
		writeSSEEvent(w, flusher, "image_notice", snap.ImageNotice)
	}
	// run_python cards, latest frame each — a refresh in the middle of a
	// 60s sandbox run must not lose the code the model is running.
	for i := range snap.CodeExecutions {
		writeSSEEvent(w, flusher, "code_execution", snap.CodeExecutions[i])
	}
	// Generic tool activity, latest frame each — a reconnect mid-tool
	// restores the "using <tool>…" indicator.
	for i := range snap.ToolActivities {
		writeSSEEvent(w, flusher, "tool_activity", snap.ToolActivities[i])
	}
	if snap.Content != "" {
		writeSSEEvent(w, flusher, "content_delta", map[string]any{"delta": snap.Content})
	}
}

// ---------------------------------------------------------------------------
// Regenerate
// ---------------------------------------------------------------------------

// Regenerate replays the assistant message identified by
// “messageID“ using the same conversation context the original
// reply was built from. Streams the new content over SSE the same
// way Send does, so the front-end can share its rendering code.
//
//	POST /api/v1/conversations/{id}/messages/{messageID}/regenerate
//	→ SSE stream (same event format as Send with stream=true).
func (h *MessageHandler) Regenerate(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	msgID, err := uuid.Parse(chi.URLParam(r, "messageID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message id")
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}

	stream, err := h.messages.Regenerate(r.Context(), service.RegenerateInput{
		TenantID:       caller.TenantUUID,
		UserID:         caller.UserID,
		ConversationID: convID,
		MessageID:      msgID,
		RequestID:      r.Header.Get("X-Request-Id"),
	})
	if err != nil {
		h.writeRegenerateError(w, err)
		return
	}

	// SSE headers, then consume + forward the events.
	rw := h.openRunStream(w, stream)
	defer rw.ensureTerminal(flusher)

	// FIX28 §2 (F3): the client renders the user turn under an optimistic
	// temp id; hand it the persisted row id here (Edit already did) so an
	// edit → fork can name a real message without a reload.
	startPayload := map[string]any{
		"message_id":      stream.AssistantID.String(),
		"conversation_id": stream.ConversationID.String(),
	}
	if stream.UserMessage != nil {
		startPayload["user_message_id"] = stream.UserMessage.ID.String()
	}
	writeSSEEvent(rw, flusher, "message_start", startPayload)

	res := drainStreamEvents(rw, flusher, r.Context().Done(), stream.Events, detachOnDisconnect)

	persistFinishReason := res.finishReason
	if res.err != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.Finish(res.content, res.reasoning, persistFinishReason, res.usage)
	if res.servedModel != "" { // FIX35 Part B
		_ = h.messages.SetServedModel(context.Background(), stream.AssistantID, res.servedModel)
	}
	h.finishStream(rw, flusher, r.Context(), caller.TenantUUID, stream, res)
}

// openRunStream writes the SSE headers and returns the writer that owns
// this run's envelope + ledger. Send has its own copy because its
// early-open path has to run before the StreamHandle exists.
func (h *MessageHandler) openRunStream(w http.ResponseWriter, stream *service.StreamHandle) *runWriter {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	asstID := stream.AssistantID
	rw := newRunWriter(w, stream.RunID, func(event string, build func(seq, final int64) []byte) (int64, bool) {
		return h.messages.RecordFrame(asstID, event, build)
	})
	rw.started = true
	return rw
}

// ---------------------------------------------------------------------------
// Edit — re-streams a user message with new content (branching fork)
// ---------------------------------------------------------------------------

type editRequest struct {
	Content string `json:"content"`
}

func (h *MessageHandler) Edit(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	msgID, err := uuid.Parse(chi.URLParam(r, "messageID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message id")
		return
	}
	var req editRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}

	stream, err := h.messages.EditMessage(r.Context(), service.EditInput{
		TenantID:       caller.TenantUUID,
		UserID:         caller.UserID,
		ConversationID: convID,
		MessageID:      msgID,
		NewContent:     req.Content,
		RequestID:      r.Header.Get("X-Request-Id"),
	})
	if err != nil {
		h.writeEditError(w, err)
		return
	}

	// SSE — identical event format to Send so the front-end shares
	// its rendering pipeline.
	rw := h.openRunStream(w, stream)
	defer rw.ensureTerminal(flusher)

	writeSSEEvent(rw, flusher, "message_start", map[string]any{
		"message_id":      stream.AssistantID.String(),
		"conversation_id": stream.ConversationID.String(),
		"user_message_id": stream.UserMessage.ID.String(),
	})

	res := drainStreamEvents(rw, flusher, r.Context().Done(), stream.Events, detachOnDisconnect)

	persistFinishReason := res.finishReason
	if res.err != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.Finish(res.content, res.reasoning, persistFinishReason, res.usage)
	if res.servedModel != "" { // FIX35 Part B
		_ = h.messages.SetServedModel(context.Background(), stream.AssistantID, res.servedModel)
	}
	h.finishStream(rw, flusher, r.Context(), caller.TenantUUID, stream, res)
}

func (h *MessageHandler) writeEditError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrMessageNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
	case errors.Is(err, service.ErrEditWrongRole):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"only user messages can be edited")
	case errors.Is(err, service.ErrMessageEmptyContent):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "content is required")
	case strings.Contains(err.Error(), "insufficient_credits"):
		// Gateway's portal credit gate fired (cost-based deduction).
		// 402 + a stable code so the frontend shows the Thai
		// credits-exhausted notice instead of a generic provider error.
		WriteError(w, http.StatusPaymentRequired, "INSUFFICIENT_CREDITS",
			"เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		if writeGatewayEnvelope(w, err) {
			return
		}
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", "the AI service is unavailable")
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// ---------------------------------------------------------------------------
// Switch branch
// ---------------------------------------------------------------------------

type switchBranchRequest struct {
	BranchIndex int `json:"branch_index"`
}

// SwitchBranch toggles the active branch inside a fork group. The
// front-end re-fetches the message list after a 200 to pick up the
// new active chain.
func (h *MessageHandler) SwitchBranch(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	msgID, err := uuid.Parse(chi.URLParam(r, "messageID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message id")
		return
	}
	var req switchBranchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if err := h.messages.SwitchBranch(
		r.Context(), caller.TenantUUID, caller.UserID, convID, msgID, req.BranchIndex,
	); err != nil {
		switch {
		case errors.Is(err, service.ErrMessageConversationNotFound),
			errors.Is(err, service.ErrMessageNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
		case errors.Is(err, service.ErrSwitchBranchNoFork):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"message is not part of a fork group")
		case errors.Is(err, service.ErrSwitchBranchMissing):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"branch_index does not exist")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// writeRegenerateError maps service errors to HTTP responses for
// the pre-stream phase. Once SSE headers are out we can't change
// status, so only callers BEFORE the first frame use this.
func (h *MessageHandler) writeRegenerateError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrMessageNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
	case errors.Is(err, service.ErrMessageNotAssistant):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"can only regenerate assistant messages")
	case errors.Is(err, service.ErrMessageModelNotAllowed):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"conversation model is no longer allowed")
	case strings.Contains(err.Error(), "insufficient_credits"):
		// Gateway's portal credit gate fired (cost-based deduction).
		// 402 + a stable code so the frontend shows the Thai
		// credits-exhausted notice instead of a generic provider error.
		WriteError(w, http.StatusPaymentRequired, "INSUFFICIENT_CREDITS",
			"เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		if writeGatewayEnvelope(w, err) {
			return
		}
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", "the AI service is unavailable")
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// Ensure imports compile when only a subset of variables are used.
var _ = context.Background

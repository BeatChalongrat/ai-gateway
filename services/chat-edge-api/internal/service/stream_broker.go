package service

// Phase 5 — stream resume. StreamBroker fans out in-flight generation
// events so a client that disconnected (refresh, network blip) can
// reattach to the SAME generation and receive the already-produced
// prefix plus the live tail.
//
// IN-PROCESS ONLY. Entries live in this process's memory, which matches
// the single-instance chat-edge deployment (docker-compose.webchat.yml
// and docker-compose.prod.yml both pin one container, no replicas). If
// chat-edge is ever scaled horizontally, a reattach can land on an
// instance that doesn't own the generation and will fall back to the
// DB-terminal path — cross-instance resume then needs a shared broker
// (e.g. Redis pub/sub on the already-wired redis-webchat). main.go logs
// this limitation at startup.
//
// Lifecycle: Tee registers an entry keyed by the assistant message id
// and mirrors every StreamUpdate flowing to the primary handler into
// the entry (prefix accumulation + fan-out). When the pump closes the
// channel the entry turns terminal and is kept for resumeRetainTTL so a
// reattach racing the finish-persist still resolves, then it is
// dropped. Subscribers never block the primary stream: a slow/full
// subscriber is marked lagged and closed — the client simply reattaches
// and receives the full prefix again.

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

// resumeRetainTTL keeps a terminal entry around long enough for a
// reattach that raced completion to be served from memory while the
// finish callback persists the row.
const resumeRetainTTL = 60 * time.Second

// resumeSubBuffer is the per-subscriber channel capacity. Overflow ⇒
// the subscriber is closed with lagged=true (client retries fresh).
const resumeSubBuffer = 1024

// FIX41 — the run ledger.
//
// The snapshot above answers "what does the message look like right
// now". It cannot answer "what did the client miss", because folding
// throws the ORDER away: a run whose live stream was
// content/tool/content/code/tool/content/code/content came back from a
// resume as code/tool/content — eight frames as three, tool cards ahead
// of all the text (docs/fix41/A-baseline.md, P1b). No replay code can
// recover that; the information was never stored.
//
// So the entry also keeps the frames it actually sent, in order, each
// with the seq it went out under. A resume replays THOSE — byte for
// byte, original seq — and then follows new appends live. That is what
// makes "no gaps, no duplicates" a property of the data rather than a
// hope about two writers agreeing.
//
// What is stored is exactly what went on the wire, which has already
// been through the gateway's DLP masking. chat-edge holds no rawer copy
// than the client does.

// LedgerEvent is one recorded wire frame.
type LedgerEvent struct {
	Seq   int64
	Event string
	// Data is the exact JSON payload that was written, already carrying
	// run_id / seq / final_seq.
	Data json.RawMessage
}

// ledgerDefaultTTL / ledgerDefaultMaxEvents are the fallbacks when the
// deployment sets no bounds. Both are overridable per broker
// (SetLedgerBounds) so docker-compose owns the real numbers.
const (
	ledgerDefaultTTL       = 600 * time.Second
	ledgerDefaultMaxEvents = 20000
)

// frameSubBuffer is the per-frame-subscriber channel capacity.
const frameSubBuffer = 4096

// StreamSnapshot is the point-in-time prefix a reattaching client must
// replay before consuming live events. Phase markers are sticky so a
// reattach mid image-generation / web-search restores the placeholder.
type StreamSnapshot struct {
	Content   string
	Reasoning string
	// AnalysisStatus: "" | "started" | "completed" (last seen).
	AnalysisStatus string
	ImageStatus    string
	VideoStatus    string
	SearchStatus   string
	FallbackInfo   map[string]any
	// ServedModel (FIX35 Part B): the model that answered, sticky so a
	// reattaching client gets the ``model_resolved`` frame too.
	ServedModel string
	// ReasoningNotice: sticky like FallbackInfo — a reattaching client
	// still learns the answer was produced without reasoning.
	ReasoningNotice *ReasoningNotice
	// ProviderStall: sticky, and carrying the LATEST state rather than
	// the first. A client reattaching mid-stall must learn the turn is
	// stalled — showing it a normal spinner during a dead turn is the
	// exact blindness this feature exists to remove — while a client
	// reattaching after recovery must NOT see a stale warning, which is
	// why the recovery frame overwrites this instead of clearing a flag.
	ProviderStall *ProviderStallNotice
	// ImageNotice: sticky like FallbackInfo — a client reattaching after
	// the one-shot image_notice frame still learns an attachment failed.
	ImageNotice *ImageAttachNotice
	// CodeExecutions are the run_python cards seen so far, in first-seen
	// order, each collapsed to its LATEST frame (the "running" frame is
	// superseded by the terminal one). Replaying them restores the
	// code+output cards after a mid-stream reconnect.
	CodeExecutions []CodeExecutionUpdate
	// ToolActivities mirrors CodeExecutions for the generic tool_activity
	// frames: latest frame per tool_call id, first-seen order, so a
	// reconnect mid-tool restores the "using <tool>…" indicator (and a
	// finished frame supersedes the started one).
	ToolActivities []ToolActivityUpdate
	// Terminal is non-nil when the generation already ended; Events
	// from Subscribe is nil in that case.
	Terminal *StreamUpdate
}

type resumeSubscriber struct {
	ch     chan StreamUpdate
	lagged bool
}

type brokerEntry struct {
	mu        sync.Mutex
	content   strings.Builder
	reasoning strings.Builder
	analysis  string
	image     string
	video     string
	search    string
	fallback  map[string]any
	served    string // FIX35 Part B — sticky like fallback: a reattaching client learns the model too
	rnotice   *ReasoningNotice
	stall     *ProviderStallNotice
	inotice   *ImageAttachNotice
	terminal  *StreamUpdate
	// codeRuns keeps the latest frame per run_python call, plus the
	// order they first appeared, so the snapshot replays them the way
	// the live stream delivered them.
	codeRuns  map[string]CodeExecutionUpdate
	codeOrder []string
	// toolActs mirrors codeRuns for generic tool_activity frames.
	toolActs  map[string]ToolActivityUpdate
	toolOrder []string
	subs      map[int]*resumeSubscriber
	nextSub   int

	// FIX41 run ledger.
	runID string
	// seq is the last seq handed out for this run. message_start takes 1.
	seq int64
	// ledger holds the frames still available for replay, oldest first.
	ledger []LedgerEvent
	// oldestSeq is ledger[0].Seq, or 0 when the ledger is empty. Once the
	// cap has dropped frames this is > 1, and a starting_after below it
	// must be answered RESUME_TOO_OLD rather than silently under-served.
	oldestSeq int64
	// finalSeq / terminalEvent record how the run ended. finalSeq stays 0
	// while the run is in flight.
	finalSeq      int64
	terminalEvent string
	maxEvents     int

	frameSubs    map[int]*frameSubscriber
	nextFrameSub int
}

type frameSubscriber struct {
	ch     chan LedgerEvent
	lagged bool
}

func (e *brokerEntry) snapshotLocked() StreamSnapshot {
	var runs []CodeExecutionUpdate
	for _, id := range e.codeOrder {
		if r, ok := e.codeRuns[id]; ok {
			runs = append(runs, r)
		}
	}
	var acts []ToolActivityUpdate
	for _, id := range e.toolOrder {
		if a, ok := e.toolActs[id]; ok {
			acts = append(acts, a)
		}
	}
	return StreamSnapshot{
		Content:         e.content.String(),
		Reasoning:       e.reasoning.String(),
		AnalysisStatus:  e.analysis,
		ImageStatus:     e.image,
		VideoStatus:     e.video,
		SearchStatus:    e.search,
		FallbackInfo:    e.fallback,
		ServedModel:     e.served,
		ReasoningNotice: e.rnotice,
		ProviderStall:   e.stall,
		ImageNotice:     e.inotice,
		CodeExecutions:  runs,
		ToolActivities:  acts,
		Terminal:        e.terminal,
	}
}

// StreamBroker owns the entry map. Zero value is NOT usable — construct
// with NewStreamBroker.
type StreamBroker struct {
	mu      sync.Mutex
	entries map[uuid.UUID]*brokerEntry
	// retainTTL / afterFunc are swappable for tests.
	retainTTL time.Duration
	afterFunc func(d time.Duration, f func()) *time.Timer
	// FIX41 — how long a finished run stays resumable, and how many
	// frames one run may keep. Set from config; see SetLedgerBounds.
	ledgerTTL       time.Duration
	ledgerMaxEvents int
	// store (FIX41 chunk 2) mirrors the ledger somewhere this process's
	// memory is not, so a restart can still answer a resume. nil ⇒
	// in-memory only, which is chunk 1's behaviour.
	store RunStore
}

// SetRunStore attaches the durable mirror. Nil is valid and means
// "in-memory only" — a deployment without Redis degrades to chunk 1
// rather than refusing to stream.
func (b *StreamBroker) SetRunStore(st RunStore) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.store = st
}

func NewStreamBroker() *StreamBroker {
	return &StreamBroker{
		entries:         make(map[uuid.UUID]*brokerEntry),
		retainTTL:       resumeRetainTTL,
		afterFunc:       time.AfterFunc,
		ledgerTTL:       ledgerDefaultTTL,
		ledgerMaxEvents: ledgerDefaultMaxEvents,
	}
}

// SetLedgerBounds applies the deployment's ledger TTL / cap. Zero or
// negative values keep the current setting, so a half-configured
// environment degrades to the defaults instead of to "keep nothing".
//
// The TTL is how long a FINISHED run stays replayable. It also becomes
// the entry-retention TTL, replacing the 60 s that only ever had to
// cover a reattach racing the finish-persist: once a resume can ask for
// "everything after seq N", the entry has to outlive the tab that went
// away, not just the request that raced it.
func (b *StreamBroker) SetLedgerBounds(ttl time.Duration, maxEvents int) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if ttl > 0 {
		b.ledgerTTL = ttl
		b.retainTTL = ttl
	}
	if maxEvents > 0 {
		b.ledgerMaxEvents = maxEvents
	}
}

// OpenRun creates (or returns) the broker entry for a run so frames can
// be recorded against it BEFORE the pump exists.
//
// This is load-bearing for the FIX33 early-open path: when the context
// compiler is about to spend seconds summarising, the handler opens the
// SSE response and writes message_start + compaction_status immediately —
// which is before SendStream has reached teeStream. Without this those
// first frames would fall outside the ledger, and the run's seq would
// restart at 1 once the pump registered, handing the client two frames
// numbered 1. An entry whose runID already matches is reused untouched.
func (b *StreamBroker) OpenRun(msgID uuid.UUID, runID string) *brokerEntry {
	b.mu.Lock()
	defer b.mu.Unlock()
	if e, ok := b.entries[msgID]; ok && (runID == "" || e.runID == runID) {
		return e
	}
	e := &brokerEntry{
		subs:      make(map[int]*resumeSubscriber),
		frameSubs: make(map[int]*frameSubscriber),
		runID:     runID,
		maxEvents: b.ledgerMaxEvents,
	}
	b.entries[msgID] = e
	return e
}

// Tee registers msgID and returns a channel that mirrors “in“.
// The returned channel is what the primary StreamHandle consumer
// drains — same buffering behaviour as the raw pump channel. The
// mirror goroutine owns the entry lifecycle.
func (b *StreamBroker) Tee(msgID uuid.UUID, in <-chan StreamUpdate) <-chan StreamUpdate {
	return b.TeeRun(msgID, "", in)
}

// TeeRun is Tee with the run's id attached (FIX41). Every frame the
// handler records against msgID is stamped with this run_id, and a
// resume reports it so a client can tell one generation from the next
// (a regenerate of the same message is a different run).
func (b *StreamBroker) TeeRun(msgID uuid.UUID, runID string, in <-chan StreamUpdate) <-chan StreamUpdate {
	e := b.OpenRun(msgID, runID)

	out := make(chan StreamUpdate, 16)
	go func() {
		defer close(out)
		for ev := range in {
			e.publish(ev)
			out <- ev
		}
		// Pump closed. Guarantee a terminal marker for late subscribers
		// even if the pump's last event wasn't Done/Err (defensive; the
		// pump contract always sends one).
		e.mu.Lock()
		if e.terminal == nil {
			t := StreamUpdate{Done: true}
			e.terminal = &t
			for id, sub := range e.subs {
				close(sub.ch)
				delete(e.subs, id)
			}
		}
		e.mu.Unlock()
		b.afterFunc(b.retainTTL, func() { b.drop(msgID) })
	}()
	return out
}

func (e *brokerEntry) publish(ev StreamUpdate) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if ev.Delta != "" {
		e.content.WriteString(ev.Delta)
	}
	if ev.ReasoningDelta != "" {
		e.reasoning.WriteString(ev.ReasoningDelta)
	}
	if ev.AnalysisStatus != "" {
		e.analysis = ev.AnalysisStatus
	}
	if ev.ImageStatus != "" {
		e.image = ev.ImageStatus
	}
	if ev.VideoStatus != "" {
		e.video = ev.VideoStatus
	}
	if ev.SearchStatus != "" {
		e.search = ev.SearchStatus
	}
	if ev.FallbackInfo != nil {
		e.fallback = ev.FallbackInfo
	}
	if ev.ServedModel != "" {
		e.served = ev.ServedModel
	}
	if ev.ReasoningNotice != nil {
		e.rnotice = ev.ReasoningNotice
	}
	if ev.ProviderStall != nil {
		e.stall = ev.ProviderStall
	}
	if ev.ImageNotice != nil {
		e.inotice = ev.ImageNotice
	}
	if ev.CodeExecution != nil && ev.CodeExecution.ID != "" {
		if e.codeRuns == nil {
			e.codeRuns = make(map[string]CodeExecutionUpdate)
		}
		if _, seen := e.codeRuns[ev.CodeExecution.ID]; !seen {
			e.codeOrder = append(e.codeOrder, ev.CodeExecution.ID)
		}
		e.codeRuns[ev.CodeExecution.ID] = *ev.CodeExecution
	}
	if ev.ToolActivity != nil && ev.ToolActivity.ID != "" {
		if e.toolActs == nil {
			e.toolActs = make(map[string]ToolActivityUpdate)
		}
		if _, seen := e.toolActs[ev.ToolActivity.ID]; !seen {
			e.toolOrder = append(e.toolOrder, ev.ToolActivity.ID)
		}
		e.toolActs[ev.ToolActivity.ID] = *ev.ToolActivity
	}
	terminal := ev.Done || ev.Err != nil
	if terminal {
		t := ev
		e.terminal = &t
	}
	for id, sub := range e.subs {
		select {
		case sub.ch <- ev:
		default:
			// Subscriber can't keep up — cut it loose; the client
			// reattaches and replays the full prefix.
			sub.lagged = true
			close(sub.ch)
			delete(e.subs, id)
			continue
		}
		if terminal {
			close(sub.ch)
			delete(e.subs, id)
		}
	}
}

// Subscribe attaches to an in-flight generation. ok=false ⇒ the broker
// doesn't know the message (never teed here, or already dropped after
// TTL) — the caller falls back to the persisted row. When the entry is
// terminal, Snapshot.Terminal is set and events is nil. “cancel“ is
// always safe to call (idempotent) and MUST be called when the caller
// stops consuming, or the subscriber slot leaks until terminal.
// “lagged“ reports (after events closes) whether the subscriber was
// dropped for falling behind.
func (b *StreamBroker) Subscribe(msgID uuid.UUID) (
	snap StreamSnapshot, events <-chan StreamUpdate, cancel func(), lagged func() bool, ok bool,
) {
	b.mu.Lock()
	e, found := b.entries[msgID]
	b.mu.Unlock()
	if !found {
		return StreamSnapshot{}, nil, func() {}, func() bool { return false }, false
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	snap = e.snapshotLocked()
	if e.terminal != nil {
		return snap, nil, func() {}, func() bool { return false }, true
	}
	sub := &resumeSubscriber{ch: make(chan StreamUpdate, resumeSubBuffer)}
	id := e.nextSub
	e.nextSub++
	e.subs[id] = sub
	cancel = func() {
		e.mu.Lock()
		defer e.mu.Unlock()
		if s, still := e.subs[id]; still {
			close(s.ch)
			delete(e.subs, id)
		}
	}
	lagged = func() bool {
		e.mu.Lock()
		defer e.mu.Unlock()
		return sub.lagged
	}
	return snap, sub.ch, cancel, lagged, true
}

// Has reports whether an in-flight (or recently finished, within TTL)
// generation is registered for msgID.
func (b *StreamBroker) Has(msgID uuid.UUID) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	_, ok := b.entries[msgID]
	return ok
}

// SubscriberCount is a test hook for leak assertions.
func (b *StreamBroker) SubscriberCount(msgID uuid.UUID) int {
	b.mu.Lock()
	e, ok := b.entries[msgID]
	b.mu.Unlock()
	if !ok {
		return 0
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.subs)
}

func (b *StreamBroker) drop(msgID uuid.UUID) {
	b.mu.Lock()
	e, ok := b.entries[msgID]
	delete(b.entries, msgID)
	b.mu.Unlock()
	if !ok {
		return
	}
	// Close any straggler subscribers so their drains terminate.
	e.mu.Lock()
	for id, sub := range e.subs {
		close(sub.ch)
		delete(e.subs, id)
	}
	// FIX41 — same for frame subscribers. A resume viewer attached to a
	// run whose handler died without recording a terminal would otherwise
	// wait forever; the TTL drop is its backstop.
	for id, sub := range e.frameSubs {
		close(sub.ch)
		delete(e.frameSubs, id)
	}
	e.mu.Unlock()
}

// ---------------------------------------------------------------------------
// FIX41 — the run ledger
// ---------------------------------------------------------------------------

// terminalFrames are the three event names that end a run. Recording one
// closes the run: its seq becomes final_seq and every attached frame
// subscriber is released.
var terminalFrames = map[string]bool{
	"message_complete": true,
	"error":            true,
	"cancelled":        true,
}

// Record assigns the next seq for the run, hands it to “build“ (which
// must return the exact bytes going out on the wire), stores the result
// and fans it out to every attached frame subscriber.
//
// Only the PRIMARY writer calls this. A resume stream replays what is
// already here; if it recorded too, the same event would land twice under
// two different numbers and the "gapless, no duplicates" guarantee would
// be a lie told by whichever writer got there first.
//
// Returns ok=false when the broker doesn't know msgID (nil broker,
// direct-constructed test service, or an entry already dropped). The
// caller then writes the frame without a seq, which is exactly the
// pre-FIX41 behaviour.
func (b *StreamBroker) Record(
	msgID uuid.UUID, event string, build func(seq, finalSeq int64) []byte,
) (int64, bool) {
	b.mu.Lock()
	e, ok := b.entries[msgID]
	store := b.store
	b.mu.Unlock()
	if !ok {
		return 0, false
	}

	e.mu.Lock()
	e.seq++
	seq := e.seq
	final := int64(0)
	if terminalFrames[event] {
		final = seq
	}
	data := build(seq, final)
	le := LedgerEvent{Seq: seq, Event: event, Data: append(json.RawMessage(nil), data...)}
	e.ledger = append(e.ledger, le)
	if e.oldestSeq == 0 {
		e.oldestSeq = seq
	}
	// Cap: drop the OLDEST frames, never refuse to record a new one. A
	// reattaching client needs the live tail more than it needs the
	// beginning, and a starting_after that points into the dropped region
	// is answered RESUME_TOO_OLD rather than silently under-served.
	if e.maxEvents > 0 && len(e.ledger) > e.maxEvents {
		drop := len(e.ledger) - e.maxEvents
		e.ledger = e.ledger[drop:]
		e.oldestSeq = e.ledger[0].Seq
	}
	if final != 0 {
		e.finalSeq = seq
		e.terminalEvent = event
	}
	// The durable mirror gets the SAME bytes. Append never blocks (see
	// stream_ledger_store.go): a turn is not made slower by bookkeeping.
	if store != nil {
		store.Append(msgID, le)
	}
	for id, sub := range e.frameSubs {
		select {
		case sub.ch <- le:
		default:
			sub.lagged = true
			close(sub.ch)
			delete(e.frameSubs, id)
			continue
		}
		if final != 0 {
			close(sub.ch)
			delete(e.frameSubs, id)
		}
	}
	e.mu.Unlock()
	return seq, true
}

// RunID reports the run id registered for msgID.
func (b *StreamBroker) RunID(msgID uuid.UUID) string {
	b.mu.Lock()
	e, ok := b.entries[msgID]
	b.mu.Unlock()
	if !ok {
		return ""
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.runID
}

// FrameAttachment is what a resume needs to serve a client from the
// ledger: the frames it missed, then the ones still to come.
type FrameAttachment struct {
	RunID string
	// Replay are the recorded frames with Seq > startingAfter, oldest
	// first. Written verbatim.
	Replay []LedgerEvent
	// Live is nil when the run already ended (Replay then contains its
	// terminal). Otherwise it closes when the terminal is recorded.
	Live <-chan LedgerEvent
	// Cancel unsubscribes. Idempotent; MUST be called.
	Cancel func()
	// Lagged reports (after Live closes) that this subscriber fell behind
	// and was cut loose, so the client must reattach rather than believe
	// the run ended.
	Lagged func() bool
	// FinalSeq is the run's terminal seq, or 0 while it is in flight.
	FinalSeq int64
	// Interrupted is set when the stored frames end WITHOUT a terminal —
	// the process that was producing them went away. The handler must end
	// such a replay on `error`, never on a `message_complete` nobody sent:
	// a truncated answer presented as a finished one is the failure this
	// whole change exists to remove.
	Interrupted bool
}

// ErrLedgerTooOld — startingAfter points at frames the cap or the TTL
// already dropped. The caller must answer 409 RESUME_TOO_OLD; pretending
// the run is complete would show the user a truncated answer as if it
// were the whole one.
var ErrLedgerTooOld = errors.New("resume: requested position is older than the ledger")

// SubscribeFrames attaches to a run's ledger. Replay + registration
// happen under one lock, so no frame can slip between "what you missed"
// and "what comes next" — and none can arrive twice.
//
// ok=false ⇒ the broker doesn't know msgID; the caller falls back to the
// persisted row.
func (b *StreamBroker) SubscribeFrames(
	msgID uuid.UUID, startingAfter int64,
) (*FrameAttachment, error, bool) {
	b.mu.Lock()
	e, found := b.entries[msgID]
	b.mu.Unlock()
	if !found {
		return nil, nil, false
	}

	e.mu.Lock()
	defer e.mu.Unlock()

	// Nothing recorded yet: the handler registered the run but has not
	// written message_start. Treat as not-attachable so the client
	// retries rather than receiving an empty "complete".
	if len(e.ledger) == 0 && e.finalSeq == 0 {
		return nil, nil, false
	}
	// A gap between what the client has and what survives is a hole the
	// client cannot see. Say so.
	if startingAfter > 0 && e.oldestSeq > startingAfter+1 {
		return nil, ErrLedgerTooOld, true
	}

	var replay []LedgerEvent
	for _, le := range e.ledger {
		if le.Seq > startingAfter {
			replay = append(replay, le)
		}
	}

	att := &FrameAttachment{
		RunID:    e.runID,
		Replay:   replay,
		FinalSeq: e.finalSeq,
		Cancel:   func() {},
		Lagged:   func() bool { return false },
	}
	if e.finalSeq != 0 {
		// Already ended — the terminal is inside Replay.
		return att, nil, true
	}

	sub := &frameSubscriber{ch: make(chan LedgerEvent, frameSubBuffer)}
	id := e.nextFrameSub
	e.nextFrameSub++
	e.frameSubs[id] = sub
	att.Live = sub.ch
	att.Cancel = func() {
		e.mu.Lock()
		defer e.mu.Unlock()
		if s, still := e.frameSubs[id]; still {
			close(s.ch)
			delete(e.frameSubs, id)
		}
	}
	att.Lagged = func() bool {
		e.mu.Lock()
		defer e.mu.Unlock()
		return sub.lagged
	}
	return att, nil, true
}

// LedgerLen / FrameSubscriberCount are test hooks.
func (b *StreamBroker) LedgerLen(msgID uuid.UUID) int {
	b.mu.Lock()
	e, ok := b.entries[msgID]
	b.mu.Unlock()
	if !ok {
		return 0
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.ledger)
}

func (b *StreamBroker) FrameSubscriberCount(msgID uuid.UUID) int {
	b.mu.Lock()
	e, ok := b.entries[msgID]
	b.mu.Unlock()
	if !ok {
		return 0
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.frameSubs)
}

// LoadStoredFrames answers a resume for a run this process never had —
// the usual case being that the process that DID have it was restarted.
//
// The live tail is nil by construction: the generation died with the
// process that owned its upstream connection, and keeping it running
// would be a background run, which FIX41 deliberately does not build.
// What comes back is the answer so far, in order, under its original
// numbers — and Interrupted says so, so the handler can end the run on
// `error` instead of an invented `message_complete`.
func (b *StreamBroker) LoadStoredFrames(
	ctx context.Context, msgID uuid.UUID, startingAfter int64,
) (*FrameAttachment, error, bool) {
	b.mu.Lock()
	store := b.store
	b.mu.Unlock()
	if store == nil {
		return nil, nil, false
	}
	events, ok := store.Load(ctx, msgID)
	if !ok || len(events) == 0 {
		return nil, nil, false
	}
	oldest := events[0].Seq
	if startingAfter > 0 && oldest > startingAfter+1 {
		return nil, ErrLedgerTooOld, true
	}
	last := events[len(events)-1]
	att := &FrameAttachment{
		Cancel:      func() {},
		Lagged:      func() bool { return false },
		Interrupted: !terminalFrames[last.Event],
	}
	if !att.Interrupted {
		att.FinalSeq = last.Seq
	}
	for _, ev := range events {
		if ev.Seq > startingAfter {
			att.Replay = append(att.Replay, ev)
		}
	}
	// The run id lives inside the frames (every one carries it), so the
	// store needs no second key to remember it.
	var env struct {
		RunID string `json:"run_id"`
	}
	if json.Unmarshal(events[0].Data, &env) == nil {
		att.RunID = env.RunID
	}
	return att, nil, true
}

// ForgetStoredRun drops a run's durable copy. Called once the row it
// belongs to has been settled, so a settled turn is not resurrected by a
// later resume.
func (b *StreamBroker) ForgetStoredRun(msgID uuid.UUID) {
	b.mu.Lock()
	store := b.store
	b.mu.Unlock()
	if store != nil {
		store.Forget(msgID)
	}
}

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
}

func NewStreamBroker() *StreamBroker {
	return &StreamBroker{
		entries:   make(map[uuid.UUID]*brokerEntry),
		retainTTL: resumeRetainTTL,
		afterFunc: time.AfterFunc,
	}
}

// Tee registers msgID and returns a channel that mirrors “in“.
// The returned channel is what the primary StreamHandle consumer
// drains — same buffering behaviour as the raw pump channel. The
// mirror goroutine owns the entry lifecycle.
func (b *StreamBroker) Tee(msgID uuid.UUID, in <-chan StreamUpdate) <-chan StreamUpdate {
	e := &brokerEntry{subs: make(map[int]*resumeSubscriber)}
	b.mu.Lock()
	b.entries[msgID] = e
	b.mu.Unlock()

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
	e.mu.Unlock()
}

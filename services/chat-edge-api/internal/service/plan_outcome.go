package service

// FIX131 A3 — a failed plan execution survives a reload.
//
// NIGHT3: the card said "doc: generator failed: script reported failure",
// and after a reload it offered "สร้างเอกสาร" again as if nothing had
// happened. A SUCCESS already survives: the generated file is linked to the
// message that wrote the plan (FIX126 F4) and GET /messages returns it on
// that message. A failure left nothing behind.
//
// Now a failure is kept per (message, fence, output filename) and GET
// /messages returns it on that message as plan_outcomes; the card opens as
// "ไม่สำเร็จ — <reason>" with a retry button. A later success clears it (the
// file wins anyway on the page).
//
// No migration: the outcome lives in Redis (chat-edge already holds a client
// for the run ledger and the memory-extraction marks, FIX130 A1e) under one
// hash per conversation with a 30-day TTL, or in process when Redis is not
// wired (tests, a single dev instance). What is kept is ids, the reason code
// and the plan's output filename — no plan text. Losing an outcome (TTL,
// flush) costs only that the card offers the button again, which is what it
// did before; a new table would make it permanent (offered to the owner).

import (
	"context"
	"encoding/json"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// planOutcomeTTL — an idle conversation's outcomes expire; each write
// renews the whole hash.
const planOutcomeTTL = 30 * 24 * time.Hour

// PlanOutcomeFailed is the only status stored (a success is the file).
const PlanOutcomeFailed = "failed"

// PlanOutcome is one plan card's last failed execution.
type PlanOutcome struct {
	MessageID      uuid.UUID  `json:"message_id"`
	Fence          string     `json:"fence"`  // doc-plan | pptx-plan | excel-plan
	Format         string     `json:"format"` // docx | pdf | pptx | xlsx
	OutputFilename string     `json:"output_filename,omitempty"`
	Status         string     `json:"status"`
	Reason         string     `json:"reason"`
	SourceFileID   *uuid.UUID `json:"source_file_id,omitempty"`
	At             time.Time  `json:"at"`
}

// PlanOutcomeStore keeps failed plan executions per conversation.
type PlanOutcomeStore interface {
	RecordFailure(ctx context.Context, convID uuid.UUID, o PlanOutcome)
	ClearFailure(ctx context.Context, convID, messageID uuid.UUID, fence, outputFilename string)
	List(ctx context.Context, convID uuid.UUID) []PlanOutcome
}

// NewPlanOutcomeStore returns the Redis store, or the in-process one when
// rdb is nil.
func NewPlanOutcomeStore(rdb *redis.Client) PlanOutcomeStore {
	if rdb == nil {
		return NewMemoryPlanOutcomeStore()
	}
	return &redisPlanOutcomes{rdb: rdb, fallback: NewMemoryPlanOutcomeStore()}
}

func planOutcomeField(messageID uuid.UUID, fence, outputFilename string) string {
	return messageID.String() + "|" + fence + "|" + strings.TrimSpace(outputFilename)
}

func planOutcomeKey(convID uuid.UUID) string { return "chat-edge:planexec:" + convID.String() }

// sortOutcomes keeps the wire order stable: oldest first.
func sortOutcomes(out []PlanOutcome) []PlanOutcome {
	sort.SliceStable(out, func(i, j int) bool { return out[i].At.Before(out[j].At) })
	return out
}

// memoryPlanOutcomes is the in-process store.
type memoryPlanOutcomes struct {
	mu   sync.Mutex
	byID map[uuid.UUID]map[string]PlanOutcome
}

// NewMemoryPlanOutcomeStore is the in-process store (tests, dev, fallback).
func NewMemoryPlanOutcomeStore() PlanOutcomeStore {
	return &memoryPlanOutcomes{byID: map[uuid.UUID]map[string]PlanOutcome{}}
}

func (m *memoryPlanOutcomes) RecordFailure(_ context.Context, convID uuid.UUID, o PlanOutcome) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.byID[convID] == nil {
		m.byID[convID] = map[string]PlanOutcome{}
	}
	m.byID[convID][planOutcomeField(o.MessageID, o.Fence, o.OutputFilename)] = o
}

func (m *memoryPlanOutcomes) ClearFailure(_ context.Context, convID, messageID uuid.UUID, fence, outputFilename string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.byID[convID], planOutcomeField(messageID, fence, outputFilename))
}

func (m *memoryPlanOutcomes) List(_ context.Context, convID uuid.UUID) []PlanOutcome {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]PlanOutcome, 0, len(m.byID[convID]))
	for _, o := range m.byID[convID] {
		out = append(out, o)
	}
	return sortOutcomes(out)
}

// redisPlanOutcomes keeps one hash per conversation. A Redis error falls
// back to the in-process store for that call (logged), so an outage costs
// only cross-replica visibility, never the request.
type redisPlanOutcomes struct {
	rdb      *redis.Client
	fallback PlanOutcomeStore
}

func (r *redisPlanOutcomes) RecordFailure(ctx context.Context, convID uuid.UUID, o PlanOutcome) {
	r.fallback.RecordFailure(ctx, convID, o)
	b, err := json.Marshal(o)
	if err != nil {
		return
	}
	key := planOutcomeKey(convID)
	pipe := r.rdb.TxPipeline()
	pipe.HSet(ctx, key, planOutcomeField(o.MessageID, o.Fence, o.OutputFilename), b)
	pipe.Expire(ctx, key, planOutcomeTTL)
	if _, err := pipe.Exec(ctx); err != nil {
		slog.Warn("plan_outcome: redis write failed; kept in process", "err", err)
	}
}

func (r *redisPlanOutcomes) ClearFailure(ctx context.Context, convID, messageID uuid.UUID, fence, outputFilename string) {
	r.fallback.ClearFailure(ctx, convID, messageID, fence, outputFilename)
	if err := r.rdb.HDel(ctx, planOutcomeKey(convID), planOutcomeField(messageID, fence, outputFilename)).Err(); err != nil {
		slog.Warn("plan_outcome: redis clear failed", "err", err)
	}
}

func (r *redisPlanOutcomes) List(ctx context.Context, convID uuid.UUID) []PlanOutcome {
	vals, err := r.rdb.HGetAll(ctx, planOutcomeKey(convID)).Result()
	if err != nil {
		slog.Warn("plan_outcome: redis read failed; using in-process", "err", err)
		return r.fallback.List(ctx, convID)
	}
	out := make([]PlanOutcome, 0, len(vals))
	for _, v := range vals {
		var o PlanOutcome
		if json.Unmarshal([]byte(v), &o) == nil && o.MessageID != uuid.Nil {
			out = append(out, o)
		}
	}
	return sortOutcomes(out)
}

// PlanOutcomeFromError builds the outcome to keep for a failed execution.
func PlanOutcomeFromError(messageID uuid.UUID, format, outputFilename string, sourceFileID uuid.UUID, err error) PlanOutcome {
	reason := PlanFailInternal
	if pe, ok := AsPlanExecError(err); ok {
		reason = pe.Reason
	}
	o := PlanOutcome{
		MessageID:      messageID,
		Fence:          PlanFenceForFormat(format),
		Format:         planOutputKind(format),
		OutputFilename: strings.TrimSpace(outputFilename),
		Status:         PlanOutcomeFailed,
		Reason:         reason,
		At:             time.Now().UTC(),
	}
	if sourceFileID != uuid.Nil {
		id := sourceFileID
		o.SourceFileID = &id
	}
	return o
}

package service

// FIX130 A3 — the page is told when an answer used memory, and when one
// saved (or suggested) a memory.
//
// Before this the only trace was the saved_memory rows in
// response_context_sources, returned on GET /messages and rendered by a
// sources card that has been hidden since FIX43 — so a user could not tell
// that an answer knew their name because of a memory, and a save made
// during a turn was announced by nothing at all (NIGHT2 scored memory 3/10
// partly on this). Two frames, both recorded in the run ledger so a
// reattach replays them, and both derivable after a reload from the rows
// that already exist:
//
//	memory_used   — once, before the answer's first token, when the turn
//	                injected at least one saved memory. Reload: the
//	                saved_memory context sources.
//	memory_saved  — after each successful save_memory call. Reload: the
//	                memory_saved context sources (recordMemorySaved).
//
// The desktop host emits the same two frames over its bridge for the turns
// it assembles itself, so the page renders one component for both.

import (
	"log/slog"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// memoryNoticeLabelRunes caps an item label on the wire; the popover shows
// a short line per memory, not the whole text.
const memoryNoticeLabelRunes = 80

// MemoryNoticeItem is one memory named by a memory_used / memory_saved frame.
type MemoryNoticeItem struct {
	ID    string `json:"id"`
	Label string `json:"label"`
	// Status — memory_saved only: "active" (saved) or
	// "pending_confirmation" (a suggestion waiting for the user).
	Status string `json:"status,omitempty"`
}

// MemoryUsedNotice is the memory_used payload.
type MemoryUsedNotice struct {
	MessageID string             `json:"message_id"`
	Count     int                `json:"count"`
	Items     []MemoryNoticeItem `json:"items"`
}

// MemorySavedNotice is the memory_saved payload.
type MemorySavedNotice struct {
	MessageID string             `json:"message_id"`
	Items     []MemoryNoticeItem `json:"items"`
}

// memoryNoticeLabel is the one-line label: whitespace collapsed, capped.
func memoryNoticeLabel(content string) string {
	s := strings.Join(strings.Fields(content), " ")
	r := []rune(s)
	if len(r) > memoryNoticeLabelRunes {
		return string(r[:memoryNoticeLabelRunes-1]) + "…"
	}
	return s
}

func messageIDString(id uuid.UUID) string {
	if id == uuid.Nil {
		return ""
	}
	return id.String()
}

// memoryUsedFromSources builds the memory_used payload from what the
// compile step recorded, or nil when no saved memory was injected. Only
// the injection block's rows count (a search_past_conversations memory hit
// is recorded later and carries no source id).
func memoryUsedFromSources(messageID uuid.UUID, srcs []ContextSource) *MemoryUsedNotice {
	var items []MemoryNoticeItem
	for i := range srcs {
		s := &srcs[i]
		if s.SourceType != model.SourceTypeSavedMemory || s.SourceID == "" {
			continue
		}
		items = append(items, MemoryNoticeItem{ID: s.SourceID, Label: memoryNoticeLabel(s.DisplayLabel)})
	}
	if len(items) == 0 {
		return nil
	}
	return &MemoryUsedNotice{MessageID: messageIDString(messageID), Count: len(items), Items: items}
}

// emitMemoryUsed enqueues the memory_used frame before the pump starts,
// like emitImageNotice, so it is the answer's first frame after
// message_start. No-op when the turn read no memory.
//
// FIX132 A — the web page no longer shows a "used N memories" chip, so the
// server keeps the record: besides the saved_memory context-source rows and
// this ledger frame, one log line per turn names the memory ids. Ids only —
// a label is the user's own words and never goes to the log.
func emitMemoryUsed(ch chan<- StreamUpdate, messageID uuid.UUID, acc *SourcesAccumulator) {
	if n := memoryUsedFromSources(messageID, acc.Snapshot()); n != nil {
		ids := make([]string, len(n.Items))
		for i := range n.Items {
			ids[i] = n.Items[i].ID
		}
		slog.Info("memory_used", "message_id", n.MessageID, "count", n.Count, "memory_ids", ids)
		ch <- StreamUpdate{MemoryUsed: n}
	}
}

// mergeMemorySaved folds a new memory_saved frame into the broker's sticky
// copy: one item per memory id, first-seen order, latest status wins.
func mergeMemorySaved(prev, next *MemorySavedNotice) *MemorySavedNotice {
	if prev == nil {
		cp := *next
		cp.Items = append([]MemoryNoticeItem(nil), next.Items...)
		return &cp
	}
	out := &MemorySavedNotice{MessageID: prev.MessageID, Items: append([]MemoryNoticeItem(nil), prev.Items...)}
	if out.MessageID == "" {
		out.MessageID = next.MessageID
	}
	for _, it := range next.Items {
		replaced := false
		for i := range out.Items {
			if out.Items[i].ID == it.ID {
				out.Items[i] = it
				replaced = true
				break
			}
		}
		if !replaced {
			out.Items = append(out.Items, it)
		}
	}
	return out
}

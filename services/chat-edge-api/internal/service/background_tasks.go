package service

// FIX126 O1 — background work does not compete with the person's turn.
//
// The conversation-state summary (background extraction and the compaction
// summariser) and the preference extractor called the conversation's OWN
// model: CONTEXT_SUMMARY_MODEL was empty and the extractor's model was never
// wired. On a slow self-hosted model (qwen) those calls queued behind — and
// in front of — the user's next turn on the same box.
//
// (a) One configured background model, BACKGROUND_TASK_MODEL (default
//     DefaultBackgroundTaskModel). CONTEXT_SUMMARY_MODEL, when set, still
//     wins for the summariser. Both callers fall back to the conversation's
//     model when the background model produced nothing (not served, not
//     usable by the tenant, failed) — the call goes through the gateway as
//     the conversation's own tenant and user either way.
//     Title generation is NOT a background task here: FIX124 names the chat
//     from the model that answered, and that is unchanged.
//
// (b) A background call waits while its conversation has a turn in flight
//     (the FIX125 M5 claim, conversation_turns.go) and runs once the turn
//     has ended. The call is kicked from finalizeConversation — inside the
//     turn — so it always waits at least for that turn's release. If the
//     conversation stays busy past backgroundWaitMax the work is skipped;
//     the triggers are count-based, so the next finished turn kicks it again.

import (
	"context"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"
)

// DefaultBackgroundTaskModel is BACKGROUND_TASK_MODEL's default (config.go
// carries the same literal as its envDefault).
const DefaultBackgroundTaskModel = "deepseek-v4-flash"

// BackgroundModels resolves the configured models: the summariser
// (CONTEXT_SUMMARY_MODEL, else the background model) and the preference
// extractor's (the background model). "off" / "none" disables the background
// model — the conversation's model is used, as before FIX126.
func BackgroundModels(contextSummaryModel, backgroundTaskModel string) (summariser, extractor string) {
	bg := strings.TrimSpace(backgroundTaskModel)
	switch strings.ToLower(bg) {
	case "off", "none", "-":
		bg = ""
	}
	summariser = strings.TrimSpace(contextSummaryModel)
	if summariser == "" {
		summariser = bg
	}
	return summariser, bg
}

// backgroundModelOrder is the order a background call tries models in: the
// configured one, then the conversation's, without repeats or blanks.
func backgroundModelOrder(configured, conversation string) []string {
	out := make([]string, 0, 2)
	for _, id := range []string{strings.TrimSpace(configured), strings.TrimSpace(conversation)} {
		if id != "" && (len(out) == 0 || out[0] != id) {
			out = append(out, id)
		}
	}
	return out
}

// Waiting for the conversation to be free.
const (
	backgroundWaitPoll = 250 * time.Millisecond
	backgroundWaitMax  = 10 * time.Minute
)

// waitTurnIdle blocks until the conversation holds no turn claim, and reports
// whether it got there (false: ctx ended or maxWait passed while still busy).
func (s *MessageService) waitTurnIdle(ctx context.Context, convID uuid.UUID, poll, maxWait time.Duration) bool {
	deadline := time.Now().Add(maxWait)
	for s.TurnInFlight(convID) {
		if time.Now().After(deadline) {
			return false
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(poll):
		}
	}
	return true
}

// runWhenTurnIdle runs a background task for a conversation once it has no
// turn in flight, on its own goroutine; skipped (logged by the caller's
// name) when the conversation stays busy past backgroundWaitMax.
func (s *MessageService) runWhenTurnIdle(name string, convID uuid.UUID, fn func(ctx context.Context)) {
	poll, maxWait := s.bgWaitPoll, s.bgWaitMax
	if poll <= 0 {
		poll = backgroundWaitPoll
	}
	if maxWait <= 0 {
		maxWait = backgroundWaitMax
	}
	safeGo(name, func() {
		bg := context.Background()
		if !s.waitTurnIdle(bg, convID, poll, maxWait) {
			logBackgroundSkipped(name, convID, maxWait)
			return
		}
		fn(bg)
	})
}

func logBackgroundSkipped(name string, convID uuid.UUID, waited time.Duration) {
	slog.Info("background task skipped: conversation stayed busy",
		"task", name, "conversation_id", convID.String(), "waited", waited.String())
}

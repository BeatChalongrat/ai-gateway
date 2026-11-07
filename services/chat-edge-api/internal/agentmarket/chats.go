package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// ── Chats under each agent (AM10) ────────────────────────────────────────
//
// The sidebar lists each installed agent's chats beneath it, newest first,
// named for what they are (the period of their latest round, or when they
// began), with rename, pin and delete, and "แชทใหม่กับ agent นี้". This
// supersedes AM4's "one workspace, rounds collapsed" where the two disagree:
// a person who wants two rounds side by side opens two chats.
//
// Deleting a chat takes it off the list and nothing else. Its rounds, their
// files, the run pages and the audit rows live on and stay reachable from the
// agent's dashboard: a tidy sidebar is not a reason to lose a working paper.
// A round still queued or waiting in that chat is cancelled (it delivered
// nothing and is charged nothing), the way removing the install cancels its
// rounds. The install's first chat ("main") has no row: its name, pin and
// deletion live on the install (migration 008).

// ThreadMeta is what a person set on a chat.
type ThreadMeta struct {
	Title     string
	PinnedAt  *time.Time
	DeletedAt *time.Time
}

const maxChatTitle = 80

// ThreadMetaOf reads a chat's name, pin and deletion (nil thread = the first chat).
func (s *Store) ThreadMetaOf(ctx context.Context, q Querier, install uuid.UUID, thread *uuid.UUID) (ThreadMeta, error) {
	var m ThreadMeta
	var title *string
	var err error
	if thread == nil {
		err = q.QueryRow(ctx, `SELECT first_chat_title, first_chat_pinned_at, first_chat_deleted_at
			FROM agent_market.agent_installs WHERE id = $1`, install).Scan(&title, &m.PinnedAt, &m.DeletedAt)
	} else {
		err = q.QueryRow(ctx, `SELECT title, pinned_at, deleted_at FROM agent_market.agent_threads
			WHERE id = $1 AND install_id = $2`, *thread, install).Scan(&title, &m.PinnedAt, &m.DeletedAt)
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return m, ErrNotFound
	}
	if title != nil {
		m.Title = *title
	}
	return m, err
}

// ThreadDeleted: the chat was deleted (a deleted chat is no longer "owned"
// for reading or writing, and its run pages are reached another way).
func (s *Store) ThreadDeleted(ctx context.Context, install uuid.UUID, thread *uuid.UUID) (bool, error) {
	m, err := s.ThreadMetaOf(ctx, s.Pool, install, thread)
	if err != nil {
		return false, err
	}
	return m.DeletedAt != nil, nil
}

// RenameThread sets the chat's own name ("" puts the derived name back).
func (s *Store) RenameThread(ctx context.Context, install uuid.UUID, thread *uuid.UUID, title string) error {
	title = strings.TrimSpace(title)
	if r := []rune(title); len(r) > maxChatTitle {
		title = string(r[:maxChatTitle])
	}
	var t *string
	if title != "" {
		t = &title
	}
	if thread == nil {
		_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET first_chat_title = $2, updated_at = now() WHERE id = $1`, install, t)
		return err
	}
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_threads SET title = $3 WHERE id = $1 AND install_id = $2`, *thread, install, t)
	return err
}

// PinThread pins or unpins a chat.
func (s *Store) PinThread(ctx context.Context, install uuid.UUID, thread *uuid.UUID, on bool) (*time.Time, error) {
	var at *time.Time
	var err error
	if thread == nil {
		err = s.Pool.QueryRow(ctx, `UPDATE agent_market.agent_installs
			SET first_chat_pinned_at = CASE WHEN $2 THEN COALESCE(first_chat_pinned_at, now()) ELSE NULL END, updated_at = now()
			WHERE id = $1 RETURNING first_chat_pinned_at`, install, on).Scan(&at)
	} else {
		err = s.Pool.QueryRow(ctx, `UPDATE agent_market.agent_threads
			SET pinned_at = CASE WHEN $3 THEN COALESCE(pinned_at, now()) ELSE NULL END
			WHERE id = $1 AND install_id = $2 RETURNING pinned_at`, *thread, install, on).Scan(&at)
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return at, err
}

// DeleteThread takes a chat off the list: a date on the chat, its unfinished
// rounds cancelled (settled: charged nothing). Everything else stays. It
// returns how many rounds were cancelled and how many finished rounds the
// chat keeps.
func (s *Store) DeleteThread(ctx context.Context, install uuid.UUID, thread *uuid.UUID) (cancelled, kept int, err error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return 0, 0, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var tag pgconn.CommandTag
	if thread == nil {
		tag, err = tx.Exec(ctx, `UPDATE agent_market.agent_installs SET first_chat_deleted_at = now(), updated_at = now()
			WHERE id = $1 AND first_chat_deleted_at IS NULL`, install)
	} else {
		tag, err = tx.Exec(ctx, `UPDATE agent_market.agent_threads SET deleted_at = now()
			WHERE id = $1 AND install_id = $2 AND deleted_at IS NULL`, *thread, install)
	}
	if err != nil {
		return 0, 0, err
	}
	if tag.RowsAffected() == 0 {
		return 0, 0, ErrNotFound
	}
	// the same stop as removing the install (store.go RemoveInstall), for this chat only
	ctag, err := tx.Exec(ctx, `
		UPDATE agent_market.agent_runs SET cancel_requested = true,
			status = CASE WHEN status IN ('queued', 'awaiting_input') THEN 'cancelled' ELSE status END,
			status_reason = CASE WHEN status IN ('queued', 'awaiting_input') THEN 'cancelled_by_user' ELSE status_reason END,
			finished_at = CASE WHEN status IN ('queued', 'awaiting_input') THEN now() ELSE finished_at END,
			awaiting_ms = awaiting_ms + CASE WHEN status = 'awaiting_input' AND awaiting_since IS NOT NULL
				THEN (extract(epoch FROM now() - awaiting_since) * 1000)::bigint ELSE 0 END,
			awaiting_since = CASE WHEN status = 'awaiting_input' THEN NULL ELSE awaiting_since END,
			updated_at = now()
		WHERE install_id = $1 AND thread_id IS NOT DISTINCT FROM $2::uuid AND status IN ('queued', 'awaiting_input', 'running')`,
		install, thread)
	if err != nil {
		return 0, 0, err
	}
	cancelled = int(ctag.RowsAffected())
	if err := tx.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs
		WHERE install_id = $1 AND thread_id IS NOT DISTINCT FROM $2::uuid AND kind = 'run' AND status IN ('succeeded', 'failed')`,
		install, thread).Scan(&kept); err != nil {
		return 0, 0, err
	}
	// cards still open in that chat close with their rounds
	if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_run_clarifications c SET status = 'cancelled'
		FROM agent_market.agent_runs r WHERE c.run_id = r.id AND r.install_id = $1 AND r.thread_id IS NOT DISTINCT FROM $2::uuid
		AND r.status = 'cancelled' AND c.status IN ('open', 'interpreting')`, install, thread); err != nil {
		return 0, 0, err
	}
	if err := tx.Commit(ctx); err != nil {
		return 0, 0, err
	}
	if cancelled > 0 {
		// AM6: a round that was stopped delivered nothing, and is charged nothing
		if err := s.SettleInstall(ctx, s.Pool, install); err != nil {
			return cancelled, kept, err
		}
	}
	return cancelled, kept, nil
}

// ── handlers ─────────────────────────────────────────────────────────────

// chatParam reads {thread} of a chat route: "main" is the first chat.
func (a *API) chatParam(w http.ResponseWriter, r *http.Request, in Install) (*uuid.UUID, bool) {
	v := strings.TrimSpace(chi.URLParam(r, "thread"))
	if v == "" || v == "main" {
		return nil, true
	}
	id, err := uuid.Parse(v)
	if err != nil {
		badRequest(w, "BAD_THREAD")
		return nil, false
	}
	return &id, a.threadOf(w, r, in, &id)
}

// patchThread renames or pins a chat: {"title": "…"} (empty = the derived
// name again), {"pinned": true|false}.
func (a *API) patchThread(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	thread, ok := a.chatParam(w, r, in)
	if !ok {
		return
	}
	var body struct {
		Title  *string `json:"title"`
		Pinned *bool   `json:"pinned"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	if gone, err := a.store.ThreadDeleted(ctx, in.ID, thread); err != nil {
		a.fail(w, err, "thread")
		return
	} else if gone {
		say(w, http.StatusNotFound, "THREAD_GONE")
		return
	}
	if body.Title != nil {
		if len([]rune(strings.TrimSpace(*body.Title))) > maxChatTitle {
			say(w, http.StatusBadRequest, "TITLE_TOO_LONG")
			return
		}
		if err := a.store.RenameThread(ctx, in.ID, thread, *body.Title); err != nil {
			a.fail(w, err, "thread")
			return
		}
	}
	if body.Pinned != nil {
		if _, err := a.store.PinThread(ctx, in.ID, thread, *body.Pinned); err != nil {
			a.fail(w, err, "thread")
			return
		}
	}
	list, err := a.store.ListThreads(ctx, in)
	if err != nil {
		a.fail(w, err, "thread")
		return
	}
	for _, t := range list {
		if (t.ID == nil && thread == nil) || (t.ID != nil && thread != nil && *t.ID == *thread) {
			writeJSON(w, http.StatusOK, map[string]any{"thread": t})
			return
		}
	}
	a.fail(w, ErrNotFound, "thread")
}

// deleteThread takes a chat off the list; the evidence of its rounds stays.
func (a *API) deleteThread(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	thread, ok := a.chatParam(w, r, in)
	if !ok {
		return
	}
	cancelled, kept, err := a.store.DeleteThread(r.Context(), in.ID, thread)
	if errors.Is(err, ErrNotFound) {
		say(w, http.StatusNotFound, "THREAD_GONE")
		return
	}
	if err != nil {
		a.fail(w, err, "thread")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"deleted": true, "cancelled_runs": cancelled, "kept_runs": kept})
}

// chatRoutes mounts AM10's routes (inside the allowed group).
func (a *API) chatRoutes(r chi.Router) {
	r.Patch("/installs/{id}/threads/{thread}", a.patchThread)
	r.Delete("/installs/{id}/threads/{thread}", a.deleteThread)
}

"use client";

// FIX127 D1 — the message queue above the composer.
//
// A message typed while an answer is still streaming used to go nowhere
// (Enter was a silent no-op). It now waits here, in order, and is sent as
// its own turn when the answer finishes. Each waiting message can be
// edited in place, removed, or sent now (which stops the current answer
// first). One turn in flight per chat is kept: the store releases one
// queued message per finished turn, never two at once.

import { Pencil, Send, X } from "lucide-react";
import { useState } from "react";

import { useT } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";

interface Props {
  conversationId: string;
}

export function MessageQueue({ conversationId }: Props) {
  const t = useT();
  const queue = useChatStore((s) => s.queuedMessages[conversationId]);
  const update = useChatStore((s) => s.updateQueuedMessage);
  const remove = useChatStore((s) => s.removeQueuedMessage);
  const sendNow = useChatStore((s) => s.sendQueuedNow);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);

  if (!queue || queue.length === 0) return null;

  function saveEdit() {
    if (!editing) return;
    const text = editing.text.trim();
    if (text) update(conversationId, editing.id, text);
    else remove(conversationId, editing.id);
    setEditing(null);
  }

  return (
    <div className="px-4" data-testid="message-queue">
      <div
        role="status"
        aria-label={t("queue.waiting")}
        className="mx-auto flex max-w-3xl flex-col gap-1 rounded-t-lg border border-b-0 border-border bg-bg-elevated px-3 py-1.5 text-xs text-text-secondary"
      >
        <div className="text-[11px] font-medium text-text-muted">
          {t("queue.waiting")} · {queue.length}
        </div>
        <ul className="flex flex-col gap-1">
          {queue.map((q) => (
            <li key={q.id} className="flex items-start gap-2" data-testid="queued-message">
              {editing?.id === q.id ? (
                <textarea
                  autoFocus
                  aria-label={t("queue.edit")}
                  className="min-h-[2.25rem] flex-1 resize-y rounded-md border border-border bg-bg-base px-2 py-1 text-sm text-text-primary"
                  value={editing.text}
                  onChange={(e) => setEditing({ id: q.id, text: e.target.value })}
                  onBlur={saveEdit}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      saveEdit();
                    } else if (e.key === "Escape") {
                      setEditing(null);
                    }
                  }}
                />
              ) : (
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate rounded-md px-1 py-0.5 text-left text-sm text-text-primary hover:bg-bg-surface"
                  title={q.text}
                  onClick={() => setEditing({ id: q.id, text: q.text })}
                >
                  {q.text}
                  {q.fileIds.length > 0 ? (
                    <span className="ml-1 text-[11px] text-text-muted">· {q.fileIds.length} 📎</span>
                  ) : null}
                </button>
              )}
              <button
                type="button"
                aria-label={t("queue.edit")}
                className="focus-ring rounded p-1 text-text-muted hover:text-text-primary"
                onClick={() => setEditing({ id: q.id, text: q.text })}
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                aria-label={t("queue.sendNow")}
                title={t("queue.sendNowHint")}
                data-testid="queue-send-now"
                className="focus-ring rounded p-1 text-accent hover:opacity-80"
                onClick={() => void sendNow(conversationId, q.id)}
              >
                <Send className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                aria-label={t("queue.delete")}
                data-testid="queue-delete"
                className="focus-ring rounded p-1 text-text-muted hover:text-status-danger"
                onClick={() => remove(conversationId, q.id)}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

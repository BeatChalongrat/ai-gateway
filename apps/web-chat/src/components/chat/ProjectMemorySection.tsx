"use client";

// The memories scoped to ONE project, listed so the user can read and
// delete them individually.
//
// FIX58 gave every project its own memory and sealed it off from the
// global set and from other projects — but left no way to look at it,
// which makes it memory the user cannot know about and therefore cannot
// remove. A list of the actual rows is the point; a generated summary
// would be a second thing to keep in sync and could not be deleted item
// by item.
//
// Reuses MemoryCard and MemoryFormDialog exactly as the Memory page
// does, so edit / pin / delete behave identically in both places.
//
// The `scope_id` below is load-bearing. Without it the request would ask
// for "project memories" with no project named — the cross-project leak
// FIX58 closed, re-opened through a screen. The server rejects that
// shape with a 400 rather than answering it, so the mistake surfaces
// here as a visible error and never as another project's facts.

import { useCallback, useEffect, useState } from "react";

import { MemoryCard } from "@/components/chat/MemoryCard";
import { MemoryFormDialog } from "@/components/chat/MemoryFormDialog";
import { toast } from "@/components/ui/Toast";
import { api } from "@/lib/api";
import { tOutside, useT } from "@/lib/i18n";
import type { Memory } from "@/lib/types";

export function ProjectMemorySection({ projectId }: { projectId: string }) {
  const t = useT();
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [editing, setEditing] = useState<Memory | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const res = await api.listMemories({
        scope_type: "project",
        scope_id: projectId,
        limit: 100,
      });
      setMemories(res.memories ?? []);
    } catch {
      // An empty list and a failed load mean opposite things to
      // someone deciding whether their data is still stored. Never
      // render a failure as "nothing here".
      setMemories(null);
      setFailed(true);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleDelete(id: string) {
    const before = memories;
    setMemories((cur) => (cur ?? []).filter((m) => m.id !== id));
    try {
      await api.deleteMemory(id);
    } catch {
      setMemories(before);
      toast(tOutside("projects.memory.deleteFailed"));
    }
  }

  async function handleTogglePin(id: string, pinned: boolean) {
    try {
      const updated = await api.pinMemory(id, pinned);
      setMemories((cur) => (cur ?? []).map((m) => (m.id === id ? updated : m)));
    } catch {
      toast(tOutside("projects.memory.pinFailed"));
    }
  }

  return (
    <div>
      <h4 className="mb-1 text-xs font-medium uppercase tracking-wider text-text-secondary">
        {t("projects.memory.title")}
      </h4>
      <p className="mb-2 text-[11px] text-text-muted">
        {t("projects.memory.hint")}
      </p>

      {failed ? (
        <div className="flex items-center gap-3 py-2">
          <span className="text-xs text-status-danger">
            {t("projects.memory.loadFailed")}
          </span>
          <button
            type="button"
            onClick={() => void load()}
            className="focus-ring rounded-md border border-border bg-bg-surface px-2.5 py-1 text-xs text-text-primary hover:bg-bg-elevated"
          >
            {t("projects.memory.retry")}
          </button>
        </div>
      ) : memories === null ? (
        <p className="py-2 text-xs text-text-muted">
          {t("projects.memory.loading")}
        </p>
      ) : memories.length === 0 ? (
        <p className="py-2 text-xs text-text-muted">
          {t("projects.memory.empty")}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {memories.map((m) => (
            <li key={m.id}>
              <MemoryCard
                memory={m}
                onEdit={(mem) => setEditing(mem)}
                onDelete={(id) => void handleDelete(id)}
                onTogglePin={(id, pinned) => void handleTogglePin(id, pinned)}
              />
            </li>
          ))}
        </ul>
      )}

      <MemoryFormDialog
        open={editing !== null}
        mode="edit"
        initial={editing}
        onClose={() => setEditing(null)}
        onSaved={(m) =>
          setMemories((cur) => (cur ?? []).map((x) => (x.id === m.id ? m : x)))
        }
      />
    </div>
  );
}


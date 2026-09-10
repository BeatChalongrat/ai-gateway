"use client";

// FIX22 §1c — the desktop agent's LOCAL notes, listed where the user already
// manages memory. The `remember` tool writes the sidecar's own store on this
// computer (scope=global carries into every desktop conversation); the host
// folds those notes into every turn's prompt next to the server memories. Until
// FIX22 nothing listed them: hostApi.listGlobalNotes/deleteGlobalNote existed
// (globalNotes.list/delete are in the closed host.query op set) but no screen
// called them — so a poisoned note could not be found, let alone deleted.
//
// Hosted mode only: the ops exist only when the Electron host is present.

import { useCallback, useEffect, useState } from "react";
import { Laptop, Trash2 } from "lucide-react";

import { toast } from "@/components/ui/Toast";
import { useHostedMode } from "@/hooks/useHostedMode";
import { hostApi, type GlobalNoteDto } from "@/lib/hostApi";
import { useT } from "@/lib/i18n";

export function LocalNotesSection() {
  const hosted = useHostedMode();
  const t = useT();
  const [notes, setNotes] = useState<GlobalNoteDto[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setNotes(await hostApi.listGlobalNotes());
    } catch {
      setNotes(null);
      setError(t("memory.local.loadError"));
    }
  }, [t]);

  useEffect(() => {
    if (hosted) void load();
  }, [hosted, load]);

  if (!hosted) return null;

  async function remove(id: string) {
    setBusy(id);
    try {
      const r = await hostApi.deleteGlobalNote(id);
      if (!r.ok) throw new Error(r.error ?? "refused");
      setNotes((prev) => prev?.filter((n) => n.id !== id) ?? null);
      toast(t("memory.local.toastDeleted"));
    } catch {
      toast(t("memory.local.toastDeleteError"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="my-8 h-px bg-border-subtle" />
      <section data-testid="local-notes-section">
        <div className="flex items-baseline gap-2">
          <Laptop className="h-3.5 w-3.5 text-text-muted" aria-hidden />
          <h2 className="text-sm font-semibold text-text-primary">{t("memory.local.title")}</h2>
        </div>
        <p className="mt-1 text-sm text-text-secondary">{t("memory.local.description")}</p>
        <div className="mt-3">
          {error ? (
            <p className="text-xs text-text-muted">{error}</p>
          ) : notes === null ? null : notes.length === 0 ? (
            <p className="rounded-lg border border-border-subtle bg-bg-surface px-4 py-4 text-center text-xs text-text-muted">
              {t("memory.local.empty")}
            </p>
          ) : (
            <ul className="space-y-2">
              {notes.map((n) => (
                <li
                  key={n.id}
                  className="flex items-start gap-3 rounded-lg border border-border-subtle bg-bg-surface px-3 py-2 text-sm text-text-primary"
                >
                  <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{n.content}</span>
                  <button
                    type="button"
                    disabled={busy === n.id}
                    onClick={() => void remove(n.id)}
                    aria-label={t("memory.local.delete")}
                    className="focus-ring shrink-0 rounded-md p-1 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:opacity-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </>
  );
}

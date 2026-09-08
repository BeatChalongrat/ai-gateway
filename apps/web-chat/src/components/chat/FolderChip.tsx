"use client";

// FIX25 §2 — the granted-folders chip in the composer (desktop/hosted only).
//
// Shows the folders the agent's file tools may reach, where the user can see
// them while typing, with the native picker one click away. It DISPLAYS the
// grants that already exist and can ADD one — through the existing
// `folders.list` / `folders.pickAndGrant` control-plane ops (no new op; the
// page never names a path — the folder comes from the host's native dialog).
// Revoke stays in Settings. It is NOT a per-conversation "working folder":
// nothing here scopes a turn, and it does not fix the tools resolving "the
// granted folder" to the home directory (sweep defect #8) — it only makes
// the real list visible.
//
// Freshness: the list is read on mount and re-read when the window regains
// focus / becomes visible, so a revoke done on the Settings screen (a
// different route → this remounts) or in another window is picked up.

import { useCallback, useEffect, useRef, useState } from "react";
import { useDismissable } from "@/hooks/useDismissable";
import Link from "next/link";
import { FolderOpen, FolderPlus } from "lucide-react";

import { useHostedMode } from "@/hooks/useHostedMode";
import { hostApi } from "@/lib/hostApi";
import { useT } from "@/lib/i18n";
import { toast } from "@/components/ui/Toast";

/** The last path segment, for the pill; the full path stays in the title. */
export function folderBasename(p: string): string {
  const trimmed = p.replace(/[\\/]+$/, "");
  const i = Math.max(trimmed.lastIndexOf("\\"), trimmed.lastIndexOf("/"));
  return i >= 0 ? trimmed.slice(i + 1) || trimmed : trimmed;
}

export function FolderChip({ disabled }: { disabled?: boolean }) {
  const hosted = useHostedMode();
  const t = useT();
  const [folders, setFolders] = useState<string[] | null>(null);
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      setFolders(await hostApi.listFolders());
    } catch {
      /* keep whatever we had; the host answers on the next refresh */
    }
  }, []);

  useEffect(() => {
    if (!hosted) return;
    void refresh();
    const onFocus = () => void refresh();
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [hosted, refresh]);

  // FIX84 row 3 — outside-click AND Escape, through the one shared rule.
  const closePopover = useCallback(() => setOpen(false), []);
  useDismissable(ref, open, closePopover);

  if (!hosted) return null;

  async function addFolder() {
    if (adding) return;
    setAdding(true);
    try {
      const r = await hostApi.pickAndGrantFolder();
      if (!r.ok) {
        if (r.error !== "cancelled") toast(t("folders.chip.addFailed"));
        return;
      }
      if (r.data?.cancelled) return;
      if (r.data?.duplicate) {
        toast(t("folders.chip.duplicate"));
        return;
      }
      if (r.data?.folders) setFolders(r.data.folders);
      else await refresh();
    } finally {
      setAdding(false);
    }
  }

  const count = folders?.length ?? 0;
  const label =
    folders === null
      ? t("folders.chip.title")
      : count === 0
        ? t("folders.chip.none")
        : count === 1
          ? folderBasename(folders[0]!)
          : t("folders.chip.many", { count: String(count) });

  return (
    <div ref={ref} className="relative" data-testid="folder-chip">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="focus-ring flex max-w-[180px] items-center gap-1 rounded-full px-2 py-1 text-xs text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`${t("folders.chip.title")}: ${label}`}
        title={folders && count > 0 ? folders.join("\n") : t("folders.chip.hint")}
        data-testid="folder-chip-trigger"
        data-count={folders === null ? "" : String(count)}
      >
        {count === 0 ? (
          <FolderPlus width={12} height={12} className="text-text-tertiary" aria-hidden />
        ) : (
          <FolderOpen width={12} height={12} className="text-text-tertiary" aria-hidden />
        )}
        <span className={`truncate font-medium ${count === 0 ? "" : "text-text-primary"}`}>{label}</span>
      </button>

      {open ? (
        <>
          <div aria-hidden onClick={() => setOpen(false)} className="fixed inset-0 z-30 bg-black/55 backdrop-blur-sm sm:hidden" />
          <div
            role="dialog"
            aria-label={t("folders.chip.title")}
            className="
              popover-appear sheet-up z-40 flex flex-col overflow-hidden
              border border-border bg-bg-surface text-xs shadow-2xl
              fixed inset-x-0 bottom-0 max-h-[70vh] rounded-t-2xl safe-bottom
              sm:absolute sm:bottom-full sm:left-0 sm:inset-x-auto sm:mb-2
              sm:w-[min(360px,92vw)] sm:max-h-[420px] sm:rounded-xl
            "
          >
            <div className="flex justify-center pt-2 sm:hidden" aria-hidden>
              <span className="h-1 w-10 rounded-full bg-border" />
            </div>
            <div className="px-3 pb-1 pt-3">
              <div className="text-[11px] font-medium uppercase tracking-wider text-text-muted">{t("folders.chip.title")}</div>
              <p className="mt-1 text-text-muted">{t("folders.chip.hint")}</p>
            </div>
            <div className="scrollbar-thin flex flex-col gap-1 overflow-y-auto px-1.5 py-1.5">
              {folders === null ? null : count === 0 ? (
                <p className="px-2.5 py-2 text-text-secondary" data-testid="folder-chip-empty">
                  {t("folders.chip.empty")}
                </p>
              ) : (
                <ul className="flex flex-col gap-1" data-testid="folder-chip-list">
                  {folders.map((f) => (
                    <li key={f} title={f} className="flex items-center gap-2 rounded-md px-2.5 py-1.5">
                      <FolderOpen width={12} height={12} className="shrink-0 text-text-tertiary" aria-hidden />
                      <span className="truncate font-medium text-text-primary">{folderBasename(f)}</span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-muted">{f}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="flex items-center justify-between gap-2 border-t border-border px-2.5 py-2">
              <button
                type="button"
                onClick={() => void addFolder()}
                disabled={adding}
                data-testid="folder-chip-add"
                className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-medium text-text-primary transition-colors hover:bg-bg-elevated disabled:opacity-60"
              >
                <FolderPlus width={13} height={13} aria-hidden />
                {t("folders.chip.add")}
              </button>
              <Link href="/chat/settings" onClick={() => setOpen(false)} className="focus-ring rounded-md px-2 py-1 text-text-muted transition-colors hover:text-text-primary">
                {t("folders.chip.manage")}
              </Link>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

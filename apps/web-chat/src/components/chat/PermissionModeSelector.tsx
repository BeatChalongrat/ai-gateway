"use client";

// FIX25 §1 — the permission-mode selector, in the composer beside the model
// picker (desktop/hosted mode only; a plain browser has no tool consent and
// renders nothing).
//
// Three states, the existing vocabulary — manual | auto | skip — the same
// strings the scheduled-tasks page uses, so the two screens agree. It reads
// and writes the ONE stored value through the existing `settings.get` /
// `settings.set` control-plane ops: no new bridge op, and settings.set is a
// page op the model can never call (control-plane.test.ts pins that). The
// host reads the value per tool call, so a change here applies to the next
// tool call — never to a consent card already answered.
//
// `skip` is visibly different (§1c.2): a warning glyph on the trigger and in
// the menu, and the menu says it resets to "Asks each action" on restart
// (§1c.3 — the host enforces that; this only tells the user).

import { useCallback, useEffect, useRef, useState } from "react";
import { useDismissable } from "@/hooks/useDismissable";
import { AlertTriangle, FastForward, Hand } from "lucide-react";

import { useHostedMode } from "@/hooks/useHostedMode";
import { bridge } from "@/lib/bridge";
import { hostApi } from "@/lib/hostApi";
import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import { toast } from "@/components/ui/Toast";

export type PermissionMode = "manual" | "auto" | "skip";

// FIX26 Part 4 — one distinct mark per mode (the benchmark's): a hand for
// manual ("stop and ask"), a fast-forward for auto, the amber triangle for
// skip. The same icon on the pill and in the menu.
const MODE_ICONS: Record<PermissionMode, typeof Hand> = { manual: Hand, auto: FastForward, skip: AlertTriangle };

export const PERMISSION_MODES: { id: PermissionMode; labelKey: DictKey; hintKey: DictKey }[] = [
  { id: "manual", labelKey: "perm.mode.manual", hintKey: "perm.mode.manual.hint" },
  { id: "auto", labelKey: "perm.mode.auto", hintKey: "perm.mode.auto.hint" },
  { id: "skip", labelKey: "perm.mode.skip", hintKey: "perm.mode.skip.hint" },
];

export function parsePermissionMode(raw: unknown): PermissionMode | null {
  return raw === "manual" || raw === "auto" || raw === "skip" ? raw : null;
}

export function PermissionModeSelector({ disabled }: { disabled?: boolean }) {
  const hosted = useHostedMode();
  const t = useT();
  const [mode, setMode] = useState<PermissionMode | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Read the stored mode on mount (hosted only — the first commit is the
  // hydration pass, so the effect depends on `hosted`, not `[]`), again when
  // the window regains focus, and when the host says it RESET the mode at
  // start (§1c.3): the page can come up and read `skip` in the gap between
  // the sidecar answering and the host writing `manual` back — seen live in
  // FIX25's own restart check — so the reset notice is also the pill's cue.
  useEffect(() => {
    if (!hosted) return;
    let alive = true;
    const read = async () => {
      try {
        const s = await hostApi.getSettings();
        if (alive) setMode(parsePermissionMode(s.approvalMode));
      } catch {
        /* the pill shows a neutral label until the host answers */
      }
    };
    void read();
    const onFocus = () => void read();
    window.addEventListener("focus", onFocus);
    const offNotice = bridge.onHostNotice((n) => {
      if (n.kind === "approval-mode-reset") {
        const to = parsePermissionMode(n.to);
        if (to) setMode(to);
        else void read();
      }
    });
    return () => {
      alive = false;
      window.removeEventListener("focus", onFocus);
      offNotice();
    };
  }, [hosted]);

  // FIX84 row 3 — outside-click AND Escape, through the one shared rule.
  const closePopover = useCallback(() => setOpen(false), []);
  useDismissable(ref, open, closePopover);

  if (!hosted) return null;

  async function pick(next: PermissionMode) {
    setOpen(false);
    if (next === mode || saving) return;
    const previous = mode;
    setMode(next); // optimistic; reverted on refusal
    setSaving(true);
    try {
      const r = await hostApi.setSetting("approvalMode", next);
      if (!r.ok) {
        setMode(previous);
        toast(t("perm.mode.saveFailed"));
      }
    } catch {
      setMode(previous);
      toast(t("perm.mode.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  const current = PERMISSION_MODES.find((m) => m.id === mode) ?? null;
  const isSkip = mode === "skip";

  return (
    <div ref={ref} className="relative" data-testid="permission-mode-selector">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`focus-ring flex items-center gap-1 rounded-full px-2 py-1 text-xs transition-colors hover:bg-bg-elevated disabled:cursor-not-allowed disabled:opacity-40 ${
          isSkip ? "text-amber-500 hover:text-amber-400" : "text-text-secondary hover:text-text-primary"
        }`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={current ? `${t("perm.mode.label")}: ${t(current.labelKey)}` : t("perm.mode.label")}
        title={current ? `${t("perm.mode.label")}: ${t(current.labelKey)}` : t("perm.mode.label")}
        data-testid="permission-mode-trigger"
        data-mode={mode ?? ""}
      >
        {(() => {
          const Icon = mode ? MODE_ICONS[mode] : Hand;
          return <Icon width={12} height={12} className={isSkip ? undefined : "text-text-tertiary"} aria-hidden />;
        })()}
        <span className={`font-medium ${isSkip ? "" : "text-text-primary"}`}>
          {current ? t(current.labelKey) : t("perm.mode.label")}
        </span>
        <svg width={10} height={10} viewBox="0 0 24 24" fill="none" aria-hidden className="text-text-muted">
          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open ? (
        <>
          <div aria-hidden onClick={() => setOpen(false)} className="fixed inset-0 z-30 bg-black/55 backdrop-blur-sm sm:hidden" />
          <div
            role="menu"
            aria-label={t("perm.mode.label")}
            className="
              popover-appear sheet-up z-40 flex flex-col overflow-hidden
              border border-border bg-bg-surface text-xs shadow-2xl
              fixed inset-x-0 bottom-0 rounded-t-2xl safe-bottom
              sm:absolute sm:bottom-full sm:left-0 sm:inset-x-auto sm:mb-2
              sm:w-[min(340px,92vw)] sm:rounded-xl
            "
          >
            <div className="flex justify-center pt-2 sm:hidden" aria-hidden>
              <span className="h-1 w-10 rounded-full bg-border" />
            </div>
            <div className="px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-text-muted">
              {t("perm.mode.label")}
            </div>
            <div className="flex flex-col p-1.5">
              {PERMISSION_MODES.map((m) => {
                const active = m.id === mode;
                const warn = m.id === "skip";
                return (
                  <button
                    key={m.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={active}
                    onClick={() => void pick(m.id)}
                    data-testid={`permission-mode-${m.id}`}
                    className={`flex items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-bg-elevated ${
                      active ? "bg-bg-elevated" : ""
                    }`}
                  >
                    <span className="mt-0.5 shrink-0">
                      {(() => {
                        const Icon = MODE_ICONS[m.id];
                        return <Icon width={14} height={14} className={warn ? "text-amber-500" : active ? "text-text-primary" : "text-text-tertiary"} aria-hidden />;
                      })()}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className={`block font-medium ${warn ? "text-amber-500" : "text-text-primary"}`}>{t(m.labelKey)}</span>
                      <span className="block text-text-muted">{t(m.hintKey)}</span>
                      {warn ? <span className="mt-0.5 block text-amber-500/90">{t("perm.mode.skip.warning")}</span> : null}
                    </span>
                    {active ? (
                      <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden className="mt-0.5 shrink-0 text-text-primary">
                        <path d="M5 12l5 5L20 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

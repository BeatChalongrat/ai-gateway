"use client";

// FIX33 A-W6 / A-W7 / A-D5 — the context meter and "summarize and continue".
//
// QUIET BY DESIGN (spec addendum #1): renders NOTHING until the conversation
// has used 70 % of its context budget. Never a permanent bar, never a number
// below the threshold.
//
// FIX107 Part A2 — the row says what the system does, not what it lacks.
// Approaching the first trim it counts down to an AUTOMATIC summary; once
// trimming is routine it says older messages were summarized for you. The
// numbers ("sees 1 of 18 messages") are still true and still one click away,
// in the details popover, next to the manual control — which used to sit on
// the row as the loudest thing there, reading as if the system were waiting
// for the user when it had already acted.
//
// FIX130 C3.5 — two shapes of the same meter. "header" is the row in the
// desktop header (hidden below md, as the whole header is). "compact" is a
// tappable chip above the composer on a phone: NIGHT2 F7 measured the header
// row at 0 × 0 at 360 px, so a phone user was never told that 32 of 40
// messages were no longer sent. Both now say HOW MANY are sent
// ("ส่งให้ผู้ช่วย 8 จาก 40 ข้อความ") — the header row's label is clickable too
// and opens the same details (NIGHT2 F10: 11-px grey text that did nothing).
// This supersedes FIX107's "no counts on the row": the count is the fact the
// person needs, and hiding it behind a chevron is how it went unseen.
//
// Data: on the web it asks GET /context-info (a dry run of the compiler)
// when the conversation opens and after each turn ends; in the desktop shell
// the host pushes the same numbers as `context_budget` turn events and the
// `context.info` op answers the on-open read. Both paths land in
// chatStore.contextBudgetByConv, so this component is the same on both.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ChevronDown, Info } from "lucide-react";

import { useHostedMode } from "@/hooks/useHostedMode";
import { api, ApiError } from "@/lib/api";
import { COMPACT_TRIGGER_RATIO, headLeftOut, isTrimming, messagesSeen, meterPercent, shouldShowMeter } from "@/lib/compaction";
import { hostApi } from "@/lib/hostApi";
import { useT } from "@/lib/i18n";
import { memoryModeOf } from "@/lib/memoryMode";
import type { ContextInfo } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { toast, toastError } from "@/components/ui/Toast";

// Where the details open: under the header row; above the phone chip (it
// sits just over the composer), as wide as the screen allows.
const PANEL_UNDER_ROW = "absolute right-0 top-full z-20 mt-1 w-80";
const PANEL_ABOVE_CHIP = "absolute bottom-full left-4 z-20 mb-1 w-[calc(100vw-2rem)] max-w-80";

export function ContextMeter({
  conversationId,
  variant = "header",
  passive = false,
}: {
  conversationId: string;
  variant?: "header" | "compact";
  /** FIX130 — read the numbers only; another instance (the header's) fetches them. */
  passive?: boolean;
}) {
  const t = useT();
  const hosted = useHostedMode();
  const info = useChatStore((s) => s.contextBudgetByConv[conversationId] ?? null);
  const setContextBudget = useChatStore((s) => s.setContextBudget);
  const messageCount = useChatStore((s) => (s.messages[conversationId] ?? []).length);
  const streaming = useChatStore((s) => (s.messages[conversationId] ?? []).some((m) => m.isStreaming));
  const [open, setOpen] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  // FIX132 N3 — a summary is kept in the chat's memory; with memory off (or
  // a temporary chat) the server refuses it every time. Say so inline and
  // offer nothing that can only fail.
  const memoryMode = useChatStore((s) => memoryModeOf(s.conversations.find((c) => c.id === conversationId)));
  const canSummarize = memoryMode === "on";
  const unavailableId = useId();

  const refresh = useCallback(async () => {
    try {
      const next: ContextInfo = hosted ? await hostApi.contextInfo(conversationId) : await api.getContextInfo(conversationId);
      setContextBudget(conversationId, next);
    } catch {
      /* the meter is informational — a failed read just leaves the last numbers */
    }
  }, [conversationId, hosted, setContextBudget]);

  // On open, and after every turn ends (message count changes while not
  // streaming). In hosted mode the host also pushes live values mid-turn.
  useEffect(() => {
    if (streaming || passive) return;
    void refresh();
  }, [refresh, messageCount, streaming, passive]);

  const panelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  if (!shouldShowMeter(info)) return null;
  // FIX102 — two states, never a number over what is sent. Approaching the
  // first trim: a countdown to the automatic summary. Trimming routinely: a
  // short, settled line; the counts live in the details.
  const trimming = isTrimming(info!);
  let label: string;
  let chip: string;
  let detail: string;
  let tokensTitle: string | undefined;
  if (trimming) {
    const { sent, total } = messagesSeen(info!);
    const counts = { sent: String(sent), total: String(total) };
    // FIX105 — when the start of the conversation is among what is left out, say so.
    const head = headLeftOut(info!);
    const key = info!.state_present ? (head ? "context.rolling.headSummarized" : "context.rolling") : head ? "context.rolling.headNoSummary" : "context.rolling.noSummary";
    label = t(info!.state_present ? "context.label.summarizedCount" : "context.label.noSummaryCount", counts);
    chip = t("context.chip.sent", counts);
    detail = t(key, counts);
    tokensTitle = typeof info!.sent_tokens === "number" ? t("context.tokensTitle", { used: info!.sent_tokens.toLocaleString(), budget: info!.messages_budget.toLocaleString() }) : undefined;
  } else {
    const pct = meterPercent(info!);
    const n = Math.max(0, info!.est_messages_remaining);
    label = n > 0 && !info!.over_budget ? t("context.meter", { pct: String(pct), n: String(n) }) : t("context.meter.full", { pct: String(pct) });
    chip = t("context.chip.approaching", { pct: String(pct) });
    detail = t("context.detail.approaching", { trigger: String(Math.round(COMPACT_TRIGGER_RATIO * 100)) });
    tokensTitle = t("context.tokensTitle", { used: info!.history_tokens.toLocaleString(), budget: info!.messages_budget.toLocaleString() });
  }
  // "Nothing to do" is true whenever the system summarizes on its own; with no
  // summary (memory off) there is nothing it will do, so it is not said.
  const automatic = !trimming || info!.state_present;

  async function submit() {
    if (busy || !canSummarize) return;
    setBusy(true);
    try {
      if (hosted) await hostApi.compactContext(conversationId, instructions);
      else await api.compactConversation(conversationId, instructions);
      toast(t("context.compact.done"));
      setOpen(false);
      setInstructions("");
      await refresh();
    } catch (err) {
      const memoryOff = err instanceof ApiError && err.status === 400;
      const hostMemoryOff = err instanceof Error && /memory/i.test(err.message);
      toastError(memoryOff || hostMemoryOff ? t("context.compact.memoryOff") : t("context.compact.failed"));
    } finally {
      setBusy(false);
    }
  }

  const toggle = () => setOpen((v) => !v);
  if (variant === "compact") {
    return (
      <div className="relative px-4 pt-2" data-testid="context-meter-compact">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-label={`${t("context.meter.aria")}: ${chip}`}
          data-state={trimming ? "trimming" : "approaching"}
          className={`focus-ring inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${
            trimming ? "border-status-warning/40 bg-status-warning/10 text-text-primary" : "border-border bg-bg-surface text-text-secondary"
          }`}
        >
          <Info size={12} aria-hidden="true" className="shrink-0" />
          <span role="status" className="truncate">{chip}</span>
          <ChevronDown size={12} aria-hidden="true" className={open ? "shrink-0 rotate-180 transition-transform" : "shrink-0 transition-transform"} />
        </button>
        {open ? renderPanel(PANEL_ABOVE_CHIP) : null}
      </div>
    );
  }

  return (
    <div className="relative flex items-center gap-1" data-testid="context-meter">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        disabled={busy}
        data-testid="context-meter-label"
        className="focus-ring rounded text-left text-[11px] text-text-muted transition-colors hover:text-text-secondary"
      >
        <span
          role="status"
          aria-label={t("context.meter.aria")}
          title={tokensTitle}
          data-state={trimming ? "trimming" : "approaching"}
        >
          {label}
        </span>
      </button>
      <button
        type="button"
        className="focus-ring rounded p-0.5 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-secondary disabled:opacity-50"
        onClick={() => setOpen((v) => !v)}
        disabled={busy || streaming}
        aria-expanded={open}
        aria-label={t("context.details")}
        title={t("context.details")}
        data-testid="context-compact-button"
      >
        <ChevronDown size={12} aria-hidden="true" className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>
      {open ? renderPanel(PANEL_UNDER_ROW) : null}
    </div>
  );

  function renderPanel(position: string) {
    return (
      <div
        ref={panelRef}
        tabIndex={-1}
        className={`${position} rounded-lg border border-border bg-bg-elevated p-3 shadow-lg outline-none`}
        role="dialog"
        aria-label={t("context.detail.title")}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
      >
        <p className="text-xs font-medium text-text-primary">{t("context.detail.title")}</p>
        <p className="mt-1 text-[11px] text-text-secondary" data-testid="context-detail">
          {detail}
        </p>
        {automatic ? <p className="mt-1 text-[11px] text-text-muted">{t("context.detail.auto")}</p> : null}
        <div className="mt-3 border-t border-border pt-2">
          <p className="text-[11px] font-medium text-text-secondary">{t("context.compact.title")}</p>
          {canSummarize ? (
            <>
              <p className="mt-1 text-[11px] text-text-muted">{t("context.compact.desc")}</p>
              <textarea
                className="focus-ring mt-2 w-full resize-none rounded-md border border-border bg-bg-base p-2 text-xs text-text-primary"
                rows={2}
                maxLength={2000}
                placeholder={t("context.compact.placeholder")}
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
              />
            </>
          ) : (
            <p id={unavailableId} className="mt-1 text-[11px] text-text-secondary" data-testid="context-compact-unavailable">
              {t("context.compact.memoryOff")}
            </p>
          )}
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" className="focus-ring rounded-md px-2 py-1 text-xs text-text-muted hover:text-text-primary" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="focus-ring rounded-md border border-border px-3 py-1 text-xs text-text-secondary hover:bg-bg-base hover:text-text-primary disabled:opacity-50"
              onClick={() => void submit()}
              disabled={busy || streaming || !canSummarize}
              aria-describedby={canSummarize ? undefined : unavailableId}
              data-testid="context-compact-submit"
            >
              {busy ? t("context.compact.working") : t("context.compact.submit")}
            </button>
          </div>
        </div>
      </div>
    );
  }
}

"use client";

// FIX33 A-W6 / A-W7 / A-D5 — the context meter and "summarize and continue".
//
// QUIET BY DESIGN (spec addendum #1): renders NOTHING until the conversation
// has used 70 % of its context budget. From there: one muted line in the
// header ("Context 74% used · ~12 messages left"), a "summarized" mark once
// older messages have been folded, and the "Summarize and continue" control
// with an optional instructions box (Claude's `instructions`). Never a
// permanent bar, never a number below the threshold.
//
// Data: on the web it asks GET /context-info (a dry run of the compiler)
// when the conversation opens and after each turn ends; in the desktop shell
// the host pushes the same numbers as `context_budget` turn events and the
// `context.info` op answers the on-open read. Both paths land in
// chatStore.contextBudgetByConv, so this component is the same on both.

import { useCallback, useEffect, useRef, useState } from "react";

import { useHostedMode } from "@/hooks/useHostedMode";
import { api, ApiError } from "@/lib/api";
import { isTrimming, messagesSeen, meterPercent, shouldShowMeter } from "@/lib/compaction";
import { hostApi } from "@/lib/hostApi";
import { useT } from "@/lib/i18n";
import type { ContextInfo } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { toast } from "@/components/ui/Toast";

export function ContextMeter({ conversationId }: { conversationId: string }) {
  const t = useT();
  const hosted = useHostedMode();
  const info = useChatStore((s) => s.contextBudgetByConv[conversationId] ?? null);
  const setContextBudget = useChatStore((s) => s.setContextBudget);
  const messageCount = useChatStore((s) => (s.messages[conversationId] ?? []).length);
  const streaming = useChatStore((s) => (s.messages[conversationId] ?? []).some((m) => m.isStreaming));
  const [open, setOpen] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const boxRef = useRef<HTMLTextAreaElement | null>(null);

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
    if (streaming) return;
    void refresh();
  }, [refresh, messageCount, streaming]);

  useEffect(() => {
    if (open) boxRef.current?.focus();
  }, [open]);

  if (!shouldShowMeter(info)) return null;
  // FIX102 — two states, never a number over what is sent. Approaching the
  // first trim: a countdown. Trimming routinely: what the assistant sees.
  const trimming = isTrimming(info!);
  let label: string;
  let tokensTitle: string | undefined;
  if (trimming) {
    const { sent, total } = messagesSeen(info!);
    label = t(info!.state_present ? "context.rolling" : "context.rolling.noSummary", { sent: String(sent), total: String(total) });
    tokensTitle = typeof info!.sent_tokens === "number" ? `${info!.sent_tokens.toLocaleString()} / ${info!.messages_budget.toLocaleString()} tokens` : undefined;
  } else {
    const pct = meterPercent(info!);
    const n = Math.max(0, info!.est_messages_remaining);
    label = n > 0 && !info!.over_budget ? t("context.meter", { pct: String(pct), n: String(n) }) : t("context.meter.full", { pct: String(pct) });
    tokensTitle = `${info!.history_tokens.toLocaleString()} / ${info!.messages_budget.toLocaleString()} tokens`;
  }

  async function submit() {
    if (busy) return;
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
      toast(memoryOff || hostMemoryOff ? t("context.compact.memoryOff") : t("context.compact.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex items-center gap-2" data-testid="context-meter">
      <span
        className="text-[11px] text-text-muted"
        role="status"
        aria-label={t("context.meter.aria")}
        title={tokensTitle}
        data-state={trimming ? "trimming" : "approaching"}
      >
        {label}
      </span>
      <button
        type="button"
        className="focus-ring rounded-md border border-border px-2 py-0.5 text-[11px] text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:opacity-50"
        onClick={() => setOpen((v) => !v)}
        disabled={busy || streaming}
        aria-expanded={open}
        data-testid="context-compact-button"
      >
        {busy ? t("context.compact.working") : t("context.compactNow")}
      </button>
      {open ? (
        <div
          className="absolute right-0 top-full z-20 mt-1 w-80 rounded-lg border border-border bg-bg-elevated p-3 shadow-lg"
          role="dialog"
          aria-label={t("context.compact.title")}
        >
          <p className="text-xs font-medium text-text-primary">{t("context.compact.title")}</p>
          <p className="mt-1 text-[11px] text-text-muted">{t("context.compact.desc")}</p>
          <textarea
            ref={boxRef}
            className="focus-ring mt-2 w-full resize-none rounded-md border border-border bg-bg-base p-2 text-xs text-text-primary"
            rows={3}
            maxLength={2000}
            placeholder={t("context.compact.placeholder")}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
            }}
          />
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" className="focus-ring rounded-md px-2 py-1 text-xs text-text-muted hover:text-text-primary" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="focus-ring rounded-md bg-accent px-3 py-1 text-xs font-medium text-bg-base disabled:opacity-50"
              onClick={() => void submit()}
              disabled={busy}
              data-testid="context-compact-submit"
            >
              {busy ? t("context.compact.working") : t("context.compact.submit")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

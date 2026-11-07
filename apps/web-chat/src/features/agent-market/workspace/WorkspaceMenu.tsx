"use client";

// AM4 — everything in the workspace header but its one obvious action
// ("ที่มาของตัวเลข"): other chats with this agent, a new chat, pin, the
// overview, settings and "report a problem", in a single menu.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { ChartColumn, Check, MessageSquarePlus, MessageSquareWarning, MoreHorizontal, Pin, PinOff, Settings } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

import { amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import { ProblemDialog } from "../problem/ProblemButton";
import type { AgentThread } from "../types";
import { cls } from "../ui";
import { dashboardHref, settingsHref, workspaceHref } from "./context";

const ITEM =
  "focus-ring flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-elevated disabled:opacity-50";

export function WorkspaceMenu({
  installId,
  threadId,
  pinned,
  onPin,
}: {
  installId: string;
  threadId: string | null;
  pinned: boolean;
  onPin: () => void;
}) {
  const t = useAmT();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState(false);
  const [threads, setThreads] = useState<AgentThread[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    let alive = true;
    amApi.threads(installId).then(
      (r) => alive && setThreads(r?.threads ?? []),
      () => alive && setThreads([]),
    );
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      alive = false;
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, installId]);

  const newChat = () => {
    setBusy(true);
    setError(null);
    amApi.newThread(installId).then(
      (r) => {
        setBusy(false);
        setOpen(false);
        // AM11b: the button opens the empty chat that is already there — the first chat has no id
        if (r?.thread) router.push(workspaceHref(installId, r.thread.id));
      },
      (err) => {
        setBusy(false);
        setError(`${t("ws.newChatFailed")}: ${errorMessage(err)}`);
      },
    );
  };

  return (
    <div ref={ref} className="relative">
      <Button
        type="button"
        variant="secondary"
        size="sm"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={t("ws.more")}
        title={t("ws.more")}
        onClick={() => setOpen((v) => !v)}
        data-testid="am-ws-more"
      >
        <MoreHorizontal width={16} height={16} aria-hidden />
      </Button>
      {open ? (
        <div
          id={menuId}
          role="menu"
          className="absolute right-0 top-full z-30 mt-1.5 w-72 overflow-hidden rounded-xl border border-border bg-bg-surface py-1 shadow-lg"
          data-testid="am-ws-menu"
        >
          <button type="button" role="menuitem" onClick={newChat} disabled={busy} className={ITEM} data-testid="am-ws-new-chat">
            {busy ? <Spinner size={14} /> : <MessageSquarePlus width={15} height={15} aria-hidden />}
            <span>{t("ws.menu.newChat")}</span>
          </button>
          {threads && threads.length > 1 ? (
            <div className="border-y border-border-subtle py-1" role="group" aria-label={t("ws.menu.chats")}>
              <p className="px-3 pb-1 pt-1.5 text-[11px] font-medium text-text-muted">{t("ws.menu.chats")}</p>
              {threads.map((th) => {
                const current = (th.id ?? null) === (threadId ?? null);
                return (
                  <Link
                    key={th.id ?? "main"}
                    href={workspaceHref(installId, th.id)}
                    role="menuitem"
                    aria-current={current ? "page" : undefined}
                    onClick={() => setOpen(false)}
                    className={cls(ITEM, current ? "font-medium" : "")}
                    data-testid="am-ws-thread"
                  >
                    <span className="inline-flex w-[15px] justify-center" aria-hidden>
                      {current ? <Check width={14} height={14} /> : null}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{th.title}</span>
                    <span className="shrink-0 text-[11px] text-text-muted">
                      {th.waiting ? t("ws.chatWaiting") : t("ws.chatRounds", { n: String(th.rounds) })}
                    </span>
                  </Link>
                );
              })}
            </div>
          ) : null}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onPin();
            }}
            className={ITEM}
            data-testid="am-ws-pin"
          >
            {pinned ? <PinOff width={15} height={15} aria-hidden /> : <Pin width={15} height={15} aria-hidden />}
            <span>{pinned ? t("ws.menu.unpin") : t("ws.menu.pin")}</span>
          </button>
          <Link href={dashboardHref(installId)} role="menuitem" className={ITEM} data-testid="am-ws-dashboard">
            <ChartColumn width={15} height={15} aria-hidden />
            <span>{t("ws.menu.overview")}</span>
          </Link>
          <Link href={settingsHref(installId)} role="menuitem" className={ITEM} data-testid="am-ws-settings">
            <Settings width={15} height={15} aria-hidden />
            <span>{t("ws.menu.settings")}</span>
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              setProblem(true);
            }}
            className={ITEM}
            data-testid="am-problem-open"
          >
            <MessageSquareWarning width={15} height={15} aria-hidden />
            <span>{t("ws.menu.problem")}</span>
          </button>
          {error ? (
            <p role="alert" className="px-3 py-1.5 text-xs text-status-danger">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
      <ProblemDialog target={{ kind: "install", installId }} open={problem} onClose={() => setProblem(false)} />
    </div>
  );
}

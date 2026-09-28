"use client";

// /chat/agents/i/[installId] — the agent workspace (board 3), the primary
// surface: an inner rail of my agents (md+), the agent's header, the thread
// (loaded, then followed live) and the composer.
//
// AM4: one obvious action in the header — "ที่มาของตัวเลข" — and everything
// else (other chats, pin, overview, settings, report a problem) in one menu;
// an empty workspace says in one loud sentence what to drop in; a finished
// round closes with a divider and "เริ่มรอบใหม่"; ?thread=<id> shows another
// chat with the same agent.

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ListChecks, Plus } from "lucide-react";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";

import { AmError, amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import type { InstallDetail, InstallRow, ProgressPayload, ThreadItem } from "../types";
import { AgentTile, BTN_SECONDARY, BTN_SMALL, cls, ErrorPanel, FeatureGate, PageShell, Spin } from "../ui";
import { notifyInstallsChanged, onInstallsChanged, useAgentMarket } from "../useAgentMarket";
import { togglePin } from "../useRail";
import { BudgetBanner } from "./BudgetBanner";
import { Composer, type ComposerHandle } from "./Composer";
import { provenanceHref, WorkspaceContext, workspaceHref, type WorkspaceCtx } from "./context";
import { EmptyDrop } from "./EmptyDrop";
import { ModelControl, useModelPicker } from "./ModelControl";
import { hasOwnRound, latestRound } from "./rounds";
import { ThreadView } from "./ThreadView";
import { UploadInfo } from "./UploadInfo";
import { useThread } from "./useThread";
import { acceptFromManifest, DEFAULT_MAX_MB } from "./useUploads";
import { WorkspaceMenu } from "./WorkspaceMenu";

export function WorkspacePage({ installId, threadId = null }: { installId: string; threadId?: string | null }) {
  return (
    <FeatureGate>
      <Workspace installId={installId} threadId={threadId || null} />
    </FeatureGate>
  );
}

function useInstalls(): InstallRow[] {
  const [rows, setRows] = useState<InstallRow[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () =>
      amApi.installs().then(
        (r) => alive && setRows(r?.installs ?? []),
        () => {},
      );
    void load();
    const off = onInstallsChanged(() => void load());
    return () => {
      alive = false;
      off();
    };
  }, []);
  return rows;
}

function Workspace({ installId, threadId }: { installId: string; threadId: string | null }) {
  const t = useAmT();
  const snap = useAgentMarket();
  const [detail, setDetail] = useState<InstallDetail | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const installs = useInstalls();
  const thread = useThread(installId, threadId);
  const composerRef = useRef<ComposerHandle>(null);
  const [pinnedAt, setPinnedAt] = useState<string | null | undefined>(undefined);
  const [picker, setPicker] = useModelPicker(installId);
  const [roundModel, setRoundModel] = useState<string | null>(null);

  // AM3: "recently used" in the rail starts from opening the workspace.
  useEffect(() => {
    amApi.opened(installId).then(
      () => {},
      () => {},
    );
  }, [installId]);

  useEffect(() => {
    let alive = true;
    setLoadError(null);
    amApi.getInstall(installId).then(
      (d) => alive && setDetail(d),
      (err) => alive && setLoadError(err),
    );
    return () => {
      alive = false;
    };
  }, [installId, reload]);

  // The rail's attention dots follow the thread: refresh the list when a
  // run in this thread changes state.
  const progressSig = useMemo(
    () =>
      thread.items
        .filter((i) => i.kind === "progress")
        .map((i) => `${i.id}:${(i.payload as ProgressPayload)?.status}`)
        .join(","),
    [thread.items],
  );
  const firstSig = useRef(true);
  useEffect(() => {
    if (firstSig.current) {
      firstSig.current = false;
      return;
    }
    // One list read, debounced; the sidebar section refreshes with it.
    const id = setTimeout(notifyInstallsChanged, 800);
    return () => clearTimeout(id);
  }, [progressSig]);

  const manifest = detail?.manifest;
  const install = detail?.install ?? installs.find((i) => i.id === installId);
  const pinned = pinnedAt !== undefined ? !!pinnedAt : !!detail?.install?.pinned_at;
  const onPin = () => {
    const was = pinned;
    setPinnedAt(was ? null : new Date().toISOString());
    togglePin(installId, was).then(
      (at) => {
        setPinnedAt(at);
        notifyInstallsChanged();
      },
      () => setPinnedAt(was ? detail?.install?.pinned_at ?? new Date().toISOString() : null),
    );
  };
  const icon = manifest?.icon ?? installs.find((i) => i.id === installId)?.icon;
  const title = manifest?.greeting?.role || install?.title || "";
  const tagline = manifest?.greeting?.tagline || "";
  // What the server takes for this agent (its inputs' accept lists), nothing more.
  const accept = useMemo(() => acceptFromManifest(manifest), [manifest]);
  const maxMb = manifest?.limits?.max_file_mb || DEFAULT_MAX_MB;
  const photoNotice = manifest?.photo_notice || undefined;

  // AM4: "ที่มาของตัวเลข" opens the latest round of the person's own files.
  const latest = useMemo(() => latestRound(thread.items), [thread.items]);
  const empty = thread.status !== "loading" && !hasOwnRound(thread.items);

  // the self-tests in this thread (their files are the sample's, not to be signed off)
  const selfTestSig = useMemo(
    () =>
      thread.items
        .filter((i) => i.kind === "progress" && (i.payload as ProgressPayload)?.kind === "self_test")
        .map((i) => (i.payload as ProgressPayload).run_id)
        .join(","),
    [thread.items],
  );
  const claimedAt = useRef(0);
  const ctx: WorkspaceCtx = useMemo(
    () => ({
      installId,
      icon,
      agentName: title,
      merge: thread.merge,
      accept,
      maxMb,
      photoNotice,
      claimDrop: () => {
        claimedAt.current = Date.now();
      },
      selfTestRuns: new Set(selfTestSig ? selfTestSig.split(",") : []),
      threadId,
      focusComposer: () => composerRef.current?.focus(),
      admin: snap.admin,
    }),
    [installId, icon, title, thread.merge, accept, maxMb, photoNotice, selfTestSig, threadId, snap.admin],
  );

  const onDropFiles = useCallback((files: File[]) => {
    if (Date.now() - claimedAt.current < 300) return; // a card's slot took them
    composerRef.current?.addFiles(files);
  }, []);

  const onSent = useCallback(
    (item: ThreadItem | undefined) => {
      if (item) thread.merge([item]);
    },
    [thread],
  );

  const notFound = loadError instanceof AmError && (loadError.status === 404 || loadError.status === 400);
  if (notFound) {
    return (
      <PageShell width="max-w-xl">
        <div className="mt-16 flex flex-col items-center text-center" data-testid="am-ws-not-found">
          <h1 className="text-lg font-semibold text-text-primary">{t("ws.notFound.title")}</h1>
          <p className="mt-1.5 text-sm text-text-secondary">{t("ws.notFound.body")}</p>
          <Link href="/chat/agents" className={cls(BTN_SECONDARY, "mt-6")}>
            {t("ws.backToMarket")}
          </Link>
        </div>
      </PageShell>
    );
  }

  return (
    <WorkspaceContext.Provider value={ctx}>
      <div className="flex min-h-0 flex-1" data-testid="am-workspace">
        <Rail installs={installs} activeId={installId} />
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex shrink-0 items-center gap-3 border-b border-border-subtle px-4 py-2.5 md:px-6">
            <div className="md:hidden">
              <AgentTile icon={icon} size={28} />
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="min-h-[1.5rem] truncate text-base font-semibold text-text-primary">{title}</h1>
              {tagline ? <p className="truncate text-xs text-text-muted">{tagline}</p> : null}
            </div>
            {latest ? (
              <Link
                href={provenanceHref(latest.runId)}
                className={BTN_SMALL}
                title={t("ws.provenanceTitle")}
                data-testid="am-ws-view-run"
              >
                <ListChecks width={15} height={15} aria-hidden />
                <span>{t("ws.provenance")}</span>
              </Link>
            ) : null}
            <WorkspaceMenu installId={installId} threadId={threadId} pinned={pinned} onPin={onPin} />
          </header>

          <AttachmentDropZone onFiles={onDropFiles} className="flex min-h-0 flex-1 flex-col">
            <ThreadScroller items={thread.items} status={thread.status}>
              {loadError && !detail ? (
                <ErrorPanel message={errorMessage(loadError)} title={t("ws.loadFailed")} onRetry={() => setReload((n) => n + 1)} />
              ) : null}
              {thread.status === "error" ? (
                <ErrorPanel message={errorMessage(thread.error)} title={t("ws.loadFailed")} onRetry={thread.retry} />
              ) : null}
              <ThreadView items={thread.items} />
              {empty && manifest ? <EmptyDrop manifest={manifest} onPick={() => composerRef.current?.pickFiles()} /> : null}
            </ThreadScroller>
            <div className="px-4 md:px-10">
              <BudgetBanner installId={installId} refreshKey={progressSig} />
            </div>
            <Composer
              ref={composerRef}
              installId={installId}
              onSent={(item) => {
                setRoundModel(null);
                onSent(item);
              }}
              accept={accept}
              maxMb={maxMb}
              photoNotice={photoNotice}
              threadId={threadId}
              modelChoice={roundModel}
              tools={<ModelControl installId={installId} picker={picker} onPicker={setPicker} roundChoice={roundModel} onRoundChoice={setRoundModel} />}
              footer={
                <div className="flex flex-col items-center gap-0.5" data-testid="am-ws-footer">
                  {manifest?.cannot ? <p className="text-center text-[11px] text-text-muted">{manifest.cannot}</p> : null}
                  {!empty ? <UploadInfo manifest={manifest} center /> : null}
                </div>
              }
            />
          </AttachmentDropZone>
        </div>
      </div>
    </WorkspaceContext.Provider>
  );
}

function ThreadScroller({ items, status, children }: { items: ThreadItem[]; status: string; children: React.ReactNode }) {
  const t = useAmT();
  const ref = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const seeded = useRef(false);
  const sig = items.length ? `${items.length}:${items[items.length - 1]!.id}:${items.reduce((m, i) => Math.max(m, i.rev), 0)}` : "";

  // Follow the bottom while the reader is there; land at the bottom once
  // the thread first loads.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !items.length) return;
    if (!seeded.current || atBottom.current) {
      el.scrollTop = el.scrollHeight;
      seeded.current = true;
    }
  }, [sig, items.length]);

  return (
    <div
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      }}
      className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-10"
      aria-live="polite"
      aria-busy={status === "loading"}
      aria-label={t("ws.threadAria")}
      role="log"
      data-testid="am-thread"
    >
      <div className="mx-auto flex w-full max-w-chat flex-col gap-4 pb-10">
        {status === "loading" && !items.length ? (
          <div className="flex items-center gap-2 text-sm text-text-muted">
            <Spin size={14} />
            <span>{t("common.loading")}</span>
          </div>
        ) : null}
        {children}
        {status === "reconnecting" ? (
          <div role="status" className="flex items-center gap-2 self-center text-xs text-text-muted">
            <Spin size={12} />
            <span>{t("ws.reconnecting")}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** AM4: the workspace's own list is capped like the left rail (the rest are one click away). */
export const RAIL_CAP = 6;

export function railShown(installs: InstallRow[], activeId: string): InstallRow[] {
  const head = installs.slice(0, RAIL_CAP);
  if (head.some((i) => i.id === activeId)) return head;
  const active = installs.find((i) => i.id === activeId);
  return active ? [...head.slice(0, RAIL_CAP - 1), active] : head;
}

function Rail({ installs, activeId }: { installs: InstallRow[]; activeId: string }) {
  const t = useAmT();
  const shown = railShown(installs, activeId);
  return (
    <nav aria-label={t("ws.agentsNav")} className="hidden w-[200px] shrink-0 flex-col gap-0.5 border-r border-border-subtle px-2 py-4 md:flex" data-testid="am-rail">
      <div className="px-2.5 pb-2 text-[11px] font-medium tracking-wide text-text-muted">{t("ws.myAgents")}</div>
      {shown.map((i) => {
        const active = i.id === activeId;
        return (
          <Link
            key={i.id}
            href={workspaceHref(i.id)}
            aria-current={active ? "page" : undefined}
            className={cls(
              "focus-ring flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors hover:bg-bg-elevated hover:text-text-primary",
              active ? "bg-bg-elevated font-medium text-text-primary" : "text-text-secondary",
            )}
          >
            <AgentTile icon={i.icon} size={24} quiet={!active} />
            <span className="min-w-0 flex-1 truncate">{i.title}</span>
            {i.attention ? (
              <span className="h-2 w-2 shrink-0 rounded-full bg-status-warning" title={t("rail.attention")}>
                <span className="sr-only">{t("rail.attention")}</span>
              </span>
            ) : null}
          </Link>
        );
      })}
      {installs.length > shown.length ? (
        <Link
          href="/chat/agents/mine"
          className="focus-ring rounded-lg px-2.5 py-1.5 text-[12px] text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
          data-testid="am-rail-all"
        >
          {t("rail.all", { n: String(installs.length) })}
        </Link>
      ) : null}
      <Link
        href="/chat/agents"
        className="focus-ring mt-1 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      >
        <span className="inline-flex h-6 w-6 items-center justify-center" aria-hidden>
          <Plus width={15} height={15} />
        </span>
        <span>{t("ws.addFromMarket")}</span>
      </Link>
    </nav>
  );
}


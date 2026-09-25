"use client";

// /chat/agents/i/[installId] — the agent workspace (board 3), the primary
// surface: an inner rail of my agents (md+), the agent's header, the thread
// (loaded, then followed live) and the composer.

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ListChecks, Plus, Settings } from "lucide-react";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";

import { AmError, amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import type { InstallDetail, InstallRow, OutputsPayload, ProgressPayload, ThreadItem } from "../types";
import { AgentTile, BTN_SECONDARY, BTN_SMALL, cls, ErrorPanel, FeatureGate, PageShell, Spin } from "../ui";
import { notifyInstallsChanged, onInstallsChanged } from "../useAgentMarket";
import { Composer, type ComposerHandle } from "./Composer";
import { runHref, settingsHref, WorkspaceContext, workspaceHref, type WorkspaceCtx } from "./context";
import { ThreadView } from "./ThreadView";
import { useThread } from "./useThread";
import { DEFAULT_ACCEPT, DEFAULT_MAX_MB } from "./useUploads";

export function WorkspacePage({ installId }: { installId: string }) {
  return (
    <FeatureGate>
      <Workspace installId={installId} />
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

function Workspace({ installId }: { installId: string }) {
  const t = useAmT();
  const [detail, setDetail] = useState<InstallDetail | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const installs = useInstalls();
  const thread = useThread(installId);
  const composerRef = useRef<ComposerHandle>(null);

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
  const icon = manifest?.icon ?? installs.find((i) => i.id === installId)?.icon;
  const title = manifest?.greeting?.role || install?.title || "";
  const tagline = manifest?.greeting?.tagline || "";
  const accept = useMemo(() => {
    const all = (manifest?.inputs ?? []).flatMap((i) => i.accept ?? []);
    return all.length ? [...new Set([...all, ".pdf", ".xlsm"].map((a) => a.toLowerCase()))] : DEFAULT_ACCEPT;
  }, [manifest]);
  const maxMb = manifest?.limits?.max_file_mb || DEFAULT_MAX_MB;

  // The latest run with a progress card is the one "ดูขั้นตอน…" opens.
  const latestRunId = useMemo(() => {
    for (let i = thread.items.length - 1; i >= 0; i--) {
      const it = thread.items[i]!;
      if (it.kind === "progress") return (it.payload as ProgressPayload)?.run_id ?? it.run_id ?? null;
    }
    return null;
  }, [thread.items]);
  const lastModel = useMemo(() => {
    for (let i = thread.items.length - 1; i >= 0; i--) {
      const it = thread.items[i]!;
      if (it.kind === "outputs") return (it.payload as OutputsPayload)?.model ?? null;
    }
    return null;
  }, [thread.items]);

  const claimedAt = useRef(0);
  const ctx: WorkspaceCtx = useMemo(
    () => ({
      installId,
      icon,
      agentName: title,
      merge: thread.merge,
      accept,
      maxMb,
      claimDrop: () => {
        claimedAt.current = Date.now();
      },
    }),
    [installId, icon, title, thread.merge, accept, maxMb],
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
            {latestRunId ? (
              <Link href={runHref(latestRunId)} className={BTN_SMALL} data-testid="am-ws-view-run">
                <ListChecks width={15} height={15} aria-hidden />
                <span className="hidden sm:inline">{t("ws.viewRun")}</span>
                <span className="sm:hidden">{t("ws.viewRunShort")}</span>
              </Link>
            ) : (
              <span className="hidden text-xs text-text-muted lg:inline">{t("ws.noRunYet")}</span>
            )}
            <Link
              href={settingsHref(installId)}
              className={BTN_SMALL}
              aria-label={t("ws.settings")}
              data-testid="am-ws-settings"
            >
              <Settings width={15} height={15} aria-hidden />
              <span className="hidden sm:inline">{t("ws.settings")}</span>
            </Link>
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
            </ThreadScroller>
            <Composer
              ref={composerRef}
              installId={installId}
              onSent={onSent}
              accept={accept}
              maxMb={maxMb}
              footer={
                <p className="text-center text-[11px] text-text-muted" data-testid="am-ws-footer">
                  {manifest?.cannot}
                  {lastModel?.model ? (
                    <span className="whitespace-nowrap">{` · ${lastModel.self_hosted ? t("ws.modelInOrg") : t("ws.modelExternal")}`}</span>
                  ) : null}
                </p>
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
      className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8"
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

function Rail({ installs, activeId }: { installs: InstallRow[]; activeId: string }) {
  const t = useAmT();
  return (
    <nav aria-label={t("ws.agentsNav")} className="hidden w-[200px] shrink-0 flex-col gap-0.5 border-r border-border-subtle px-2 py-4 md:flex" data-testid="am-rail">
      <div className="px-2.5 pb-2 text-[11px] font-medium tracking-wide text-text-muted">{t("ws.myAgents")}</div>
      {installs.map((i) => {
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


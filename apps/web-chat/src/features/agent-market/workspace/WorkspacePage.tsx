"use client";

// /chat/agents/i/[installId] — the agent workspace (board 3), the primary
// surface: the agent's header, the thread (loaded, then followed live) and
// the composer, in the column the chat uses.
//
// AM5: the workspace had a list of my agents of its own beside the app's
// sidebar, which lists the same agents: two navigation columns, and the
// agent's column 100 px off the chat's. The sidebar is the one list.
//
// AM4: one obvious action in the header — "ที่มาของตัวเลข" — and everything
// else (other chats, pin, overview, settings, report a problem) in one menu;
// an empty workspace says in one loud sentence what to drop in; a finished
// round closes with a divider and "เริ่มรอบใหม่"; ?thread=<id> shows another
// chat with the same agent.
//
// AM6: ?run=<id> brings that round into view once the thread has loaded — a
// notification's link, a reminder's waiting round. A round of another chat
// with the agent opens that chat on it.

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListChecks } from "lucide-react";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";
import { ButtonLink } from "@/components/ui/Button";

import { AmError, amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import type { InstallDetail, InstallRow, ProgressPayload, ThreadItem } from "../types";
import { AgentTile, cls, ErrorPanel, FeatureGate, lookOf, PageShell } from "../ui";
import { notifyInstallsChanged, onInstallsChanged, useAgentMarket } from "../useAgentMarket";
import { togglePin } from "../useRail";
import { BudgetBanner } from "./BudgetBanner";
import { BesidePane } from "./BesidePane";
import { Composer, type ComposerHandle } from "./Composer";
import { provenanceHref, runHref, WorkspaceContext, workspaceHref, type WorkspaceCtx } from "./context";
import { EmptyDrop } from "./EmptyDrop";
import { useEstimate } from "./Estimate";
import { ModelControl, useAgentModels } from "./ModelControl";
import { hasFinishedRound, hasOwnRound, latestRound, roundUnderway } from "./rounds";
import { ThreadScroller } from "./ThreadScroller";
import { ThreadView } from "./ThreadView";
import { UploadInfo } from "./UploadInfo";
import { useThread } from "./useThread";
import { acceptFromManifest, DEFAULT_MAX_MB } from "./useUploads";
import { WorkspaceMenu } from "./WorkspaceMenu";

export function WorkspacePage({
  installId,
  threadId = null,
  runId = null,
  beside = null,
}: {
  installId: string;
  threadId?: string | null;
  runId?: string | null;
  /** AM10: ?beside=<chat> — a second chat of the same agent, read beside this one */
  beside?: string | null;
}) {
  return (
    <FeatureGate>
      {/* AM6: a link names the first thread "main" (a reminder's notification); the API calls it none */}
      <Workspace installId={installId} threadId={threadId && threadId !== "main" ? threadId : null} runId={runId || null} beside={beside || null} />
    </FeatureGate>
  );
}

/** A run id as it may stand in a selector (ids are uuids). */
const RUN_ID = /^[\w-]{1,80}$/;

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

function Workspace({ installId, threadId, runId, beside }: { installId: string; threadId: string | null; runId: string | null; beside: string | null }) {
  const t = useAmT();
  const router = useRouter();
  const snap = useAgentMarket();
  const [detail, setDetail] = useState<InstallDetail | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const installs = useInstalls();
  const thread = useThread(installId, threadId);
  const composerRef = useRef<ComposerHandle>(null);
  const [pinnedAt, setPinnedAt] = useState<string | null | undefined>(undefined);
  const [models, setModels] = useAgentModels(installId);
  // AM6: what a round costs, said where a person decides to start one.
  const estimate = useEstimate(installId);

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
  const look = lookOf(detail?.look, manifest, installs.find((i) => i.id === installId));
  const title = manifest?.greeting?.role || install?.title || "";
  const tagline = manifest?.greeting?.tagline || "";
  // What the server takes for this agent (its inputs' accept lists), nothing more.
  const accept = useMemo(() => acceptFromManifest(manifest), [manifest]);
  const maxMb = manifest?.limits?.max_file_mb || DEFAULT_MAX_MB;
  const photoNotice = manifest?.photo_notice || undefined;

  // AM4: "ที่มาของตัวเลข" opens the latest round of the person's own files.
  const latest = useMemo(() => latestRound(thread.items), [thread.items]);
  const empty = thread.status !== "loading" && !hasOwnRound(thread.items);
  // AM6: the composer's suggestions go while the agent is at work, and two of them need a round to ask about.
  const underway = useMemo(() => roundUnderway(thread.items), [thread.items]);
  const finished = useMemo(() => hasFinishedRound(thread.items), [thread.items]);

  // the self-tests in this thread (their files are the sample's, not to be signed off)
  const selfTestSig = useMemo(
    () =>
      thread.items
        .filter((i) => i.kind === "progress" && (i.payload as ProgressPayload)?.kind === "self_test")
        .map((i) => (i.payload as ProgressPayload).run_id)
        .join(","),
    [thread.items],
  );
  // AM6: the self-test's files card in this thread, which a chip shows before a round of the person's own
  const sampleRunId = useMemo(() => {
    const selfTests = new Set(selfTestSig ? selfTestSig.split(",") : []);
    for (let i = thread.items.length - 1; i >= 0; i--) {
      const it = thread.items[i]!;
      if (it.kind === "outputs" && it.run_id && selfTests.has(it.run_id)) return it.run_id;
    }
    return null;
  }, [thread.items, selfTestSig]);
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
      expectedSeconds: estimate.seconds,
    }),
    [installId, icon, title, thread.merge, accept, maxMb, photoNotice, selfTestSig, threadId, snap.admin, estimate.seconds],
  );

  // ?run=<id>: once, when the thread has loaded — the thread moving on afterwards does not pull the reader back
  const broughtRun = useRef<string | null>(null);
  useEffect(() => {
    if (!runId || !RUN_ID.test(runId) || broughtRun.current === runId) return;
    if (thread.status !== "live" && thread.status !== "reconnecting") return;
    broughtRun.current = runId;
    const el = document.querySelector(`[data-testid="am-thread"] [data-run="${runId}"]`);
    if (el) {
      el.scrollIntoView?.({ block: "start" });
      return;
    }
    amApi.run(runId).then(
      (d) => {
        const run = d?.run;
        const other = run?.thread_id ?? null;
        if (!run || run.install_id !== installId || other === threadId) return;
        router.replace(`${workspaceHref(installId, other)}${other ? "&" : "?"}run=${encodeURIComponent(runId)}`);
      },
      () => {},
    );
  }, [runId, thread.status, thread.items, installId, threadId, router]);

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

  // AM10: the chat was deleted (from the sidebar, perhaps in another window):
  // a notification's round opens on its run page, anything else opens the
  // agent's latest chat — or a new one when none is left. Nothing is lost.
  const chatGone = thread.status === "error" && thread.error instanceof AmError && thread.error.code === "THREAD_GONE";
  const movedOn = useRef(false);
  useEffect(() => {
    if (!chatGone || movedOn.current) return;
    movedOn.current = true;
    if (runId && RUN_ID.test(runId)) {
      router.replace(runHref(runId));
      return;
    }
    amApi.threads(installId).then(
      (r) => {
        const next = r?.threads?.[0];
        if (next) {
          router.replace(workspaceHref(installId, next.id));
          return;
        }
        amApi.newThread(installId).then(
          (n) => router.replace(workspaceHref(installId, n.thread.id)),
          () => router.replace("/chat/agents"),
        );
      },
      () => router.replace("/chat/agents"),
    );
  }, [chatGone, installId, runId, router]);

  const currentKey = threadId ?? "main";
  const besideKey = beside && beside !== currentKey ? beside : null;
  const [mobilePane, setMobilePane] = useState<"main" | "beside">("main");

  const notFound = loadError instanceof AmError && (loadError.status === 404 || loadError.status === 400);
  if (chatGone) {
    return (
      <PageShell width="max-w-xl">
        <p className="mt-16 text-center text-sm text-text-secondary" role="status" data-testid="am-ws-chat-gone">
          {t("ws.chatGone")}
        </p>
      </PageShell>
    );
  }
  if (notFound) {
    return (
      <PageShell width="max-w-xl">
        <div className="mt-16 flex flex-col items-center text-center" data-testid="am-ws-not-found">
          <h1 className="text-lg font-semibold text-text-primary">{t("ws.notFound.title")}</h1>
          <p className="mt-1.5 text-sm text-text-secondary">{t("ws.notFound.body")}</p>
          <ButtonLink href="/chat/agents" variant="secondary" className="mt-6">
            {t("ws.backToMarket")}
          </ButtonLink>
        </div>
      </PageShell>
    );
  }

  return (
    <WorkspaceContext.Provider value={ctx}>
      <div className="flex min-h-0 flex-1 flex-col" data-testid="am-workspace" data-beside={besideKey || undefined}>
        {besideKey ? (
          <div className="flex shrink-0 border-b border-border-subtle lg:hidden" role="tablist" aria-label={t("beside.title")} data-testid="am-beside-tabs">
            {(["main", "beside"] as const).map((p) => (
              <button
                key={p}
                type="button"
                role="tab"
                aria-selected={mobilePane === p}
                onClick={() => setMobilePane(p)}
                className={cls(
                  "focus-ring flex-1 px-3 py-2 text-xs",
                  mobilePane === p ? "border-b-2 border-text-primary text-text-primary" : "text-text-muted",
                )}
                data-testid={`am-beside-tab-${p}`}
              >
                {p === "main" ? t("beside.back") : t("beside.show")}
              </button>
            ))}
          </div>
        ) : null}
        <div className="flex min-h-0 flex-1">
        <div className={cls("flex min-w-0 flex-1 flex-col", besideKey && mobilePane === "beside" ? "hidden lg:flex" : "")}>
          <header className="flex shrink-0 items-center gap-3 border-b border-border-subtle px-4 py-2.5 md:px-6">
            <div className="shrink-0" data-testid="am-ws-tile">
              <AgentTile icon={icon} look={look} size={28} />
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="min-h-[1.5rem] truncate text-base font-semibold text-text-primary">{title}</h1>
              {tagline ? <p className="truncate text-xs text-text-muted">{tagline}</p> : null}
            </div>
            {latest ? (
              <ButtonLink
                href={provenanceHref(latest.runId)}
                variant="secondary"
                size="sm"
                title={t("ws.provenanceTitle")}
                data-testid="am-ws-view-run"
              >
                <ListChecks width={15} height={15} aria-hidden />
                <span>{t("ws.provenance")}</span>
              </ButtonLink>
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
              {empty && manifest ? <EmptyDrop manifest={manifest} onPick={() => composerRef.current?.pickFiles()} estimate={estimate.line} /> : null}
            </ThreadScroller>
            <div className="px-4 md:px-10">
              <BudgetBanner installId={installId} refreshKey={progressSig} />
            </div>
            <Composer
              ref={composerRef}
              installId={installId}
              onSent={onSent}
              accept={accept}
              maxMb={maxMb}
              photoNotice={photoNotice}
              threadId={threadId}
              agentName={manifest?.short_name || manifest?.name || install?.title}
              roundUnderway={underway}
              finishedRound={finished}
              sampleRunId={sampleRunId}
              datasheetHref={install?.slug ? `/chat/agents/${encodeURIComponent(install.slug)}/datasheet` : null}
              // the empty workspace says it itself, once
              estimate={empty ? null : estimate.line}
              tools={<ModelControl installId={installId} models={models} onModels={setModels} />}
              footer={
                <div className="flex flex-col items-center gap-0.5" data-testid="am-ws-footer">
                  {manifest?.cannot ? <p className="text-center text-[11px] text-text-muted">{manifest.cannot}</p> : null}
                  {!empty ? <UploadInfo manifest={manifest} center /> : null}
                </div>
              }
            />
          </AttachmentDropZone>
        </div>
        {besideKey ? (
          <div className={cls("flex min-w-0 flex-1 flex-col", mobilePane === "main" ? "hidden lg:flex" : "")}>
            <BesidePane
              installId={installId}
              currentKey={currentKey}
              besideKey={besideKey}
              base={{ installId, icon, agentName: title, accept, maxMb, photoNotice, selfTestRuns: ctx.selfTestRuns, admin: snap.admin }}
            />
          </div>
        ) : null}
        </div>
      </div>
    </WorkspaceContext.Provider>
  );
}

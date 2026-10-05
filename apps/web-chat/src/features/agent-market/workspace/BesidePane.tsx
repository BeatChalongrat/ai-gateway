"use client";

// AM10 — a second chat beside the one on screen, so two rounds can be read
// side by side without losing place: the chat on screen keeps its scroll
// and its composer; this pane follows its own chat live and reads only.
// On a narrow screen the two take turns (a tab bar), on a wide one they
// share the width.

import { useRouter } from "next/navigation";
import { useEffect, useMemo } from "react";
import { ArrowLeftRight, X } from "lucide-react";

import { Button } from "@/components/ui/Button";

import { AmError, errorMessage } from "../api";
import { besideHref } from "../ChatRows";
import { chatKey, useChats } from "../chats";
import { useAmT } from "../i18n";
import { ErrorPanel } from "../ui";
import { WorkspaceContext, workspaceHref, type WorkspaceCtx } from "./context";
import { ThreadScroller } from "./ThreadScroller";
import { ThreadView } from "./ThreadView";
import { useThread } from "./useThread";

export function BesidePane({
  installId,
  currentKey,
  besideKey,
  base,
}: {
  installId: string;
  /** the chat on screen ("main" = the first) */
  currentKey: string;
  /** the chat shown beside it */
  besideKey: string;
  /** what the thread's cards need from the workspace around them */
  base: Pick<WorkspaceCtx, "installId" | "icon" | "agentName" | "accept" | "maxMb" | "photoNotice" | "selfTestRuns" | "admin">;
}) {
  const t = useAmT();
  const router = useRouter();
  const thread = useThread(installId, besideKey === "main" ? null : besideKey);
  const { chats } = useChats(installId);
  const title = chats?.find((c) => chatKey(c) === besideKey)?.title ?? "";
  const closeHref = workspaceHref(installId, currentKey === "main" ? null : currentKey);

  // the chat to compare was deleted meanwhile: back to one chat, nothing lost
  const gone = thread.status === "error" && thread.error instanceof AmError && thread.error.code === "THREAD_GONE";
  useEffect(() => {
    if (gone) router.replace(closeHref);
  }, [gone, router, closeHref]);

  const ctx: WorkspaceCtx = useMemo(
    () => ({ ...base, merge: thread.merge, threadId: besideKey === "main" ? null : besideKey, readOnly: true }),
    [base, thread.merge, besideKey],
  );

  return (
    <WorkspaceContext.Provider value={ctx}>
      <section className="flex min-h-0 min-w-0 flex-1 flex-col border-l border-border-subtle" aria-label={t("beside.title")} data-testid="am-beside" data-chat={besideKey}>
        <header className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-4 py-2 md:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-text-primary" data-testid="am-beside-title">
              {title || t("beside.title")}
            </h2>
            <p className="truncate text-[11px] text-text-muted">{t("beside.readOnly")}</p>
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            title={t("beside.swap")}
            aria-label={t("beside.swap")}
            onClick={() => router.push(besideHref(installId, besideKey, currentKey))}
            data-testid="am-beside-swap"
          >
            <ArrowLeftRight width={14} height={14} aria-hidden />
          </Button>
          <Button type="button" variant="secondary" size="sm" title={t("beside.close")} aria-label={t("beside.close")} onClick={() => router.push(closeHref)} data-testid="am-beside-close">
            <X width={14} height={14} aria-hidden />
          </Button>
        </header>
        <ThreadScroller items={thread.items} status={thread.status} testId="am-thread-beside">
          {thread.status === "error" && !gone ? <ErrorPanel message={errorMessage(thread.error)} title={t("ws.loadFailed")} onRetry={thread.retry} /> : null}
          <ThreadView items={thread.items} />
        </ThreadScroller>
      </section>
    </WorkspaceContext.Provider>
  );
}

"use client";

import { createContext, useContext } from "react";

import type { ThreadItem } from "../types";

/** What the thread's cards need from the workspace around them. */
export interface WorkspaceCtx {
  installId: string;
  icon?: string;
  agentName?: string;
  /** Merge a server-returned item into the thread (a POST's echo). */
  merge: (items: ThreadItem[]) => void;
  /** Accepted upload types and size for this agent. */
  accept?: string[];
  maxMb?: number;
  /** The agent's note on photos (manifest photo_notice). */
  photoNotice?: string;
  /** A card's own drop slot took the files of the drop now in flight, so
   *  the workspace-wide drop zone must not stage them in the composer too. */
  claimDrop?: () => void;
  /** AM3: self-test runs (their outputs card offers no sign-off). */
  selfTestRuns?: ReadonlySet<string>;
  /** AM4: the thread on screen (null = the install's first). */
  threadId?: string | null;
  /** AM4: put the cursor in the composer (after "เริ่มรอบใหม่"). */
  focusComposer?: () => void;
  /** AM4: an admin sees the run panel's details open. */
  admin?: boolean;
}

export const WorkspaceContext = createContext<WorkspaceCtx | null>(null);

export function useWorkspace(): WorkspaceCtx | null {
  return useContext(WorkspaceContext);
}

export const runHref = (runId: string) => `/chat/agents/runs/${runId}`;
export const settingsHref = (installId: string) => `/chat/agents/i/${installId}/settings`;
export const dashboardHref = (installId: string) => `/chat/agents/i/${installId}/dashboard`;
export const workspaceHref = (installId: string, threadId?: string | null) =>
  threadId ? `/chat/agents/i/${installId}?thread=${encodeURIComponent(threadId)}` : `/chat/agents/i/${installId}`;
/** AM4: the run page opened on its numbers ("ที่มาของตัวเลข"). */
export const provenanceHref = (runId: string) => `/chat/agents/runs/${runId}?tab=numbers`;

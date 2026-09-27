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
}

export const WorkspaceContext = createContext<WorkspaceCtx | null>(null);

export function useWorkspace(): WorkspaceCtx | null {
  return useContext(WorkspaceContext);
}

export const runHref = (runId: string) => `/chat/agents/runs/${runId}`;
export const settingsHref = (installId: string) => `/chat/agents/i/${installId}/settings`;
export const dashboardHref = (installId: string) => `/chat/agents/i/${installId}/dashboard`;
export const workspaceHref = (installId: string) => `/chat/agents/i/${installId}`;

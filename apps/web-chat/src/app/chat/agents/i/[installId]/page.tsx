"use client";

import { WorkspacePage } from "@/features/agent-market/workspace/WorkspacePage";

export default function AgentWorkspaceRoute({
  params,
  searchParams,
}: {
  params: { installId: string };
  searchParams?: { thread?: string; run?: string };
}) {
  // AM4: ?thread=<id> is another chat with the same agent (absent = the first)
  // AM6: ?run=<id> brings that round into view (a notification's link, a reminder's round)
  return (
    <WorkspacePage
      key={`${params.installId}:${searchParams?.thread ?? ""}`}
      installId={params.installId}
      threadId={searchParams?.thread ?? null}
      runId={searchParams?.run ?? null}
    />
  );
}

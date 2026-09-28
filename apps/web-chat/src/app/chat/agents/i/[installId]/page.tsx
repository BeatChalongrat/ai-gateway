"use client";

import { WorkspacePage } from "@/features/agent-market/workspace/WorkspacePage";

export default function AgentWorkspaceRoute({
  params,
  searchParams,
}: {
  params: { installId: string };
  searchParams?: { thread?: string };
}) {
  // AM4: ?thread=<id> is another chat with the same agent (absent = the first)
  return <WorkspacePage key={`${params.installId}:${searchParams?.thread ?? ""}`} installId={params.installId} threadId={searchParams?.thread ?? null} />;
}

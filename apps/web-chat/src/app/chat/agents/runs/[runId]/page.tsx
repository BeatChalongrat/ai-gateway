"use client";

import { RunDetailPage } from "@/features/agent-market/runs/RunDetailPage";

export default function AgentRunRoute({
  params,
  searchParams,
}: {
  params: { runId: string };
  searchParams?: { tab?: string };
}) {
  // AM4: ?tab=numbers = "ที่มาของตัวเลข". AM7: an admin opens someone else's run in the admin app.
  return <RunDetailPage key={params.runId} runId={params.runId} initialTab={searchParams?.tab ?? null} />;
}

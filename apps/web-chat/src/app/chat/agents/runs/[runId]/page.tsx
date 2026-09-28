"use client";

import { RunDetailPage } from "@/features/agent-market/runs/RunDetailPage";

export default function AgentRunRoute({
  params,
  searchParams,
}: {
  params: { runId: string };
  searchParams?: { tab?: string; as?: string };
}) {
  // AM4: ?tab=numbers = "ที่มาของตัวเลข"; ?as=admin = an admin's reason-gated view
  return (
    <RunDetailPage
      key={`${params.runId}:${searchParams?.as ?? ""}`}
      runId={params.runId}
      initialTab={searchParams?.tab ?? null}
      asAdmin={searchParams?.as === "admin"}
    />
  );
}

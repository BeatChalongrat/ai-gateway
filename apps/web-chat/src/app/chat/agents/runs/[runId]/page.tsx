"use client";

import { RunDetailPage } from "@/features/agent-market/runs/RunDetailPage";

export default function AgentRunRoute({ params }: { params: { runId: string } }) {
  return <RunDetailPage runId={params.runId} />;
}

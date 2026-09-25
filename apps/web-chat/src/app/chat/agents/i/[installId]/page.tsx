"use client";

import { WorkspacePage } from "@/features/agent-market/workspace/WorkspacePage";

export default function AgentWorkspaceRoute({ params }: { params: { installId: string } }) {
  return <WorkspacePage installId={params.installId} />;
}

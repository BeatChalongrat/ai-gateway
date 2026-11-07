"use client";

import { DashboardPage } from "@/features/agent-market/dashboard/DashboardPage";

export default function AgentDashboardRoute({ params }: { params: { installId: string } }) {
  return <DashboardPage installId={params.installId} />;
}

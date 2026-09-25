"use client";

import { AgentDetailPage } from "@/features/agent-market/market/AgentDetailPage";

export default function AgentDetailRoute({ params }: { params: { slug: string } }) {
  return <AgentDetailPage slug={decodeURIComponent(params.slug)} />;
}

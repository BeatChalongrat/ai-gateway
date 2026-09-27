"use client";

import { DatasheetPage } from "@/features/agent-market/market/DatasheetPage";

export default function AgentDatasheetRoute({ params }: { params: { slug: string } }) {
  return <DatasheetPage slug={decodeURIComponent(params.slug)} />;
}

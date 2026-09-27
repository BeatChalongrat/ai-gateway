"use client";

import { SharedRunPage } from "@/features/agent-market/share/SharedRunPage";

export default function SharedRunRoute({ params }: { params: { id: string } }) {
  return <SharedRunPage shareId={params.id} />;
}

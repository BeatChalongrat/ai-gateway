"use client";

import { SettingsPage } from "@/features/agent-market/settings/SettingsPage";

export default function AgentSettingsRoute({ params }: { params: { installId: string } }) {
  return <SettingsPage installId={params.installId} />;
}

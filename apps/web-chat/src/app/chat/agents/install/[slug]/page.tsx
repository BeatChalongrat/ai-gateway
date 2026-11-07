"use client";

import { InstallWizard } from "@/features/agent-market/install/InstallWizard";

export default function AgentInstallRoute({ params }: { params: { slug: string } }) {
  return <InstallWizard slug={decodeURIComponent(params.slug)} />;
}

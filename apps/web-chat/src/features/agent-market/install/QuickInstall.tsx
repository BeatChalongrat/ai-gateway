"use client";

// AM4 — installing can end in one click. Most people want the defaults:
// "ใช้ค่าแนะนำทั้งหมด" installs with the agent's own install defaults (the
// manifest's), starts the self-test on its sample data and opens the
// workspace, where the test's files appear. Theme, file set, company details
// and the rest are editable later from the agent's settings page; the one
// thing that cannot be defaulted — a column the agent cannot recognise — is
// asked on the first run, and remembered.

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Sparkles } from "lucide-react";

import { Button, ButtonLink } from "@/components/ui/Button";

import { amApi, errorMessage, newClientId } from "../api";
import { useAmT } from "../i18n";
import { notifyInstallsChanged } from "../useAgentMarket";
import { workspaceHref } from "../workspace/context";

export function QuickInstall({ slug }: { slug: string }) {
  const t = useAmT();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // one key per page: a double click installs once
  const [key] = useState(() => newClientId());

  const install = () => {
    setBusy(true);
    setError(null);
    amApi.install({ slug, idempotency_key: key, self_test: true }).then(
      (res) => {
        notifyInstallsChanged();
        router.push(workspaceHref(res.install.id));
      },
      (err) => {
        setBusy(false);
        setError(`${t("install.recommendedFailed")}: ${errorMessage(err)}`);
      },
    );
  };

  return (
    <div className="flex flex-col gap-2" data-testid="am-quick-install">
      <Button type="button" onClick={install} loading={busy} data-testid="am-detail-install">
        {busy ? null : <Sparkles width={15} height={15} aria-hidden />}
        <span>{busy ? t("install.recommending") : t("install.recommended")}</span>
      </Button>
      <p className="text-[11px] leading-relaxed text-text-muted">{t("install.recommendedBody")}</p>
      {error ? (
        <p role="alert" className="text-xs text-status-danger">
          {error}
        </p>
      ) : null}
      <ButtonLink href={`/chat/agents/install/${encodeURIComponent(slug)}`} variant="secondary" data-testid="am-detail-install-custom">
        {t("install.custom")}
      </ButtonLink>
    </div>
  );
}

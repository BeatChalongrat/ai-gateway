"use client";

// /chat/agents/shared/[id] — a run a colleague shared with you (AM3). You
// see it because you are signed in as the person it was shared with: its
// files, what it left for a person to decide and what was decided, who
// signed it off — and, when the share allows it, your own sign-off. A
// recipient needs no Agent Market access of their own, so the page asks only
// that the feature is switched on; the server decides everything else.

import { useEffect, useState } from "react";
import { Eye } from "lucide-react";

import { AmError, amApi, errorMessage } from "../api";
import { useAmLocale, useAmT } from "../i18n";
import { extensionOf, formatDate, formatDateTime, periodLabel } from "../format";
import { groupExceptions, reasonText } from "../exceptions/exceptions";
import { outcomeKey, outcomeOf, outcomeTone } from "../outcome";
import type { RunExceptionRow, SharedRun } from "../types";
import { BackLink, Card, ErrorPanel, NotAvailable, PageShell, PageSkeleton, Pill, type Tone } from "../ui";
import { useAgentMarket } from "../useAgentMarket";
import { DownloadRow } from "../market/AgentDetailPage";
import { ProblemButton } from "../problem/ProblemButton";
import { ReviewsList, SignOffButton } from "../review/SignOff";

export function SharedRunPage({ shareId }: { shareId: string }) {
  const { state } = useAgentMarket();
  if (state === "unknown") return <PageSkeleton />;
  if (state === "off") return <NotAvailable />;
  return <Shared shareId={shareId} />;
}


function Shared({ shareId }: { shareId: string }) {
  const t = useAmT();
  const locale = useAmLocale();
  const [data, setData] = useState<SharedRun | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  const { state } = useAgentMarket();

  useEffect(() => {
    let alive = true;
    amApi.sharedRun(shareId).then(
      (d) => alive && setData(d),
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [shareId, tick]);

  if (error && !data) {
    const refused = error instanceof AmError && (error.status === 403 || error.status === 404);
    return (
      <PageShell width="max-w-2xl">
        <div className="mt-10" data-testid="am-shared-refused">
          <ErrorPanel title={refused ? t("shared.refusedTitle") : undefined} message={errorMessage(error)} />
        </div>
      </PageShell>
    );
  }
  if (!data) return <PageSkeleton />;

  const run = data.run;
  const groups = groupExceptions(data.exceptions);
  const arts = data.artifacts ?? [];

  return (
    <PageShell width="max-w-4xl">
      {state === "available" ? <BackLink href="/chat/agents">{t("detail.back")}</BackLink> : null}
      <div className="mt-4 flex flex-wrap items-start gap-3" data-testid="am-shared">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-xs text-text-muted">
            <Eye width={13} height={13} aria-hidden />
            <span>{t("shared.kicker", { who: data.owner })}</span>
          </p>
          <h1 className="mt-1 text-lg font-semibold text-text-primary sm:text-xl">
            {data.agent.name || data.agent.title}
            {data.outputs?.period_label || run.period ? (
              <span className="font-normal text-text-muted">{` · ${t("run.period", { period: periodLabel(run.period, locale, data.outputs?.period_label) })}`}</span>
            ) : null}
          </h1>
          <p className="mt-0.5 text-xs text-text-muted">
            {[t("run.agentVersion", { v: data.agent.version }), run.finished_at ? t("shared.finished", { when: formatDateTime(run.finished_at, locale) }) : null]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {/* AM6: named by what the run came to */}
        <span data-testid="am-shared-status" data-outcome={outcomeOf(run)}>
          <Pill tone={outcomeTone(outcomeOf(run))} size="md">
            {t(outcomeKey(outcomeOf(run), run.status_reason) ?? "progress.title.running")}
          </Pill>
        </span>
      </div>

      <p className="mt-3 rounded-lg border border-border bg-bg-surface px-3 py-2 text-xs text-text-secondary">
        {data.share.can_review ? t("shared.canReview") : t("shared.readOnly")}
        {data.share.expires_at ? ` ${t("shared.until", { when: formatDate(data.share.expires_at, locale) })}` : ""}
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        {data.share.can_review && run.status === "succeeded" ? <SignOffButton shareId={shareId} onDone={() => setTick((n) => n + 1)} /> : null}
        <ProblemButton target={{ kind: "shared", shareId }} />
      </div>

      <div className="mt-5 flex flex-col gap-4">
        <Card>
          <h2 className="text-sm font-semibold text-text-primary">{t("shared.files")}</h2>
          {arts.length ? (
            <ul className="mt-2 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle" data-testid="am-shared-files">
              {arts.map((a) => (
                <DownloadRow
                  key={a.id}
                  name={a.name}
                  size={a.size}
                  ext={extensionOf(a.format)}
                  label={t("outputs.version", { n: String(a.version) })}
                  download={() => amApi.downloadShared(shareId, a.id, a.name)}
                />
              ))}
            </ul>
          ) : (
            <p className="mt-1.5 text-sm text-text-muted">{t("files.none")}</p>
          )}
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-text-primary">{t("shared.reviews")}</h2>
          <div className="mt-2">
            <ReviewsList reviews={data.reviews ?? []} />
          </div>
        </Card>

        {(data.exceptions ?? []).length ? (
          <Card>
            <h2 className="text-sm font-semibold text-text-primary">{t("shared.exceptions")}</h2>
            <ul className="mt-2 flex flex-col gap-2" data-testid="am-shared-exceptions">
              {[...groups.open, ...groups.resolved, ...groups.noted].map((r) => (
                <ExceptionLine key={r.id} row={r} />
              ))}
            </ul>
          </Card>
        ) : null}

        {(run.assumptions ?? []).length ? (
          <Card>
            <h2 className="text-sm font-semibold text-text-primary">{t("shared.assumptions")}</h2>
            <ul className="mt-2 flex flex-col gap-1">
              {(run.assumptions ?? []).map((a, i) => (
                <li key={`${a.code}-${i}`} className="text-xs text-text-secondary">
                  {a.text}
                </li>
              ))}
            </ul>
          </Card>
        ) : null}
      </div>
    </PageShell>
  );
}

function ExceptionLine({ row }: { row: RunExceptionRow }) {
  const t = useAmT();
  const ref = row.source_ref ?? {};
  const decision = ref.decision;
  const tone: Tone = row.status === "open" ? "warning" : row.status === "resolved" ? "success" : "neutral";
  const statusKey = row.status === "open" ? "shared.exOpen" : row.status === "resolved" ? "shared.exDecided" : "shared.exNoted";
  return (
    <li className="rounded-lg border border-border-subtle bg-bg-base px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-text-primary">{ref.subject ? `${ref.subject} · ${reasonText(row, t)}` : reasonText(row, t)}</span>
        <Pill tone={tone}>{t(statusKey)}</Pill>
      </div>
      {ref.detail ? <p className="mt-0.5 text-xs text-text-secondary">{ref.detail}</p> : null}
      {decision?.label ? (
        <p className="mt-0.5 text-xs text-text-secondary">
          {t("shared.decision", { label: decision.label })}
          {decision.note ? ` · ${decision.note}` : ""}
        </p>
      ) : null}
    </li>
  );
}

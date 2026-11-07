"use client";

// /agent-market/runs/[id] — the content of someone else's run, for an admin
// who opened it with a reason. chat-edge answers ACCESS_REASON_NEEDED until
// the admin says why (and again once the hour is up); the reason form here
// records the opening, the run's owner is told, and the page loads again.
// Downloads go through the admin-ui proxy (same origin, the session cookie
// rides along), so the admin bearer never reaches the browser.

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import Badge from "../../../../components/ui/Badge";
import PageHeader from "../../../../components/ui/PageHeader";
import LoadingState from "../../../../components/ui/LoadingState";
import { useI18n } from "../../../../lib/i18n";
import { amAdmin, amErrorCode, amErrorMessage, type RunDetail } from "../../../../lib/agentMarketApi";
import {
  DASH,
  LoadError,
  ReasonForm,
  code,
  formatBytes,
  formatCredits,
  formatDateTime,
  h2,
  linkStyle,
  muted,
  outcomeLabel,
  outcomeOf,
  outcomeVariant,
  secondary,
  stepLabel,
  warnBand,
} from "../../_sections/shared";

export default function AdminRunPage({ params }: { params: { id: string } }) {
  const { t, locale } = useI18n();
  const runId = params.id;
  const [data, setData] = useState<RunDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    let alive = true;
    setError(null);
    amAdmin.run(runId).then(
      (r) => {
        if (alive) setData(r);
      },
      (err) => {
        if (!alive) return;
        setData(null);
        setError(err);
      },
    );
    return () => {
      alive = false;
    };
  }, [runId, n]);

  const back = (
    <Link href="/agent-market?section=runs" style={{ ...linkStyle, fontSize: "0.8125rem" }} data-testid="am-run-back">
      {`← ${t("am.run.back")}`}
    </Link>
  );

  let body: ReactNode;
  if (error && amErrorCode(error) === "ACCESS_REASON_NEEDED") {
    body = (
      <section className="sran-card" style={{ maxWidth: "40rem" }} data-testid="am-run-reason">
        <h2 style={h2}>{t("am.run.needReason")}</h2>
        <p style={{ ...secondary, marginTop: "0.35rem" }}>{amErrorMessage(error, t("am.runs.reasonTitle"), t)}</p>
        <p style={{ ...muted, margin: "0.5rem 0 0.9rem", lineHeight: 1.6 }}>{t("am.runs.reasonBody")}</p>
        <ReasonForm
          confirm={t("am.runs.confirmOpen")}
          onConfirm={async (reason) => {
            await amAdmin.openRun(runId, reason);
            setN((x) => x + 1);
          }}
        />
      </section>
    );
  } else if (error) {
    body = <LoadError error={error} onRetry={() => setN((x) => x + 1)} />;
  } else if (!data) {
    body = <LoadingState />;
  } else {
    body = <RunContent runId={runId} data={data} locale={locale} />;
  }

  const title = data?.install?.title || t("am.run.title");
  const period = data?.run.period ? t("am.run.period", { period: data.run.period }) : undefined;

  return (
    <div>
      <div style={{ marginBottom: "0.75rem" }}>{back}</div>
      <PageHeader title={title} subtitle={period} />
      {body}
    </div>
  );
}

const tableWrap = { overflowX: "auto" as const, marginTop: "0.6rem" };

function RunContent({ runId, data, locale }: { runId: string; data: RunDetail; locale: string }) {
  const { t } = useI18n();
  const run = data.run;
  const outcome = outcomeOf(run);
  const files = data.files ?? [];
  const artifacts = data.artifacts ?? [];
  const steps = [...(data.steps ?? [])].sort((a, b) => a.seq - b.seq);
  const log = data.access_log ?? [];

  const model = run.model_used
    ? `${run.model_used} · ${run.model_self_hosted ? t("am.where.inOrg") : t("am.where.outside")}`
    : t("am.run.modelNone");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }} data-testid="am-run-detail">
      <p role="note" style={warnBand} data-testid="am-run-admin-view">
        {t("am.run.adminView")}
      </p>
      {run.evidence_removed_at ? (
        <p role="note" className="sran-card" style={{ ...secondary, padding: "0.6rem 0.8rem" }} data-testid="am-run-evidence-removed">
          {t("am.run.evidenceRemoved")}
        </p>
      ) : null}

      <section className="sran-card" style={{ display: "grid", gap: "0.9rem", gridTemplateColumns: "repeat(auto-fit, minmax(12rem, 1fr))" }}>
        <Fact label={t("am.run.outcome")}>
          <span data-testid="am-run-outcome" data-outcome={outcome}>
            <Badge variant={outcomeVariant(outcome)}>{outcomeLabel(outcome, run.status_reason, t)}</Badge>
          </span>
        </Fact>
        <Fact label={t("am.run.credits")}>{formatCredits(run.credits_spent ?? 0)}</Fact>
        <Fact label={t("am.run.model")}>{model}</Fact>
        <Fact label={t("field.created")}>{formatDateTime(run.created_at, locale)}</Fact>
      </section>

      <section className="sran-card" aria-labelledby="am-run-log" data-testid="am-run-access-log">
        <h2 id="am-run-log" style={h2}>
          {t("am.run.accessLog")}
        </h2>
        {log.length ? (
          <ul style={{ margin: "0.5rem 0 0", paddingLeft: "1.1rem", display: "flex", flexDirection: "column", gap: "0.2rem" }}>
            {log.map((a) => (
              <li key={`${a.opened_at}-${a.admin_email}`} style={secondary}>
                {/* at_label is chat-edge's Thai wording of opened_at; in English the page words the same moment itself */}
                {t("am.run.accessLine", { who: a.admin_name || a.admin_email, when: (locale === "th" && a.at_label) || formatDateTime(a.opened_at, locale), reason: a.reason })}
              </li>
            ))}
          </ul>
        ) : (
          <p style={{ ...muted, marginTop: "0.4rem" }}>{DASH}</p>
        )}
      </section>

      <section className="sran-card" aria-labelledby="am-run-inputs" data-testid="am-run-inputs">
        <h2 id="am-run-inputs" style={h2}>
          {t("am.run.inputs")}
        </h2>
        {files.length ? (
          <div style={tableWrap}>
            <table className="sran-table">
              <thead>
                <tr>
                  <th>{t("am.run.fileName")}</th>
                  <th style={{ textAlign: "right" }}>{t("am.run.size")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {files.map((f) => (
                  <tr key={f.id}>
                    <td style={{ wordBreak: "break-all" }}>{f.name}</td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{formatBytes(f.size)}</td>
                    <td>
                      {run.evidence_removed_at ? null : (
                        <a href={amAdmin.fileHref(runId, f.id)} download aria-label={t("am.run.downloadNamed", { name: f.name })} style={linkStyle}>
                          {t("am.run.download")}
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p style={{ ...muted, marginTop: "0.4rem" }}>{t("am.run.noFiles")}</p>
        )}
      </section>

      <section className="sran-card" aria-labelledby="am-run-outputs" data-testid="am-run-outputs">
        <h2 id="am-run-outputs" style={h2}>
          {t("am.run.outputs")}
        </h2>
        {artifacts.length ? (
          <div style={tableWrap}>
            <table className="sran-table">
              <thead>
                <tr>
                  <th>{t("am.run.fileName")}</th>
                  <th>{t("am.run.format")}</th>
                  <th style={{ textAlign: "right" }}>{t("am.run.version")}</th>
                  <th style={{ textAlign: "right" }}>{t("am.run.size")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {artifacts.map((a) => (
                  <tr key={a.id}>
                    <td style={{ wordBreak: "break-all" }}>{a.name}</td>
                    <td style={{ textTransform: "uppercase", fontSize: "0.75rem" }}>{a.format}</td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{a.version}</td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{formatBytes(a.size)}</td>
                    <td>
                      {run.evidence_removed_at ? null : (
                        <a href={amAdmin.artifactHref(runId, a.id)} download aria-label={t("am.run.downloadNamed", { name: a.name })} style={linkStyle}>
                          {t("am.run.download")}
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p style={{ ...muted, marginTop: "0.4rem" }}>{t("am.run.noFiles")}</p>
        )}
      </section>

      <section className="sran-card" aria-labelledby="am-run-steps" data-testid="am-run-steps">
        <h2 id="am-run-steps" style={h2}>
          {t("am.run.steps")}
        </h2>
        {steps.length ? (
          <div style={tableWrap}>
            <table className="sran-table">
              <thead>
                <tr>
                  <th style={{ width: 48 }}>#</th>
                  <th>{t("am.run.stepName")}</th>
                  <th>{t("am.run.stepStatus")}</th>
                  <th>{t("am.run.stepError")}</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((s) => (
                  <tr key={s.id} style={{ verticalAlign: "top" }}>
                    <td style={{ fontVariantNumeric: "tabular-nums", color: "var(--text-muted)" }}>{s.seq}</td>
                    <td>{stepLabel(s.name, t)}</td>
                    <td>
                      <Badge variant={outcomeVariant(s.status)}>{outcomeLabel(s.status, null, t)}</Badge>
                    </td>
                    <td>{s.error ? <code style={code}>{s.error}</code> : <span style={muted}>{DASH}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p style={{ ...muted, marginTop: "0.4rem" }}>{t("am.run.noSteps")}</p>
        )}
      </section>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div className="sran-label">{label}</div>
      <div style={{ fontSize: "0.875rem", color: "var(--text-primary)", overflowWrap: "anywhere" }}>{children}</div>
    </div>
  );
}

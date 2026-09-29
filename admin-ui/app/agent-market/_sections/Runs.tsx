"use client";

// รอบงานทั้งหมด — every run of the organisation as metadata only: outcome,
// cost, model, the step where it stopped. The files, figures and answers of
// someone else's run open one run at a time: the admin says why, the
// opening is recorded, the run's owner is told, and it lasts an hour. A
// finished run's files can be removed before the retention period ends,
// with a reason (the record that the run happened stays).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import Badge from "../../../components/ui/Badge";
import { useI18n, type DictKey } from "../../../lib/i18n";
import { amAdmin, type RunMeta } from "../../../lib/agentMarketApi";
import {
  DEFAULT_PRICING,
  FINISHED,
  LoadError,
  Loading,
  Note,
  ReasonDialog,
  bahtOf,
  formatDateTime,
  h2,
  linkStyle,
  muted,
  outcomeLabel,
  outcomeOf,
  outcomeVariant,
  secondary,
  stepLabel,
  useOrg,
  type Msg,
} from "./shared";

const FILTERS: readonly { id: string; key: DictKey }[] = [
  { id: "all", key: "am.runs.all" },
  { id: "failed", key: "am.runs.failedOnly" },
  { id: "awaiting_input", key: "am.runs.waiting" },
];

export const adminRunHref = (id: string) => `/agent-market/runs/${encodeURIComponent(id)}`;

export default function RunsSection() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const { org } = useOrg(); // the rates, for the cost in baht
  const pricing = org?.pricing ?? DEFAULT_PRICING;
  const [status, setStatus] = useState("all");
  const [rows, setRows] = useState<RunMeta[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  const [opening, setOpening] = useState<RunMeta | null>(null);
  const [removing, setRemoving] = useState<RunMeta | null>(null);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    amAdmin.runs(status).then(
      (r) => {
        if (alive) setRows(r?.runs ?? []);
      },
      (err) => {
        if (alive) setError(err);
      },
    );
    return () => {
      alive = false;
    };
  }, [status, n]);

  return (
    <section aria-labelledby="am-runs-title" data-testid="am-runs">
      <h2 id="am-runs-title" style={h2}>
        {t("am.runs.title")}
      </h2>
      <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem", lineHeight: 1.6 }} data-testid="am-runs-body">
        {t("am.runs.body")}
      </p>
      <div role="group" aria-label={t("am.runs.filter")} style={{ marginTop: "0.8rem", display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
        {FILTERS.map(({ id, key }) => (
          <button
            key={id}
            type="button"
            aria-pressed={status === id}
            onClick={() => setStatus(id)}
            className={`sran-btn sran-btn-sm ${status === id ? "sran-btn-primary" : "sran-btn-secondary"}`}
            style={{ borderRadius: 999 }}
            data-testid={`am-runs-filter-${id}`}
          >
            {t(key)}
          </button>
        ))}
      </div>
      {msg ? (
        <div style={{ marginTop: "0.6rem" }}>
          <Note msg={msg} testId="am-runs-note" />
        </div>
      ) : null}

      <div style={{ marginTop: "0.8rem" }}>
        {error ? <LoadError error={error} onRetry={() => setN((x) => x + 1)} /> : null}
        {!rows && !error ? <Loading /> : null}
        {rows && !rows.length ? <p style={secondary}>{t("am.runs.none")}</p> : null}
        {rows && rows.length ? (
          <div className="sran-card" style={{ padding: 0, overflowX: "auto" }}>
            <table className="sran-table" style={{ minWidth: 820 }}>
              <thead>
                <tr>
                  <th>{t("am.runs.person")}</th>
                  <th>{t("am.runs.agent")}</th>
                  <th>{t("am.runs.status")}</th>
                  <th style={{ textAlign: "right" }}>{t("am.runs.cost")}</th>
                  <th>{t("am.runs.model")}</th>
                  <th>
                    <span className="sr-only">{t("field.actions")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const outcome = outcomeOf(r);
                  return (
                    <tr key={r.id} data-testid="am-run" style={{ verticalAlign: "top" }}>
                      <td>
                        <div>{r.person || r.email || "—"}</div>
                        <div style={muted}>{formatDateTime(r.created_at, locale)}</div>
                      </td>
                      <td>
                        <div>{r.agent}</div>
                        {r.period_label ? <div style={muted}>{r.period_label}</div> : null}
                      </td>
                      <td>
                        <span data-testid="am-run-status" data-outcome={outcome}>
                          <Badge variant={outcomeVariant(outcome)}>{outcomeLabel(outcome, r.reason, t)}</Badge>
                        </span>
                        {r.stopped_at_step ? <div style={{ ...muted, marginTop: "0.25rem" }}>{`${t("am.runs.stopped")}: ${stepLabel(r.stopped_at_step, t)}`}</div> : null}
                      </td>
                      <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>
                        {t("am.runs.baht", { baht: bahtOf(r.credits, pricing) })}
                      </td>
                      <td style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                        {r.model ? `${r.model} · ${r.model_self_hosted ? t("am.where.inOrg") : t("am.where.outside")}` : "—"}
                      </td>
                      <td>
                        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: "0.3rem" }}>
                          {r.open_now ? (
                            <Link href={adminRunHref(r.id)} className="sran-btn sran-btn-secondary sran-btn-sm" data-testid="am-run-view">
                              {t("am.runs.openNow")}
                            </Link>
                          ) : (
                            <button type="button" className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => setOpening(r)} data-testid="am-run-open">
                              {t("am.runs.open")}
                            </button>
                          )}
                          {r.times_opened ? <span style={muted}>{t("am.runs.opened", { n: r.times_opened })}</span> : null}
                          {FINISHED.has(r.status) ? (
                            <button
                              type="button"
                              onClick={() => setRemoving(r)}
                              style={{ ...linkStyle, color: "var(--text-muted)", background: "none", border: "none", padding: 0, cursor: "pointer", fontSize: "0.7rem" }}
                              data-testid="am-run-remove"
                            >
                              {t("am.runs.removeEvidence")}
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      <ReasonDialog
        open={!!opening}
        title={t("am.runs.reasonTitle")}
        body={t("am.runs.reasonBody")}
        confirm={t("am.runs.confirmOpen")}
        onClose={() => setOpening(null)}
        onConfirm={async (reason) => {
          const r = opening;
          if (!r) return;
          await amAdmin.openRun(r.id, reason);
          setOpening(null);
          router.push(adminRunHref(r.id));
        }}
      />
      <ReasonDialog
        open={!!removing}
        danger
        title={t("am.runs.removeTitle")}
        body={t("am.runs.removeBody")}
        confirm={t("am.runs.confirmRemove")}
        onClose={() => setRemoving(null)}
        onConfirm={async (reason) => {
          const r = removing;
          if (!r) return;
          const res = await amAdmin.removeEvidence(r.id, reason);
          setRemoving(null);
          setMsg({ text: t("am.runs.removed", { n: res?.removed_objects ?? 0 }), ok: true });
          setN((x) => x + 1);
        }}
      />
    </section>
  );
}

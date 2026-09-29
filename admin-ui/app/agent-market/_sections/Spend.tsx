"use client";

// การใช้จ่าย — credits used in the last 7 / 30 / 90 days, by person and by
// agent, in baht too. AM6: a server that counts rounds that finished below
// the agent's bar sends them apart from failures, with what the platform
// paid for rounds that delivered nothing (charged to nobody).

import { useEffect, useState } from "react";

import { useI18n } from "../../../lib/i18n";
import { amAdmin, type AdminSpend, type Pricing, type SpendRow } from "../../../lib/agentMarketApi";
import { DEFAULT_PRICING, LoadError, Loading, bahtOf, formatCredits, formatDateTime, h2, muted, secondary } from "./shared";

const WINDOWS = [7, 30, 90] as const;

export default function SpendSection() {
  const { t } = useI18n();
  const [days, setDays] = useState<number>(30);
  const [data, setData] = useState<AdminSpend | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    amAdmin.spend(days).then(
      (r) => {
        if (alive) setData(r);
      },
      (err) => {
        if (alive) setError(err);
      },
    );
    return () => {
      alive = false;
    };
  }, [days, n]);

  const p = data?.pricing ?? DEFAULT_PRICING;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }} data-testid="am-spend">
      <div role="group" aria-label={t("am.spend.window")} style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
        {WINDOWS.map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={days === d}
            onClick={() => setDays(d)}
            className={`sran-btn sran-btn-sm ${days === d ? "sran-btn-primary" : "sran-btn-secondary"}`}
            style={{ borderRadius: 999 }}
            data-testid={`am-spend-${d}`}
          >
            {t("am.spend.days", { n: d })}
          </button>
        ))}
      </div>
      {error && !data ? <LoadError error={error} onRetry={() => setN((x) => x + 1)} /> : null}
      {!data && !error ? <Loading /> : null}
      {data ? (
        <>
          <p style={{ fontSize: "0.9375rem", color: "var(--text-primary)", margin: 0 }} data-testid="am-spend-total">
            {t("am.spend.total", { credits: formatCredits(data.total_credits), baht: bahtOf(data.total_credits, p), n: data.days })}
          </p>
          {typeof data.total_cost_not_charged === "number" ? (
            <p style={muted} data-testid="am-spend-not-charged-total">
              {t("am.spend.totalNotCharged", { credits: formatCredits(data.total_cost_not_charged), baht: bahtOf(data.total_cost_not_charged, p) })}
            </p>
          ) : null}
          <section className="sran-card" style={{ padding: "1rem 1.25rem" }}>
            <h2 style={h2}>{t("am.spend.byUser")}</h2>
            <SpendTable rows={data.by_user ?? []} kind="user" pricing={p} />
          </section>
          <section className="sran-card" style={{ padding: "1rem 1.25rem" }}>
            <h2 style={h2}>{t("am.spend.byAgent")}</h2>
            <SpendTable rows={data.by_agent ?? []} kind="agent" pricing={p} />
          </section>
          <p style={muted} data-testid="am-spend-samples">
            {t("am.spend.samples", { credits: formatCredits(data.catalog_samples_credits), baht: bahtOf(data.catalog_samples_credits, p) })}
          </p>
        </>
      ) : null}
    </div>
  );
}

const num = { textAlign: "right" as const, fontVariantNumeric: "tabular-nums" as const };

function SpendTable({ rows, kind, pricing }: { rows: SpendRow[]; kind: "user" | "agent"; pricing: Pricing }) {
  const { t, locale } = useI18n();
  if (!rows.length) return <p style={{ ...secondary, marginTop: "0.6rem" }}>{t("am.spend.none")}</p>;
  const counted = rows.some((r) => typeof r.completed_open === "number");
  return (
    <div style={{ overflowX: "auto", marginTop: "0.6rem" }}>
      <table className="sran-table" style={{ minWidth: 760 }} data-testid={`am-spend-${kind}`}>
        <thead>
          <tr>
            <th>{kind === "user" ? t("am.spend.person") : t("am.spend.agent")}</th>
            <th style={num}>{t("am.spend.runs")}</th>
            <th style={num}>{counted ? t("am.spend.outcomes") : t("am.spend.okFailWait")}</th>
            <th style={num}>{t("am.spend.credits")}</th>
            {counted ? <th style={num}>{t("am.spend.notCharged")}</th> : null}
            <th style={num}>{t("am.spend.month")}</th>
            <th>{t("am.spend.lastRun")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} data-testid="am-spend-row" style={{ verticalAlign: "top" }}>
              <td>
                <div>{kind === "user" ? r.name || r.email || r.key.slice(0, 8) : r.name || r.key}</div>
                <div style={muted}>
                  {kind === "user" ? [r.email, r.department].filter(Boolean).join(" · ") : t("am.spend.users", { n: r.users ?? 0 })}
                </div>
              </td>
              <td style={num}>{r.runs}</td>
              <td style={{ ...num, color: "var(--text-secondary)" }}>
                {counted ? `${r.succeeded} / ${r.completed_open ?? 0} / ${r.failed} / ${r.waiting}` : `${r.succeeded} / ${r.failed} / ${r.waiting}`}
              </td>
              <td style={num}>
                {formatCredits(r.credits)}
                <div style={muted}>{`≈ ${bahtOf(r.credits, pricing)}`}</div>
              </td>
              {counted ? <td style={{ ...num, color: "var(--text-secondary)" }}>{formatCredits(r.cost_not_charged ?? 0)}</td> : null}
              <td style={num}>
                {formatCredits(r.month_spent)}
                {r.monthly_limit ? <div style={muted}>{t("am.spend.ofLimit", { limit: formatCredits(r.monthly_limit) })}</div> : null}
              </td>
              <td style={{ color: "var(--text-secondary)", fontSize: "0.75rem" }}>{formatDateTime(r.last_run_at, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

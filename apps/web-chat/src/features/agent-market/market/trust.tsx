"use client";

// AM3 — the pieces a stranger reads before trusting an agent with their
// files: which models see what (and whether each runs inside the
// organisation), what nothing but rules touches, and what a run costs.
// Shared by the agent's page and its data sheet.

import { useEffect, useState } from "react";
import { Building2, Cloud } from "lucide-react";

import { amApi } from "../api";
import { useAmT, type AmKey } from "../i18n";
import { creditsToBaht, formatBaht, formatCredits, formatDuration } from "../format";
import type { CatalogSample, Datasheet, DatasheetModel, Manifest, Pricing, SampleSummary, AgentStats } from "../types";
import { cls } from "../ui";
import { DEFAULT_PRICING } from "../useAgentMarket";

const SCOPE_KEYS: Record<string, AmKey> = { generic: "scope.generic", "org-specific": "scope.org" };
const JURISDICTION_KEYS: Record<string, AmKey> = { TH: "jurisdiction.TH" };

/** AM6 — whom an agent is made for, and whose law and tax it follows, when
 *  the server says (an older server does not: then nothing is said). */
export function ScopeLine({ scope, jurisdiction, className }: { scope?: string | null; jurisdiction?: string | null; className?: string }) {
  const t = useAmT();
  const words = [scope && SCOPE_KEYS[scope] ? t(SCOPE_KEYS[scope]!) : "", jurisdiction && JURISDICTION_KEYS[jurisdiction] ? t(JURISDICTION_KEYS[jurisdiction]!) : ""].filter(Boolean);
  if (!words.length) return null;
  return (
    <p className={cls("text-xs text-text-muted", className)} data-testid="am-scope">
      {words.join(" · ")}
    </p>
  );
}

export function useDatasheet(slug: string): { sheet: Datasheet | null; error: unknown } {
  const [sheet, setSheet] = useState<Datasheet | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let alive = true;
    setSheet(null);
    setError(null);
    amApi.datasheet(slug).then(
      (d) => alive && setSheet(d),
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [slug]);
  return { sheet, error };
}

export function useSample(slug: string): { sample: CatalogSample | null; missing: boolean } {
  const [sample, setSample] = useState<CatalogSample | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    let alive = true;
    setSample(null);
    setMissing(false);
    amApi.catalogSample(slug).then(
      (s) => alive && setSample(s),
      () => alive && setMissing(true),
    );
    return () => {
      alive = false;
    };
  }, [slug]);
  return { sample, missing };
}

const ROLE_KEY: Record<string, AmKey> = {
  primary: "trust.role.primary",
  fallback: "trust.role.fallback",
  vision: "trust.role.vision",
  questions: "trust.role.questions",
};

/** "ในองค์กร" / "ภายนอก" — the one fact a person handing over accounting data needs. */
export function WhereBadge({ selfHosted }: { selfHosted: boolean }) {
  const t = useAmT();
  return (
    <span
      className={cls(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        selfHosted ? "border-status-success/40 bg-status-success/10 text-status-success" : "border-status-warning/40 bg-status-warning/10 text-status-warning",
      )}
      data-testid="am-model-where"
    >
      {selfHosted ? <Building2 width={11} height={11} aria-hidden /> : <Cloud width={11} height={11} aria-hidden />}
      <span>{selfHosted ? t("trust.inOrg") : t("trust.external")}</span>
    </span>
  );
}

export function ModelList({ models, noModel, compact = false }: { models: DatasheetModel[]; noModel: string[]; compact?: boolean }) {
  const t = useAmT();
  return (
    <div className="flex flex-col gap-2.5" data-testid="am-models">
      {models.length === 0 ? <p className="text-sm text-text-primary">{t("trust.noModelAtAll")}</p> : null}
      {models.map((m, i) => (
        <div key={`${m.capability}-${m.model}-${i}`} className="rounded-lg border border-border-subtle bg-bg-base px-3 py-2" data-testid="am-model">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-text-primary">{m.model}</span>
            <WhereBadge selfHosted={m.self_hosted} />
            <span className="text-[11px] text-text-muted">{t(ROLE_KEY[m.role] ?? "trust.role.primary")}</span>
          </div>
          <p className={cls("mt-1 text-xs text-text-secondary", compact ? "line-clamp-2" : "")}>{t("trust.sees", { what: m.sees })}</p>
          {!m.self_hosted ? <p className="mt-1 text-[11px] text-text-muted">{t("trust.externalNote")}</p> : null}
        </div>
      ))}
      {noModel.length ? (
        <ul className="flex flex-col gap-1">
          {noModel.map((n) => (
            <li key={n} className="flex items-start gap-2 text-xs text-text-secondary">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-current" aria-hidden />
              <span>{n}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function secondsText(t: ReturnType<typeof useAmT>, s: number | null | undefined): string | null {
  if (s === null || s === undefined || !Number.isFinite(s) || s <= 0) return null;
  return formatDuration(Math.round(s * 1000), t);
}

/** What a run costs and how long it takes: the real sample run, then this organisation's own median. */
export function CostLines({
  manifest,
  sample,
  stats,
  pricing,
}: {
  manifest: Manifest;
  sample?: SampleSummary | null;
  stats?: AgentStats | null;
  pricing?: Pricing | null;
}) {
  const t = useAmT();
  const p = pricing ?? DEFAULT_PRICING;
  const lines: string[] = [];
  if (sample && sample.status === "succeeded") {
    const b = creditsToBaht(sample.credits, p);
    const time = secondsText(t, sample.seconds);
    lines.push(
      time
        ? t("cost.sample", { credits: formatCredits(sample.credits), baht: formatBaht(b), time })
        : t("cost.sampleNoTime", { credits: formatCredits(sample.credits), baht: formatBaht(b) }),
    );
  }
  if (stats && stats.runs >= 3 && stats.median_credits !== null) {
    const time = secondsText(t, stats.median_seconds) ?? "—";
    lines.push(
      t("cost.org", {
        runs: String(stats.runs),
        credits: formatCredits(stats.median_credits),
        baht: formatBaht(creditsToBaht(stats.median_credits, p)),
        time,
      }),
    );
  }
  if (stats && stats.per_document !== null && stats.documents > 0) {
    lines.push(t("cost.perDocument", { credits: formatCredits(stats.per_document), baht: formatBaht(creditsToBaht(stats.per_document, p)) }));
  }
  if (!lines.length && manifest.typical_run_seconds) {
    lines.push(t("cost.typical", { time: secondsText(t, manifest.typical_run_seconds) ?? "—" }));
  }
  if (!lines.length) lines.push(t("cost.unknown"));
  return (
    <ul className="flex flex-col gap-1" data-testid="am-cost">
      {lines.map((l) => (
        <li key={l} className="text-sm text-text-primary">
          {l}
        </li>
      ))}
      {manifest.guide?.time_note ? <li className="text-xs text-text-muted">{manifest.guide.time_note}</li> : null}
      <li className="text-[11px] text-text-muted">{t("cost.note")}</li>
    </ul>
  );
}

/** One line for a market card: "ตัวอย่างจริง ≈ 0.12 บาท · 1 นาที 20 วินาที". */
export function costLine(
  t: ReturnType<typeof useAmT>,
  sample: SampleSummary | null | undefined,
  pricing: Pricing | null | undefined,
): string | null {
  if (!sample || sample.status !== "succeeded") return null;
  const b = formatBaht(creditsToBaht(sample.credits, pricing ?? DEFAULT_PRICING));
  const time = secondsText(t, sample.seconds);
  return time ? t("cost.cardLine", { baht: b, time }) : t("cost.cardLineNoTime", { baht: b });
}

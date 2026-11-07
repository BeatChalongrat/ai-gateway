"use client";

// The install steps (which ones an agent gets: ./wizard.ts). Each one saves
// what it collected as install defaults before moving on; nothing here
// computes a number.

import { useEffect, useMemo, useRef, useState } from "react";
import { CircleHelp, FileSpreadsheet, Link2, TriangleAlert, Upload } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

import { AmError, amApi, errorMessage } from "../api";
import { tAm, useAmT, type AmKey } from "../i18n";
import { formatInt } from "../format";
import type { Artifact, Default, Manifest, MappingPreview, ProgressPayload, RunDetail, ThemePreset, ThreadItem } from "../types";
import { cls, ErrorPanel, Pill } from "../ui";
import { isUserItem } from "../thread";
import { useAgentMarket } from "../useAgentMarket";
import { ProblemButton } from "../problem/ProblemButton";
import { savedTemplateOf } from "../template/mapping";
import { TemplateSection } from "../template/TemplateSection";
import { WorkspaceContext, type WorkspaceCtx } from "../workspace/context";
import { ArtifactRow } from "../workspace/OutputsCard";
import { ThreadView } from "../workspace/ThreadView";
import { useThread } from "../workspace/useThread";
import { checkCompanyTax, CompanyTaxFields, draftFromDefaults, EMPTY_COMPANY_TAX, saveCompanyTax, type CompanyTaxDraft } from "./CompanyTax";
import { asksCurrency, STEP_WHY, type StepId } from "./wizard";

export type PreviewState =
  | { status: "none" }
  | { status: "uploading"; name: string; progress: number }
  | { status: "reading"; name: string }
  | { status: "ready"; name: string; preview: MappingPreview; rows?: number; sheet?: string }
  | { status: "error"; name?: string; message: string };

const SAMPLE_ACCEPT = [".xlsx", ".xlsm", ".csv"];
const POLL_MS = 700;
const POLL_LIMIT_MS = 30_000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function StepHeader({ title, body }: { title: string; body?: string }) {
  return (
    <div>
      <h2 className="text-base font-semibold text-text-primary">{title}</h2>
      {body ? <p className="mt-1 text-sm leading-relaxed text-text-secondary">{body}</p> : null}
    </div>
  );
}

export function Footer({ children }: { children: React.ReactNode }) {
  return <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-border-subtle pt-4">{children}</div>;
}

/** AM3: why this step asks, and what happens if the answer is wrong or skipped (and where to fix it). */
export function StepWhy({ step }: { step: StepId }) {
  const t = useAmT();
  const w = STEP_WHY[step];
  return (
    <div className="mb-4 flex flex-col gap-1.5 rounded-lg bg-bg-base px-3 py-2.5 text-xs leading-relaxed" data-testid="am-step-why" data-step={step}>
      <p className="flex items-start gap-2 text-text-secondary">
        <CircleHelp width={14} height={14} className="mt-px shrink-0 text-text-muted" aria-hidden />
        <span>
          <span className="font-medium text-text-primary">{t("wiz.whyLabel")}</span> {t(w.why)}
        </span>
      </p>
      <p className="flex items-start gap-2 text-text-secondary">
        <TriangleAlert width={14} height={14} className="mt-px shrink-0 text-text-muted" aria-hidden />
        <span>
          <span className="font-medium text-text-primary">{t("wiz.ifWrongLabel")}</span> {t(w.ifWrong)}
        </span>
      </p>
    </div>
  );
}

// ── 1 · data source ────────────────────────────────────────────────────

async function pollPreview(jobId: string, alive: () => boolean): Promise<{ preview: MappingPreview; summary?: Record<string, unknown> }> {
  const deadline = Date.now() + POLL_LIMIT_MS;
  while (Date.now() < deadline) {
    await sleep(POLL_MS);
    if (!alive()) throw new AmError("ABORTED", "", 0);
    const d = await amApi.run(jobId);
    const status = d?.run?.status;
    if (status === "succeeded") {
      const steps = d.steps ?? [];
      const step = steps.find((s) => s.name.startsWith("preview:")) ?? steps[0];
      const out = (step?.output ?? {}) as { preview?: MappingPreview; summary?: Record<string, unknown> };
      if (!out.preview) throw new AmError("PREVIEW_EMPTY", tAm("source.previewFailed"), 0);
      return { preview: out.preview, summary: out.summary };
    }
    if (status === "failed" || status === "cancelled") throw new AmError("PREVIEW_FAILED", tAm("source.previewFailed"), 0);
  }
  throw new AmError("PREVIEW_TIMEOUT", tAm("source.previewTimeout"), 0);
}

export function SourceStep({
  installId,
  preview,
  setPreview,
  onNext,
  onSkip,
}: {
  installId: string;
  preview: PreviewState;
  setPreview: (p: PreviewState) => void;
  onNext: () => void;
  onSkip: () => void;
}) {
  const t = useAmT();
  const inputRef = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  const [over, setOver] = useState(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const busy = preview.status === "uploading" || preview.status === "reading";

  const take = async (file: File) => {
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!SAMPLE_ACCEPT.includes(ext)) {
      setPreview({ status: "error", name: file.name, message: t("error.fileType", { types: SAMPLE_ACCEPT.join(" ") }) });
      return;
    }
    setPreview({ status: "uploading", name: file.name, progress: 0 });
    try {
      const stored = await amApi.upload(installId, file, {
        onProgress: (pct) => alive.current && setPreview({ status: "uploading", name: file.name, progress: pct }),
      });
      if (!alive.current) return;
      setPreview({ status: "reading", name: file.name });
      const { job } = await amApi.mappingPreview(installId, stored.id);
      const { preview: pv, summary } = await pollPreview(job.id, () => alive.current);
      if (!alive.current) return;
      setPreview({
        status: "ready",
        name: file.name,
        preview: pv,
        rows: typeof summary?.data_rows === "number" ? summary.data_rows : undefined,
        sheet: pv.sheet ?? (typeof summary?.sheet === "string" ? summary.sheet : undefined),
      });
    } catch (err) {
      if (!alive.current || (err instanceof AmError && err.code === "ABORTED")) return;
      setPreview({ status: "error", name: file.name, message: errorMessage(err) });
    }
  };

  return (
    <div data-testid="am-step-source">
      <StepHeader title={t("source.title")} body={t("source.body")} />
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div
          className={cls(
            "flex flex-col gap-2 rounded-xl border border-dashed p-4 transition-colors",
            over ? "border-border-strong bg-bg-elevated" : "border-border bg-bg-base",
          )}
          onDragOver={(e) => {
            if (busy) return;
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            const f = e.dataTransfer.files?.[0];
            if (f && !busy) void take(f);
          }}
        >
          <div className="flex items-center gap-2 text-sm font-medium text-text-primary">
            <Upload width={16} height={16} aria-hidden />
            <span>{t("source.upload.title")}</span>
          </div>
          <p className="text-xs text-text-secondary">{t("source.upload.body")}</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Button type="button" variant="secondary" disabled={busy} onClick={() => inputRef.current?.click()}>
              {preview.status === "ready" ? t("source.replace") : t("source.choose")}
            </Button>
            <span className="text-xs text-text-muted">{t("source.drop")}</span>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={SAMPLE_ACCEPT.join(",")}
            className="hidden"
            aria-label={t("source.upload.title")}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void take(f);
            }}
          />
        </div>
        <div className="flex flex-col gap-2 rounded-xl border border-border-subtle p-4" aria-disabled="true">
          <div className="flex items-center gap-2 text-sm font-medium text-text-secondary">
            <Link2 width={16} height={16} aria-hidden />
            <span>{t("source.connect.title")}</span>
            <Pill>{t("market.soon")}</Pill>
          </div>
          <p className="text-xs text-text-muted">{t("source.connect.body")}</p>
        </div>
      </div>

      <div className="mt-4" aria-live="polite">
        {preview.status === "uploading" ? (
          <StatusLine name={preview.name} text={t("source.uploading", { pct: String(preview.progress) })} busy />
        ) : preview.status === "reading" ? (
          <StatusLine name={preview.name} text={t("source.reading")} busy />
        ) : preview.status === "ready" ? (
          <StatusLine
            name={preview.name}
            text={t("source.ready", { rows: preview.rows !== undefined ? formatInt(preview.rows) : String(preview.preview.rows.length), sheet: preview.sheet ?? "" })}
          />
        ) : preview.status === "error" ? (
          <ErrorPanel message={preview.message} />
        ) : null}
      </div>

      <Footer>
        <Button type="button" onClick={onNext} disabled={preview.status !== "ready"} data-testid="am-source-next">
          {t("common.next")}
        </Button>
        <Button type="button" variant="secondary" onClick={onSkip} disabled={busy}>
          {t("source.skip")}
        </Button>
      </Footer>
    </div>
  );
}

function StatusLine({ name, text, busy = false }: { name: string; text: string; busy?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-border bg-bg-base px-3 py-2 text-sm">
      <FileSpreadsheet width={16} height={16} className="shrink-0 text-text-muted" aria-hidden />
      <span className="min-w-0 truncate text-text-primary">{name}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-text-secondary">{text}</span>
      {busy ? <Spinner size={13} /> : null}
    </div>
  );
}

// ── 2 · column mapping ─────────────────────────────────────────────────

export const MAPPING_FIELDS = ["date", "account_code", "account_name", "debit", "credit", "amount", "dc_indicator", "cost_center", "description"] as const;
type Field = (typeof MAPPING_FIELDS)[number];
const ALWAYS_REQUIRED = new Set<Field>(["date", "account_code"]);
const AMOUNT_FIELDS = new Set<Field>(["debit", "credit", "amount"]);

/** Required: a date, an account code, and amounts (debit + credit, or an amount). */
export function mappingMissing(sel: Partial<Record<Field, string>>): Set<Field> {
  const missing = new Set<Field>();
  for (const f of ALWAYS_REQUIRED) if (!sel[f]) missing.add(f);
  const twoCols = !!sel.debit && !!sel.credit;
  if (!twoCols && !sel.amount) {
    if (!sel.debit) missing.add("debit");
    if (!sel.credit) missing.add("credit");
    missing.add("amount");
  }
  return missing;
}

function sampleFor(preview: MappingPreview, header: string | undefined): string {
  if (!header) return "";
  const idx = preview.headers.indexOf(header);
  if (idx < 0) return "";
  for (const r of preview.rows) {
    const v = r[idx];
    if (v !== undefined && v !== null && String(v).trim() !== "") return String(v);
  }
  return "";
}

export function MappingStep({ installId, preview, onBack, onNext }: { installId: string; preview: PreviewState; onBack: () => void; onNext: () => void }) {
  const t = useAmT();
  const ready = preview.status === "ready" ? preview : null;
  const pv = ready?.preview ?? null;
  const headers = useMemo(() => (pv?.headers ?? []).filter((h) => h && h.trim()), [pv]);
  const [sel, setSel] = useState<Partial<Record<Field, string>>>(() => {
    const out: Partial<Record<Field, string>> = {};
    for (const f of MAPPING_FIELDS) {
      const h = pv?.proposal?.[f]?.header;
      if (h) out[f] = h;
    }
    return out;
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const missing = mappingMissing(sel);
  const amountsMissing = missing.has("amount");
  const guessed = MAPPING_FIELDS.filter((f) => pv?.proposal?.[f]?.header).length;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const value: Record<string, string> = {};
      for (const f of MAPPING_FIELDS) if (sel[f]) value[f] = sel[f] as string;
      await amApi.putDefault(installId, "mapping", value);
      onNext();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const label = (f: Field) => t(`field.${f}` as AmKey);

  return (
    <div data-testid="am-step-mapping">
      <StepHeader title={t("mapping.title")} body={t("mapping.body")} />
      {!pv ? (
        <p className="mt-4 rounded-lg border border-border-subtle bg-bg-base px-3 py-2.5 text-sm text-text-secondary">{t("mapping.skipNote")}</p>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border bg-bg-base px-3 py-2 text-sm">
            <FileSpreadsheet width={16} height={16} className="shrink-0 text-text-muted" aria-hidden />
            <span className="min-w-0 truncate text-text-primary">{ready?.name}</span>
            <span className="text-xs text-text-muted">
              {t("mapping.fileLine", { sheet: ready?.sheet ?? "", rows: ready?.rows !== undefined ? formatInt(ready.rows) : String(pv.rows.length) })}
            </span>
            <span className="flex-1" />
            <span className="text-xs text-status-success">{t("mapping.guessed", { n: String(guessed), total: String(MAPPING_FIELDS.length) })}</span>
          </div>

          <div className="mt-4 hidden grid-cols-[minmax(0,11rem)_minmax(0,1fr)_minmax(0,9rem)] gap-3 px-3 text-[11px] text-text-muted sm:grid">
            <span>{t("mapping.colField")}</span>
            <span>{t("mapping.colHeader")}</span>
            <span>{t("mapping.colSample")}</span>
          </div>
          <ul className="mt-1.5 flex flex-col gap-2">
            {MAPPING_FIELDS.map((f) => {
              const need = missing.has(f);
              const sample = sampleFor(pv, sel[f]);
              const id = `am-map-${f}`;
              const required = ALWAYS_REQUIRED.has(f) || (AMOUNT_FIELDS.has(f) && need);
              return (
                <li
                  key={f}
                  className={cls(
                    "grid items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_minmax(0,9rem)]",
                    need ? "border-status-warning/50 bg-status-warning/10" : "border-border bg-bg-base",
                  )}
                  data-testid="am-map-row"
                  data-missing={need ? "true" : undefined}
                >
                  <label htmlFor={id} className="text-sm text-text-primary">
                    {label(f)}
                    {required ? (
                      <>
                        <span aria-hidden className="ml-0.5 text-status-danger">
                          *
                        </span>
                        <span className="sr-only">{` (${t("common.required")})`}</span>
                      </>
                    ) : null}
                  </label>
                  <select
                    id={id}
                    value={sel[f] ?? ""}
                    onChange={(e) => setSel((s) => ({ ...s, [f]: e.target.value || undefined }))}
                    className="focus-ring w-full min-w-0 rounded-md border border-border bg-bg-surface px-2.5 py-1.5 text-sm text-text-primary"
                  >
                    <option value="">{t("mapping.none")}</option>
                    {headers.map((h) => (
                      <option key={h} value={h}>
                        {h}
                      </option>
                    ))}
                  </select>
                  <span className={cls("min-w-0 truncate text-xs", need ? "text-status-warning" : "text-text-secondary")} title={sample || undefined}>
                    {need ? t("mapping.mustPick") : sample}
                  </span>
                </li>
              );
            })}
          </ul>
          {amountsMissing ? <p className="mt-2 text-xs text-text-secondary">{t("mapping.amountsRule")}</p> : null}
          <p className="mt-2 text-xs text-text-muted">{t("mapping.remembered")}</p>
        </>
      )}
      {error ? (
        <div className="mt-3">
          <ErrorPanel message={error} />
        </div>
      ) : null}
      <Footer>
        {pv ? (
          <Button type="button" onClick={() => void save()} loading={saving} disabled={missing.size > 0} data-testid="am-mapping-save">
            <span>{saving ? t("common.saving") : t("mapping.saveNext")}</span>
          </Button>
        ) : (
          <Button type="button" onClick={onNext}>
            {t("common.next")}
          </Button>
        )}
        {pv ? (
          <Button type="button" variant="secondary" onClick={onNext} disabled={saving}>
            {t("source.skip")}
          </Button>
        ) : null}
        <Button type="button" variant="secondary" onClick={onBack} disabled={saving} className="ml-auto">
          {t("common.previous")}
        </Button>
      </Footer>
    </div>
  );
}

// ── company and tax ID (install question "company_tax") ───────────────

export function CompanyTaxStep({ installId, onNext }: { installId: string; onNext: () => void }) {
  const t = useAmT();
  const [draft, setDraft] = useState<CompanyTaxDraft>(EMPTY_COMPANY_TAX);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // What was saved before (a reload, a step back) fills only the fields
  // still empty; the organisation's theme can suggest the name, and a name
  // saved for this install replaces that suggestion whichever answers first.
  const suggested = useRef("");
  useEffect(() => {
    let alive = true;
    amApi.defaults(installId).then(
      (r) => {
        if (!alive) return;
        const saved = draftFromDefaults(r?.defaults);
        setDraft((d) => ({
          company: saved.company && (!d.company || d.company === suggested.current) ? saved.company : d.company,
          taxId: d.taxId || saved.taxId,
          head: d.branch ? d.head : saved.head,
          branch: d.branch || saved.branch,
        }));
      },
      () => {},
    );
    amApi.theme().then(
      (r) => {
        const name = r?.theme?.company ? String(r.theme.company) : "";
        if (!alive || !name) return;
        suggested.current = name;
        setDraft((d) => (d.company ? d : { ...d, company: name }));
      },
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId]);

  const check = checkCompanyTax(draft);

  const save = async () => {
    if (!check.value) return;
    setSaving(true);
    setError(null);
    try {
      await saveCompanyTax(installId, check.value);
      onNext();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="am-step-company-tax">
      <StepHeader title={t("companyTax.title")} body={t("companyTax.body")} />
      <div className="mt-4">
        <CompanyTaxFields draft={draft} onChange={setDraft} disabled={saving} />
      </div>
      {error ? (
        <div className="mt-3">
          <ErrorPanel message={error} />
        </div>
      ) : null}
      <Footer>
        <Button type="button" onClick={() => void save()} loading={saving} disabled={!check.value} data-testid="am-company-tax-save">
          <span>{saving ? t("common.saving") : t("common.next")}</span>
        </Button>
      </Footer>
    </div>
  );
}

// ── 3 · document theme ─────────────────────────────────────────────────

/** The document palettes (deliverables/theme.py): primary, accent, surface.
 *  These depict the DOCUMENT's colours, so they are values, not app tokens. */
/** The presets when /theme cannot be read (the control plane's own list). They carry
 *  no name: a name is read from the dictionary where it is shown (AM11 — a name
 *  looked up here, once, when the module loads, would stay in the language of
 *  that moment, which is before the person's own language is known). */
const FALLBACK_PRESETS: ThemePreset[] = ["navy", "teal", "charcoal"].map((id) => ({ id, name: "" }));
const PRESET_NAMES: Record<string, AmKey> = { navy: "theme.preset.navy", teal: "theme.preset.teal", charcoal: "theme.preset.charcoal" };

const SWATCHES: Record<string, [string, string, string]> = {
  navy: ["#12264A", "#2F6FD6", "#F3F5F9"],
  teal: ["#0F4C4C", "#1C8C84", "#F2F7F6"],
  charcoal: ["#24272E", "#D9642B", "#F4F4F5"],
};

export function ThemeStep({
  installId,
  askCompany = true,
  onBack,
  onNext,
}: {
  installId: string;
  /** false when a company step already asked the name: not asked again, only carried onto the theme. */
  askCompany?: boolean;
  onBack: () => void;
  onNext: () => void;
}) {
  const t = useAmT();
  const [presets, setPresets] = useState<ThemePreset[] | null>(null);
  const [preset, setPreset] = useState("navy");
  const [company, setCompany] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    amApi.theme().then(
      (r) => {
        if (!alive) return;
        setPresets(r?.presets?.length ? r.presets : FALLBACK_PRESETS.slice(0, 1));
        if (r?.theme?.id) setPreset(String(r.theme.id));
        if (r?.theme?.company) setCompany((c) => c || String(r.theme!.company));
      },
      () => alive && setPresets(FALLBACK_PRESETS),
    );
    amApi.defaults(installId).then(
      (r) => {
        const d = (r?.defaults ?? []).find((x) => x.key === "company");
        // not asked here: the name the company step saved wins over the organisation's theme
        if (alive && typeof d?.value === "string") setCompany((c) => (askCompany ? c || (d.value as string) : (d.value as string)));
      },
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId, askCompany]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const name = company.trim();
      if (name && askCompany) await amApi.putDefault(installId, "company", name);
      await amApi.putTheme(name ? { id: preset, company: name } : { id: preset });
      await amApi.putDefault(installId, "theme", { id: preset });
      onNext();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="am-step-theme">
      <StepHeader title={t("theme.title")} body={askCompany ? t("theme.body") : t("theme.bodyNoCompany")} />
      {askCompany ? (
        <label className="mt-4 block text-sm">
          <span className="text-text-secondary">{t("theme.company")}</span>
          <input
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            maxLength={120}
            placeholder={t("theme.companyPlaceholder")}
            className="focus-ring mt-1 w-full rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
          />
        </label>
      ) : null}
      <fieldset className={askCompany ? "mt-5" : "mt-4"}>
        <legend className="text-sm text-text-secondary">{t("theme.presets")}</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          {(presets ?? []).map((p) => {
            const on = p.id === preset;
            const sw = SWATCHES[p.id] ?? SWATCHES.navy!;
            return (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setPreset(p.id)}
                className={cls(
                  "focus-ring flex items-center gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                  on ? "border-text-primary bg-bg-elevated" : "border-border hover:bg-bg-elevated",
                )}
                data-testid="am-theme-preset"
              >
                <span className="flex shrink-0 overflow-hidden rounded-md border border-border" aria-hidden>
                  {sw.map((c) => (
                    <span key={c} className="h-8 w-4" style={{ backgroundColor: c }} />
                  ))}
                </span>
                <span className="min-w-0 truncate text-sm text-text-primary">{p.name || (PRESET_NAMES[p.id] ? t(PRESET_NAMES[p.id]!) : p.id)}</span>
              </button>
            );
          })}
          {!presets ? <span className="skeleton h-14 rounded-xl" aria-hidden /> : null}
        </div>
        <p className="mt-2 text-xs text-text-muted">{t("theme.tenantNote")}</p>
      </fieldset>
      {error ? (
        <div className="mt-3">
          <ErrorPanel message={error} />
        </div>
      ) : null}
      <Footer>
        <Button type="button" onClick={() => void save()} loading={saving} data-testid="am-theme-save">
          <span>{saving ? t("common.saving") : t("common.next")}</span>
        </Button>
        <Button type="button" variant="secondary" onClick={onBack} disabled={saving} className="ml-auto">
          {t("common.previous")}
        </Button>
      </Footer>
    </div>
  );
}

// ── 4 · file set ───────────────────────────────────────────────────────

export const CURRENCIES = ["THB", "USD", "EUR", "JPY", "CNY", "SGD"];

export function FormatsStep({ installId, manifest, onBack, onNext }: { installId: string; manifest: Manifest; onBack: () => void; onNext: () => void }) {
  const t = useAmT();
  const outputs = manifest.outputs ?? [];
  // currency and materiality are the financial report's questions ("currency" in install.questions)
  const withCurrency = asksCurrency(manifest);
  const [formats, setFormats] = useState<string[]>(() => outputs.filter((o) => o.default).map((o) => o.format));
  const [currency, setCurrency] = useState("THB");
  const [materiality, setMateriality] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const materialityOk = !withCurrency || materiality.trim() === "" || /^\d+(\.\d+)?$/.test(materiality.trim());

  const toggle = (f: string) => setFormats((cur) => (cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]));

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const ordered = outputs.map((o) => o.format).filter((f) => formats.includes(f));
      await amApi.putDefault(installId, "formats", ordered);
      if (withCurrency) {
        await amApi.putDefault(installId, "currency", currency);
        if (materiality.trim()) await amApi.putDefault(installId, "materiality", materiality.trim());
      }
      onNext();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="am-step-formats">
      <StepHeader title={t("formats.title")} body={t("formats.body")} />
      <fieldset className="mt-4">
        <legend className="text-sm text-text-secondary">{t("formats.files")}</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {/* AM23 §1.4: a PDF is how a document prints — an option on the document it prints, never a file beside it */}
          {outputs
            .filter((o) => o.format !== "pdf")
            .map((o) => {
              const pdf = outputs.find((x) => x.format === "pdf" && (x.of ?? "docx") === o.format);
              return (
                <div key={o.format} className="flex flex-col gap-1.5" data-testid={`am-format-${o.format}`}>
                  <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-border bg-bg-base px-3 py-2.5 text-sm text-text-primary hover:bg-bg-elevated">
                    <input type="checkbox" checked={formats.includes(o.format)} onChange={() => toggle(o.format)} className="h-4 w-4 accent-current" />
                    <span>{o.label}</span>
                  </label>
                  {pdf ? (
                    <label className="ml-6 flex cursor-pointer items-center gap-2 text-xs text-text-secondary" data-testid="am-format-pdf-of">
                      <input type="checkbox" checked={formats.includes("pdf")} onChange={() => toggle("pdf")} className="h-3.5 w-3.5 accent-current" />
                      <span>
                        {t("formats.pdfOf")} · {pdf.label}
                      </span>
                    </label>
                  ) : null}
                </div>
              );
            })}
        </div>
        {formats.length === 0 ? <p className="mt-2 text-xs text-status-warning">{t("formats.needOne")}</p> : null}
      </fieldset>
      {withCurrency ? (
        <div className="mt-5 grid gap-4 sm:grid-cols-2" data-testid="am-formats-currency">
          <label className="block text-sm">
            <span className="text-text-secondary">{t("formats.currency")}</span>
            <select
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              className="focus-ring mt-1 w-full rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary"
            >
              {CURRENCIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-text-secondary">{t("formats.materiality")}</span>
            <input
              inputMode="decimal"
              value={materiality}
              onChange={(e) => setMateriality(e.target.value.replace(/[,\s]/g, ""))}
              placeholder={t("formats.materialityPlaceholder")}
              aria-invalid={!materialityOk}
              className="focus-ring mt-1 w-full rounded-lg border border-border bg-bg-base px-3 py-2 text-sm tabular-nums text-text-primary placeholder:text-text-muted"
            />
            <span className="mt-1 block text-xs text-text-muted">{t("formats.materialityHint")}</span>
          </label>
        </div>
      ) : null}
      {error ? (
        <div className="mt-3">
          <ErrorPanel message={error} />
        </div>
      ) : null}
      <Footer>
        <Button
          type="button"
          onClick={() => void save()}
          loading={saving}
          disabled={formats.length === 0 || !materialityOk}
          data-testid="am-formats-save"
        >
          <span>{saving ? t("common.saving") : t("common.next")}</span>
        </Button>
        <Button type="button" variant="secondary" onClick={onBack} disabled={saving} className="ml-auto">
          {t("common.previous")}
        </Button>
      </Footer>
    </div>
  );
}

// ── 5 · self-test ──────────────────────────────────────────────────────

export function SelfTestStep({
  installId,
  manifest,
  onBack,
  onFinish,
}: {
  installId: string;
  manifest: Manifest;
  onBack: () => void;
  onFinish: () => void;
}) {
  const t = useAmT();
  const { runnerHealthy } = useAgentMarket();
  const [runId, setRunId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const thread = useThread(installId);

  // A self-test already started for this install (a reload) is shown, not rerun.
  useEffect(() => {
    let alive = true;
    amApi.getInstall(installId).then(
      (d) => alive && d?.install?.self_test_run_id && setRunId((cur) => cur ?? d.install.self_test_run_id ?? null),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId]);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const { run } = await amApi.selfTest(installId);
      setRunId(run.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setStarting(false);
    }
  };

  // Only the agent's side of the self-test run (it has no messages of the
  // person's; the thread shown here sits on a card surface).
  const mine: ThreadItem[] = runId
    ? thread.items.filter((i) => !isUserItem(i) && (i.run_id === runId || (i.payload as { run_id?: string } | null)?.run_id === runId))
    : [];
  const progress = mine.find((i) => i.kind === "progress")?.payload as ProgressPayload | undefined;
  const done = progress?.status === "succeeded" || mine.some((i) => i.kind === "outputs");
  const failed = progress?.status === "failed" || progress?.status === "cancelled";
  const minutes = manifest.typical_run_seconds ? Math.max(1, Math.round(manifest.typical_run_seconds / 60)) : null;
  const ctx: WorkspaceCtx = { installId, icon: manifest.icon, merge: thread.merge, selfTestRuns: new Set(runId ? [runId] : []) };

  return (
    <div data-testid="am-step-selftest">
      <StepHeader title={t("selftest.title")} body={t("selftest.body")} />
      {minutes ? <p className="mt-1 text-xs text-text-muted">{t("market.runTimeMin", { n: String(minutes) })}</p> : null}
      {runnerHealthy === false && !runId ? (
        <p className="mt-3 rounded-lg border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-sm text-text-primary">{t("selftest.runnerDown")}</p>
      ) : null}
      {error ? (
        <div className="mt-3">
          <ErrorPanel message={error} />
        </div>
      ) : null}
      {runId ? (
        <div className="mt-4" aria-live="polite" data-testid="am-selftest-thread">
          <WorkspaceContext.Provider value={ctx}>
            {mine.length ? (
              <ThreadView items={done ? mine.filter((i) => i.kind !== "outputs") : mine} />
            ) : (
              <div className="flex items-center gap-2 text-sm text-text-secondary">
                <Spinner size={14} />
                <span>{t("selftest.starting")}</span>
              </div>
            )}
          </WorkspaceContext.Provider>
        </div>
      ) : null}
      {runId && done ? <SelfTestFiles runId={runId} /> : null}
      {runId && done ? (
        <p className="mt-3 text-sm font-medium text-text-primary" data-testid="am-installed-done">
          {t("wiz.installedDone")}
        </p>
      ) : null}
      {runId && failed && !done ? (
        <div role="alert" className="mt-4 rounded-xl border border-status-danger/40 bg-status-danger/10 px-4 py-3 text-sm text-text-primary" data-testid="am-selftest-failed">
          <p className="font-medium">{t("wiz.selftestFailed")}</p>
          <p className="mt-1 text-text-secondary">{t("wiz.selftestFailedNext")}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setRunId(null);
                void start();
              }}
              data-testid="am-selftest-again"
            >
              {t("wiz.selftestAgain")}
            </Button>
            <ProblemButton target={{ kind: "run", runId }} />
          </div>
        </div>
      ) : null}
      <Footer>
        {!runId ? (
          <Button type="button" onClick={() => void start()} loading={starting} data-testid="am-selftest-start">
            <span>{starting ? t("selftest.starting") : t("selftest.start")}</span>
          </Button>
        ) : (
          <Button type="button" variant={done ? "primary" : "secondary"} onClick={() => onFinish()} data-testid="am-selftest-enter">
            {t("selftest.enter")}
          </Button>
        )}
        {!runId ? (
          <Button type="button" variant="secondary" onClick={() => onFinish()} data-testid="am-selftest-skip">
            {t("selftest.skip")}
          </Button>
        ) : null}
        <Button type="button" variant="secondary" onClick={onBack} disabled={starting} className="ml-auto">
          {t("common.previous")}
        </Button>
      </Footer>
    </div>
  );
}

// ── the self-test's actual files (AM3) ─────────────────────────────────

function latestByFormat(all: Artifact[]): Artifact[] {
  const best = new Map<string, Artifact>();
  for (const a of all) {
    const cur = best.get(a.format);
    if (!cur || a.version > cur.version) best.set(a.format, a);
  }
  return [...best.values()];
}

/** The files the agent really made from its sample data — the moment a person decides whether to trust it. */
export function SelfTestFiles({ runId }: { runId: string }) {
  const t = useAmT();
  const [arts, setArts] = useState<Artifact[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let tries = 0;
    const load = () => {
      amApi.run(runId).then(
        (d: RunDetail) => {
          if (!alive) return;
          const list = latestByFormat(d?.artifacts ?? []);
          // the outputs card can arrive a moment before the run row reads succeeded
          if (!list.length && tries++ < 5) {
            setTimeout(load, 1500);
            return;
          }
          setArts(list);
        },
        (err) => {
          if (alive) setError(errorMessage(err));
        },
      );
    };
    load();
    return () => {
      alive = false;
    };
  }, [runId]);
  return (
    <section className="mt-4 rounded-xl border border-status-success/40 bg-status-success/10 px-4 py-3" data-testid="am-selftest-files" aria-labelledby="am-selftest-files-title">
      <h3 id="am-selftest-files-title" className="text-sm font-semibold text-text-primary">
        {t("wiz.selftestFilesTitle")}
      </h3>
      <p className="mt-0.5 text-xs text-text-secondary">{t("wiz.selftestFilesBody")}</p>
      {error ? (
        <div className="mt-2">
          <ErrorPanel message={error} />
        </div>
      ) : arts === null ? (
        <div className="mt-2 flex items-center gap-2 text-xs text-text-muted">
          <Spinner size={12} />
          <span>{t("common.loading")}</span>
        </div>
      ) : arts.length ? (
        <ul className="mt-2 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle bg-bg-surface">
          {arts.map((a) => (
            <ArtifactRow key={a.id} artifact={a} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-text-muted">{t("wiz.selftestNoFiles")}</p>
      )}
    </section>
  );
}

// ── the company's own template (optional, AM3) ─────────────────────────

export function TemplateStep({ installId, manifest, onBack, onNext }: { installId: string; manifest: Manifest; onBack: () => void; onNext: () => void }) {
  const t = useAmT();
  const [defaults, setDefaults] = useState<Default[] | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    amApi.defaults(installId).then(
      (r) => {
        if (alive) setDefaults(r?.defaults ?? []);
      },
      () => {
        if (alive) setDefaults([]);
      },
    );
    return () => {
      alive = false;
    };
  }, [installId, n]);
  const saved = savedTemplateOf(defaults);
  return (
    <div data-testid="am-step-template">
      <StepHeader title={t("wiz.templateTitle")} body={t("wiz.templateBody")} />
      <div className="mt-4">
        {defaults === null ? (
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <Spinner size={14} />
            <span>{t("common.loading")}</span>
          </div>
        ) : (
          <TemplateSection installId={installId} spec={manifest.templates ?? {}} saved={saved} onChanged={() => setN((x) => x + 1)} />
        )}
      </div>
      <Footer>
        <Button type="button" onClick={onNext} data-testid="am-template-next">
          {saved ? t("common.next") : t("wiz.templateSkip")}
        </Button>
        <Button type="button" variant="secondary" onClick={onBack} className="ml-auto">
          {t("common.previous")}
        </Button>
      </Footer>
    </div>
  );
}

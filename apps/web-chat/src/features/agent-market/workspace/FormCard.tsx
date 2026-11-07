"use client";

// AM10 — the form a person dropped into this round ("ใส่ในฟอร์มนี้ให้หน่อย"),
// as its thread item follows it: being read, waiting for the person to say
// which slot takes what (the install's mapping screen, saving to the round),
// ready, filled — or filled short, with every slot it lacks named: a partly
// filled form is delivered as what it is and is not a pass. The form is this
// round's only; the agent's saved form is never changed from here.
//
// AM11 — what a form lacks is a list under its own heading ("ช่องที่ยังกรอกไม่ได้"),
// each field with why. A form the agent has a value for less than half of is
// refused before any work: the card says how many fields the form expects,
// how many have a value, names the rest, and offers the mapping screen — never
// a file. A form that cannot be used says why in words.

import { useEffect, useState } from "react";
import { FileCheck2, FileWarning, FileSearch } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";

import { amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import { TemplateMapper } from "../template/TemplateMapper";
import type { FormPayload, RunFormDetail, TemplateInspect } from "../types";
import { cls } from "../ui";
import { personText } from "./personText";

const STATUS: Record<string, AmKey> = {
  inspecting: "form.inspecting",
  needs_mapping: "form.needsMapping",
  ready: "form.ready",
  filled: "form.filled",
  unfilled: "form.unfilled",
  failed: "form.failed",
};

// why the filler cannot use a form at all, in the words the mapping screen already uses for it
const FAILED_WHY: Record<string, AmKey> = {
  chart_not_updated: "tpl.reason.chart_not_updated",
  unsupported_location: "tpl.reason.unsupported_location",
};

/** What the card says for the form as it stands. */
function sentence(p: FormPayload, t: (key: AmKey, vars?: Record<string, string>) => string): string {
  if (p.status === "refused") {
    return p.reason === "no_fields"
      ? t("form.refused.noFields")
      : t("form.refused.tooFew", { fields: String(p.fields ?? 0), mapped: String(p.mapped ?? 0) });
  }
  // AM22: the round had nothing for the form — no file, and not a form that cannot be used (the why is the package's)
  if (p.status === "failed" && p.reason === "nothing_this_round") return t("form.notThisRound");
  if (p.status === "failed" && p.reason && FAILED_WHY[p.reason]) return t("form.failedWhy", { why: t(FAILED_WHY[p.reason]) });
  const n = p.status === "unfilled" ? (p.unfilled?.length ?? 0) || 1 : (p.open ?? 0);
  return t(STATUS[p.status] ?? "form.inspecting", { n: String(n) });
}

/** The mapping screen's data for a round's form (what the person mapped wins over what was proposed). */
export function inspectOf(d: RunFormDetail): TemplateInspect {
  return {
    slots: d.inspect?.slots ?? d.form.slots ?? [],
    proposal: { ...(d.inspect?.proposal ?? {}), ...(d.form.mapping ?? {}) },
    diff: { first: true, kept: [], added: [], removed: [] },
    proposed_by: d.inspect?.proposed_by ?? null,
  };
}

export function FormCard({ p }: { p: FormPayload }) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<RunFormDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setDetail(null);
    setError(null);
    amApi.runForm(p.run_id).then(
      (d) => alive && setDetail(d),
      (err) => alive && setError(errorMessage(err)),
    );
    return () => {
      alive = false;
    };
  }, [open, p.run_id]);

  const unfilled = p.unfilled ?? [];
  const whys = p.unfilled_why ?? [];
  const refused = p.status === "refused";
  const bad = p.status === "unfilled" || p.status === "failed" || refused;
  const Icon = p.status === "filled" ? FileCheck2 : bad ? FileWarning : FileSearch;
  // a refused form may still be mapped by hand, when it has fields at all
  const canMap = p.status === "needs_mapping" || p.status === "ready" || p.status === "filled" || p.status === "unfilled" || (refused && p.reason !== "no_fields");
  const why = personText(p.why ?? "");

  const download = () => {
    if (!p.artifact_id) return;
    setBusy(true);
    amApi.downloadArtifact(p.artifact_id, p.name).then(
      () => setBusy(false),
      () => setBusy(false),
    );
  };

  return (
    <div
      className={cls(
        "rounded-xl border bg-bg-surface px-4 py-3",
        bad ? "border-status-warning/40" : p.status === "needs_mapping" ? "border-status-warning/40" : "border-border",
      )}
      data-testid="am-form"
      data-status={p.status}
      data-run={p.run_id}
    >
      <div className="flex items-start gap-2.5">
        <span className={cls("mt-0.5 shrink-0", p.status === "filled" ? "text-status-success" : bad ? "text-status-warning" : "text-text-muted")}>
          {p.status === "inspecting" ? <Spinner size={15} /> : <Icon width={16} height={16} aria-hidden />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-text-primary" title={p.name} data-testid="am-form-name">
            {p.name}
          </div>
          <p className="mt-0.5 text-sm text-text-secondary" data-testid="am-form-text">
            {sentence(p, t)}
          </p>
          {p.status === "failed" && why ? <p className="mt-0.5 text-xs text-text-secondary">{why}</p> : null}
          {unfilled.length ? (
            <div className="mt-2 rounded-lg bg-status-warning/10 px-3 py-2">
              <div className="text-xs font-semibold text-text-primary" id={`form-unfilled-${p.file_id}`}>
                {t("form.unfilledTitle")}
              </div>
              <ul className="mt-1 space-y-0.5" aria-labelledby={`form-unfilled-${p.file_id}`} data-testid="am-form-unfilled">
                {unfilled.map((label, i) => {
                  const because = personText(whys[i] ?? "");
                  return (
                    <li key={`${label}-${i}`} className="text-xs text-text-primary">
                      <span className="font-medium">{label}</span>
                      {because ? <span className="text-text-secondary">{` — ${because}`}</span> : null}
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
          <p className="mt-1.5 text-[11px] text-text-muted">{t("form.thisRoundOnly")}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {canMap ? (
              <Button type="button" variant={p.status === "needs_mapping" ? "primary" : "secondary"} size="sm" onClick={() => setOpen(true)} data-testid="am-form-map">
                {p.status === "needs_mapping" || refused ? t("form.map") : t("form.remap")}
              </Button>
            ) : null}
            {p.artifact_id ? (
              <Button type="button" variant="secondary" size="sm" onClick={download} loading={busy} data-testid="am-form-download">
                {p.status === "unfilled" ? t("form.downloadDraft") : t("form.download")}
              </Button>
            ) : null}
          </div>
        </div>
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title={t("form.mapTitle")} maxWidth={640} testId="am-form-mapper">
        {error ? (
          <p className="text-sm text-status-danger" role="alert">
            {error}
          </p>
        ) : !detail ? (
          <div className="flex justify-center py-6" role="status">
            <Spinner size={18} />
          </div>
        ) : (
          <TemplateMapper
            installId=""
            spec={detail.spec}
            fileId={detail.form.file_id}
            fileName={detail.form.name}
            inspect={inspectOf(detail)}
            saved={null}
            save={(body) => amApi.saveRunForm(p.run_id, { slots: body.slots, mapping: body.mapping })}
            onSaved={() => setOpen(false)}
            onCancel={() => setOpen(false)}
          />
        )}
      </Modal>
    </div>
  );
}

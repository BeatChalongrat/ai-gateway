"use client";

// AM4 — two tabs of the admins' page.
//
// รอบงานทั้งหมด: every run of the organisation as metadata only — status,
// cost, model, the step where it stopped. The files, figures and answers of
// someone else's run open one run at a time: the admin says why, the opening
// is recorded, the run's owner is told, and it lasts an hour. No global read.
//
// โมเดลและการเก็บข้อมูล: whether documents may go to a model outside the
// organisation (off by default), and how many years the organisation's
// accounting records are kept. AM6 — an admin no longer names models by
// outcome: the agent's model menu is the chat's, and this switch is the
// policy it follows.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";

import { amApi, errorMessage } from "../api";
import { creditsToBaht, formatBaht, formatDateTime } from "../format";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import { outcomeKey, outcomeOf, outcomeTone } from "../outcome";
import { stepLabel } from "../runs/RunDetailPage";
import type { OrgSettings, Pricing, RunMeta } from "../types";
import { cls, ErrorPanel, Pill, Thai } from "../ui";

const FILTERS: readonly { id: string; key: AmKey }[] = [
  { id: "all", key: "admin.runs.all" },
  { id: "failed", key: "admin.runs.failedOnly" },
  { id: "awaiting_input", key: "admin.runs.waiting" },
];
const FINISHED = new Set(["succeeded", "failed", "cancelled"]);

const FIELD = "focus-ring w-full rounded-lg border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted";

export const adminRunHref = (runId: string) => `/chat/agents/runs/${runId}?as=admin`;

function ReasonDialog({
  open,
  title,
  body,
  confirm,
  danger = false,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  confirm: string;
  danger?: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const t = useAmT();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = () => {
    if (reason.trim().length < 8) {
      setError(t("admin.runs.reasonShort"));
      return;
    }
    setBusy(true);
    setError(null);
    onConfirm(reason.trim()).then(
      () => {
        setBusy(false);
        setReason("");
      },
      (err) => {
        setBusy(false);
        setError(errorMessage(err));
      },
    );
  };
  return (
    <Thai>
      <Modal open={open} onClose={onClose} title={title} description={body} testId="am-admin-reason">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!busy) submit();
          }}
          className="flex flex-col gap-3"
        >
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            maxLength={500}
            placeholder={t("admin.runs.reasonPlaceholder")}
            className="focus-ring w-full resize-y rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
            data-testid="am-admin-reason-text"
          />
          {error ? <ErrorPanel message={error} /> : null}
          <div className="flex gap-2">
            <Button type="submit" variant={danger ? "danger" : "primary"} size={danger ? "md" : "sm"} loading={busy} data-testid="am-admin-reason-go">
              <span>{confirm}</span>
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={busy}>
              {t("common.cancel")}
            </Button>
          </div>
        </form>
      </Modal>
    </Thai>
  );
}

export function RunsTab({ pricing }: { pricing: Pricing }) {
  const t = useAmT();
  const locale = useAmLocale();
  const router = useRouter();
  const [status, setStatus] = useState("all");
  const [rows, setRows] = useState<RunMeta[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const [opening, setOpening] = useState<RunMeta | null>(null);
  const [removing, setRemoving] = useState<RunMeta | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    amApi.adminRuns(status).then(
      (r) => alive && setRows(r?.runs ?? []),
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [status, reload]);

  return (
    <section aria-labelledby="am-admin-runs" data-testid="am-admin-runs">
      <h2 id="am-admin-runs" className="text-sm font-semibold text-text-primary">
        {t("admin.runs.title")}
      </h2>
      <p className="mt-0.5 max-w-3xl text-xs text-text-muted">{t("admin.runs.body")}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label={t("admin.runs.filter")}>
        {FILTERS.map(({ id, key }) => (
          <button
            key={id}
            type="button"
            aria-pressed={status === id}
            onClick={() => setStatus(id)}
            className={cls(
              "focus-ring rounded-full border px-3 py-1 text-xs",
              status === id ? "border-border-strong bg-bg-elevated text-text-primary" : "border-border text-text-secondary hover:text-text-primary",
            )}
          >
            {t(key)}
          </button>
        ))}
      </div>
      {note ? (
        <p role="status" className="mt-3 text-xs text-status-success">
          {note}
        </p>
      ) : null}
      <div className="mt-3">
        {error ? <ErrorPanel message={errorMessage(error)} onRetry={() => setReload((n) => n + 1)} /> : null}
        {!rows && !error ? <Spinner size={14} /> : null}
        {rows && !rows.length ? <p className="text-sm text-text-muted">{t("admin.runs.none")}</p> : null}
        {rows && rows.length ? (
          <div className="overflow-x-auto rounded-xl border border-border-subtle">
            <table className="w-full min-w-[46rem] text-left text-sm">
              <thead className="bg-bg-surface text-xs text-text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">{t("admin.runs.person")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.runs.agent")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.runs.status")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.runs.cost")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.runs.model")}</th>
                  <th className="px-3 py-2 font-medium" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {rows.map((r) => {
                  // AM6: a run is named by what it came to (a round that finished open is no failure)
                  const outcome = outcomeOf(r);
                  return (
                    <tr key={r.id} data-testid="am-admin-run">
                      <td className="px-3 py-2 align-top">
                        <div className="text-text-primary">{r.person || r.email || "—"}</div>
                        <div className="text-xs text-text-muted">{formatDateTime(r.created_at, locale)}</div>
                      </td>
                      <td className="px-3 py-2 align-top">
                        <div className="text-text-primary">{r.agent}</div>
                        {r.period_label ? <div className="text-xs text-text-muted">{r.period_label}</div> : null}
                      </td>
                      <td className="px-3 py-2 align-top">
                        <span data-testid="am-admin-run-status" data-outcome={outcome}>
                          <Pill tone={outcomeTone(outcome)}>{t(outcomeKey(outcome, r.reason) ?? "progress.title.running")}</Pill>
                        </span>
                        {r.stopped_at_step ? (
                          <div className="mt-1 text-xs text-text-muted">{`${t("admin.runs.stopped")}: ${stepLabel(r.stopped_at_step, t)}`}</div>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 align-top tabular-nums text-text-secondary">{t("admin.runs.baht", { baht: formatBaht(creditsToBaht(r.credits, pricing)) })}</td>
                      <td className="px-3 py-2 align-top text-xs text-text-secondary">
                        {r.model ? `${r.model} · ${r.model_self_hosted ? t("where.inOrg") : t("where.outside")}` : "—"}
                      </td>
                      <td className="px-3 py-2 align-top">
                        <div className="flex flex-col items-start gap-1">
                          {r.open_now ? (
                            <Button type="button" variant="secondary" size="sm" onClick={() => router.push(adminRunHref(r.id))} data-testid="am-admin-run-view">
                              {t("admin.runs.openNow")}
                            </Button>
                          ) : (
                            <Button type="button" variant="secondary" size="sm" onClick={() => setOpening(r)} data-testid="am-admin-run-open">
                              {t("admin.runs.open")}
                            </Button>
                          )}
                          {r.times_opened ? <span className="text-[11px] text-text-muted">{t("admin.runs.opened", { n: String(r.times_opened) })}</span> : null}
                          {FINISHED.has(r.status) ? (
                            <button type="button" onClick={() => setRemoving(r)} className="text-[11px] text-text-muted underline underline-offset-2 hover:text-status-danger">
                              {t("admin.runs.removeEvidence")}
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
        title={t("admin.runs.reasonTitle")}
        body={t("admin.runs.reasonBody")}
        confirm={t("admin.runs.confirmOpen")}
        onClose={() => setOpening(null)}
        onConfirm={async (reason) => {
          const r = opening!;
          await amApi.adminOpenRun(r.id, reason);
          setOpening(null);
          router.push(adminRunHref(r.id));
        }}
      />
      <ReasonDialog
        open={!!removing}
        danger
        title={t("admin.runs.removeTitle")}
        body={t("admin.runs.removeBody")}
        confirm={t("admin.runs.confirmRemove")}
        onClose={() => setRemoving(null)}
        onConfirm={async (reason) => {
          const r = removing!;
          const res = await amApi.adminRemoveEvidence(r.id, reason);
          setRemoving(null);
          setNote(t("admin.runs.removed", { n: String(res?.removed_objects ?? 0) }));
          setReload((n) => n + 1);
        }}
      />
    </section>
  );
}

export function ModelsTab({ org, onSaved }: { org: OrgSettings; onSaved: () => void }) {
  const t = useAmT();
  const [leave, setLeave] = useState(!!org.documents_may_leave);
  const [years, setYears] = useState(String(org.retention_years ?? 5));
  const [warnMb, setWarnMb] = useState(String(org.storage_warn_mb ?? 2048));
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const save = () => {
    setBusy(true);
    setSaved(null);
    setSaveError(null);
    amApi
      .adminPutOrg({ documents_may_leave: leave, retention_years: Number(years), storage_warn_mb: Number(warnMb) })
      .then(
        () => {
          setBusy(false);
          setSaved(t("admin.retention.saved"));
          onSaved();
        },
        (err) => {
          setBusy(false);
          setSaveError(errorMessage(err));
        },
      );
  };

  return (
    <div className="flex flex-col gap-8" data-testid="am-admin-models">
      <section aria-labelledby="am-leave">
        <h2 id="am-leave" className="text-sm font-semibold text-text-primary">
          {t("admin.models.leaveTitle")}
        </h2>
        <p className="mt-0.5 max-w-3xl text-xs text-text-muted">{t("admin.models.leaveBody")}</p>
        <div role="radiogroup" aria-labelledby="am-leave" className="mt-2.5 flex flex-col gap-2">
          {([false, true] as const).map((v) => (
            <label key={String(v)} className={cls("flex cursor-pointer items-start gap-2 rounded-xl border px-4 py-3 text-sm", leave === v ? "border-border-strong bg-bg-elevated" : "border-border-subtle")}>
              <input type="radio" name="am-leave" checked={leave === v} onChange={() => setLeave(v)} className="mt-1" data-testid={`am-leave-${v ? "on" : "off"}`} />
              <span className="text-text-primary">{v ? t("admin.models.leaveOn") : t("admin.models.leaveOff")}</span>
            </label>
          ))}
        </div>
      </section>

      <section aria-labelledby="am-retention">
        <h2 id="am-retention" className="text-sm font-semibold text-text-primary">
          {t("admin.retention.title")}
        </h2>
        <p className="mt-0.5 max-w-3xl text-xs text-text-muted">{t("admin.retention.body")}</p>
        <p className="mt-1 max-w-3xl text-xs text-text-muted">{t("admin.retention.notArchive")}</p>
        <div className="mt-2.5 grid max-w-md grid-cols-2 gap-3">
          <label className="text-xs text-text-secondary">
            {t("admin.retention.years")}
            <input type="number" min={1} max={30} value={years} onChange={(e) => setYears(e.target.value)} className={cls(FIELD, "mt-1")} data-testid="am-retention-years" />
          </label>
          <label className="text-xs text-text-secondary">
            {t("admin.retention.warn")}
            <input type="number" min={50} value={warnMb} onChange={(e) => setWarnMb(e.target.value)} className={cls(FIELD, "mt-1")} />
          </label>
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" size="sm" onClick={save} loading={busy} data-testid="am-admin-models-save">
          <span>{t("common.save")}</span>
        </Button>
        {saved ? (
          <span role="status" className="text-xs text-status-success">
            {saved}
          </span>
        ) : null}
        {saveError ? <ErrorPanel message={saveError} /> : null}
      </div>
    </div>
  );
}

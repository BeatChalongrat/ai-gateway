"use client";

// The expense policy (install question "policy", the expense audit) in the
// install wizard and in settings. Both start from what the install already
// has (install defaults "policy" / "holidays"), else the manifest's sample
// table, check the table here with the server's rules, and save it with
// PUT /defaults/policy and /defaults/holidays — an emptied calendar is
// removed, so the package's Thai 2569 public holidays apply. A refusal
// (400 BAD_VALUE, "ค่าไม่ถูกต้อง: … — categories.meals.per_claim: …") is
// shown as the server wrote it.

import { useEffect, useState } from "react";
import { Pencil } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useAuthStore } from "@/stores/authStore";

import { amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import { formatMoney } from "../format";
import type { Default, ExpensePolicy, Manifest } from "../types";
import { ErrorPanel, Pill } from "../ui";
import { Footer, StepHeader } from "../install/steps";
import { HolidayEditor, PolicyEditor } from "./PolicyEditor";
import {
  checkHolidays,
  checkPolicy,
  draftFromPolicy,
  holidayRows,
  holidaysConfirmed,
  holidaysSetting,
  policyFromDraft,
  type HolidayRow,
  type PolicyDraft,
} from "./policy";

export interface PolicyState {
  draft: PolicyDraft;
  holidays: HolidayRow[];
  /** a calendar was saved before (emptying it removes it) */
  hadHolidays: boolean;
  /** AM3: the person confirmed these are the company's own holidays (the rule is off until then) */
  confirmed: boolean;
}

/** What the install has, else the manifest's sample table. */
export function initialPolicy(defaults: Default[] | null | undefined, manifest: Manifest | null | undefined): PolicyState {
  const stored = (defaults ?? []).find((d) => d.key === "policy")?.value;
  const sample = manifest?.install?.defaults?.policy;
  const table = (stored && typeof stored === "object" ? stored : sample && typeof sample === "object" ? sample : null) as ExpensePolicy | null;
  const hol = (defaults ?? []).find((d) => d.key === "holidays")?.value;
  return {
    draft: draftFromPolicy(table),
    holidays: holidayRows(hol),
    hadHolidays: hol !== undefined && hol !== null,
    confirmed: holidaysConfirmed(hol),
  };
}

export function policyReady(s: PolicyState): boolean {
  return Object.keys(checkPolicy(s.draft)).length === 0 && Object.keys(checkHolidays(s.holidays)).length === 0;
}

/** Who confirms the calendar (the signed-in person's email, when the page knows it). */
function confirmer(): string {
  try {
    return useAuthStore.getState().user?.email ?? "";
  } catch {
    return "";
  }
}

/** PUT the table, then the confirmed company calendar (or DELETE it when emptied or unconfirmed). */
export async function savePolicy(installId: string, s: PolicyState): Promise<void> {
  await amApi.putDefault(installId, "policy", policyFromDraft(s.draft));
  const cal = holidaysSetting(s.holidays, s.confirmed, confirmer());
  if (cal) await amApi.putDefault(installId, "holidays", cal);
  else if (s.hadHolidays) await amApi.deleteDefault(installId, "holidays");
}

function PolicyForm({
  state,
  setState,
  showAll,
  disabled,
  idPrefix,
}: {
  state: PolicyState;
  setState: (s: PolicyState) => void;
  showAll: boolean;
  disabled: boolean;
  idPrefix: string;
}) {
  return (
    <div className="flex flex-col gap-5">
      <PolicyEditor
        draft={state.draft}
        onChange={(draft) => setState({ ...state, draft })}
        problems={checkPolicy(state.draft)}
        showAll={showAll}
        disabled={disabled}
        idPrefix={idPrefix}
      />
      <HolidayEditor
        rows={state.holidays}
        onChange={(holidays) => setState({ ...state, holidays, confirmed: holidays.length ? state.confirmed : false })}
        confirmed={state.confirmed}
        onConfirm={(confirmed) => setState({ ...state, confirmed })}
        problems={checkHolidays(state.holidays)}
        showAll={showAll}
        disabled={disabled}
        idPrefix={`${idPrefix}-holidays`}
      />
    </div>
  );
}

// ── install wizard ───────────────────────────────────────────────────────

export function PolicyStep({ installId, manifest, onBack, onNext }: { installId: string; manifest: Manifest; onBack?: () => void; onNext: () => void }) {
  const t = useAmT();
  const [state, setState] = useState<PolicyState | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    amApi.defaults(installId).then(
      (r) => alive && setState((s) => s ?? initialPolicy(r?.defaults, manifest)),
      () => alive && setState((s) => s ?? initialPolicy(null, manifest)),
    );
    return () => {
      alive = false;
    };
  }, [installId, manifest]);

  const save = async () => {
    if (!state) return;
    if (!policyReady(state)) {
      setShowAll(true);
      setError(t("policy.fix"));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await savePolicy(installId, state);
      onNext();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="am-step-policy">
      <StepHeader title={t("policy.title")} body={t("policy.body")} />
      <div className="mt-4">
        {state ? (
          <PolicyForm state={state} setState={setState} showAll={showAll} disabled={saving} idPrefix="am-policy" />
        ) : (
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <Spinner size={14} />
            <span>{t("common.loading")}</span>
          </div>
        )}
      </div>
      {error ? (
        <div className="mt-3">
          <ErrorPanel message={error} />
        </div>
      ) : null}
      <Footer>
        <Button type="button" onClick={() => void save()} loading={saving} disabled={!state} data-testid="am-policy-save">
          <span>{saving ? t("common.saving") : t("common.next")}</span>
        </Button>
        {onBack ? (
          <Button type="button" variant="secondary" onClick={onBack} disabled={saving} className="ml-auto">
            {t("common.previous")}
          </Button>
        ) : null}
      </Footer>
    </div>
  );
}

// ── settings ─────────────────────────────────────────────────────────────

export function PolicySection({
  installId,
  manifest,
  defaults,
  onChanged,
}: {
  installId: string;
  manifest: Manifest;
  defaults: Default[];
  onChanged: () => void;
}) {
  const t = useAmT();
  const saved = initialPolicy(defaults, manifest);
  const [editing, setEditing] = useState(false);
  const [state, setState] = useState<PolicyState>(saved);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const d = saved.draft;

  const save = async () => {
    if (!policyReady(state)) {
      setShowAll(true);
      setError(t("policy.fix"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await savePolicy(installId, state);
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-8" aria-labelledby="am-policy-title">
      <h2 id="am-policy-title" className="text-sm font-semibold text-text-primary">
        {t("policy.title")}
      </h2>
      <p className="mt-0.5 text-xs text-text-muted">{t("policy.body")}</p>
      <div className="mt-2.5 rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-policy-section">
        {!editing ? (
          <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
            <div className="min-w-0 flex-1 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-text-primary" data-testid="am-policy-summary">
                  {t("policy.summary", {
                    n: String(d.categories.length),
                    receipt: formatMoney(d.receipt_required_above),
                    approval: formatMoney(d.approval_threshold),
                  })}
                </span>
                {d.source === "package_sample" ? <Pill tone="warning">{t("policy.sampleBadge")}</Pill> : null}
              </div>
              <div className="mt-1 text-xs text-text-secondary">
                {d.categories.map((c) => c.label || c.code).join(" · ")}
              </div>
              <div className="mt-1 text-xs text-text-muted" data-testid="am-policy-holidays-line">
                {saved.confirmed && saved.holidays.length ? t("policy.holidaysCount", { n: String(saved.holidays.length) }) : t("policy.holidaysPackage")}
              </div>
              {d.source === "package_sample" ? <p className="mt-2 text-xs text-text-secondary">{t("policy.sample")}</p> : null}
            </div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => {
                setState(initialPolicy(defaults, manifest));
                setShowAll(false);
                setError(null);
                setEditing(true);
              }}
              data-testid="am-policy-edit"
            >
              <Pencil width={13} height={13} aria-hidden />
              <span>{t("policy.edit")}</span>
            </Button>
          </div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!busy) void save();
            }}
          >
            <PolicyForm state={state} setState={setState} showAll={showAll} disabled={busy} idPrefix="am-settings-policy" />
            {error ? (
              <div className="mt-3">
                <ErrorPanel message={error} />
              </div>
            ) : null}
            <div className="mt-4 flex gap-2">
              <Button type="submit" size="sm" loading={busy} data-testid="am-policy-submit">
                <span>{busy ? t("common.saving") : t("common.save")}</span>
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => setEditing(false)} disabled={busy}>
                {t("common.cancel")}
              </Button>
            </div>
          </form>
        )}
      </div>
    </section>
  );
}

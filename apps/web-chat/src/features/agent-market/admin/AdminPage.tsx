"use client";

// /chat/agents/admin — the organisation's Agent Market (AM3). Admins keep who
// may use it (an org-wide switch plus the list of people, their departments
// and roles), the budgets (per person, per department), see runs and spend
// per person and per agent, read what people reported, and own the catalog
// with the publishers. Publishers see the catalog tab only. Every refusal is
// shown as the server wrote it (it says what to do and that nothing changed).

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Trash2 } from "lucide-react";

import { amApi, errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import { creditsToBaht, formatBaht, formatCredits, formatDateTime } from "../format";
import type { AdminCatalogRow, AdminOrg, AdminSpend, OrgMember, Pricing, ProblemReport, SpendRow } from "../types";
import {
  AgentTile,
  BackLink,
  BTN_DANGER_OUTLINE,
  BTN_PRIMARY_SMALL,
  BTN_SMALL,
  Card,
  cls,
  ErrorPanel,
  FeatureGate,
  PageShell,
  Pill,
  Spin,
  type Tone,
} from "../ui";
import { DEFAULT_PRICING, useAgentMarket } from "../useAgentMarket";
import { runHref } from "../workspace/context";
import { ModelsTab, RunsTab } from "./Am4Tabs";

export type AdminTab = "access" | "departments" | "spend" | "runs" | "models" | "catalog" | "problems";

const TABS: { id: AdminTab; key: AmKey; adminOnly: boolean }[] = [
  { id: "access", key: "admin.tab.access", adminOnly: true },
  { id: "departments", key: "admin.tab.departments", adminOnly: true },
  { id: "spend", key: "admin.tab.spend", adminOnly: true },
  // AM4: every run as metadata (content opens one run at a time, with a reason)
  { id: "runs", key: "admin.tab.runs", adminOnly: true },
  // AM4: documents leaving the organisation, the models people may pick, retention
  { id: "models", key: "admin.tab.models", adminOnly: true },
  { id: "catalog", key: "admin.tab.catalog", adminOnly: false },
  { id: "problems", key: "admin.tab.problems", adminOnly: true },
];

export function AdminPage() {
  return (
    <FeatureGate>
      <Admin />
    </FeatureGate>
  );
}

function Admin() {
  const t = useAmT();
  const { admin, publisher, pricing } = useAgentMarket();
  const tabs = TABS.filter((x) => admin || !x.adminOnly);
  const [tab, setTab] = useState<AdminTab>(admin ? "access" : "catalog");
  if (!publisher) {
    return (
      <PageShell width="max-w-xl">
        <BackLink href="/chat/agents">{t("detail.back")}</BackLink>
        <p className="mt-10 text-center text-sm text-text-secondary" data-testid="am-admin-denied">
          {t("admin.denied")}
        </p>
      </PageShell>
    );
  }
  const p = pricing ?? DEFAULT_PRICING;
  return (
    <PageShell width="max-w-6xl">
      <BackLink href="/chat/agents">{t("detail.back")}</BackLink>
      <h1 className="mt-4 text-xl font-semibold tracking-tight text-text-primary">{t("admin.title")}</h1>
      <p className="mt-1 text-sm text-text-secondary">{admin ? t("admin.body") : t("admin.bodyPublisher")}</p>
      <div role="tablist" aria-label={t("admin.tabs")} className="scrollbar-thin mt-5 flex gap-1 overflow-x-auto border-b border-border-subtle">
        {tabs.map((x) => (
          <button
            key={x.id}
            type="button"
            role="tab"
            aria-selected={tab === x.id}
            onClick={() => setTab(x.id)}
            className={cls(
              "focus-ring -mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors",
              tab === x.id ? "border-text-primary font-medium text-text-primary" : "border-transparent text-text-secondary hover:text-text-primary",
            )}
            data-testid={`am-admin-tab-${x.id}`}
          >
            {t(x.key)}
          </button>
        ))}
      </div>
      <div className="mt-5" role="tabpanel">
        {tab === "access" ? (
          <AccessTab pricing={p} />
        ) : tab === "departments" ? (
          <DepartmentsTab pricing={p} />
        ) : tab === "spend" ? (
          <SpendTab pricing={p} />
        ) : tab === "runs" ? (
          <RunsTab pricing={p} />
        ) : tab === "models" ? (
          <ModelsTabLoader />
        ) : tab === "catalog" ? (
          <CatalogTab admin={!!admin} />
        ) : (
          <ProblemsTab />
        )}
      </div>
    </PageShell>
  );
}

const FIELD = "focus-ring w-full rounded-lg border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted";

function ModelsTabLoader() {
  const { org, error, reload } = useOrg();
  if (error) return <ErrorPanel message={errorMessage(error)} onRetry={reload} />;
  if (!org) return <Spin size={14} />;
  return <ModelsTab org={org.org} onSaved={reload} />;
}

function useOrg(): { org: AdminOrg | null; error: unknown; reload: () => void } {
  const [org, setOrg] = useState<AdminOrg | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    amApi.adminOrg().then(
      (r) => {
        if (!alive) return;
        setOrg(r);
        setError(null);
      },
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [n]);
  return { org, error, reload: useCallback(() => setN((x) => x + 1), []) };
}

function bahtPerMonth(credits: number | null | undefined, p: Pricing): string {
  return formatBaht(creditsToBaht(credits ?? 0, p));
}

// ── access ───────────────────────────────────────────────────────────────

const ACCESS: { id: "off" | "list" | "everyone"; key: AmKey; body: AmKey }[] = [
  { id: "off", key: "admin.access.off", body: "admin.access.offBody" },
  { id: "list", key: "admin.access.list", body: "admin.access.listBody" },
  { id: "everyone", key: "admin.access.everyone", body: "admin.access.everyoneBody" },
];

const ROLES: { id: string; key: AmKey }[] = [
  { id: "member", key: "admin.role.member" },
  { id: "publisher", key: "admin.role.publisher" },
  { id: "admin", key: "admin.role.admin" },
];

function AccessTab({ pricing }: { pricing: Pricing }) {
  const t = useAmT();
  const { org, error, reload } = useOrg();
  const [form, setForm] = useState<{ access: string; domains: string; contact_name: string; contact_email: string; budget: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  useEffect(() => {
    if (org && !form)
      setForm({
        access: org.org.access,
        domains: (org.org.domains ?? []).join(", "),
        contact_name: org.org.contact_name,
        contact_email: org.org.contact_email,
        budget: String(org.org.user_monthly_credits),
        name: org.org.name,
      });
  }, [org, form]);

  if (error && !org) return <ErrorPanel message={errorMessage(error)} onRetry={reload} />;
  if (!org || !form) return <Loading />;

  const save = () => {
    setBusy(true);
    setMsg(null);
    amApi
      .adminPutOrg({
        name: form.name,
        access: form.access,
        domains: form.domains.split(/[\s,]+/).filter(Boolean),
        contact_name: form.contact_name,
        contact_email: form.contact_email,
        user_monthly_credits: form.budget.replace(/,/g, ""),
      })
      .then(
        (r) => {
          setBusy(false);
          setMsg({ text: r.resumed ? t("admin.savedResumed", { n: String(r.resumed) }) : t("admin.saved"), ok: true });
          reload();
        },
        (err) => {
          setBusy(false);
          setMsg({ text: errorMessage(err), ok: false });
        },
      );
  };

  return (
    <div className="flex flex-col gap-5" data-testid="am-admin-access">
      <Card>
        <h2 className="text-sm font-semibold text-text-primary">{t("admin.access.title")}</h2>
        <fieldset className="mt-3 grid gap-2 sm:grid-cols-3">
          <legend className="sr-only">{t("admin.access.title")}</legend>
          {ACCESS.map((a) => (
            <label
              key={a.id}
              className={cls(
                "flex cursor-pointer flex-col gap-1 rounded-xl border px-3 py-2.5",
                form.access === a.id ? "border-text-primary bg-bg-elevated" : "border-border hover:bg-bg-elevated",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-medium text-text-primary">
                <input type="radio" name="am-access" checked={form.access === a.id} onChange={() => setForm({ ...form, access: a.id })} data-testid={`am-access-${a.id}`} />
                {t(a.key)}
              </span>
              <span className="text-xs text-text-secondary">{t(a.body)}</span>
            </label>
          ))}
        </fieldset>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="text-text-secondary">{t("admin.domains")}</span>
            <input value={form.domains} onChange={(e) => setForm({ ...form, domains: e.target.value })} placeholder={t("admin.domainsPlaceholder")} className={cls(FIELD, "mt-1")} />
            <span className="mt-1 block text-[11px] text-text-muted">{t("admin.domainsHint")}</span>
          </label>
          <label className="block text-sm">
            <span className="text-text-secondary">{t("admin.userBudget")}</span>
            <input value={form.budget} onChange={(e) => setForm({ ...form, budget: e.target.value })} inputMode="numeric" className={cls(FIELD, "mt-1 tabular-nums")} data-testid="am-admin-user-budget" />
            <span className="mt-1 block text-[11px] text-text-muted">
              {t("admin.budgetBaht", { baht: bahtPerMonth(Number(form.budget.replace(/,/g, "")) || 0, pricing) })}
            </span>
          </label>
          <label className="block text-sm">
            <span className="text-text-secondary">{t("admin.contactName")}</span>
            <input value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} className={cls(FIELD, "mt-1")} />
          </label>
          <label className="block text-sm">
            <span className="text-text-secondary">{t("admin.contactEmail")}</span>
            <input type="email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} className={cls(FIELD, "mt-1")} />
            <span className="mt-1 block text-[11px] text-text-muted">{t("admin.contactHint")}</span>
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button type="button" onClick={save} disabled={busy} className={BTN_PRIMARY_SMALL} data-testid="am-admin-org-save">
            {busy ? <Spin size={12} /> : null}
            <span>{t("common.save")}</span>
          </button>
          {msg ? <Note msg={msg} /> : null}
        </div>
      </Card>
      <People org={org} reload={reload} pricing={pricing} />
    </div>
  );
}

function Note({ msg }: { msg: { text: string; ok: boolean } }) {
  return (
    <span role={msg.ok ? "status" : "alert"} className={cls("text-xs", msg.ok ? "text-status-success" : "text-status-danger")} data-testid="am-admin-note">
      {msg.text}
    </span>
  );
}

function People({ org, reload, pricing }: { org: AdminOrg; reload: () => void; pricing: Pricing }) {
  const t = useAmT();
  const [add, setAdd] = useState({ email: "", display_name: "", department: "", role: "member", budget: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const members = org.members ?? [];
  const depts = org.departments ?? [];

  const addOne = () => {
    setBusy(true);
    setMsg(null);
    amApi
      .adminPutMember(add.email.trim(), {
        display_name: add.display_name,
        department: add.department,
        role: add.role,
        monthly_credits: add.budget.trim() ? add.budget.replace(/,/g, "") : null,
      })
      .then(
        () => {
          setBusy(false);
          setAdd({ email: "", display_name: "", department: "", role: "member", budget: "" });
          setMsg({ text: t("admin.saved"), ok: true });
          reload();
        },
        (err) => {
          setBusy(false);
          setMsg({ text: errorMessage(err), ok: false });
        },
      );
  };

  return (
    <Card>
      <h2 className="text-sm font-semibold text-text-primary">{t("admin.people.title", { n: String(members.length) })}</h2>
      <p className="mt-0.5 text-xs text-text-muted">{org.org.access === "list" ? t("admin.people.bodyList") : t("admin.people.bodyOther")}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy && add.email.trim()) addOne();
        }}
        className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_auto_minmax(0,0.8fr)_auto]"
        data-testid="am-admin-add-person"
      >
        <input type="email" value={add.email} onChange={(e) => setAdd({ ...add, email: e.target.value })} placeholder={t("admin.people.email")} aria-label={t("admin.people.email")} className={FIELD} data-testid="am-admin-add-email" />
        <input value={add.display_name} onChange={(e) => setAdd({ ...add, display_name: e.target.value })} placeholder={t("admin.people.name")} aria-label={t("admin.people.name")} className={FIELD} />
        <input value={add.department} onChange={(e) => setAdd({ ...add, department: e.target.value })} placeholder={t("admin.people.department")} aria-label={t("admin.people.department")} className={FIELD} list="am-admin-depts" />
        <select value={add.role} onChange={(e) => setAdd({ ...add, role: e.target.value })} aria-label={t("admin.people.role")} className={FIELD}>
          {ROLES.map((r) => (
            <option key={r.id} value={r.id}>
              {t(r.key)}
            </option>
          ))}
        </select>
        <input value={add.budget} onChange={(e) => setAdd({ ...add, budget: e.target.value })} placeholder={t("admin.people.budgetDefault")} aria-label={t("admin.people.budget")} inputMode="numeric" className={FIELD} />
        <button type="submit" disabled={busy || !add.email.trim()} className={BTN_PRIMARY_SMALL} data-testid="am-admin-add-save">
          {busy ? <Spin size={12} /> : null}
          <span>{t("admin.people.add")}</span>
        </button>
      </form>
      <datalist id="am-admin-depts">
        {depts.map((d) => (
          <option key={d.name} value={d.name} />
        ))}
      </datalist>
      {msg ? (
        <div className="mt-2">
          <Note msg={msg} />
        </div>
      ) : null}
      <div className="scrollbar-thin mt-4 overflow-x-auto">
        <table className="w-full border-collapse text-sm" data-testid="am-admin-people">
          <thead>
            <tr className="border-b border-border-subtle text-left text-[11px] text-text-muted">
              <th className="py-1.5 pr-3 font-medium">{t("admin.people.email")}</th>
              <th className="py-1.5 pr-3 font-medium">{t("admin.people.name")}</th>
              <th className="py-1.5 pr-3 font-medium">{t("admin.people.department")}</th>
              <th className="py-1.5 pr-3 font-medium">{t("admin.people.role")}</th>
              <th className="py-1.5 pr-3 font-medium">{t("admin.people.budget")}</th>
              <th className="py-1.5 font-medium">
                <span className="sr-only">{t("admin.people.actions")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <PersonRow key={m.email} m={m} reload={reload} defaultBudget={org.org.user_monthly_credits} pricing={pricing} />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function PersonRow({ m, reload, defaultBudget, pricing }: { m: OrgMember; reload: () => void; defaultBudget: number; pricing: Pricing }) {
  const t = useAmT();
  const [role, setRole] = useState(m.role);
  const [dept, setDept] = useState(m.department);
  const [budget, setBudget] = useState(m.monthly_credits === null ? "" : String(m.monthly_credits));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const changed = role !== m.role || dept !== m.department || budget !== (m.monthly_credits === null ? "" : String(m.monthly_credits));
  const save = () => {
    setBusy(true);
    setMsg(null);
    amApi.adminPutMember(m.email, { role, department: dept, monthly_credits: budget.trim() ? budget.replace(/,/g, "") : null }).then(
      () => {
        setBusy(false);
        reload();
      },
      (err) => {
        setBusy(false);
        setMsg({ text: errorMessage(err), ok: false });
      },
    );
  };
  const remove = () => {
    setBusy(true);
    setMsg(null);
    amApi.adminDeleteMember(m.email).then(
      () => {
        setBusy(false);
        reload();
      },
      (err) => {
        setBusy(false);
        setMsg({ text: errorMessage(err), ok: false });
      },
    );
  };
  return (
    <>
      <tr className="border-b border-border-subtle align-top" data-testid="am-admin-person">
        <td className="py-2 pr-3 text-text-primary">
          {m.email}
          {m.env_admin ? <span className="mt-0.5 block text-[11px] text-text-muted">{t("admin.people.envAdmin")}</span> : null}
          {!m.user_id && !m.env_admin ? <span className="mt-0.5 block text-[11px] text-text-muted">{t("admin.people.notSeen")}</span> : null}
        </td>
        <td className="py-2 pr-3 text-text-secondary">{m.display_name || "—"}</td>
        <td className="py-2 pr-3">
          <input value={dept} onChange={(e) => setDept(e.target.value)} aria-label={t("admin.people.departmentFor", { email: m.email })} className={FIELD} list="am-admin-depts" disabled={busy} />
        </td>
        <td className="py-2 pr-3">
          <select value={role} onChange={(e) => setRole(e.target.value)} aria-label={t("admin.people.roleFor", { email: m.email })} className={FIELD} disabled={busy || m.env_admin}>
            {ROLES.map((r) => (
              <option key={r.id} value={r.id}>
                {t(r.key)}
              </option>
            ))}
          </select>
        </td>
        <td className="py-2 pr-3">
          <input
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            inputMode="numeric"
            placeholder={t("admin.people.budgetDefaultN", { n: formatCredits(defaultBudget) })}
            aria-label={t("admin.people.budgetFor", { email: m.email })}
            className={cls(FIELD, "tabular-nums")}
            disabled={busy}
          />
          <span className="mt-0.5 block text-[11px] text-text-muted">
            {t("admin.budgetBaht", { baht: bahtPerMonth(budget.trim() ? Number(budget.replace(/,/g, "")) || 0 : defaultBudget, pricing) })}
          </span>
        </td>
        <td className="whitespace-nowrap py-2">
          <div className="flex gap-1.5">
            {changed ? (
              <button type="button" onClick={save} disabled={busy} className={BTN_PRIMARY_SMALL}>
                {busy ? <Spin size={12} /> : null}
                <span>{t("common.save")}</span>
              </button>
            ) : null}
            {!m.env_admin ? (
              <button type="button" onClick={remove} disabled={busy} className={BTN_SMALL} aria-label={t("admin.people.removeFor", { email: m.email })}>
                <Trash2 width={13} height={13} aria-hidden />
              </button>
            ) : null}
          </div>
        </td>
      </tr>
      {msg ? (
        <tr>
          <td colSpan={6} className="pb-2">
            <Note msg={msg} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

// ── departments and budgets ──────────────────────────────────────────────

function DepartmentsTab({ pricing }: { pricing: Pricing }) {
  const t = useAmT();
  const { org, error, reload } = useOrg();
  const [add, setAdd] = useState({ name: "", budget: "", contact_name: "", contact_email: "" });
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  if (error && !org) return <ErrorPanel message={errorMessage(error)} onRetry={reload} />;
  if (!org) return <Loading />;
  const depts = org.departments ?? [];
  const put = (name: string, body: { monthly_credits: string | null; contact_name: string; contact_email: string }) =>
    amApi.adminPutDepartment(name, body).then(
      (r) => {
        setMsg({ text: r.resumed ? t("admin.savedResumed", { n: String(r.resumed) }) : t("admin.saved"), ok: true });
        reload();
      },
      (err) => setMsg({ text: errorMessage(err), ok: false }),
    );
  return (
    <div className="flex flex-col gap-4" data-testid="am-admin-departments">
      <Card>
        <h2 className="text-sm font-semibold text-text-primary">{t("admin.dept.title")}</h2>
        <p className="mt-0.5 text-xs text-text-muted">{t("admin.dept.body")}</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (add.name.trim())
              void put(add.name.trim(), { monthly_credits: add.budget.trim() ? add.budget.replace(/,/g, "") : null, contact_name: add.contact_name, contact_email: add.contact_email }).then(() =>
                setAdd({ name: "", budget: "", contact_name: "", contact_email: "" }),
              );
          }}
          className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,1fr)_auto]"
        >
          <input value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} placeholder={t("admin.dept.name")} aria-label={t("admin.dept.name")} className={FIELD} data-testid="am-admin-dept-name" />
          <input value={add.budget} onChange={(e) => setAdd({ ...add, budget: e.target.value })} placeholder={t("admin.dept.noLimit")} aria-label={t("admin.dept.budget")} inputMode="numeric" className={FIELD} />
          <input value={add.contact_name} onChange={(e) => setAdd({ ...add, contact_name: e.target.value })} placeholder={t("admin.dept.contactName")} aria-label={t("admin.dept.contactName")} className={FIELD} />
          <input type="email" value={add.contact_email} onChange={(e) => setAdd({ ...add, contact_email: e.target.value })} placeholder={t("admin.dept.contactEmail")} aria-label={t("admin.dept.contactEmail")} className={FIELD} />
          <button type="submit" disabled={!add.name.trim()} className={BTN_PRIMARY_SMALL} data-testid="am-admin-dept-add">
            {t("admin.dept.add")}
          </button>
        </form>
        {msg ? (
          <div className="mt-2">
            <Note msg={msg} />
          </div>
        ) : null}
        <ul className="mt-4 flex flex-col gap-2" data-testid="am-admin-dept-list">
          {depts.map((d) => (
            <li key={d.name} className="rounded-lg border border-border-subtle px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-text-primary">{d.name}</span>
                <span className="text-xs text-text-muted">{t("admin.dept.members", { n: String(d.members) })}</span>
                <span className="flex-1" />
                <span className="text-xs text-text-secondary">
                  {d.monthly_credits === null
                    ? t("admin.dept.noLimit")
                    : t("admin.dept.limitLine", { credits: formatCredits(d.monthly_credits), baht: bahtPerMonth(d.monthly_credits, pricing) })}
                </span>
                <button
                  type="button"
                  onClick={() =>
                    amApi.adminDeleteDepartment(d.name).then(
                      () => reload(),
                      (err) => setMsg({ text: errorMessage(err), ok: false }),
                    )
                  }
                  className={BTN_SMALL}
                  aria-label={t("admin.dept.removeFor", { name: d.name })}
                >
                  <Trash2 width={13} height={13} aria-hidden />
                </button>
              </div>
              <p className="mt-0.5 text-xs text-text-muted">
                {d.contact_name || d.contact_email ? t("admin.dept.contactLine", { who: [d.contact_name, d.contact_email].filter(Boolean).join(" · ") }) : t("admin.dept.noContact")}
              </p>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

// ── spend ────────────────────────────────────────────────────────────────

function SpendTab({ pricing }: { pricing: Pricing }) {
  const t = useAmT();
  const locale = useAmLocale();
  const [days, setDays] = useState(30);
  const [data, setData] = useState<AdminSpend | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let alive = true;
    setData(null);
    amApi.adminSpend(days).then(
      (r) => alive && setData(r),
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [days]);
  const p = data?.pricing ?? pricing;
  return (
    <div className="flex flex-col gap-4" data-testid="am-admin-spend">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("admin.spend.window")}>
        {[7, 30, 90].map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={days === d}
            onClick={() => setDays(d)}
            className={cls("focus-ring rounded-full border px-3 py-1 text-xs", days === d ? "border-border-strong bg-bg-elevated text-text-primary" : "border-border text-text-secondary")}
          >
            {t("admin.spend.days", { n: String(d) })}
          </button>
        ))}
      </div>
      {error && !data ? <ErrorPanel message={errorMessage(error)} /> : null}
      {!data ? (
        <Loading />
      ) : (
        <>
          <p className="text-sm text-text-primary" data-testid="am-admin-spend-total">
            {t("admin.spend.total", { credits: formatCredits(data.total_credits), baht: formatBaht(creditsToBaht(data.total_credits, p)), n: String(data.days) })}
          </p>
          <Card>
            <h2 className="text-sm font-semibold text-text-primary">{t("admin.spend.byUser")}</h2>
            <SpendTable rows={data.by_user ?? []} kind="user" pricing={p} locale={locale} />
          </Card>
          <Card>
            <h2 className="text-sm font-semibold text-text-primary">{t("admin.spend.byAgent")}</h2>
            <SpendTable rows={data.by_agent ?? []} kind="agent" pricing={p} locale={locale} />
          </Card>
          <p className="text-xs text-text-muted" data-testid="am-admin-spend-samples">
            {t("admin.spend.samples", { credits: formatCredits(data.catalog_samples_credits), baht: formatBaht(creditsToBaht(data.catalog_samples_credits, p)) })}
          </p>
        </>
      )}
    </div>
  );
}

function SpendTable({ rows, kind, pricing, locale }: { rows: SpendRow[]; kind: "user" | "agent"; pricing: Pricing; locale: string }) {
  const t = useAmT();
  if (!rows.length) return <p className="mt-2 text-sm text-text-muted">{t("admin.spend.none")}</p>;
  return (
    <div className="scrollbar-thin mt-2 overflow-x-auto">
      <table className="w-full border-collapse text-sm" data-testid={`am-admin-spend-${kind}`}>
        <thead>
          <tr className="border-b border-border-subtle text-left text-[11px] text-text-muted">
            <th className="py-1.5 pr-3 font-medium">{kind === "user" ? t("admin.spend.person") : t("admin.spend.agent")}</th>
            <th className="py-1.5 pr-3 text-right font-medium">{t("admin.spend.runs")}</th>
            <th className="py-1.5 pr-3 text-right font-medium">{t("admin.spend.okFailWait")}</th>
            <th className="py-1.5 pr-3 text-right font-medium">{t("admin.spend.credits")}</th>
            <th className="py-1.5 pr-3 text-right font-medium">{t("admin.spend.month")}</th>
            <th className="py-1.5 font-medium">{t("admin.spend.lastRun")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-border-subtle align-top" data-testid="am-admin-spend-row">
              <td className="py-1.5 pr-3">
                <span className="text-text-primary">{kind === "user" ? r.name || r.email || r.key.slice(0, 8) : r.name || r.key}</span>
                {kind === "user" ? (
                  <span className="block text-[11px] text-text-muted">{[r.email, r.department].filter(Boolean).join(" · ")}</span>
                ) : (
                  <span className="block text-[11px] text-text-muted">{t("admin.spend.users", { n: String(r.users ?? 0) })}</span>
                )}
              </td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{r.runs}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums text-text-secondary">{`${r.succeeded} / ${r.failed} / ${r.waiting}`}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">
                {formatCredits(r.credits)}
                <span className="block text-[11px] text-text-muted">{`≈ ${formatBaht(creditsToBaht(r.credits, pricing))}`}</span>
              </td>
              <td className="py-1.5 pr-3 text-right tabular-nums">
                {formatCredits(r.month_spent)}
                {r.monthly_limit ? <span className="block text-[11px] text-text-muted">{t("admin.spend.ofLimit", { limit: formatCredits(r.monthly_limit) })}</span> : null}
              </td>
              <td className="py-1.5 text-xs text-text-secondary">{formatDateTime(r.last_run_at, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── the catalog ──────────────────────────────────────────────────────────

const STATUS: Record<string, { key: AmKey; tone: Tone }> = {
  published: { key: "admin.catalog.published", tone: "success" },
  unpublished: { key: "admin.catalog.unpublished", tone: "neutral" },
  disabled: { key: "admin.catalog.disabled", tone: "warning" },
};

function CatalogTab({ admin }: { admin: boolean }) {
  const t = useAmT();
  const [rows, setRows] = useState<AdminCatalogRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    amApi.adminCatalog().then(
      (r) => alive && setRows(r?.agents ?? []),
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [n]);
  if (error && !rows) return <ErrorPanel message={errorMessage(error)} onRetry={() => setN((x) => x + 1)} />;
  if (!rows) return <Loading />;
  return (
    <ul className="flex flex-col gap-3" data-testid="am-admin-catalog">
      {rows.map((r) => (
        <CatalogRow key={r.slug} row={r} admin={admin} reload={() => setN((x) => x + 1)} />
      ))}
    </ul>
  );
}

function CatalogRow({ row, admin, reload }: { row: AdminCatalogRow; admin: boolean; reload: () => void }) {
  const t = useAmT();
  const locale = useAmLocale();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const status = row.status ?? "unpublished";
  const st = STATUS[status] ?? STATUS.unpublished!;
  const act = (body: { status: string; reason?: string }) => {
    setBusy(true);
    setMsg(null);
    amApi.adminPutCatalog(row.slug, body).then(
      (r) => {
        setBusy(false);
        setMsg({ text: r.stopped_runs ? t("admin.catalog.stopped", { n: String(r.stopped_runs) }) : t("admin.saved"), ok: true });
        reload();
      },
      (err) => {
        setBusy(false);
        setMsg({ text: errorMessage(err), ok: false });
      },
    );
  };
  const sample = () => {
    setBusy(true);
    setMsg(null);
    amApi.adminSample(row.slug).then(
      () => {
        setBusy(false);
        setMsg({ text: t("admin.catalog.sampleQueued"), ok: true });
      },
      (err) => {
        setBusy(false);
        setMsg({ text: errorMessage(err), ok: false });
      },
    );
  };
  return (
    <li className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-admin-catalog-row" data-slug={row.slug}>
      <div className="flex flex-wrap items-start gap-3">
        <AgentTile icon={row.manifest?.icon} size={32} quiet={status !== "published"} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/chat/agents/${encodeURIComponent(row.slug)}`} className="text-sm font-medium text-text-primary hover:underline">
              {row.name}
            </Link>
            <Pill tone={st.tone}>{t(st.key)}</Pill>
          </div>
          <p className="mt-0.5 text-xs text-text-muted">
            {[row.slug, t("detail.version", { v: row.version }), t("admin.catalog.installs", { n: String(row.installs) })].join(" · ")}
          </p>
          {row.status_reason ? <p className="mt-0.5 text-xs text-text-secondary">{t("admin.catalog.reasonLine", { reason: row.status_reason })}</p> : null}
          {row.changed_by ? (
            <p className="mt-0.5 text-[11px] text-text-muted">{t("admin.catalog.changed", { who: row.changed_by, when: formatDateTime(row.changed_at, locale) })}</p>
          ) : null}
          <p className="mt-0.5 text-[11px] text-text-muted">
            {row.sample ? t("admin.catalog.sample", { status: t(`progress.title.${row.sample.status}` as AmKey), version: row.sample.version }) : t("admin.catalog.noSample")}
          </p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {status === "unpublished" ? (
          <button type="button" onClick={() => act({ status: "published" })} disabled={busy} className={BTN_PRIMARY_SMALL} data-testid="am-admin-publish">
            {t("admin.catalog.publish")}
          </button>
        ) : null}
        {status === "published" ? (
          <button type="button" onClick={() => act({ status: "unpublished" })} disabled={busy} className={BTN_SMALL}>
            {t("admin.catalog.unpublish")}
          </button>
        ) : null}
        {admin && status === "disabled" ? (
          <button type="button" onClick={() => act({ status: "published" })} disabled={busy} className={BTN_PRIMARY_SMALL} data-testid="am-admin-enable">
            {t("admin.catalog.enable")}
          </button>
        ) : null}
        <button type="button" onClick={sample} disabled={busy} className={BTN_SMALL}>
          {t("admin.catalog.remakeSample")}
        </button>
        {busy ? <Spin size={12} /> : null}
      </div>
      {admin && status !== "disabled" ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={t("admin.catalog.reasonPlaceholder")}
            aria-label={t("admin.catalog.reason")}
            className={cls(FIELD, "min-w-[14rem] flex-1")}
            data-testid="am-admin-disable-reason"
          />
          <button type="button" onClick={() => act({ status: "disabled", reason })} disabled={busy} className={BTN_DANGER_OUTLINE} data-testid="am-admin-disable">
            {t("admin.catalog.disable")}
          </button>
        </div>
      ) : null}
      {admin && status !== "disabled" ? <p className="mt-1 text-[11px] text-text-muted">{t("admin.catalog.disableHint")}</p> : null}
      {msg ? (
        <div className="mt-2">
          <Note msg={msg} />
        </div>
      ) : null}
    </li>
  );
}

// ── problem reports ──────────────────────────────────────────────────────

function ProblemsTab() {
  const t = useAmT();
  const [rows, setRows] = useState<ProblemReport[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    amApi.adminProblems("all").then(
      (r) => alive && setRows(r?.problems ?? []),
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [n]);
  if (error && !rows) return <ErrorPanel message={errorMessage(error)} onRetry={() => setN((x) => x + 1)} />;
  if (!rows) return <Loading />;
  if (!rows.length) return <p className="py-8 text-center text-sm text-text-muted">{t("admin.problems.none")}</p>;
  const sorted = [...rows].sort((a, b) => (a.status === b.status ? Date.parse(b.created_at) - Date.parse(a.created_at) : a.status === "open" ? -1 : 1));
  return (
    <ul className="flex flex-col gap-3" data-testid="am-admin-problems">
      {sorted.map((p) => (
        <ProblemRow key={p.id} p={p} reload={() => setN((x) => x + 1)} />
      ))}
    </ul>
  );
}

function ProblemRow({ p, reload }: { p: ProblemReport; reload: () => void }) {
  const t = useAmT();
  const locale = useAmLocale();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const open = p.status === "open";
  return (
    <li className={cls("rounded-xl border px-4 py-3", open ? "border-status-warning/40 bg-bg-surface" : "border-border-subtle bg-bg-base")} data-testid="am-admin-problem">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-text-primary">{`#${p.ref}`}</span>
        <Pill tone={open ? "warning" : "neutral"}>{open ? t("admin.problems.open") : t("admin.problems.closed")}</Pill>
        <span className="text-xs text-text-muted">{`${p.email} · ${formatDateTime(p.created_at, locale)}`}</span>
      </div>
      <p className="mt-1.5 whitespace-pre-wrap text-sm text-text-primary">{p.text}</p>
      <p className="mt-1 text-xs text-text-secondary">
        {[p.slug && `${p.slug} ${p.version}`, p.run_status && t("admin.problems.runStatus", { status: p.run_status, reason: p.run_reason || "—" }), p.page]
          .filter(Boolean)
          .join(" · ")}
      </p>
      {p.step ? (
        <p className="mt-0.5 text-xs text-text-secondary">
          {t(p.run_status === "succeeded" ? "admin.problems.lastStep" : "admin.problems.step", { step: p.step })}
          {p.step_error ? <code className="ml-1 break-all rounded bg-bg-elevated px-1 py-0.5 text-[11px]">{p.step_error}</code> : null}
        </p>
      ) : null}
      {p.run_id ? (
        <Link href={runHref(p.run_id)} className="mt-1 inline-block text-xs text-text-secondary underline underline-offset-2 hover:text-text-primary">
          {t("admin.problems.openRun", { id: p.run_id.slice(0, 8) })}
        </Link>
      ) : null}
      {open ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("admin.problems.notePlaceholder")} aria-label={t("admin.problems.note")} className={cls(FIELD, "min-w-[14rem] flex-1")} />
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setMsg(null);
              amApi.adminCloseProblem(p.id, note).then(
                () => {
                  setBusy(false);
                  reload();
                },
                (err) => {
                  setBusy(false);
                  setMsg(errorMessage(err));
                },
              );
            }}
            className={BTN_SMALL}
            data-testid="am-admin-problem-close"
          >
            {busy ? <Spin size={12} /> : null}
            <span>{t("admin.problems.close")}</span>
          </button>
        </div>
      ) : p.admin_note ? (
        <p className="mt-1 text-xs text-text-muted">{t("admin.problems.closedNote", { who: p.closed_by, note: p.admin_note })}</p>
      ) : null}
      {msg ? <p className="mt-1 text-xs text-status-danger">{msg}</p> : null}
    </li>
  );
}

function Loading() {
  const t = useAmT();
  return (
    <div className="flex items-center gap-2 text-sm text-text-secondary" role="status">
      <Spin size={14} />
      <span>{t("common.loading")}</span>
    </div>
  );
}

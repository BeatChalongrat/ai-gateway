"use client";

// การเข้าถึง — who may use Agent Market: the org-wide switch (off / the
// list / everyone), the organisation's name, email domains, the default
// monthly budget per person and the contact people see; then the list of
// people with their department, role and budget. A person added from the
// server's settings (env_admin) keeps their role and cannot be removed here.

import Link from "next/link";
import { useEffect, useState } from "react";

import { Icon } from "../../../components/ui/Icon";
import { useT, type DictKey } from "../../../lib/i18n";
import {
  amAdmin,
  amErrorMessage,
  type AdminOrg,
  type OrgMember,
  type OrgSettings,
  type Pricing,
} from "../../../lib/agentMarketApi";
import {
  DEFAULT_PRICING,
  Field,
  LoadError,
  Loading,
  Note,
  bahtOf,
  formatCredits,
  h2,
  linkStyle,
  muted,
  plainNumber,
  secondary,
  useOrg,
  withLink,
  type Msg,
} from "./shared";

const ACCESS: { id: "off" | "list" | "everyone"; key: DictKey; body: DictKey }[] = [
  { id: "off", key: "am.access.off", body: "am.access.offBody" },
  { id: "list", key: "am.access.list", body: "am.access.listBody" },
  { id: "everyone", key: "am.access.everyone", body: "am.access.everyoneBody" },
];

export const ROLES: { id: string; key: DictKey }[] = [
  { id: "member", key: "am.role.member" },
  { id: "publisher", key: "am.role.publisher" },
  { id: "admin", key: "am.role.admin" },
];

interface OrgForm {
  name: string;
  access: string;
  domains: string;
  budget: string;
  contact_name: string;
  contact_email: string;
}

const formOf = (o: OrgSettings): OrgForm => ({
  name: o.name ?? "",
  access: o.access,
  domains: (o.domains ?? []).join(", "),
  budget: String(o.user_monthly_credits ?? ""),
  contact_name: o.contact_name ?? "",
  contact_email: o.contact_email ?? "",
});

export default function AccessSection() {
  const t = useT();
  const { org, error, reload } = useOrg();
  const [form, setForm] = useState<OrgForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    if (org && !form) setForm(formOf(org.org));
  }, [org, form]);

  if (error && !org) return <LoadError error={error} onRetry={reload} />;
  if (!org || !form) return <Loading />;
  const pricing = org.pricing ?? DEFAULT_PRICING;

  const save = () => {
    setBusy(true);
    setMsg(null);
    amAdmin
      .putOrg({
        name: form.name.trim(),
        access: form.access,
        domains: form.domains.split(/[\s,]+/).filter(Boolean),
        user_monthly_credits: plainNumber(form.budget),
        contact_name: form.contact_name.trim(),
        contact_email: form.contact_email.trim(),
      })
      .then(
        (r) => {
          setBusy(false);
          if (r?.org) setForm(formOf(r.org));
          setMsg({ text: r?.resumed ? t("am.savedResumed", { n: r.resumed }) : t("am.saved"), ok: true });
          reload();
        },
        (err) => {
          setBusy(false);
          setMsg({ text: amErrorMessage(err, t("am.failed")), ok: false });
        },
      );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }} data-testid="am-access">
      <p style={secondary} data-testid="am-chat-users-line">
        {withLink(
          t("am.access.usersLine"),
          <Link href="/chat-users" style={linkStyle}>
            {t("am.access.usersLink")}
          </Link>,
        )}
      </p>

      <section className="sran-card" aria-labelledby="am-access-title">
        <h2 id="am-access-title" style={h2}>
          {t("am.access.title")}
        </h2>
        <fieldset
          style={{ border: "none", padding: 0, margin: "0.9rem 0 0", display: "grid", gap: "0.6rem", gridTemplateColumns: "repeat(auto-fit, minmax(13rem, 1fr))" }}
        >
          <legend className="sr-only">{t("am.access.title")}</legend>
          {ACCESS.map((a) => {
            const on = form.access === a.id;
            return (
              <label
                key={a.id}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.25rem",
                  cursor: "pointer",
                  borderRadius: "var(--radius-lg)",
                  padding: "0.6rem 0.8rem",
                  border: `1px solid ${on ? "var(--accent-primary)" : "var(--border-default)"}`,
                  background: on ? "var(--bg-elevated)" : "transparent",
                }}
              >
                <span style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.875rem", fontWeight: 600, color: "var(--text-primary)" }}>
                  <input
                    type="radio"
                    name="am-access"
                    checked={on}
                    onChange={() => setForm({ ...form, access: a.id })}
                    data-testid={`am-access-${a.id}`}
                  />
                  {t(a.key)}
                </span>
                <span style={muted}>{t(a.body)}</span>
              </label>
            );
          })}
        </fieldset>

        <div style={{ marginTop: "1rem", display: "grid", gap: "0.9rem", gridTemplateColumns: "repeat(auto-fit, minmax(16rem, 1fr))" }}>
          <Field label={t("am.access.orgName")}>
            <input className="sran-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} data-testid="am-org-name" />
          </Field>
          <Field label={t("am.access.domains")} hint={t("am.access.domainsHint")}>
            <input
              className="sran-input"
              value={form.domains}
              onChange={(e) => setForm({ ...form, domains: e.target.value })}
              placeholder={t("am.access.domainsPlaceholder")}
              data-testid="am-org-domains"
            />
          </Field>
          <Field label={t("am.access.userBudget")} hint={t("am.budgetBaht", { baht: bahtOf(Number(plainNumber(form.budget)) || 0, pricing) })}>
            <input
              className="sran-input"
              value={form.budget}
              onChange={(e) => setForm({ ...form, budget: e.target.value })}
              inputMode="numeric"
              style={{ fontVariantNumeric: "tabular-nums" }}
              data-testid="am-org-budget"
            />
          </Field>
          <Field label={t("am.access.contactName")}>
            <input
              className="sran-input"
              value={form.contact_name}
              onChange={(e) => setForm({ ...form, contact_name: e.target.value })}
              data-testid="am-org-contact-name"
            />
          </Field>
          <Field label={t("am.access.contactEmail")} hint={t("am.access.contactHint")}>
            <input
              type="email"
              className="sran-input"
              value={form.contact_email}
              onChange={(e) => setForm({ ...form, contact_email: e.target.value })}
              data-testid="am-org-contact-email"
            />
          </Field>
        </div>
        <div style={{ marginTop: "1rem", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.75rem" }}>
          <button type="button" className="sran-btn sran-btn-primary sran-btn-sm" onClick={save} disabled={busy} data-testid="am-org-save">
            {busy ? t("action.saving") : t("action.save")}
          </button>
          <Note msg={msg} />
        </div>
      </section>

      <People org={org} reload={reload} pricing={pricing} />
    </div>
  );
}

// ── the people ───────────────────────────────────────────────────────────

const EMPTY_ADD = { email: "", display_name: "", department: "", role: "member", budget: "" };

function People({ org, reload, pricing }: { org: AdminOrg; reload: () => void; pricing: Pricing }) {
  const t = useT();
  const [add, setAdd] = useState(EMPTY_ADD);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const members = org.members ?? [];
  const depts = org.departments ?? [];

  const addOne = () => {
    setBusy(true);
    setMsg(null);
    amAdmin
      .putMember(add.email.trim(), {
        display_name: add.display_name.trim(),
        department: add.department.trim(),
        role: add.role,
        monthly_credits: add.budget.trim() ? plainNumber(add.budget) : null,
      })
      .then(
        (r) => {
          setBusy(false);
          setAdd(EMPTY_ADD);
          setMsg({ text: r?.resumed ? t("am.savedResumed", { n: r.resumed }) : t("am.saved"), ok: true });
          reload();
        },
        (err) => {
          setBusy(false);
          setMsg({ text: amErrorMessage(err, t("am.failed")), ok: false });
        },
      );
  };

  return (
    <section className="sran-card" aria-labelledby="am-people-title">
      <h2 id="am-people-title" style={h2}>
        {t("am.people.title", { n: members.length })}
      </h2>
      <p style={{ ...muted, marginTop: "0.2rem" }}>{org.org.access === "list" ? t("am.people.bodyList") : t("am.people.bodyOther")}</p>
      <p style={{ ...muted, marginTop: "0.2rem" }} data-testid="am-role-hint">
        {t("am.role.hint")}
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy && add.email.trim()) addOne();
        }}
        style={{ marginTop: "0.9rem", display: "grid", gap: "0.5rem", gridTemplateColumns: "repeat(auto-fit, minmax(9.5rem, 1fr))", alignItems: "center" }}
        data-testid="am-add-person"
      >
        <input
          type="email"
          className="sran-input"
          value={add.email}
          onChange={(e) => setAdd({ ...add, email: e.target.value })}
          placeholder={t("am.people.email")}
          aria-label={t("am.people.email")}
          data-testid="am-add-email"
        />
        <input
          className="sran-input"
          value={add.display_name}
          onChange={(e) => setAdd({ ...add, display_name: e.target.value })}
          placeholder={t("am.people.name")}
          aria-label={t("am.people.name")}
        />
        <input
          className="sran-input"
          value={add.department}
          onChange={(e) => setAdd({ ...add, department: e.target.value })}
          placeholder={t("am.people.department")}
          aria-label={t("am.people.department")}
          list="am-depts"
        />
        <select className="sran-input" value={add.role} onChange={(e) => setAdd({ ...add, role: e.target.value })} aria-label={t("am.people.role")}>
          {ROLES.map((r) => (
            <option key={r.id} value={r.id}>
              {t(r.key)}
            </option>
          ))}
        </select>
        <input
          className="sran-input"
          value={add.budget}
          onChange={(e) => setAdd({ ...add, budget: e.target.value })}
          placeholder={t("am.people.budgetDefault")}
          aria-label={t("am.people.budget")}
          inputMode="numeric"
        />
        <button type="submit" className="sran-btn sran-btn-primary sran-btn-sm" disabled={busy || !add.email.trim()} data-testid="am-add-save">
          <Icon name="plus" size={13} />
          {t("am.people.add")}
        </button>
      </form>
      <datalist id="am-depts">
        {depts.map((d) => (
          <option key={d.name} value={d.name} />
        ))}
      </datalist>
      {msg ? (
        <div style={{ marginTop: "0.5rem" }}>
          <Note msg={msg} />
        </div>
      ) : null}

      {members.length ? (
        <div style={{ overflowX: "auto", marginTop: "1rem" }}>
          <table className="sran-table" style={{ minWidth: 820 }} data-testid="am-people">
            <thead>
              <tr>
                <th>{t("am.people.email")}</th>
                <th>{t("am.people.name")}</th>
                <th>{t("am.people.department")}</th>
                <th>{t("am.people.role")}</th>
                <th>{t("am.people.budget")}</th>
                <th>
                  <span className="sr-only">{t("am.people.actions")}</span>
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
      ) : (
        <p style={{ ...secondary, marginTop: "1rem" }}>{t("am.people.none")}</p>
      )}
    </section>
  );
}

const budgetText = (m: OrgMember) => (m.monthly_credits === null || m.monthly_credits === undefined ? "" : String(m.monthly_credits));

function PersonRow({ m, reload, defaultBudget, pricing }: { m: OrgMember; reload: () => void; defaultBudget: number; pricing: Pricing }) {
  const t = useT();
  const [role, setRole] = useState(m.role);
  const [dept, setDept] = useState(m.department ?? "");
  const [budget, setBudget] = useState(budgetText(m));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const changed = role !== m.role || dept !== (m.department ?? "") || budget !== budgetText(m);

  const run = (p: Promise<unknown>) => {
    setBusy(true);
    setMsg(null);
    p.then(
      () => {
        setBusy(false);
        reload();
      },
      (err) => {
        setBusy(false);
        setMsg({ text: amErrorMessage(err, t("am.failed")), ok: false });
      },
    );
  };
  const save = () =>
    run(
      amAdmin.putMember(m.email, {
        // an admin from the server's settings keeps the role it has there
        ...(m.env_admin ? {} : { role }),
        department: dept.trim(),
        monthly_credits: budget.trim() ? plainNumber(budget) : null,
      }),
    );
  const remove = () => run(amAdmin.deleteMember(m.email));

  return (
    <>
      <tr data-testid="am-person" style={{ verticalAlign: "top" }}>
        <td>
          <div style={{ fontWeight: 500 }}>{m.email}</div>
          {m.env_admin ? <div style={muted}>{t("am.people.envAdmin")}</div> : null}
          {!m.user_id && !m.env_admin ? <div style={muted}>{t("am.people.notSeen")}</div> : null}
        </td>
        <td style={{ color: "var(--text-secondary)" }}>{m.display_name || "—"}</td>
        <td>
          <input
            className="sran-input"
            value={dept}
            onChange={(e) => setDept(e.target.value)}
            aria-label={t("am.people.departmentFor", { email: m.email })}
            list="am-depts"
            disabled={busy}
          />
        </td>
        <td>
          <select
            className="sran-input"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            aria-label={t("am.people.roleFor", { email: m.email })}
            disabled={busy || !!m.env_admin}
          >
            {ROLES.map((r) => (
              <option key={r.id} value={r.id}>
                {t(r.key)}
              </option>
            ))}
          </select>
        </td>
        <td>
          <input
            className="sran-input"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            inputMode="numeric"
            placeholder={t("am.people.budgetDefaultN", { n: formatCredits(defaultBudget) })}
            aria-label={t("am.people.budgetFor", { email: m.email })}
            style={{ fontVariantNumeric: "tabular-nums" }}
            disabled={busy}
          />
          <div style={{ ...muted, marginTop: "0.2rem" }}>
            {t("am.budgetBaht", { baht: bahtOf(budget.trim() ? Number(plainNumber(budget)) || 0 : defaultBudget, pricing) })}
          </div>
        </td>
        <td style={{ whiteSpace: "nowrap" }}>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <button
              type="button"
              className="sran-btn sran-btn-primary sran-btn-sm"
              onClick={save}
              disabled={busy || !changed}
              aria-label={t("am.people.saveFor", { email: m.email })}
            >
              {t("action.save")}
            </button>
            {!m.env_admin ? (
              <button
                type="button"
                className="sran-btn sran-btn-secondary sran-btn-sm"
                onClick={remove}
                disabled={busy}
                aria-label={t("am.people.removeFor", { email: m.email })}
                title={t("am.people.removeFor", { email: m.email })}
                data-testid="am-person-remove"
              >
                <Icon name="trash" size={13} />
              </button>
            ) : null}
          </div>
        </td>
      </tr>
      {msg ? (
        <tr>
          <td colSpan={6}>
            <Note msg={msg} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

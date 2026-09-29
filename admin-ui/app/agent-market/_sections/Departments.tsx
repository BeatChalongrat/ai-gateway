"use client";

// แผนกและงบ — each department's monthly budget (blank = no limit) and its
// contact person. Saving a name that exists edits it (the server upserts);
// "Edit" puts a department's values back in the form.

import { useState } from "react";

import { Icon } from "../../../components/ui/Icon";
import { useT } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type OrgDepartment } from "../../../lib/agentMarketApi";
import { DEFAULT_PRICING, LoadError, Loading, Note, bahtOf, formatCredits, h2, muted, plainNumber, secondary, useOrg, type Msg } from "./shared";

const EMPTY = { name: "", budget: "", contact_name: "", contact_email: "" };

export default function DepartmentsSection() {
  const t = useT();
  const { org, error, reload } = useOrg();
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  if (error && !org) return <LoadError error={error} onRetry={reload} />;
  if (!org) return <Loading />;
  const depts = org.departments ?? [];
  const pricing = org.pricing ?? DEFAULT_PRICING;

  const save = () => {
    const name = form.name.trim();
    if (!name) return;
    setBusy(true);
    setMsg(null);
    amAdmin
      .putDepartment(name, {
        monthly_credits: form.budget.trim() ? plainNumber(form.budget) : null,
        contact_name: form.contact_name.trim(),
        contact_email: form.contact_email.trim(),
      })
      .then(
        (r) => {
          setBusy(false);
          setForm(EMPTY);
          setEditing(null);
          setMsg({ text: r?.resumed ? t("am.savedResumed", { n: r.resumed }) : t("am.saved"), ok: true });
          reload();
        },
        (err) => {
          setBusy(false);
          setMsg({ text: amErrorMessage(err, t("am.failed")), ok: false });
        },
      );
  };

  const edit = (d: OrgDepartment) => {
    setEditing(d.name);
    setMsg(null);
    setForm({
      name: d.name,
      budget: d.monthly_credits === null || d.monthly_credits === undefined ? "" : String(d.monthly_credits),
      contact_name: d.contact_name ?? "",
      contact_email: d.contact_email ?? "",
    });
  };

  const remove = (d: OrgDepartment) => {
    setMsg(null);
    amAdmin.deleteDepartment(d.name).then(
      (r) => {
        if (editing === d.name) {
          setEditing(null);
          setForm(EMPTY);
        }
        setMsg({ text: r?.resumed ? t("am.savedResumed", { n: r.resumed }) : t("am.saved"), ok: true });
        reload();
      },
      (err) => setMsg({ text: amErrorMessage(err, t("am.failed")), ok: false }),
    );
  };

  return (
    <section className="sran-card" aria-labelledby="am-dept-title" data-testid="am-departments">
      <h2 id="am-dept-title" style={h2}>
        {t("am.dept.title")}
      </h2>
      <p style={{ ...muted, marginTop: "0.2rem" }}>{t("am.dept.body")}</p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) save();
        }}
        style={{ marginTop: "0.9rem", display: "grid", gap: "0.5rem", gridTemplateColumns: "repeat(auto-fit, minmax(10rem, 1fr))", alignItems: "center" }}
      >
        <input
          className="sran-input"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder={t("am.dept.name")}
          aria-label={t("am.dept.name")}
          data-testid="am-dept-name"
        />
        <input
          className="sran-input"
          value={form.budget}
          onChange={(e) => setForm({ ...form, budget: e.target.value })}
          placeholder={t("am.dept.noLimit")}
          aria-label={t("am.dept.budget")}
          inputMode="numeric"
          data-testid="am-dept-budget"
        />
        <input
          className="sran-input"
          value={form.contact_name}
          onChange={(e) => setForm({ ...form, contact_name: e.target.value })}
          placeholder={t("am.dept.contactName")}
          aria-label={t("am.dept.contactName")}
        />
        <input
          type="email"
          className="sran-input"
          value={form.contact_email}
          onChange={(e) => setForm({ ...form, contact_email: e.target.value })}
          placeholder={t("am.dept.contactEmail")}
          aria-label={t("am.dept.contactEmail")}
        />
        <div style={{ display: "flex", gap: "0.4rem" }}>
          <button type="submit" className="sran-btn sran-btn-primary sran-btn-sm" disabled={busy || !form.name.trim()} data-testid="am-dept-save">
            {t("am.dept.add")}
          </button>
          {editing ? (
            <button
              type="button"
              className="sran-btn sran-btn-secondary sran-btn-sm"
              onClick={() => {
                setEditing(null);
                setForm(EMPTY);
              }}
            >
              {t("action.cancel")}
            </button>
          ) : null}
        </div>
      </form>
      {form.budget.trim() ? (
        <p style={{ ...muted, marginTop: "0.3rem" }}>{t("am.budgetBaht", { baht: bahtOf(Number(plainNumber(form.budget)) || 0, pricing) })}</p>
      ) : null}
      {editing ? <p style={{ ...muted, marginTop: "0.3rem" }}>{t("am.dept.editing", { name: editing })}</p> : null}
      {msg ? (
        <div style={{ marginTop: "0.5rem" }}>
          <Note msg={msg} />
        </div>
      ) : null}

      {depts.length ? (
        <ul style={{ listStyle: "none", padding: 0, margin: "1rem 0 0", display: "flex", flexDirection: "column", gap: "0.5rem" }} data-testid="am-dept-list">
          {depts.map((d) => (
            <li
              key={d.name}
              data-testid="am-dept"
              style={{ border: "1px solid var(--border-default)", borderRadius: "var(--radius-md)", padding: "0.6rem 0.8rem" }}
            >
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem" }}>
                <span style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--text-primary)" }}>{d.name}</span>
                <span style={muted}>{t("am.dept.members", { n: d.members ?? 0 })}</span>
                <span style={{ flex: 1 }} />
                <span style={secondary}>
                  {d.monthly_credits === null || d.monthly_credits === undefined
                    ? t("am.dept.noLimit")
                    : t("am.dept.limitLine", { credits: formatCredits(d.monthly_credits), baht: bahtOf(d.monthly_credits, pricing) })}
                </span>
                <button
                  type="button"
                  className="sran-btn sran-btn-secondary sran-btn-sm"
                  onClick={() => edit(d)}
                  aria-label={t("am.dept.editFor", { name: d.name })}
                  title={t("am.dept.editFor", { name: d.name })}
                >
                  <Icon name="edit" size={13} />
                </button>
                <button
                  type="button"
                  className="sran-btn sran-btn-secondary sran-btn-sm"
                  onClick={() => remove(d)}
                  aria-label={t("am.dept.removeFor", { name: d.name })}
                  title={t("am.dept.removeFor", { name: d.name })}
                >
                  <Icon name="trash" size={13} />
                </button>
              </div>
              <p style={{ ...muted, marginTop: "0.2rem" }}>
                {d.contact_name || d.contact_email
                  ? t("am.dept.contactLine", { who: [d.contact_name, d.contact_email].filter(Boolean).join(" · ") })
                  : t("am.dept.noContact")}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p style={{ ...secondary, marginTop: "1rem" }}>{t("am.dept.none")}</p>
      )}
    </section>
  );
}

"use client";

// AM9 — admin-ui accounts and their roles. Granting a role that matters
// (agent_publisher above all) used to be possible only by SQL; here an admin
// lists the accounts, changes a role, deactivates or reactivates an account,
// sets a new password, and adds an account. The backend (/admin/accounts)
// allows this to admins only, records every change in the audit log, refuses
// lowering your own role or deactivating yourself, keeps the last active
// admin, and ends a changed account's open sessions.

import { useCallback, useEffect, useState } from "react";

import PageHeader from "../../components/ui/PageHeader";
import { ApiError, api } from "../../lib/api";
import { useI18n, type DictKey } from "../../lib/i18n";

interface Account {
  id: string;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

interface Listing {
  accounts: Account[];
  roles: string[];
  me: string | null;
}

type Msg = { text: string; ok: boolean } | null;

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const m = err.body?.error?.message ?? err.body?.message;
    if (typeof m === "string" && m) return m;
  }
  return fallback;
}

export default function AccountsPage() {
  const { t } = useI18n();
  const [data, setData] = useState<Listing | null>(null);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("agent_publisher");
  const [password, setPassword] = useState("");
  // the account whose new password is being typed (a masked field on its row, never a prompt)
  const [pwFor, setPwFor] = useState<string | null>(null);
  const [pw, setPw] = useState("");

  const load = useCallback(() => {
    api.get("/admin/accounts").then(
      (r: Listing) => {
        setData(r);
        setDenied(false);
      },
      (err) => {
        if (err instanceof ApiError && err.status === 403) setDenied(true);
        else setMsg({ text: errorText(err, t("am.failed")), ok: false });
      },
    );
  }, [t]);
  useEffect(load, [load]);

  const roleLabel = (r: string) => t(`acct.role.${r}` as DictKey) || r;

  const change = (a: Account, body: { role?: string; is_active?: boolean }) => {
    setBusy(true);
    setMsg(null);
    api.patch(`/admin/accounts/${encodeURIComponent(a.id)}`, body).then(
      () => {
        setBusy(false);
        setMsg({ text: t("acct.saved", { email: a.email }), ok: true });
        load();
      },
      (err) => {
        setBusy(false);
        setMsg({ text: errorText(err, t("am.failed")), ok: false });
        load();
      },
    );
  };

  const newPassword = (a: Account) => {
    if (pw.length < 12) return;
    setBusy(true);
    setMsg(null);
    api.post(`/admin/accounts/${encodeURIComponent(a.id)}/password`, { password: pw }).then(
      () => {
        setBusy(false);
        setPwFor(null);
        setPw("");
        setMsg({ text: t("acct.passwordSaved", { email: a.email }), ok: true });
      },
      (err) => {
        setBusy(false);
        setMsg({ text: errorText(err, t("am.failed")), ok: false });
      },
    );
  };

  const create = () => {
    setBusy(true);
    setMsg(null);
    api.post("/admin/accounts", { email, role, password }).then(
      (r: { account: Account }) => {
        setBusy(false);
        setEmail("");
        setPassword("");
        setMsg({ text: t("acct.created", { email: r.account.email }), ok: true });
        load();
      },
      (err) => {
        setBusy(false);
        setMsg({ text: errorText(err, t("am.failed")), ok: false });
      },
    );
  };

  return (
    <div>
      <PageHeader title={t("acct.title")} subtitle={t("acct.subtitle")} />
      {denied ? (
        <div className="sran-card" data-testid="acct-denied">
          {t("acct.denied")}
        </div>
      ) : !data ? null : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
          <section className="sran-card" data-testid="acct-list">
            <table className="sran-table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>{t("acct.email")}</th>
                  <th style={{ textAlign: "left" }}>{t("acct.role")}</th>
                  <th style={{ textAlign: "left" }}>{t("acct.status")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.accounts.map((a) => {
                  const mine = a.id === data.me;
                  return (
                    <tr key={a.id} data-testid={`acct-row-${a.email}`}>
                      <td>
                        {a.email}
                        {mine ? <span style={{ color: "var(--text-muted)" }}> · {t("acct.you")}</span> : null}
                      </td>
                      <td>
                        <select
                          className="sran-input"
                          value={a.role}
                          disabled={busy || mine}
                          onChange={(e) => change(a, { role: e.target.value })}
                          aria-label={`${t("acct.role")} ${a.email}`}
                          data-testid={`acct-role-${a.email}`}
                        >
                          {(data.roles.includes(a.role) ? data.roles : [a.role, ...data.roles]).map((r) => (
                            <option key={r} value={r}>
                              {roleLabel(r)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>{a.is_active ? t("acct.active") : t("acct.inactive")}</td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {mine ? null : (
                          <button type="button" className="sran-btn sran-btn-sm" disabled={busy}
                            onClick={() => change(a, { is_active: !a.is_active })} data-testid={`acct-active-${a.email}`}>
                            {a.is_active ? t("acct.deactivate") : t("acct.activate")}
                          </button>
                        )}{" "}
                        {pwFor === a.id ? (
                          <span style={{ display: "inline-flex", gap: "0.4rem", alignItems: "center" }}>
                            <input className="sran-input" type="password" autoComplete="new-password" value={pw}
                              aria-label={t("acct.passwordPrompt", { email: a.email })} placeholder={t("acct.password")}
                              onChange={(e) => setPw(e.target.value)} data-testid={`acct-pw-${a.email}`} />
                            <button type="button" className="sran-btn sran-btn-primary sran-btn-sm" disabled={busy || pw.length < 12}
                              onClick={() => newPassword(a)} data-testid={`acct-pw-save-${a.email}`}>
                              {t("action.save")}
                            </button>
                          </span>
                        ) : (
                          <button type="button" className="sran-btn sran-btn-sm" disabled={busy}
                            onClick={() => { setPwFor(a.id); setPw(""); }}>
                            {t("acct.setPassword")}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>

          <section className="sran-card" data-testid="acct-new">
            <h2 style={{ fontSize: "1rem", fontWeight: 600 }}>{t("acct.newTitle")}</h2>
            <div style={{ marginTop: "0.8rem", display: "grid", gap: "0.8rem", gridTemplateColumns: "repeat(auto-fit, minmax(14rem, 1fr))" }}>
              <label>
                <span style={{ display: "block", fontSize: "0.8rem" }}>{t("acct.email")}</span>
                <input className="sran-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} data-testid="acct-new-email" />
              </label>
              <label>
                <span style={{ display: "block", fontSize: "0.8rem" }}>{t("acct.role")}</span>
                <select className="sran-input" value={role} onChange={(e) => setRole(e.target.value)} data-testid="acct-new-role">
                  {data.roles.map((r) => (
                    <option key={r} value={r}>
                      {roleLabel(r)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span style={{ display: "block", fontSize: "0.8rem" }}>{t("acct.password")}</span>
                <input className="sran-input" type="password" autoComplete="new-password" value={password}
                  onChange={(e) => setPassword(e.target.value)} data-testid="acct-new-password" />
              </label>
            </div>
            <button type="button" className="sran-btn sran-btn-primary sran-btn-sm" style={{ marginTop: "0.8rem" }}
              disabled={busy || !email || password.length < 12} onClick={create} data-testid="acct-create">
              {t("acct.create")}
            </button>
          </section>
        </div>
      )}
      {msg ? (
        <p role="status" style={{ marginTop: "0.8rem", color: msg.ok ? "var(--status-success)" : "var(--status-error)" }} data-testid="acct-msg">
          {msg.text}
        </p>
      ) : null}
    </div>
  );
}

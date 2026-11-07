"use client";

// อีเมล (AM7 phase 5) — the mail server Agent Market sends through: host,
// port, TLS mode, username and sender. The password never reaches this
// page: it lives in the server's .env, and the page only says whether it is
// set and under which key. There is no password input, on purpose.

import { useEffect, useState } from "react";

import { useI18n, type DictKey } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type MailSettings, type MailState } from "../../../lib/agentMarketApi";
import { Field, LoadError, Loading, Note, formatDateTime, h2, muted, secondary, warnBand, type Msg } from "./shared";

const TLS: { id: string; key: DictKey }[] = [
  { id: "starttls", key: "am.mail.tls.starttls" },
  { id: "tls", key: "am.mail.tls.tls" },
  { id: "none", key: "am.mail.tls.none" },
];

type Form = { host: string; port: string; tls_mode: string; username: string; sender: string };

const formOf = (s: MailSettings): Form => ({
  host: s.host ?? "",
  port: s.port ? String(s.port) : "",
  tls_mode: s.tls_mode || "starttls",
  username: s.username ?? "",
  sender: s.sender ?? "",
});

export default function MailSection({ defaultTo }: { defaultTo: string }) {
  const { t, locale } = useI18n();
  const [state, setState] = useState<MailState | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [to, setTo] = useState(defaultTo);
  const [testing, setTesting] = useState(false);
  const [testMsg, setTestMsg] = useState<Msg>(null);

  useEffect(() => {
    let alive = true;
    amAdmin.mail().then(
      (r) => {
        if (!alive) return;
        setState(r);
        setForm(formOf(r.settings));
        setError(null);
      },
      (err) => {
        if (alive) setError(err);
      },
    );
    return () => {
      alive = false;
    };
  }, [n]);

  if (error && !state) return <LoadError error={error} onRetry={() => setN((x) => x + 1)} />;
  if (!state || !form) return <Loading />;

  const save = () => {
    setBusy(true);
    setMsg(null);
    amAdmin
      .putMail({
        host: form.host.trim(),
        port: Number(form.port) || 0,
        tls_mode: form.tls_mode,
        username: form.username.trim(),
        sender: form.sender.trim(),
      })
      .then(
        (r) => {
          setBusy(false);
          if (r?.settings) {
            setState(r);
            setForm(formOf(r.settings));
          }
          setMsg({ text: t("am.saved"), ok: true });
        },
        (err) => {
          setBusy(false);
          setMsg({ text: amErrorMessage(err, t("am.failed"), t), ok: false });
        },
      );
  };

  const test = () => {
    const addr = to.trim();
    if (!addr) return;
    setTesting(true);
    setTestMsg(null);
    amAdmin.testMail(addr).then(
      (r) => {
        setTesting(false);
        setTestMsg({ text: t("am.mail.testSent", { to: r?.to || addr }), ok: true });
      },
      (err) => {
        setTesting(false);
        setTestMsg({ text: amErrorMessage(err, t("am.failed"), t), ok: false });
      },
    );
  };

  const s = state.settings;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }} data-testid="am-mail">
      {!state.ready && state.not_ready ? (
        <div role="note" style={warnBand} data-testid="am-mail-not-ready">
          <strong style={{ display: "block", marginBottom: "0.2rem" }}>{t("am.mail.notReadyTitle")}</strong>
          {state.not_ready}
        </div>
      ) : null}

      <section className="sran-card" aria-labelledby="am-mail-title">
        <h2 id="am-mail-title" style={h2}>
          {t("am.mail.title")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem" }}>{t("am.mail.body")}</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!busy) save();
          }}
        >
          <div style={{ marginTop: "0.9rem", display: "grid", gap: "0.9rem", gridTemplateColumns: "repeat(auto-fit, minmax(15rem, 1fr))" }}>
            <Field label={t("am.mail.host")}>
              <input className="sran-input" value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="smtp.example.co.th" data-testid="am-mail-host" />
            </Field>
            <Field label={t("am.mail.port")}>
              <input type="number" min={1} max={65535} className="sran-input" value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} data-testid="am-mail-port" />
            </Field>
            <Field label={t("am.mail.tls")}>
              <select className="sran-input" value={form.tls_mode} onChange={(e) => setForm({ ...form, tls_mode: e.target.value })} data-testid="am-mail-tls">
                {TLS.map((o) => (
                  <option key={o.id} value={o.id}>
                    {t(o.key)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("am.mail.username")}>
              <input className="sran-input" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} autoComplete="off" data-testid="am-mail-username" />
            </Field>
            <Field label={t("am.mail.sender")}>
              <input type="email" className="sran-input" value={form.sender} onChange={(e) => setForm({ ...form, sender: e.target.value })} data-testid="am-mail-sender" />
            </Field>
          </div>
          <p style={{ ...secondary, marginTop: "0.9rem" }} data-testid="am-mail-password">
            {state.password_set ? t("am.mail.passwordSet", { env: state.password_env }) : t("am.mail.passwordUnset", { env: state.password_env })}
          </p>
          <div style={{ marginTop: "0.9rem", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.75rem" }}>
            <button type="submit" className="sran-btn sran-btn-primary sran-btn-sm" disabled={busy} data-testid="am-mail-save">
              {busy ? t("action.saving") : t("action.save")}
            </button>
            <Note msg={msg} />
          </div>
          {s.updated_by || s.updated_at ? (
            <p style={{ ...muted, marginTop: "0.5rem" }}>{t("am.changedBy", { who: s.updated_by || "—", when: formatDateTime(s.updated_at, locale) })}</p>
          ) : null}
        </form>
      </section>

      <section className="sran-card" aria-labelledby="am-mail-test-title">
        <h2 id="am-mail-test-title" style={h2}>
          {t("am.mail.testTitle")}
        </h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!testing) test();
          }}
          style={{ marginTop: "0.75rem", display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: "0.5rem" }}
        >
          <div style={{ flex: 1, minWidth: "16rem" }}>
            <Field label={t("am.mail.testTo")}>
              <input type="email" className="sran-input" value={to} onChange={(e) => setTo(e.target.value)} data-testid="am-mail-test-to" />
            </Field>
          </div>
          <button type="submit" className="sran-btn sran-btn-secondary sran-btn-sm" disabled={testing || !to.trim()} data-testid="am-mail-test">
            {testing ? t("action.testing") : t("am.mail.testButton")}
          </button>
        </form>
        {testMsg ? (
          <div style={{ marginTop: "0.5rem" }}>
            <Note msg={testMsg} testId="am-mail-test-note" />
          </div>
        ) : null}
      </section>
    </div>
  );
}

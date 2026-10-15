"use client";

// หน้าตาเอกสาร (AM21) — the theme every agent's documents, decks and workbooks take, and the
// organisation's brand on them: its name, a primary and an accent colour. A choice here re-skins every
// deliverable of every agent and touches no figure; it is written to the admin audit log like every
// other setting. A name with a build or card marker, or a colour too light to read, is refused by the
// server with the reason.

import { useEffect, useState } from "react";

import { useI18n } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type LookPreset, type LookState } from "../../../lib/agentMarketApi";
import { Field, LoadError, Loading, Note, h2, muted, secondary, type Msg } from "./shared";

type Form = { id: string; company: string; primary: string; accent: string };

const formOf = (s: LookState): Form => ({
  id: s.theme?.id || s.presets[0]?.id || "navy",
  company: s.theme?.company ?? "",
  primary: s.theme?.palette?.primary ?? "",
  accent: s.theme?.palette?.accent ?? "",
});

function Swatch({ hex, size = 18 }: { hex: string; size?: number }) {
  return <span aria-hidden style={{ display: "inline-block", width: size, height: size, borderRadius: 4, background: `#${hex}`, border: "1px solid var(--border-default)" }} />;
}

export default function LookSection() {
  const { t } = useI18n();
  const [state, setState] = useState<LookState | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    let alive = true;
    amAdmin.look().then(
      (r) => {
        if (!alive) return;
        setState(r);
        setForm(formOf(r));
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
    const body: { id: string; company: string; primary?: string; accent?: string } = { id: form.id, company: form.company.trim() };
    if (form.primary.trim()) body.primary = form.primary.trim();
    if (form.accent.trim()) body.accent = form.accent.trim();
    amAdmin.putLook(body).then(
      (r) => {
        setBusy(false);
        setState({ ...state, theme: r.theme, version: r.version });
        setMsg({ text: t("am.look.saved", { version: String(r.version) }), ok: true });
      },
      (err) => {
        setBusy(false);
        setMsg({ text: amErrorMessage(err, t("am.failed"), t), ok: false });
      },
    );
  };

  const preset: LookPreset | undefined = state.presets.find((p) => p.id === form.id);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }} data-testid="am-look">
      <section className="sran-card" aria-labelledby="am-look-title">
        <h2 id="am-look-title" style={h2}>
          {t("am.look.title")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem", lineHeight: 1.6 }}>{t("am.look.body")}</p>
        <div role="radiogroup" aria-labelledby="am-look-title" style={{ marginTop: "0.8rem", display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {state.presets.map((p) => {
            const on = form.id === p.id;
            return (
              <label
                key={p.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.6rem",
                  cursor: "pointer",
                  borderRadius: "var(--radius-lg)",
                  padding: "0.65rem 0.85rem",
                  border: `1px solid ${on ? "var(--accent-primary)" : "var(--border-default)"}`,
                  background: on ? "var(--bg-elevated)" : "transparent",
                  fontSize: "0.875rem",
                  color: "var(--text-primary)",
                  minWidth: "12rem",
                }}
              >
                <input type="radio" name="am-look-theme" checked={on} onChange={() => setForm({ ...form, id: p.id })} data-testid={`am-look-${p.id}`} />
                <Swatch hex={p.primary} />
                <Swatch hex={p.accent} size={12} />
                <span>{p.name}</span>
              </label>
            );
          })}
        </div>
        <div style={{ marginTop: "1rem", display: "grid", gap: "0.8rem", maxWidth: "36rem" }}>
          <Field label={t("am.look.company")} hint={t("am.look.companyHint")}>
            <input className="sran-input" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} maxLength={120} data-testid="am-look-company" />
          </Field>
          <Field label={t("am.look.primary")} hint={t("am.look.colourHint", { theme: preset?.primary ?? "" })}>
            <span style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <Swatch hex={/^[0-9A-Fa-f]{6}$/.test(form.primary) ? form.primary : preset?.primary ?? "FFFFFF"} />
              <input className="sran-input" value={form.primary} onChange={(e) => setForm({ ...form, primary: e.target.value })} maxLength={7} placeholder={preset?.primary} data-testid="am-look-primary" style={{ maxWidth: "9rem" }} />
            </span>
          </Field>
          <Field label={t("am.look.accent")} hint={t("am.look.colourHint", { theme: preset?.accent ?? "" })}>
            <span style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <Swatch hex={/^[0-9A-Fa-f]{6}$/.test(form.accent) ? form.accent : preset?.accent ?? "FFFFFF"} />
              <input className="sran-input" value={form.accent} onChange={(e) => setForm({ ...form, accent: e.target.value })} maxLength={7} placeholder={preset?.accent} data-testid="am-look-accent" style={{ maxWidth: "9rem" }} />
            </span>
          </Field>
        </div>
        <div style={{ marginTop: "1rem", display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <button type="button" className="sran-btn sran-btn-primary sran-btn-sm" onClick={save} disabled={busy} data-testid="am-look-save">
            {t("am.look.save")}
          </button>
          <span style={secondary}>{t("am.look.version", { version: String(state.version) })}</span>
        </div>
        <Note msg={msg} testId="am-look-note" />
      </section>
      <section className="sran-card" aria-labelledby="am-look-file-title">
        <h2 id="am-look-file-title" style={h2}>
          {t("am.look.fileTitle")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem", lineHeight: 1.6 }}>
          {t("am.look.fileBody")} <code>{state.file}</code>
        </p>
      </section>
    </div>
  );
}

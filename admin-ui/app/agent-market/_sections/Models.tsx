"use client";

// โมเดลและการเก็บข้อมูล — whether documents may go to a model outside the
// organisation (off by default), how many years a run's evidence is kept,
// and when an agent's storage is worth a warning. AM6: an admin no longer
// names models by outcome; the agent's model menu is the chat's, and this
// switch is the policy it follows.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useI18n } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type OrgSettings } from "../../../lib/agentMarketApi";
import AgentEgressCard from "./AgentEgress";
import { Field, LoadError, Loading, Note, formatDateTime, h2, linkStyle, muted, secondary, useOrg, withLink, type Msg } from "./shared";

export default function ModelsSection() {
  const { t, locale } = useI18n();
  const { org, error, reload } = useOrg();
  const [settings, setSettings] = useState<OrgSettings | null>(null);
  const [leave, setLeave] = useState(false);
  const [years, setYears] = useState("");
  const [warnMb, setWarnMb] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  const adopt = useCallback((o: OrgSettings) => {
    setSettings(o);
    setLeave(!!o.documents_may_leave);
    setYears(String(o.retention_years ?? ""));
    setWarnMb(String(o.storage_warn_mb ?? ""));
  }, []);

  useEffect(() => {
    if (org && !settings) adopt(org.org);
  }, [org, settings, adopt]);

  if (error && !org) return <LoadError error={error} onRetry={reload} />;
  if (!org || !settings) return <Loading />;

  const save = () => {
    const y = Number(years);
    const w = Number(warnMb);
    if (!Number.isInteger(y) || y < 1 || y > 30) {
      setMsg({ text: t("am.retention.yearsRange"), ok: false });
      return;
    }
    if (!Number.isFinite(w) || w < 50) {
      setMsg({ text: t("am.retention.warnMin"), ok: false });
      return;
    }
    setBusy(true);
    setMsg(null);
    amAdmin.putOrg({ documents_may_leave: leave, retention_years: y, storage_warn_mb: w }).then(
      (r) => {
        setBusy(false);
        if (r?.org) adopt(r.org);
        setMsg({ text: t("am.saved"), ok: true });
        reload();
      },
      (err) => {
        setBusy(false);
        setMsg({ text: amErrorMessage(err, t("am.failed"), t), ok: false });
      },
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }} data-testid="am-models">
      <section className="sran-card" aria-labelledby="am-leave-title">
        <h2 id="am-leave-title" style={h2}>
          {t("am.models.leaveTitle")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem", lineHeight: 1.6 }}>{t("am.models.leaveBody")}</p>
        <div role="radiogroup" aria-labelledby="am-leave-title" style={{ marginTop: "0.8rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {([false, true] as const).map((v) => {
            const on = leave === v;
            return (
              <label
                key={String(v)}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "0.6rem",
                  cursor: "pointer",
                  borderRadius: "var(--radius-lg)",
                  padding: "0.65rem 0.85rem",
                  border: `1px solid ${on ? "var(--accent-primary)" : "var(--border-default)"}`,
                  background: on ? "var(--bg-elevated)" : "transparent",
                  fontSize: "0.875rem",
                  color: "var(--text-primary)",
                }}
              >
                <input
                  type="radio"
                  name="am-leave"
                  checked={on}
                  onChange={() => setLeave(v)}
                  style={{ marginTop: "0.2rem" }}
                  data-testid={`am-leave-${v ? "on" : "off"}`}
                />
                <span>{v ? t("am.models.leaveOn") : t("am.models.leaveOff")}</span>
              </label>
            );
          })}
        </div>
      </section>

      <AgentEgressCard orgVersion={`${settings.documents_may_leave}:${settings.updated_at ?? ""}`} />

      <section className="sran-card" aria-labelledby="am-retention-title">
        <h2 id="am-retention-title" style={h2}>
          {t("am.retention.title")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem", lineHeight: 1.6 }}>{t("am.retention.body")}</p>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem" }}>{t("am.retention.notArchive")}</p>
        <div style={{ marginTop: "0.9rem", display: "grid", gap: "0.9rem", gridTemplateColumns: "repeat(auto-fit, minmax(12rem, 16rem))" }}>
          <Field label={t("am.retention.years")}>
            <input type="number" min={1} max={30} step={1} className="sran-input" value={years} onChange={(e) => setYears(e.target.value)} data-testid="am-retention-years" />
          </Field>
          <Field label={t("am.retention.warn")}>
            <input type="number" min={50} className="sran-input" value={warnMb} onChange={(e) => setWarnMb(e.target.value)} data-testid="am-storage-warn" />
          </Field>
        </div>
      </section>

      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.75rem" }}>
        <button type="button" className="sran-btn sran-btn-primary sran-btn-sm" onClick={save} disabled={busy} data-testid="am-models-save">
          {busy ? t("action.saving") : t("action.save")}
        </button>
        <Note msg={msg} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
        {settings.updated_by || settings.updated_at ? (
          <p style={muted} data-testid="am-models-changed">
            {t("am.changedBy", { who: settings.updated_by || "—", when: formatDateTime(settings.updated_at, locale) })}
          </p>
        ) : null}
        <p style={secondary} data-testid="am-models-audit">
          {withLink(
            t("am.models.audit"),
            <Link href="/audit-log" style={linkStyle}>
              {t("am.models.auditLink")}
            </Link>,
          )}
        </p>
      </div>
    </div>
  );
}

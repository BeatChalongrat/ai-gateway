"use client";

// AM9 — each agent's own answer under the organisation's switch. The
// organisation's switch stays the ceiling; an agent's own setting can only be
// stricter (documents leave only when both allow it), and every agent is off
// until an admin turns it on here. Every change is an admin's, written to the
// audit log before it is kept (chat-edge refuses it otherwise).
//
// AM16 — masking is a second switch beside it, per agent, and its own: it used
// to follow the first (AM13b). Both are off by default. With masking off, what
// leaves is sent as typed; with it on, text is masked and a page — an image,
// which cannot be masked — is read inside only. The row says, in words, what
// the two together mean for that agent now.

import { useCallback, useEffect, useState } from "react";

import { useI18n } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type AgentEgressRow } from "../../../lib/agentMarketApi";
import { AgentName, LoadError, Loading, Note, agentName, h2, muted, useLooks, type Msg } from "./shared";

export default function AgentEgressCard({ orgVersion }: { orgVersion: string }) {
  const { t, locale } = useI18n();
  const [rows, setRows] = useState<AgentEgressRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const looks = useLooks();

  const load = useCallback(() => {
    amAdmin.egress().then(
      (r) => {
        setRows(r.agents);
        setError(null);
      },
      (err) => setError(err),
    );
  }, []);

  // read again when the organisation's switch was saved above (the ceiling moved)
  useEffect(load, [load, orgVersion]);

  if (error && !rows) return <LoadError error={error} onRetry={load} />;
  if (!rows) return <Loading />;

  const toggle = (row: AgentEgressRow, which: "leave" | "masked" = "leave") => {
    setBusy(row.slug);
    setMsg(null);
    (which === "masked" ? amAdmin.putMasked(row.slug, !row.masked) : amAdmin.putEgress(row.slug, !row.agent)).then(
      (r) => {
        setBusy(null);
        setRows((cur) => (cur ?? []).map((x) => (x.slug === row.slug ? { ...x, ...r.egress, slug: x.slug, name: x.name } : x)));
        setMsg({ text: t("am.egress.saved", { name: agentName(row.name, looks[row.slug]?.name_en, locale) }), ok: true });
      },
      (err) => {
        setBusy(null);
        setMsg({ text: amErrorMessage(err, t("am.failed"), t), ok: false });
      },
    );
  };

  return (
    <section className="sran-card" aria-labelledby="am-egress-title" data-testid="am-egress">
      <h2 id="am-egress-title" style={h2}>
        {t("am.egress.title")}
      </h2>
      <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem", lineHeight: 1.6 }}>{t("am.egress.body")}</p>
      {/* AM16: masking is its own switch beside this one — what each does, said where both are decided */}
      <p style={{ ...muted, marginTop: "0.5rem", maxWidth: "52rem", lineHeight: 1.6 }} data-testid="am-egress-masking">
        {t("am.egress.masking")}
      </p>
      <table className="sran-table" style={{ marginTop: "0.8rem", width: "100%" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>{t("am.egress.agent")}</th>
            <th style={{ textAlign: "left" }}>{t("am.egress.allow")}</th>
            <th style={{ textAlign: "left" }}>{t("am.egress.masked")}</th>
            <th style={{ textAlign: "left" }}>{t("am.egress.now")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.slug} data-testid={`am-egress-${r.slug}`}>
              <td>
                <AgentName name={r.name} look={looks[r.slug]} />
              </td>
              <td>
                <label style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={r.agent}
                    disabled={busy !== null}
                    onChange={() => toggle(r)}
                    data-testid={`am-egress-toggle-${r.slug}`}
                  />
                  {r.agent_default && r.web && !r.agent ? (
                    <span style={muted}>{t("am.egress.web.default")}</span>
                  ) : r.agent_default && !r.agent ? (
                    <span style={muted}>{t("am.egress.default")}</span>
                  ) : null}
                </label>
              </td>
              <td>
                <label style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={!!r.masked}
                    disabled={busy !== null}
                    onChange={() => toggle(r, "masked")}
                    data-testid={`am-masked-toggle-${r.slug}`}
                  />
                  {!r.masked ? <span style={muted}>{t("am.egress.masked.default")}</span> : null}
                </label>
              </td>
              <td data-testid={`am-egress-now-${r.slug}`} data-leaves={r.effective ? "yes" : "no"} data-masked={r.masked ? "yes" : "no"}>
                {/* AM12: an agent on the web does not send documents: it searches and opens pages outside */}
                {r.web ? (r.effective ? t("am.egress.web.leaves") : t("am.egress.web.stays")) : r.effective ? t("am.egress.leaves") : t("am.egress.stays")}
                {!r.effective && r.agent && !r.org ? <span style={muted}> · {t("am.egress.ceiling")}</span> : null}
                {/* AM16: and how — as typed or masked — in the same cell, so the two switches read as one answer */}
                <span data-testid={`am-masked-now-${r.slug}`}>
                  {" · "}
                  {r.masked
                    ? r.effective && !r.web
                      ? t("am.egress.how.maskedOut")
                      : t("am.egress.how.masked")
                    : r.effective
                      ? t("am.egress.how.typedOut")
                      : t("am.egress.how.typedIn")}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ marginTop: "0.6rem" }}>
        <Note msg={msg} />
      </div>
    </section>
  );
}

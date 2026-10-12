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
//
// AM16 — and to whom. "May leave" said yes or no, not who receives: the admin
// names the recipients, each a route and the company that runs the model
// behind it, because both read what is sent. The organisation's list is the
// ceiling; an agent's ticks can only be among them; and with nobody ticked an
// agent whose documents may leave reaches nobody, which the row says.

import { useCallback, useEffect, useState } from "react";

import { useI18n } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type AgentEgressRow, type RecipientOption } from "../../../lib/agentMarketApi";
import { AgentName, LoadError, Loading, Note, agentName, h2, muted, useLooks, type Msg } from "./shared";

// How a company and a route are written for a person. A name nobody has written here is shown as the
// catalogue gives it — never hidden.
const COMPANY: Record<string, string> = {
  google: "Google",
  anthropic: "Anthropic",
  amazon: "Amazon Bedrock",
  microsoft: "Microsoft Azure",
  openai: "OpenAI",
  deepseek: "DeepSeek",
};
const ROUTE: Record<string, string> = { openrouter: "OpenRouter", deepseek: "DeepSeek", google: "Google", anthropic: "Anthropic", openai: "OpenAI" };

export function splitRecipient(key: string): { route: string; company: string } {
  const i = key.indexOf(":");
  return i < 0 ? { route: key, company: key } : { route: key.slice(0, i), company: key.slice(i + 1) };
}

export default function AgentEgressCard({ orgVersion }: { orgVersion: string }) {
  const { t, locale } = useI18n();
  const [rows, setRows] = useState<AgentEgressRow[] | null>(null);
  const [available, setAvailable] = useState<RecipientOption[]>([]);
  const [orgList, setOrgList] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const looks = useLooks();

  const load = useCallback(() => {
    amAdmin.egress().then(
      (r) => {
        setRows(r.agents);
        setAvailable(r.available ?? []);
        setOrgList(r.org_recipients ?? []);
        setError(null);
      },
      (err) => setError(err),
    );
  }, []);

  // read again when the organisation's switch was saved above (the ceiling moved)
  useEffect(load, [load, orgVersion]);

  if (error && !rows) return <LoadError error={error} onRetry={load} />;
  if (!rows) return <Loading />;

  // both names, always: the company that runs the model, and the route it is reached by
  const recipientName = (key: string) => {
    const { route, company } = splitRecipient(key);
    const c = COMPANY[company] ?? company;
    return route === company ? t("am.recipient.direct", { company: c }) : t("am.recipient.via", { company: c, route: ROUTE[route] ?? route });
  };
  const failed = (err: unknown) => {
    setBusy(null);
    setMsg({ text: amErrorMessage(err, t("am.failed"), t), ok: false });
  };

  const toggle = (row: AgentEgressRow, which: "leave" | "masked" = "leave") => {
    setBusy(row.slug);
    setMsg(null);
    (which === "masked" ? amAdmin.putMasked(row.slug, !row.masked) : amAdmin.putEgress(row.slug, !row.agent)).then((r) => {
      setBusy(null);
      setRows((cur) => (cur ?? []).map((x) => (x.slug === row.slug ? { ...x, ...r.egress, slug: x.slug, name: x.name } : x)));
      setMsg({ text: t("am.egress.saved", { name: agentName(row.name, looks[row.slug]?.name_en, locale) }), ok: true });
    }, failed);
  };

  // the organisation's own list — the ceiling. The whole list is sent; what the server kept is read back,
  // every agent's row with it (taking a recipient off the list takes it from every agent at once).
  const toggleOrg = (key: string) => {
    const next = orgList.includes(key) ? orgList.filter((k) => k !== key) : [...orgList, key];
    setBusy("org");
    setMsg(null);
    amAdmin.putOrgRecipients(next).then((r) => {
      setBusy(null);
      setOrgList(r.org_recipients ?? []);
      setMsg({ text: t("am.recipients.saved"), ok: true });
      load();
    }, failed);
  };

  const toggleRecipient = (row: AgentEgressRow, key: string) => {
    const own = row.agent_recipients ?? [];
    const next = own.includes(key) ? own.filter((k) => k !== key) : [...own, key];
    setBusy(row.slug);
    setMsg(null);
    amAdmin.putRecipients(row.slug, next).then((r) => {
      setBusy(null);
      setRows((cur) => (cur ?? []).map((x) => (x.slug === row.slug ? { ...x, ...r.egress, slug: x.slug, name: x.name } : x)));
      setMsg({ text: t("am.egress.saved", { name: agentName(row.name, looks[row.slug]?.name_en, locale) }), ok: true });
    }, failed);
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

      {/* AM16: to whom — the organisation's own list, above every agent's */}
      <div style={{ marginTop: "0.9rem" }} data-testid="am-recipients-org">
        <h3 style={{ fontSize: "0.95rem", fontWeight: 600, margin: 0 }}>{t("am.recipients.title")}</h3>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "52rem", lineHeight: 1.6 }}>{t("am.recipients.body")}</p>
        {available.length === 0 && orgList.length === 0 ? (
          <p style={{ ...muted, marginTop: "0.4rem" }}>{t("am.recipients.none")}</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: "0.5rem 0 0", display: "grid", gap: "0.35rem" }}>
            {[...available.map((o) => o.key), ...orgList.filter((k) => !available.some((o) => o.key === k))].map((key) => {
              const option = available.find((o) => o.key === key);
              return (
                <li key={key}>
                  <label style={{ display: "inline-flex", alignItems: "baseline", gap: "0.45rem", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={orgList.includes(key)}
                      disabled={busy !== null}
                      onChange={() => toggleOrg(key)}
                      data-testid={`am-recipient-org-${key}`}
                    />
                    <span>
                      <strong style={{ fontWeight: 600 }}>{recipientName(key)}</strong>
                      <span style={muted}> · {option ? t("am.recipients.models", { models: option.models.join(", ") }) : t("am.recipients.gone")}</span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <table className="sran-table" style={{ marginTop: "0.8rem", width: "100%" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>{t("am.egress.agent")}</th>
            <th style={{ textAlign: "left" }}>{t("am.egress.allow")}</th>
            <th style={{ textAlign: "left" }}>{t("am.egress.masked")}</th>
            <th style={{ textAlign: "left" }}>{t("am.recipients.col")}</th>
            <th style={{ textAlign: "left" }}>{t("am.egress.now")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const own = r.agent_recipients ?? [];
            const reaches = r.recipients ?? [];
            // an agent can tick what the organisation named; a tick of its own that the organisation has
            // since taken off its list is shown too — it counts for nothing, and can be removed here
            const offered = [...orgList, ...own.filter((k) => !orgList.includes(k))];
            return (
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
                <td data-testid={`am-recipients-${r.slug}`}>
                  {offered.length === 0 ? (
                    <span style={muted}>{t("am.recipients.orgEmpty")}</span>
                  ) : (
                    <div style={{ display: "grid", gap: "0.2rem" }}>
                      {offered.map((key) => (
                        <label key={key} style={{ display: "inline-flex", alignItems: "baseline", gap: "0.4rem", cursor: "pointer" }}>
                          <input
                            type="checkbox"
                            checked={own.includes(key)}
                            disabled={busy !== null}
                            onChange={() => toggleRecipient(r, key)}
                            data-testid={`am-recipient-${r.slug}-${key}`}
                          />
                          <span>
                            {recipientName(key)}
                            {!orgList.includes(key) ? <span style={muted}> · {t("am.recipients.notCounted")}</span> : null}
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </td>
                <td
                  data-testid={`am-egress-now-${r.slug}`}
                  data-leaves={r.effective ? "yes" : "no"}
                  data-masked={r.masked ? "yes" : "no"}
                  data-reaches={reaches.join(" ")}
                >
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
                  {/* AM16: and to whom. May leave, and nobody named: it reaches nobody — said here, not left to be found out */}
                  {r.effective ? (
                    <span data-testid={`am-reaches-now-${r.slug}`}>
                      {" · "}
                      {reaches.length === 0 ? (
                        <strong style={{ fontWeight: 600 }}>{t("am.recipients.nobody")}</strong>
                      ) : (
                        t("am.recipients.to", { list: reaches.map(recipientName).join(", ") })
                      )}
                    </span>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ marginTop: "0.6rem" }}>
        <Note msg={msg} />
      </div>
    </section>
  );
}

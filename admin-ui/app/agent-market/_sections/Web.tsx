"use client";

// AM12 — what an admin holds over agents that work on the web.
//
// An agent on the web searches for what a person asked for and opens pages
// outside the organisation. Whether it may at all is the per-agent switch on
// the Models tab (off until an admin turns it on); this tab holds the rest:
// the ceilings of one run (how many searches and pages, how long, how much
// of a page is kept), how long a saved page that was not used is kept, the
// sites that are never opened, the organisation's own suppliers (told to the
// agent as preferred — never a list it is limited to), and where each run
// went. There is no list of sites the agent is allowed to open: which page
// to open is decided for each request, and a site's own rules for automated
// reading are read when its page is opened.
//
// Every change is written to the admin audit log before it is kept
// (chat-edge refuses it otherwise).

import { useCallback, useEffect, useState } from "react";

import { Icon } from "../../../components/ui/Icon";
import { useI18n } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type AdminWeb, type WebRunRow, type WebSupplier } from "../../../lib/agentMarketApi";
import { AgentName, LoadError, Loading, Note, formatBytes, formatDateTime, h2, muted, secondary, useLooks, type Msg } from "./shared";

const NO_SUPPLIER = { id: "", name: "", tax_id: "", website: "", note: "" };
type PolicyForm = { max_searches: string; max_pages: string; max_seconds: string; max_source_mb: string; unused_keep_days: string };

const grid = { marginTop: "0.8rem", display: "grid", gap: "0.5rem", gridTemplateColumns: "repeat(auto-fit, minmax(11rem, 1fr))", alignItems: "end" } as const;
const list = { listStyle: "none", padding: 0, margin: "0.9rem 0 0", display: "flex", flexDirection: "column", gap: "0.45rem" } as const;
const item = { border: "1px solid var(--border-default)", borderRadius: "var(--radius-md)", padding: "0.55rem 0.8rem" } as const;
const row = { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem" } as const;
const strong = { fontWeight: 600, fontSize: "0.875rem", color: "var(--text-primary)", wordBreak: "break-all" } as const;

export default function WebSection() {
  const { t, locale } = useI18n();
  const [web, setWeb] = useState<AdminWeb | null>(null);
  const [runs, setRuns] = useState<WebRunRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [policy, setPolicy] = useState<PolicyForm | null>(null);
  const [host, setHost] = useState({ host: "", note: "" });
  const [supplier, setSupplier] = useState(NO_SUPPLIER);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Record<string, Msg>>({});
  const looks = useLooks();

  const take = useCallback((w: AdminWeb) => {
    setWeb(w);
    const n = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));
    setPolicy({
      max_searches: n(w.policy.max_searches),
      max_pages: n(w.policy.max_pages),
      max_seconds: n(w.policy.max_seconds),
      max_source_mb: n(w.policy.max_source_mb),
      unused_keep_days: n(w.policy.unused_keep_days),
    });
  }, []);
  const load = useCallback(() => {
    amAdmin.web().then(
      (w) => {
        take(w);
        setError(null);
      },
      (err) => setError(err),
    );
    amAdmin.webCalls(30).then(
      (r) => setRuns(r.runs ?? []),
      () => setRuns([]),
    );
  }, [take]);
  useEffect(load, [load]);

  if (error && !web) return <LoadError error={error} onRetry={load} />;
  if (!web || !policy) return <Loading />;

  const act = (part: string, call: Promise<Partial<AdminWeb>>, after?: () => void) => {
    setBusy(true);
    setMsg((m) => ({ ...m, [part]: null }));
    call.then(
      (r) => {
        setBusy(false);
        take({ ...web, ...r });
        after?.();
        setMsg((m) => ({ ...m, [part]: { text: t("am.saved"), ok: true } }));
      },
      (err) => {
        setBusy(false);
        setMsg((m) => ({ ...m, [part]: { text: amErrorMessage(err, t("am.failed"), t), ok: false } }));
      },
    );
  };
  const num = (s: string) => (s.trim() === "" ? null : Number(s.trim()));
  const ceiling = web.ceiling;
  const policyField = (key: keyof PolicyForm, label: string, hint: string) => (
    <label style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
      <span style={secondary}>{label}</span>
      <input
        className="sran-input"
        value={policy[key]}
        onChange={(e) => setPolicy({ ...policy, [key]: e.target.value })}
        inputMode="numeric"
        placeholder={hint}
        aria-label={label}
        data-testid={`am-web-policy-${key}`}
      />
    </label>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }} data-testid="am-web">
      <section className="sran-card" aria-labelledby="am-web-title">
        <h2 id="am-web-title" style={h2}>
          {t("am.web.title")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "56rem", lineHeight: 1.6 }}>{t("am.web.body")}</p>
        <ul style={{ ...list, marginTop: "0.7rem" }} data-testid="am-web-status">
          <li style={secondary} data-testid="am-web-search" data-on={String(web.search)}>
            {web.search ? t("am.web.search.on") : t("am.web.search.off")}
          </li>
          <li style={secondary} data-testid="am-web-fetcher" data-on={String(web.fetcher)}>
            {web.fetcher ? t("am.web.fetcher.on") : t("am.web.fetcher.off")}
          </li>
        </ul>
        {web.agents.length ? (
          <table className="sran-table" style={{ marginTop: "0.8rem", width: "100%" }} data-testid="am-web-agents">
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>{t("am.web.agent")}</th>
                <th style={{ textAlign: "left" }}>{t("am.web.agentNow")}</th>
                <th style={{ textAlign: "left" }}>{t("am.web.agentCaps")}</th>
              </tr>
            </thead>
            <tbody>
              {web.agents.map((a) => (
                <tr key={a.slug} data-testid={`am-web-agent-${a.slug}`} data-on={String(a.effective)}>
                  <td>
                    <AgentName name={a.name} look={looks[a.slug]} />
                  </td>
                  <td>
                    {a.effective ? t("am.web.agentOn") : !a.agent ? t("am.web.agentOff") : t("am.web.agentCeiling")}
                    {!a.effective ? <span style={muted}> · {t("am.web.agentWhere")}</span> : null}
                  </td>
                  <td style={secondary}>
                    {t("am.web.capsLine", { searches: a.caps.searches, pages: a.caps.pages, seconds: a.caps.seconds, mb: a.caps.source_mb })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ ...secondary, marginTop: "0.8rem" }}>{t("am.web.noAgents")}</p>
        )}
      </section>

      <section className="sran-card" aria-labelledby="am-web-policy-title" data-testid="am-web-policy">
        <h2 id="am-web-policy-title" style={h2}>
          {t("am.web.policy.title")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "56rem", lineHeight: 1.6 }}>{t("am.web.policy.body")}</p>
        <form
          style={grid}
          onSubmit={(e) => {
            e.preventDefault();
            if (busy) return;
            act(
              "policy",
              amAdmin.putWebPolicy({
                max_searches: num(policy.max_searches),
                max_pages: num(policy.max_pages),
                max_seconds: num(policy.max_seconds),
                max_source_mb: num(policy.max_source_mb),
                unused_keep_days: num(policy.unused_keep_days),
              }),
            );
          }}
        >
          {policyField("max_searches", t("am.web.policy.searches"), t("am.web.policy.upTo", { n: ceiling.searches }))}
          {policyField("max_pages", t("am.web.policy.pages"), t("am.web.policy.upTo", { n: ceiling.pages }))}
          {policyField("max_seconds", t("am.web.policy.seconds"), t("am.web.policy.upTo", { n: ceiling.seconds }))}
          {policyField("max_source_mb", t("am.web.policy.sourceMb"), t("am.web.policy.upTo", { n: ceiling.source_mb }))}
          {policyField("unused_keep_days", t("am.web.policy.keepDays"), "7")}
          <div>
            <button type="submit" className="sran-btn sran-btn-primary sran-btn-sm" disabled={busy} data-testid="am-web-policy-save">
              {t("action.save")}
            </button>
          </div>
        </form>
        <p style={{ ...muted, marginTop: "0.5rem", maxWidth: "56rem", lineHeight: 1.6 }}>{t("am.web.policy.keepNote")}</p>
        <div style={{ marginTop: "0.5rem" }}>
          <Note msg={msg.policy ?? null} testId="am-web-policy-note" />
        </div>
      </section>

      <section className="sran-card" aria-labelledby="am-web-deny-title" data-testid="am-web-deny">
        <h2 id="am-web-deny-title" style={h2}>
          {t("am.web.deny.title")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "56rem", lineHeight: 1.6 }}>{t("am.web.deny.body")}</p>
        <form
          style={grid}
          onSubmit={(e) => {
            e.preventDefault();
            if (busy || !host.host.trim()) return;
            act("deny", amAdmin.putWebDeny(host.host.trim(), host.note.trim()), () => setHost({ host: "", note: "" }));
          }}
        >
          <input
            className="sran-input"
            value={host.host}
            onChange={(e) => setHost({ ...host, host: e.target.value })}
            placeholder={t("am.web.deny.host")}
            aria-label={t("am.web.deny.host")}
            data-testid="am-web-deny-host"
          />
          <input
            className="sran-input"
            value={host.note}
            onChange={(e) => setHost({ ...host, note: e.target.value })}
            placeholder={t("am.web.note")}
            aria-label={t("am.web.note")}
          />
          <div>
            <button type="submit" className="sran-btn sran-btn-primary sran-btn-sm" disabled={busy || !host.host.trim()} data-testid="am-web-deny-add">
              {t("am.web.deny.add")}
            </button>
          </div>
        </form>
        <div style={{ marginTop: "0.5rem" }}>
          <Note msg={msg.deny ?? null} testId="am-web-deny-note" />
        </div>
        {web.deny.length ? (
          <ul style={list} data-testid="am-web-deny-list">
            {web.deny.map((d) => (
              <li key={d.host} style={item} data-testid="am-web-denied">
                <div style={row}>
                  <span style={strong}>{d.host}</span>
                  {d.note ? <span style={secondary}>{d.note}</span> : null}
                  <span style={{ flex: 1 }} />
                  <span style={muted}>{t("am.changedBy", { who: d.added_by || "—", when: formatDateTime(d.added_at, locale) })}</span>
                  <button
                    type="button"
                    className="sran-btn sran-btn-secondary sran-btn-sm"
                    disabled={busy}
                    onClick={() => act("deny", amAdmin.deleteWebDeny(d.host))}
                    aria-label={t("am.web.deny.removeFor", { host: d.host })}
                    title={t("am.web.deny.removeFor", { host: d.host })}
                  >
                    <Icon name="trash" size={13} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p style={{ ...secondary, marginTop: "0.9rem" }}>{t("am.web.deny.none")}</p>
        )}
      </section>

      <section className="sran-card" aria-labelledby="am-web-sup-title" data-testid="am-web-suppliers">
        <h2 id="am-web-sup-title" style={h2}>
          {t("am.web.sup.title")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "56rem", lineHeight: 1.6 }}>{t("am.web.sup.body")}</p>
        <form
          style={grid}
          onSubmit={(e) => {
            e.preventDefault();
            if (busy || !supplier.name.trim()) return;
            act(
              "sup",
              amAdmin.putWebSupplier({
                ...(supplier.id ? { id: supplier.id } : {}),
                name: supplier.name.trim(),
                tax_id: supplier.tax_id.trim(),
                website: supplier.website.trim(),
                note: supplier.note.trim(),
              }),
              () => setSupplier(NO_SUPPLIER),
            );
          }}
        >
          <input
            className="sran-input"
            value={supplier.name}
            onChange={(e) => setSupplier({ ...supplier, name: e.target.value })}
            placeholder={t("am.web.sup.name")}
            aria-label={t("am.web.sup.name")}
            data-testid="am-web-sup-name"
          />
          <input
            className="sran-input"
            value={supplier.tax_id}
            onChange={(e) => setSupplier({ ...supplier, tax_id: e.target.value })}
            placeholder={t("am.web.sup.taxId")}
            aria-label={t("am.web.sup.taxId")}
            inputMode="numeric"
            data-testid="am-web-sup-tax"
          />
          <input
            className="sran-input"
            value={supplier.website}
            onChange={(e) => setSupplier({ ...supplier, website: e.target.value })}
            placeholder={t("am.web.sup.website")}
            aria-label={t("am.web.sup.website")}
          />
          <input
            className="sran-input"
            value={supplier.note}
            onChange={(e) => setSupplier({ ...supplier, note: e.target.value })}
            placeholder={t("am.web.note")}
            aria-label={t("am.web.note")}
          />
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <button type="submit" className="sran-btn sran-btn-primary sran-btn-sm" disabled={busy || !supplier.name.trim()} data-testid="am-web-sup-save">
              {supplier.id ? t("action.save") : t("am.web.sup.add")}
            </button>
            {supplier.id ? (
              <button type="button" className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => setSupplier(NO_SUPPLIER)}>
                {t("action.cancel")}
              </button>
            ) : null}
          </div>
        </form>
        <div style={{ marginTop: "0.5rem" }}>
          <Note msg={msg.sup ?? null} testId="am-web-sup-note" />
        </div>
        {web.suppliers.length ? (
          <ul style={list} data-testid="am-web-sup-list">
            {web.suppliers.map((s: WebSupplier) => (
              <li key={s.id} style={item} data-testid="am-web-supplier">
                <div style={row}>
                  <span style={strong}>{s.name}</span>
                  {s.tax_id ? <span style={secondary}>{t("am.web.sup.taxLine", { id: s.tax_id })}</span> : null}
                  {s.website ? <span style={secondary}>{s.website}</span> : null}
                  <span style={{ flex: 1 }} />
                  <button
                    type="button"
                    className="sran-btn sran-btn-secondary sran-btn-sm"
                    onClick={() => setSupplier({ id: s.id, name: s.name, tax_id: s.tax_id ?? "", website: s.website ?? "", note: s.note ?? "" })}
                    aria-label={t("am.web.sup.editFor", { name: s.name })}
                    title={t("am.web.sup.editFor", { name: s.name })}
                  >
                    <Icon name="edit" size={13} />
                  </button>
                  <button
                    type="button"
                    className="sran-btn sran-btn-secondary sran-btn-sm"
                    disabled={busy}
                    onClick={() => act("sup", amAdmin.deleteWebSupplier(s.id))}
                    aria-label={t("am.web.sup.removeFor", { name: s.name })}
                    title={t("am.web.sup.removeFor", { name: s.name })}
                  >
                    <Icon name="trash" size={13} />
                  </button>
                </div>
                {s.note ? <p style={{ ...muted, marginTop: "0.2rem" }}>{s.note}</p> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p style={{ ...secondary, marginTop: "0.9rem" }}>{t("am.web.sup.none")}</p>
        )}
      </section>

      <section className="sran-card" aria-labelledby="am-web-runs-title" data-testid="am-web-runs">
        <h2 id="am-web-runs-title" style={h2}>
          {t("am.web.runs.title")}
        </h2>
        <p style={{ ...muted, marginTop: "0.25rem", maxWidth: "56rem", lineHeight: 1.6 }}>{t("am.web.runs.body")}</p>
        {runs === null ? (
          <Loading />
        ) : runs.length ? (
          <table className="sran-table" style={{ marginTop: "0.8rem", width: "100%" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>{t("am.web.runs.when")}</th>
                <th style={{ textAlign: "left" }}>{t("am.web.runs.who")}</th>
                <th style={{ textAlign: "left" }}>{t("am.web.runs.did")}</th>
                <th style={{ textAlign: "left" }}>{t("am.web.runs.reached")}</th>
                <th style={{ textAlign: "left" }}>{t("am.web.runs.refused")}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id} data-testid="am-web-run" style={{ verticalAlign: "top" }}>
                  <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(r.started_at, locale)}</td>
                  <td>
                    <AgentName name={r.agent || r.slug} look={looks[r.slug]} />
                    <div style={muted}>{r.user}</div>
                  </td>
                  <td style={secondary}>{t("am.web.runs.didLine", { searches: r.searches, pages: r.fetches, size: formatBytes(r.bytes) })}</td>
                  <td style={{ wordBreak: "break-all" }}>{r.reached.length ? r.reached.join(" · ") : "—"}</td>
                  <td>
                    {r.refused.length ? (
                      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                        {r.refused.map((x, i) => (
                          <li key={`${x.host}:${i}`} data-testid="am-web-run-refused">
                            <span style={{ wordBreak: "break-all" }}>{x.host}</span>
                            <div style={muted}>{x.words?.[locale] || x.words?.th || t("am.web.runs.noReason")}</div>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ ...secondary, marginTop: "0.8rem" }}>{t("am.web.runs.none")}</p>
        )}
      </section>
    </div>
  );
}

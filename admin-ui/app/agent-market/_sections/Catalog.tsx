"use client";

// แคตตาล็อก — the organisation's agents: publish, unpublish and remake the
// sample result (admins and agent publishers); disable for everyone with a
// reason people will see, and enable again (admins only — me.can_disable).
// After each action the server's sentence is shown under the row.

import { useEffect, useState } from "react";

import Badge from "../../../components/ui/Badge";
import { useI18n, type DictKey } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type AdminCatalogRow } from "../../../lib/agentMarketApi";
import { AgentTile, LoadError, Loading, Note, formatDateTime, muted, outcomeLabel, secondary, type BadgeVariant, type Msg } from "./shared";

const STATUS: Record<string, { key: DictKey; variant: BadgeVariant }> = {
  published: { key: "am.catalog.published", variant: "success" },
  unpublished: { key: "am.catalog.unpublished", variant: "neutral" },
  disabled: { key: "am.catalog.disabled", variant: "warning" },
};

export default function CatalogSection({ canDisable }: { canDisable: boolean }) {
  const { t } = useI18n();
  const [rows, setRows] = useState<AdminCatalogRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    let alive = true;
    amAdmin.catalog().then(
      (r) => {
        if (!alive) return;
        setRows(r?.agents ?? []);
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

  if (error && !rows) return <LoadError error={error} onRetry={() => setN((x) => x + 1)} />;
  if (!rows) return <Loading />;
  if (!rows.length) return <p style={secondary}>{t("am.catalog.none")}</p>;
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.75rem" }} data-testid="am-catalog">
      {rows.map((r) => (
        <CatalogRow key={r.slug} row={r} canDisable={canDisable} reload={() => setN((x) => x + 1)} />
      ))}
    </ul>
  );
}

function CatalogRow({ row, canDisable, reload }: { row: AdminCatalogRow; canDisable: boolean; reload: () => void }) {
  const { t, locale } = useI18n();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const status = row.status ?? "unpublished";
  const st = STATUS[status] ?? STATUS.unpublished;

  const act = (body: { status: string; reason?: string }) => {
    setBusy(true);
    setMsg(null);
    amAdmin.putCatalog(row.slug, body).then(
      (r) => {
        setBusy(false);
        setMsg({ text: r?.stopped_runs ? t("am.catalog.stopped", { n: r.stopped_runs }) : t("am.saved"), ok: true });
        if (body.status === "disabled") setReason("");
        reload();
      },
      (err) => {
        setBusy(false);
        setMsg({ text: amErrorMessage(err, t("am.failed")), ok: false });
      },
    );
  };
  const disable = () => {
    if (!reason.trim()) {
      setMsg({ text: t("am.catalog.reasonRequired"), ok: false });
      return;
    }
    act({ status: "disabled", reason: reason.trim() });
  };
  const sample = () => {
    setBusy(true);
    setMsg(null);
    amAdmin.remakeSample(row.slug).then(
      () => {
        setBusy(false);
        setMsg({ text: t("am.catalog.sampleQueued"), ok: true });
      },
      (err) => {
        setBusy(false);
        setMsg({ text: amErrorMessage(err, t("am.failed")), ok: false });
      },
    );
  };

  const sampleStatus = row.sample ? outcomeLabel(row.sample.outcome || row.sample.status, row.sample.reason, t) : "";

  return (
    <li className="sran-card" style={{ padding: "0.9rem 1.1rem" }} data-testid="am-catalog-row" data-slug={row.slug}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: "0.75rem" }}>
        <AgentTile icon={row.manifest?.icon} quiet={status !== "published"} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem" }}>
            <span style={{ fontWeight: 600, fontSize: "0.9rem", color: "var(--text-primary)" }}>{row.name}</span>
            <span data-testid="am-catalog-status" data-status={status}>
              <Badge variant={st.variant}>{t(st.key)}</Badge>
            </span>
          </div>
          <p style={{ ...muted, marginTop: "0.15rem" }}>
            {[row.slug, t("am.catalog.version", { v: row.version }), t("am.catalog.installs", { n: row.installs ?? 0 })].join(" · ")}
          </p>
          {row.status_reason ? <p style={{ ...secondary, marginTop: "0.15rem" }}>{t("am.catalog.reasonLine", { reason: row.status_reason })}</p> : null}
          {row.changed_by ? (
            <p style={{ ...muted, marginTop: "0.15rem" }}>{t("am.catalog.changed", { who: row.changed_by, when: formatDateTime(row.changed_at, locale) })}</p>
          ) : null}
          <p style={{ ...muted, marginTop: "0.15rem" }}>
            {row.sample ? t("am.catalog.sample", { status: sampleStatus, version: row.sample.version }) : t("am.catalog.noSample")}
          </p>
        </div>
      </div>

      <div style={{ marginTop: "0.75rem", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem" }}>
        {status === "unpublished" ? (
          <button type="button" className="sran-btn sran-btn-primary sran-btn-sm" onClick={() => act({ status: "published" })} disabled={busy} data-testid="am-publish">
            {t("am.catalog.publish")}
          </button>
        ) : null}
        {status === "published" ? (
          <button type="button" className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => act({ status: "unpublished" })} disabled={busy} data-testid="am-unpublish">
            {t("am.catalog.unpublish")}
          </button>
        ) : null}
        {canDisable && status === "disabled" ? (
          <button type="button" className="sran-btn sran-btn-primary sran-btn-sm" onClick={() => act({ status: "published" })} disabled={busy} data-testid="am-enable">
            {t("am.catalog.enable")}
          </button>
        ) : null}
        <button type="button" className="sran-btn sran-btn-secondary sran-btn-sm" onClick={sample} disabled={busy} data-testid="am-remake-sample">
          {t("am.catalog.remakeSample")}
        </button>
      </div>

      {canDisable && status !== "disabled" ? (
        <div style={{ marginTop: "0.75rem" }}>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem" }}>
            <input
              className="sran-input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("am.catalog.reasonPlaceholder")}
              aria-label={t("am.catalog.reason")}
              style={{ flex: 1, minWidth: "14rem" }}
              data-testid="am-disable-reason"
            />
            <button type="button" className="sran-btn sran-btn-danger sran-btn-sm" onClick={disable} disabled={busy} data-testid="am-disable">
              {t("am.catalog.disable")}
            </button>
          </div>
          <p style={{ ...muted, marginTop: "0.3rem" }}>{t("am.catalog.disableHint")}</p>
        </div>
      ) : null}

      {msg ? (
        <div style={{ marginTop: "0.5rem" }}>
          <Note msg={msg} />
        </div>
      ) : null}
    </li>
  );
}

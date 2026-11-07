"use client";

// FIX120 — the Fallback page: which chat models have a rescue chain and
// which do not (a number, not silence), the kill switch (takes effect on
// the next turn, no deploy), and the stored policy overrides.
//
// Reads:  GET /admin/fallback/policy, GET /admin/fallback/chains
// Writes: PUT /admin/fallback/kill-switch, PUT|DELETE /admin/fallback/overrides/...

import { useMemo, useState } from "react";
import PageHeader from "../../components/ui/PageHeader";
import Badge from "../../components/ui/Badge";
import DataTable, { Column } from "../../components/ui/DataTable";
import ConfirmModal from "../../components/ui/ConfirmModal";
import { useApi } from "../../lib/useApi";
import { api, getApiErrorMessage } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { useT } from "../../lib/i18n";

// ---------------------------------------------------------------------------
// Types — derived from backend routes/admin_fallback.py
// ---------------------------------------------------------------------------

interface Hop {
  provider: string;
  model: string;
}

interface ChainRow {
  provider: string;
  model: string;
  has_chain: boolean;
  chain: Hop[];
  source: "none" | "curated" | "tier" | "override";
}

interface ChainsResponse {
  summary: { total: number; with_chain: number; without_chain: number };
  models: ChainRow[];
  kill_switch_enabled: boolean;
}

interface StoredOverride {
  override: Record<string, unknown> | null;
  error: string | null;
  updated_at: string | null;
}

interface PolicyResponse {
  version: number;
  contract_sha256: string;
  kill_switch: { setting_key: string; enabled: boolean; stored_value: string | null; updated_at: string | null };
  limits: {
    non_stream: { max_candidates: number; attempt_timeout_s: number; total_budget_s: number | null };
    stream: { max_candidates: number; total_budget_s: number | null };
  };
  deployment_override: StoredOverride | null;
  tenant_overrides: (StoredOverride & { tenant_id: string })[];
  effective: {
    limits: PolicyResponse["limits"];
    breaker: { enabled: boolean; allowed_fails: number; window_s: number; cooldown_s: number };
    cost_cap: { enabled: boolean; max_cost_ratio_vs_requested: number | null; tenant_daily_usd: number | null };
    opt_in: { context_window_fallback: boolean; content_policy_fallback: boolean };
  };
}

const SOURCE_VARIANT: Record<ChainRow["source"], "neutral" | "info" | "warning" | "success"> = {
  none: "warning",
  curated: "info",
  tier: "neutral",
  override: "success",
};

function fmtChain(chain: Hop[]): string {
  return chain.map((h) => `${h.provider}:${h.model}`).join("  →  ");
}

export default function FallbackPage() {
  const t = useT();
  const { toast } = useToast();
  const policy = useApi<PolicyResponse>("/admin/fallback/policy");
  const chains = useApi<ChainsResponse>("/admin/fallback/chains");

  const [confirmSwitch, setConfirmSwitch] = useState<null | boolean>(null); // the value to set
  const [switching, setSwitching] = useState(false);

  const refetchAll = () => {
    policy.refetch();
    chains.refetch();
  };

  async function applySwitch(enabled: boolean) {
    setSwitching(true);
    try {
      await api.put("/admin/fallback/kill-switch", { enabled });
      toast(t("fallback.switch.saved"));
      setConfirmSwitch(null);
      refetchAll();
    } catch (err) {
      toast(getApiErrorMessage(err, t("fallback.switch.failed"), t), "error");
    } finally {
      setSwitching(false);
    }
  }

  const enabled = policy.data?.kill_switch.enabled ?? chains.data?.kill_switch_enabled ?? true;

  const columns: Column<ChainRow>[] = useMemo(
    () => [
      { key: "provider", header: t("fallback.col.provider"), searchable: true, width: "14%" },
      { key: "model", header: t("fallback.col.model"), searchable: true, width: "30%", render: (r) => <code>{r.model}</code> },
      {
        key: "chain",
        header: t("fallback.col.chain"),
        render: (r) =>
          r.has_chain ? (
            <code data-testid={`chain-${r.model}`}>{fmtChain(r.chain)}</code>
          ) : (
            <span className="sran-text-muted" data-testid={`chain-${r.model}`}>
              {t("fallback.chain.none")}
            </span>
          ),
      },
      {
        key: "source",
        header: t("fallback.col.source"),
        width: "12%",
        render: (r) => <Badge variant={SOURCE_VARIANT[r.source]}>{t(`fallback.source.${r.source}` as "fallback.source.none")}</Badge>,
      },
    ],
    [t],
  );

  const eff = policy.data?.effective;
  const lim = eff?.limits;

  return (
    <div>
      <PageHeader
        title={t("fallback.title")}
        subtitle={t("fallback.subtitle")}
        actions={
          <button type="button" className="sran-btn sran-btn-secondary" onClick={refetchAll}>
            {t("fallback.refresh")}
          </button>
        }
      />

      {/* Kill switch */}
      <section className="sran-card" style={{ marginBottom: 16 }} data-testid="kill-switch" data-enabled={String(enabled)}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Badge variant={enabled ? "success" : "error"}>{enabled ? "ON" : "OFF"}</Badge>
            <span>{enabled ? t("fallback.switch.on") : t("fallback.switch.off")}</span>
          </div>
          <button
            type="button"
            className={enabled ? "sran-btn sran-btn-danger" : "sran-btn sran-btn-primary"}
            disabled={switching || policy.loading}
            onClick={() => setConfirmSwitch(!enabled)}
            data-testid="kill-switch-toggle"
          >
            {enabled ? t("fallback.switch.turnOff") : t("fallback.switch.turnOn")}
          </button>
        </div>
      </section>

      <ConfirmModal
        open={confirmSwitch !== null}
        onClose={() => setConfirmSwitch(null)}
        onConfirm={() => confirmSwitch !== null && void applySwitch(confirmSwitch)}
        title={confirmSwitch ? t("fallback.switch.confirmOnTitle") : t("fallback.switch.confirmOffTitle")}
        message={confirmSwitch ? t("fallback.switch.confirmOn") : t("fallback.switch.confirmOff")}
        confirmLabel={confirmSwitch ? t("fallback.switch.turnOn") : t("fallback.switch.turnOff")}
        loading={switching}
      />

      {/* Coverage */}
      <section className="sran-card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }} data-testid="coverage-summary">
          <Stat label={t("fallback.summary.total")} value={chains.data?.summary.total} testId="stat-total" />
          <Stat label={t("fallback.summary.withChain")} value={chains.data?.summary.with_chain} testId="stat-with" />
          <Stat label={t("fallback.summary.withoutChain")} value={chains.data?.summary.without_chain} testId="stat-without" />
        </div>
        <p className="sran-text-muted" style={{ marginTop: 8 }}>
          {t("fallback.summary.hint")}
        </p>
      </section>

      {/* Ceilings / breaker / cost cap / opt-ins (effective at deployment level) */}
      {eff && lim ? (
        <section className="sran-card" style={{ marginBottom: 16 }} data-testid="limits">
          <h2 className="sran-section-title">{t("fallback.limits.title")}</h2>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            <li>
              {t("fallback.limits.nonStream", {
                n: lim.non_stream.max_candidates,
                t: lim.non_stream.attempt_timeout_s,
                b: lim.non_stream.total_budget_s ?? t("fallback.limits.unset"),
              })}
            </li>
            <li>
              {t("fallback.limits.stream", {
                n: lim.stream.max_candidates,
                b: lim.stream.total_budget_s === null ? t("fallback.limits.unset") : `${lim.stream.total_budget_s} s`,
              })}
            </li>
            <li>
              {eff.breaker.enabled
                ? t("fallback.breaker.on", { f: eff.breaker.allowed_fails, w: eff.breaker.window_s, c: eff.breaker.cooldown_s })
                : t("fallback.breaker.off", { c: eff.breaker.cooldown_s })}
            </li>
            <li>
              {eff.cost_cap.enabled
                ? t("fallback.cost.on", { r: String(eff.cost_cap.max_cost_ratio_vs_requested ?? "-"), d: String(eff.cost_cap.tenant_daily_usd ?? "-") })
                : t("fallback.cost.off")}
            </li>
            <li>
              {t("fallback.optin.context")}: {eff.opt_in.context_window_fallback ? t("fallback.optin.on") : t("fallback.optin.off")} ·{" "}
              {t("fallback.optin.content")}: {eff.opt_in.content_policy_fallback ? t("fallback.optin.on") : t("fallback.optin.off")}
            </li>
          </ul>
        </section>
      ) : null}

      {/* Chains */}
      <section className="sran-card" style={{ marginBottom: 16 }}>
        <h2 className="sran-section-title">{t("fallback.chains.title")}</h2>
        <DataTable<ChainRow>
          columns={columns}
          data={chains.data?.models ?? null}
          loading={chains.loading}
          error={chains.error}
          onRetry={chains.refetch}
          emptyMessage={t("fallback.chains.empty")}
          rowKey={(r) => `${r.provider}:${r.model}`}
          searchable
          searchPlaceholder={t("fallback.chains.search")}
          pageSize={25}
        />
      </section>

      {/* Overrides */}
      <section className="sran-card">
        <h2 className="sran-section-title">{t("fallback.overrides.title")}</h2>
        <p className="sran-text-muted">{t("fallback.overrides.hint")}</p>
        <OverrideEditor
          label={t("fallback.overrides.deployment")}
          stored={policy.data?.deployment_override ?? null}
          path="/admin/fallback/overrides/deployment"
          onChanged={refetchAll}
          testId="override-deployment"
        />
        <TenantOverrideEditor tenants={policy.data?.tenant_overrides ?? []} onChanged={refetchAll} />
      </section>
    </div>
  );
}

function Stat({ label, value, testId }: { label: string; value: number | undefined; testId: string }) {
  return (
    <div>
      <div className="sran-text-muted" style={{ fontSize: 12 }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 600 }} data-testid={testId}>
        {value ?? "–"}
      </div>
    </div>
  );
}

function OverrideEditor({
  label,
  stored,
  path,
  onChanged,
  testId,
}: {
  label: string;
  stored: StoredOverride | null;
  path: string;
  onChanged: () => void;
  testId: string;
}) {
  const t = useT();
  const { toast } = useToast();
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const current = stored?.override ? JSON.stringify(stored.override, null, 2) : "";
  const value = text ?? current;

  async function save() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(value || "{}");
    } catch {
      toast(t("fallback.overrides.badJson"), "error");
      return;
    }
    setBusy(true);
    try {
      await api.put(path, { override: parsed });
      toast(t("fallback.overrides.saved"));
      setText(null);
      onChanged();
    } catch (err) {
      toast(getApiErrorMessage(err, t("fallback.overrides.failed"), t), "error");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.delete(path);
      toast(t("fallback.overrides.removed"));
      setText(null);
      onChanged();
    } catch (err) {
      toast(getApiErrorMessage(err, t("fallback.overrides.failed"), t), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 12 }} data-testid={testId}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <strong>{label}</strong>
        {stored?.error ? <Badge variant="error">{t("fallback.overrides.invalid")}</Badge> : null}
        {!stored?.override && !stored?.error ? <Badge variant="neutral">{t("fallback.overrides.none")}</Badge> : null}
      </div>
      <textarea
        className="sran-input"
        style={{ width: "100%", minHeight: 96, fontFamily: "monospace", marginTop: 6 }}
        value={value}
        onChange={(e) => setText(e.target.value)}
        aria-label={label}
        spellCheck={false}
      />
      <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
        <button type="button" className="sran-btn sran-btn-primary" disabled={busy} onClick={() => void save()}>
          {t("fallback.overrides.save")}
        </button>
        <button type="button" className="sran-btn sran-btn-secondary" disabled={busy || !stored} onClick={() => void remove()}>
          {t("fallback.overrides.remove")}
        </button>
      </div>
    </div>
  );
}

function TenantOverrideEditor({ tenants, onChanged }: { tenants: (StoredOverride & { tenant_id: string })[]; onChanged: () => void }) {
  const t = useT();
  const [tenantId, setTenantId] = useState("");
  const stored = tenants.find((x) => x.tenant_id === tenantId) ?? null;
  return (
    <div style={{ marginTop: 20 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <strong>{t("fallback.overrides.tenant")}</strong>
        <input
          className="sran-input"
          placeholder={t("fallback.overrides.tenantId")}
          value={tenantId}
          onChange={(e) => setTenantId(e.target.value.trim())}
          aria-label={t("fallback.overrides.tenantId")}
          list="fallback-tenant-ids"
          style={{ minWidth: 280 }}
        />
        <datalist id="fallback-tenant-ids">
          {tenants.map((x) => (
            <option key={x.tenant_id} value={x.tenant_id} />
          ))}
        </datalist>
      </div>
      {tenantId ? (
        <OverrideEditor
          key={tenantId}
          label={`${t("fallback.overrides.tenant")} · ${tenantId}`}
          stored={stored}
          path={`/admin/fallback/overrides/tenant/${encodeURIComponent(tenantId)}`}
          onChanged={onChanged}
          testId="override-tenant"
        />
      ) : null}
    </div>
  );
}

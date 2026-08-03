"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import PageHeader from "../../components/ui/PageHeader";
import StatCard from "../../components/ui/StatCard";
import Badge from "../../components/ui/Badge";
import ErrorState from "../../components/ui/ErrorState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { api, ApiError } from "../../lib/api";
import { useT, type DictKey } from "../../lib/i18n";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Provider = "anthropic" | "openai" | "openrouter" | "custom";
// ScanMode type removed — replaced by two independent toggles

interface Settings {
  enabled: boolean;
  monitored_key_ids: string[] | null;
  scan_limit: number;
  last_scan_at: string | null;
  review_provider: Provider | null;
  review_model: string | null;
  review_api_key_set: boolean;
  review_endpoint: string | null;
  realtime_enabled: boolean;
  realtime_sample_rate: number;
}

interface Summary {
  total_findings: number;
  new_findings: number;
  new_patterns: number;
  already_covered: number;
  last_scan_at: string | null;
  enabled: boolean;
  monitored_key_ids: string[] | null;
}

interface Finding {
  id: string; request_id: string; scan_run_id: string;
  entity_type: string; description: string; sample_text: string;
  risk_level: string; is_in_coverage: boolean;
  suggested_detector: string | null;
  status: string; created_at: string | null;
}

interface ApiKeyItem {
  key_id: string; key_prefix: string; tenant_id: string;
  project_id: string; name: string; status: string;
}

interface ScanRun {
  scan_run_id: string; trigger: string; provider: string; model: string;
  requests_scanned: number; findings_count: number;
  prompt_tokens: number; completion_tokens: number;
  total_tokens: number; estimated_cost_usd: number;
  status: string; error_message: string | null; created_at: string | null;
}

interface ScanRunsResponse {
  items: ScanRun[]; total: number;
  aggregate: { total_runs: number; total_tokens: number; total_cost_usd: number };
}

interface ConnTestResult {
  success: boolean; latency_ms: number; model_used: string; error: string | null;
}

type Tab = "new_patterns" | "already_covered" | "all" | "history";

const PROVIDER_DEFAULTS: Record<Provider, string> = {
  anthropic: "claude-haiku-4-5-20251001", openai: "gpt-4o-mini",
  openrouter: "anthropic/claude-haiku-4", custom: "",
};
const PROVIDER_KEY_HINT: Record<Provider, string> = {
  anthropic: "sk-ant-...", openai: "sk-...", openrouter: "sk-or-...", custom: "api key",
};
const LS_SETTINGS_OPEN = "ai_review_settings_open";

function formatCost(n: number | null | undefined): string {
  if (n == null || n === 0) return "$0";
  return `$${n.toFixed(6)}`;
}
function readLsBool(key: string, fb: boolean): boolean {
  if (typeof window === "undefined") return fb;
  try { const v = localStorage.getItem(key); return v === null ? fb : v === "true"; } catch { return fb; }
}

// ---------------------------------------------------------------------------
// KeyPicker — reusable tenant-grouped key selector
// ---------------------------------------------------------------------------

interface KeyPickerProps {
  open: boolean;
  scope: "all" | "specific";
  selectedIds: string[];
  allKeys: ApiKeyItem[];
  tenantMap: Record<string, string>;
  onConfirm: (scope: "all" | "specific", ids: string[]) => void;
  onCancel: () => void;
  onOpen: () => void;
  onPillRemove: (id: string) => void;
}

function KeyPicker({ open, scope, selectedIds, allKeys, tenantMap, onConfirm, onCancel, onOpen, onPillRemove }: KeyPickerProps) {
  const t = useT();
  const [search, setSearch] = useState("");
  const [draftScope, setDraftScope] = useState(scope);
  const [draftIds, setDraftIds] = useState(selectedIds);

  // Sync draft when picker opens
  useEffect(() => {
    if (open) { setDraftScope(scope); setDraftIds([...selectedIds]); setSearch(""); }
  }, [open, scope, selectedIds]);

  const filtered = useMemo(() => {
    if (!search.trim()) return allKeys;
    const q = search.toLowerCase();
    return allKeys.filter((k) => k.name.toLowerCase().includes(q) || k.key_prefix.toLowerCase().includes(q));
  }, [allKeys, search]);

  const byTenant = useMemo(() => {
    const acc: Record<string, ApiKeyItem[]> = {};
    for (const k of filtered) { if (!acc[k.tenant_id]) acc[k.tenant_id] = []; acc[k.tenant_id].push(k); }
    return acc;
  }, [filtered]);

  const allIds = useMemo(() => allKeys.map((k) => k.key_id), [allKeys]);

  const keyNameMap = useMemo(() => {
    const m: Record<string, string> = {};
    for (const k of allKeys) m[k.key_id] = k.name;
    return m;
  }, [allKeys]);

  function toggleKey(kid: string, on: boolean) {
    setDraftIds(on ? [...draftIds, kid] : draftIds.filter((id) => id !== kid));
  }

  // --- Pill summary (when closed) ---
  if (!open) {
    const MAX_PILLS = 3;
    return (
      <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
        {scope === "all" ? (
          <span style={{ display: "inline-flex", alignItems: "center", padding: "2px 10px", borderRadius: "99px", background: "var(--color-status-success-bg)", color: "var(--color-status-success)", fontSize: "12px", fontWeight: 600 }}>{t("obs.air.key.all")}</span>
        ) : selectedIds.length === 0 ? (
          <span style={{ fontSize: "12px", color: "var(--color-text-muted)", fontStyle: "italic" }}>{t("obs.air.key.none")}</span>
        ) : (
          <>
            {selectedIds.slice(0, MAX_PILLS).map((id) => (
              <span key={id} style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "2px 8px", borderRadius: "99px", background: "var(--color-surface-tertiary)", border: "1px solid var(--color-border-default)", fontSize: "12px" }}>
                {keyNameMap[id] || id.slice(0, 10)}
                <button onClick={(e) => { e.stopPropagation(); onPillRemove(id); }} style={{ background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: "12px", lineHeight: 1, color: "var(--color-text-muted)" }}>&times;</button>
              </span>
            ))}
            {selectedIds.length > MAX_PILLS && (
              <span style={{ fontSize: "12px", color: "var(--color-text-muted)", fontWeight: 500 }}>{t("obs.air.key.more", { n: selectedIds.length - MAX_PILLS })}</span>
            )}
          </>
        )}
        <button onClick={(e) => { e.stopPropagation(); onOpen(); }} className="sran-btn sran-btn-secondary" style={{ padding: "2px 10px", fontSize: "11px", marginLeft: "auto" }}>
          {scope === "all" ? t("obs.air.key.change") : t("obs.air.key.edit")}
        </button>
        {scope === "specific" && selectedIds.length > 0 && (
          <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>{t("obs.air.key.selectedOf", { n: selectedIds.length, total: allKeys.length })}</span>
        )}
      </div>
    );
  }

  // --- Expanded picker (when open) ---
  return (
    <div style={{ border: "1.5px solid var(--color-status-info)", borderRadius: "var(--radius-md)", overflow: "hidden", marginTop: "6px" }}>
      {/* Scope toggle pills */}
      <div style={{ display: "flex", gap: "6px", padding: "10px 12px", borderBottom: "1px solid var(--color-border-default)" }}>
        {(["all", "specific"] as const).map((s) => {
          const active = draftScope === s;
          return (
            <button key={s} onClick={() => { setDraftScope(s); if (s === "all") setDraftIds([]); }}
              style={{ padding: "4px 14px", borderRadius: "99px", fontSize: "12px", fontWeight: active ? 600 : 400, cursor: "pointer",
                border: active ? "1.5px solid var(--color-status-info)" : "1px solid var(--color-border-default)",
                background: active ? "var(--color-status-info-bg)" : "transparent",
                color: active ? "var(--color-status-info)" : "var(--color-text-primary)" }}>
              {s === "all" ? t("obs.air.key.all") : t("obs.air.key.specific")}
            </button>
          );
        })}
      </div>

      {draftScope === "specific" && (
        <>
          {/* Search + count header */}
          <div style={{ display: "flex", alignItems: "center", borderBottom: "1px solid var(--color-border-default)" }}>
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder={t("obs.air.key.searchPlaceholder")}
              style={{ flex: 1, padding: "8px 12px", border: "none", fontSize: "13px", outline: "none", background: "var(--color-surface-primary)" }} />
            <span style={{ fontSize: "12px", color: "var(--color-text-secondary)", padding: "0 12px", whiteSpace: "nowrap" }}>{t("obs.air.key.selectedCount", { n: draftIds.length })}</span>
          </div>

          {/* Scrollable key list */}
          <div style={{ maxHeight: "240px", overflowY: "auto" }}>
            {allKeys.length === 0 ? (
              <div style={{ padding: "12px", fontSize: "12px", color: "var(--color-text-muted)" }}>{t("obs.air.key.noneActive")}</div>
            ) : filtered.length === 0 ? (
              <div style={{ padding: "12px", fontSize: "12px", color: "var(--color-text-muted)" }}>{t("obs.air.key.noMatch", { q: search })}</div>
            ) : Object.entries(byTenant).map(([tid, keys]) => {
              const tName = tenantMap[tid] || tid.slice(0, 12);
              const tIds = keys.map((k) => k.key_id);
              const allSel = tIds.every((id) => draftIds.includes(id));
              return (
                <div key={tid}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 10px", background: "var(--color-surface-secondary)", borderBottom: "1px solid var(--color-border-default)" }}>
                    <span style={{ fontSize: "12px", fontWeight: 500 }}>{tName} ({keys.length})</span>
                    <span style={{ fontSize: "11px" }}>
                      {!allSel && <button onClick={() => setDraftIds([...new Set([...draftIds, ...tIds])])} style={{ background: "none", border: "none", color: "var(--color-status-info)", cursor: "pointer", fontSize: "11px", padding: 0, marginRight: "6px" }}>{t("obs.air.key.selectAll")}</button>}
                      <button onClick={() => setDraftIds(draftIds.filter((id) => !tIds.includes(id)))} style={{ background: "none", border: "none", color: "var(--color-text-muted)", cursor: "pointer", fontSize: "11px", padding: 0 }}>{t("action.clear")}</button>
                    </span>
                  </div>
                  {keys.map((k) => (
                    <label key={k.key_id} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "6px 12px", fontSize: "13px", cursor: "pointer", borderBottom: "1px solid var(--color-border-default)" }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "var(--color-surface-secondary)"; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = ""; }}>
                      <input type="checkbox" checked={draftIds.includes(k.key_id)}
                        onChange={(e) => toggleKey(k.key_id, e.target.checked)} style={{ flexShrink: 0 }} />
                      <code style={{ fontSize: "12px", color: "var(--color-text-muted)" }}>{k.key_prefix}</code>
                      <span>{k.name}</span>
                    </label>
                  ))}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", padding: "10px 12px", borderTop: "1px solid var(--color-border-default)", background: "var(--color-surface-secondary)" }}>
        <button className="sran-btn sran-btn-secondary" onClick={onCancel} style={{ fontSize: "12px", padding: "4px 12px" }}>{t("action.cancel")}</button>
        <button className="sran-btn sran-btn-primary" onClick={() => onConfirm(draftScope, draftIds)} style={{ fontSize: "12px", padding: "4px 12px" }}>{t("obs.air.key.confirm")}</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// StatusStrip — always reads SAVED settings
// ---------------------------------------------------------------------------

function StatusStrip({ settings }: { settings: Settings }) {
  const t = useT();
  const provider = settings.review_provider ?? "anthropic";
  // `provider / model` stays verbatim — both are identifiers.
  const model = settings.review_model || PROVIDER_DEFAULTS[provider] || "default";
  const items: { label: string; variant: "success" | "error" | "warning" | "info" }[] = [
    settings.enabled ? { label: t("field.enabled"), variant: "success" } : { label: t("field.disabled"), variant: "error" },
    { label: `${provider} / ${model}`, variant: "info" },
    settings.review_api_key_set ? { label: t("obs.air.status.keySet"), variant: "success" } : { label: t("obs.air.status.noKey"), variant: "warning" },
    settings.realtime_enabled ? { label: t("obs.air.status.realtimeOn"), variant: "info" } : { label: t("obs.air.status.realtimeOff"), variant: "info" },
  ];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
      {items.map((it, i) => <Badge key={i} variant={it.variant}>{it.label}</Badge>)}
      {settings.last_scan_at && <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>{t("obs.air.lastScan", { time: new Date(settings.last_scan_at).toLocaleString() })}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ToggleSwitch — compact toggle UI
// ---------------------------------------------------------------------------

function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button onClick={() => onChange(!checked)} role="switch" aria-checked={checked}
      style={{ position: "relative", width: 36, height: 20, borderRadius: 10, border: "none", cursor: "pointer", flexShrink: 0,
        background: checked ? "var(--color-status-info)" : "var(--color-border-strong)", transition: "background 0.15s" }}>
      <span style={{ position: "absolute", top: 2, left: checked ? 18 : 2, width: 16, height: 16, borderRadius: "50%", background: "#fff", transition: "left 0.15s", boxShadow: "0 1px 2px rgba(0,0,0,0.2)" }} />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AiReviewPage() {
  const t = useT();
  const [savedSettings, setSavedSettings] = useState<Settings | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [scanRuns, setScanRuns] = useState<ScanRun[]>([]);
  const [scanAggregate, setScanAggregate] = useState<ScanRunsResponse["aggregate"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("new_patterns");
  const [settingsOpen, setSettingsOpen] = useState(() => readLsBool(LS_SETTINGS_OPEN, false));

  // Provider drafts
  const [draftProvider, setDraftProvider] = useState<Provider>("anthropic");
  const [draftModel, setDraftModel] = useState("");
  const [draftEndpoint, setDraftEndpoint] = useState("");
  const [draftKey, setDraftKey] = useState("");

  // Mode drafts — two independent toggles
  const [draftRtEnabled, setDraftRtEnabled] = useState(false);
  const [draftRtScope, setDraftRtScope] = useState<"all" | "specific">("all");
  const [draftRtIds, setDraftRtIds] = useState<string[]>([]);
  // No draftManualEnabled: manual scan is always available via "Run scan now"
  // (POST /admin/ai-review/scan); the backend has no persisted manual-enabled flag.
  const [draftManualScope, setDraftManualScope] = useState<"all" | "specific">("all");
  const [draftManualIds, setDraftManualIds] = useState<string[]>([]);
  const [draftScanLimit, setDraftScanLimit] = useState(50);

  // Picker open/close per section
  const [rtPickerOpen, setRtPickerOpen] = useState(false);
  const [manualPickerOpen, setManualPickerOpen] = useState(false);

  // Key data
  const [allKeys, setAllKeys] = useState<ApiKeyItem[]>([]);
  const [tenantMap, setTenantMap] = useState<Record<string, string>>({});

  // Misc
  const [connTesting, setConnTesting] = useState(false);
  const [connResult, setConnResult] = useState<ConnTestResult | null>(null);
  const [scanSaved, setScanSaved] = useState(false);

  // --- Helpers to sync all mode drafts from a Settings object ---
  function syncModeDrafts(st: Settings) {
    const scope: "all" | "specific" = st.monitored_key_ids === null ? "all" : "specific";
    const ids = st.monitored_key_ids ?? [];
    setDraftRtEnabled(st.realtime_enabled);
    setDraftRtScope(scope); setDraftRtIds([...ids]);
    setDraftManualScope(scope); setDraftManualIds([...ids]);
    setDraftScanLimit(st.scan_limit);
    setRtPickerOpen(false); setManualPickerOpen(false);
  }

  function toggleSettings() {
    const next = !settingsOpen;
    setSettingsOpen(next);
    try { localStorage.setItem(LS_SETTINGS_OPEN, String(next)); } catch { /* */ }
    if (next && savedSettings) {
      setDraftProvider(savedSettings.review_provider ?? "anthropic");
      setDraftModel(savedSettings.review_model ?? "");
      setDraftEndpoint(savedSettings.review_endpoint ?? "");
      setDraftKey("");
      syncModeDrafts(savedSettings);
      loadKeysAndTenants();
    }
  }

  async function loadKeysAndTenants() {
    try {
      const [kr, tr] = await Promise.all([api.get("/admin/api-keys"), api.get("/admin/tenants")]);
      const active = ((kr as { items: ApiKeyItem[] }).items ?? []).filter((k) => k.status === "active");
      setAllKeys(active);
      const m: Record<string, string> = {};
      for (const t of ((tr as { items: { tenant_id: string; name: string }[] }).items ?? [])) m[t.tenant_id] = t.name;
      setTenantMap(m);
    } catch { /* */ }
  }

  const reloadAll = useCallback(async () => {
    setError(null); setLoading(true);
    try {
      const [s, sum, f, runs] = await Promise.all([
        api.get("/admin/ai-review/settings"), api.get("/admin/ai-review/summary"),
        api.get("/admin/ai-review/findings?limit=200"), api.get("/admin/ai-review/scan-runs?limit=100"),
      ]);
      const st = s as Settings;
      setSavedSettings(st); setSettings(st);
      setSummary(sum as Summary);
      setFindings(((f as { items: Finding[] }).items) ?? []);
      const rb = runs as ScanRunsResponse;
      setScanRuns(rb.items ?? []); setScanAggregate(rb.aggregate ?? null);
      setDraftProvider(st.review_provider ?? "anthropic");
      setDraftModel(st.review_model ?? ""); setDraftEndpoint(st.review_endpoint ?? ""); setDraftKey("");
      syncModeDrafts(st);
    } catch (err) {
      setError(err instanceof ApiError && err.body?.error?.message ? err.body.error.message : t("obs.air.err.load"));
    } finally { setLoading(false); }
  }, [t]);

  useEffect(() => { reloadAll(); loadKeysAndTenants(); }, [reloadAll]);

  // --- PATCH helpers (only two triggers: enable toggle + explicit Save) ---

  async function patchSettingsImmediate(patch: Record<string, unknown>) {
    try {
      const st = (await api.patch("/admin/ai-review/settings", patch)) as Settings;
      setSettings(st); setSavedSettings(st);
      setSummary((await api.get("/admin/ai-review/summary")) as Summary);
    } catch (err) {
      setError(err instanceof ApiError && err.body?.error?.message ? err.body.error.message : t("obs.air.err.updateSettings"));
    }
  }

  async function saveProviderSettings() {
    const p: Record<string, unknown> = { review_provider: draftProvider, review_model: draftModel || null };
    if (draftProvider === "custom") p.review_endpoint = draftEndpoint || null;
    if (draftKey) p.review_api_key = draftKey;
    try {
      const st = (await api.patch("/admin/ai-review/settings", p)) as Settings;
      setSettings(st); setSavedSettings(st); setDraftKey("");
      setDraftProvider(st.review_provider ?? "anthropic");
      setDraftModel(st.review_model ?? ""); setDraftEndpoint(st.review_endpoint ?? "");
    } catch (err) {
      setError(err instanceof ApiError && err.body?.error?.message ? err.body.error.message : t("obs.air.err.saveProvider"));
    }
  }

  async function clearProviderKey() {
    try {
      const st = (await api.patch("/admin/ai-review/settings", { review_api_key: null })) as Settings;
      setSettings(st); setSavedSettings(st); setDraftKey("");
    } catch (err) {
      setError(err instanceof ApiError && err.body?.error?.message ? err.body.error.message : t("obs.air.err.clearKey"));
    }
  }

  async function saveModeSettings() {
    // Use realtime scope if rt is on, otherwise fall back to manual scope
    const activeScope = draftRtEnabled ? draftRtScope : draftManualScope;
    const activeIds = draftRtEnabled ? draftRtIds : draftManualIds;
    const payload: Record<string, unknown> = {
      realtime_enabled: draftRtEnabled,
      monitored_key_ids: activeScope === "all" ? null : activeIds,
      scan_limit: draftScanLimit,
    };
    try {
      const st = (await api.patch("/admin/ai-review/settings", payload)) as Settings;
      setSettings(st); setSavedSettings(st);
      syncModeDrafts(st);
      setScanSaved(true); setTimeout(() => setScanSaved(false), 3000);
    } catch (err) {
      setError(err instanceof ApiError && err.body?.error?.message ? err.body.error.message : t("obs.air.err.saveSettings"));
    }
  }

  function cancelModeSettings() {
    if (savedSettings) syncModeDrafts(savedSettings);
  }

  async function testConnection() {
    setConnTesting(true); setConnResult(null);
    try { setConnResult((await api.post("/admin/ai-review/test-connection", {})) as ConnTestResult); }
    catch (err) { setConnResult({ success: false, latency_ms: 0, model_used: "", error: err instanceof ApiError && err.body?.error?.message ? err.body.error.message : t("obs.air.err.connTest") }); }
    finally { setConnTesting(false); setTimeout(() => setConnResult(null), 10000); }
  }

  async function runScan() {
    setScanning(true); setError(null);
    try { await api.post("/admin/ai-review/scan", {}); await reloadAll(); }
    catch (err) { setError(err instanceof ApiError && err.body?.error?.message ? err.body.error.message : t("obs.air.err.scan")); }
    finally { setScanning(false); }
  }

  async function patchFinding(fid: string, status: string) {
    try { await api.patch(`/admin/ai-review/findings/${fid}`, { status }); await reloadAll(); }
    catch (err) { setError(err instanceof ApiError && err.body?.error?.message ? err.body.error.message : t("obs.air.err.updateFinding")); }
  }

  function handleProviderSwitch(p: Provider) {
    const old = PROVIDER_DEFAULTS[draftProvider];
    if (!draftModel || draftModel === old) setDraftModel(PROVIDER_DEFAULTS[p]);
    setDraftProvider(p);
  }

  const visibleFindings = useMemo(() => {
    if (tab === "new_patterns") return findings.filter((f) => !f.is_in_coverage);
    if (tab === "already_covered") return findings.filter((f) => f.is_in_coverage);
    if (tab === "all") return findings;
    return [];
  }, [tab, findings]);

  return (
    <>
      <PageHeader title={t("obs.air.title")} subtitle={t("obs.air.subtitle")} />

      {error && <div className="sran-card" style={{ marginBottom: "1rem" }}><ErrorState message={error} onRetry={reloadAll} /></div>}

      {/* Settings card */}
      <div className="sran-card" style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", userSelect: "none" }} onClick={toggleSettings}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {savedSettings ? <StatusStrip settings={savedSettings} /> : <span style={{ color: "var(--color-text-muted)", fontSize: "var(--font-size-xs)" }}>{t("state.loading")}</span>}
          </div>
          <button className="sran-btn sran-btn-secondary" style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem", padding: "0.375rem 0.75rem", fontSize: "var(--font-size-xs)", flexShrink: 0, marginLeft: "0.75rem" }}
            onClick={(e) => { e.stopPropagation(); toggleSettings(); }}>
            <Icon name="settings" size={12} /> {t("obs.air.settings")} {settingsOpen ? <Icon name="chevron-up" size={12} /> : <Icon name="chevron-down" size={12} />}
          </button>
        </div>

        {settingsOpen && settings && (
          <div style={{ marginTop: "1rem", borderTop: "1px solid var(--color-border-default)", paddingTop: "1rem" }}>
            {/* Enable toggle */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: "var(--font-size-base)" }}>{t("obs.air.enabledTitle")}</div>
                <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>{t("obs.air.enabledHint")}</div>
              </div>
              <label style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
                <input type="checkbox" checked={settings.enabled} disabled={loading}
                  onChange={(e) => patchSettingsImmediate({ enabled: e.target.checked })}
                  style={{ width: 18, height: 18, cursor: "pointer" }} />
                <span style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>{settings.enabled ? t("obs.air.on") : t("obs.air.off")}</span>
              </label>
            </div>

            {!settings.enabled ? (
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-muted)" }}>{t("obs.air.disabledHint")}</div>
            ) : (
              <>
                {/* Provider config */}
                <div style={{ borderTop: "1px solid var(--color-border-default)", paddingTop: "1rem", marginBottom: "1rem" }}>
                  <div style={{ fontWeight: 600, fontSize: "var(--font-size-sm)", marginBottom: "0.75rem" }}>{t("obs.air.providerConfig")}</div>
                  <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem", flexWrap: "wrap" }}>
                    {(["anthropic", "openai", "openrouter", "custom"] as Provider[]).map((p) => {
                      const a = draftProvider === p;
                      return <button key={p} onClick={() => handleProviderSwitch(p)} style={{ padding: "0.5rem 1rem", borderRadius: "var(--radius-sm)", border: `1px solid ${a ? "var(--color-status-info)" : "var(--color-border-default)"}`, background: a ? "var(--color-status-info-bg)" : "var(--color-surface-secondary)", color: a ? "var(--color-status-info)" : "var(--color-text-primary)", fontWeight: a ? 600 : 400, fontSize: "var(--font-size-xs)", cursor: "pointer", textTransform: "capitalize" }}>{p}</button>;
                    })}
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2" style={{ gap: "1rem", marginBottom: "0.75rem" }}>
                    <div>
                      <label className="sran-label">{t("field.model")}</label>
                      <input type="text" className="sran-input" value={draftModel} onChange={(e) => setDraftModel(e.target.value)} placeholder={PROVIDER_DEFAULTS[draftProvider]} />
                    </div>
                    <div>
                      <label className="sran-label">{t("obs.air.apiKey")} {settings.review_api_key_set && <span style={{ color: "var(--color-status-success)", fontSize: "var(--font-size-xs)" }}>{t("obs.air.keySetNote")}</span>}</label>
                      <div style={{ display: "flex", gap: "0.25rem" }}>
                        <input type="password" className="sran-input" value={draftKey} onChange={(e) => setDraftKey(e.target.value)}
                          placeholder={settings.review_api_key_set ? t("obs.air.keyPlaceholder") : PROVIDER_KEY_HINT[draftProvider]} />
                        {settings.review_api_key_set && <button className="sran-btn sran-btn-secondary" style={{ padding: "0 0.5rem" }} onClick={clearProviderKey}>{t("action.clear")}</button>}
                      </div>
                      <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>{t("obs.air.keyOnlyNote")}</div>
                    </div>
                  </div>
                  {draftProvider === "custom" && (
                    <div style={{ marginBottom: "0.75rem" }}>
                      <label className="sran-label">{t("obs.air.endpointUrl")}</label>
                      <input type="text" className="sran-input" value={draftEndpoint} onChange={(e) => setDraftEndpoint(e.target.value)} placeholder="https://your-llm-endpoint.com/v1/chat/completions" />
                      <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>{t("obs.air.endpointNote")}</div>
                    </div>
                  )}
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                    <button className="sran-btn sran-btn-primary" onClick={saveProviderSettings}>{t("obs.air.saveProvider")}</button>
                    <button className="sran-btn sran-btn-secondary" onClick={testConnection} disabled={connTesting}>{connTesting ? t("action.testing") : t("obs.air.testConnection")}</button>
                    {connResult && <span style={{ fontSize: "var(--font-size-xs)", fontWeight: 600, color: connResult.success ? "var(--color-status-success)" : "var(--color-status-error)" }}>{connResult.success ? t("obs.air.connOk", { ms: connResult.latency_ms, model: connResult.model_used }) : t("obs.air.connFail", { error: connResult.error ?? "" })}</span>}
                  </div>
                </div>

                {/* Scan mode — two independent toggles in one card */}
                <div style={{ borderTop: "1px solid var(--color-border-default)", paddingTop: "1rem" }}>
                  <div style={{ fontWeight: 600, fontSize: "var(--font-size-sm)", marginBottom: "0.75rem" }}>{t("obs.air.scanMode")}</div>

                  <div style={{ border: "1px solid var(--color-border-default)", borderRadius: "var(--radius-md)", overflow: "hidden", marginBottom: "1rem" }}>
                    {/* Real-time monitoring */}
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "12px 16px" }}>
                        <div style={{ flex: 1, opacity: draftRtEnabled ? 1 : 0.5 }}>
                          <div style={{ fontWeight: 600, fontSize: "13px" }}>{t("obs.air.realtimeTitle")}</div>
                          <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>{t("obs.air.realtimeHint")}</div>
                        </div>
                        <span style={{ fontSize: "10px", padding: "2px 8px", borderRadius: "99px", background: "var(--color-status-success-bg)", color: "var(--color-status-success)", fontWeight: 600, opacity: draftRtEnabled ? 1 : 0.5 }}>{t("obs.air.live")}</span>
                        <ToggleSwitch checked={draftRtEnabled} onChange={setDraftRtEnabled} />
                      </div>
                      {draftRtEnabled && (
                        <div style={{ borderTop: "1px solid var(--color-border-default)", padding: "12px 16px", background: "var(--color-surface-secondary)" }}>
                          <label className="sran-label" style={{ marginBottom: "6px", display: "block" }}>{t("obs.air.monitorKeys")}</label>
                          <KeyPicker open={rtPickerOpen} scope={draftRtScope} selectedIds={draftRtIds} allKeys={allKeys} tenantMap={tenantMap}
                            onOpen={() => setRtPickerOpen(true)} onCancel={() => setRtPickerOpen(false)}
                            onConfirm={(s, ids) => { setDraftRtScope(s); setDraftRtIds(ids); setRtPickerOpen(false); }}
                            onPillRemove={(id) => { setDraftRtIds(draftRtIds.filter((i) => i !== id)); }} />
                        </div>
                      )}
                    </div>

                    {/* Divider */}
                    <div style={{ borderTop: "1px solid var(--color-border-default)" }} />

                    {/* Manual scan */}
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "12px 16px" }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontWeight: 600, fontSize: "13px" }}>{t("obs.air.manualTitle")}</div>
                          <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>{t("obs.air.manualHint")}</div>
                        </div>
                        {/* No toggle: manual scan is always available via "Run scan now"
                            below, so the scope/limit config is always shown. */}
                        <span style={{ fontSize: "10px", padding: "2px 8px", borderRadius: "99px", background: "var(--color-status-info-bg)", color: "var(--color-status-info)", fontWeight: 600 }}>{t("obs.air.onDemand")}</span>
                      </div>
                        <div style={{ borderTop: "1px solid var(--color-border-default)", padding: "12px 16px", background: "var(--color-surface-secondary)" }}>
                          <label className="sran-label" style={{ marginBottom: "6px", display: "block" }}>{t("obs.air.scanScope")}</label>
                          <KeyPicker open={manualPickerOpen} scope={draftManualScope} selectedIds={draftManualIds} allKeys={allKeys} tenantMap={tenantMap}
                            onOpen={() => setManualPickerOpen(true)} onCancel={() => setManualPickerOpen(false)}
                            onConfirm={(s, ids) => { setDraftManualScope(s); setDraftManualIds(ids); setManualPickerOpen(false); }}
                            onPillRemove={(id) => { setDraftManualIds(draftManualIds.filter((i) => i !== id)); }} />
                          <div style={{ marginTop: "0.75rem" }}>
                            <label className="sran-label">{t("obs.air.scanLimit")}</label>
                            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                              <input type="number" min={1} max={200} className="sran-input" style={{ width: "5rem" }} value={draftScanLimit}
                                onChange={(e) => { const n = parseInt(e.target.value, 10); if (!isNaN(n)) setDraftScanLimit(Math.max(1, Math.min(200, n))); }} />
                              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>{t("obs.air.perRun")}</span>
                            </div>
                          </div>
                        </div>
                    </div>
                  </div>

                  {/* Save / Cancel row */}
                  <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
                    <button className="sran-btn sran-btn-secondary" onClick={cancelModeSettings}>{t("action.cancel")}</button>
                    <button className="sran-btn sran-btn-primary" onClick={saveModeSettings}>{t("obs.air.saveSettings")}</button>
                    {scanSaved && <span style={{ fontSize: "var(--font-size-xs)", fontWeight: 600, color: "var(--color-status-success)" }}>{t("state.saved")} &#10003;</span>}
                    <button className="sran-btn sran-btn-primary" onClick={runScan} disabled={scanning || loading} style={{ marginLeft: "auto" }}>
                      {scanning ? t("obs.air.scanning") : t("obs.air.runScan")}
                    </button>
                    <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
                      {t("obs.air.lastScan", {
                        time: settings.last_scan_at ? new Date(settings.last_scan_at).toLocaleString() : t("obs.air.never"),
                      })}
                    </span>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Summary stat cards */}
      {summary && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "1rem", marginBottom: "1.5rem" }}>
          <StatCard label={t("obs.air.stat.newPatterns")} value={summary.new_patterns} trend={summary.new_patterns > 0 ? "up" : undefined} subtitle={t("obs.air.stat.newPatternsSub")} />
          <StatCard label={t("obs.air.stat.totalFindings")} value={summary.total_findings} subtitle={t("obs.air.stat.totalFindingsSub")} />
          <StatCard label={t("obs.air.stat.covered")} value={summary.already_covered} subtitle={t("obs.air.stat.coveredSub")} />
          <StatCard label={t("obs.air.stat.unreviewed")} value={summary.new_findings} subtitle={t("obs.air.stat.unreviewedSub")} />
        </div>
      )}

      {/* Findings + scan history */}
      <div className="sran-card" style={{ padding: 0 }}>
        <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--color-border-default)" }}>
          {([["new_patterns", "obs.air.tab.newPatterns"], ["already_covered", "obs.air.tab.covered"], ["all", "obs.air.tab.all"], ["history", "obs.air.tab.history"]] as [Tab, DictKey][]).map(([key, label]) => {
            const a = tab === key;
            return <button key={key} onClick={() => setTab(key)} style={{ padding: "0.75rem 1.25rem", background: "none", border: "none", borderBottom: a ? "2px solid var(--color-status-info)" : "2px solid transparent", fontWeight: a ? 600 : 400, fontSize: "var(--font-size-sm)", color: a ? "var(--color-text-primary)" : "var(--color-text-muted)", cursor: "pointer" }}>{t(label)}</button>;
          })}
        </div>

        {tab === "history" ? (
          <div>
            {scanRuns.length === 0 ? <div style={{ padding: "2rem", textAlign: "center", color: "var(--color-text-muted)" }}>{t("obs.air.noRuns")}</div> : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", fontSize: "var(--font-size-xs)", borderCollapse: "collapse" }}>
                  <thead><tr>{([
                    "obs.air.col.time",
                    "obs.air.col.trigger",
                    "field.provider",
                    "field.model",
                    "obs.air.col.requestsScanned",
                    "obs.air.col.findings",
                    "obs.air.col.tokens",
                    "obs.air.col.cost",
                    "field.status",
                  ] as DictKey[]).map((h) => <th key={h} style={{ textAlign: "left", padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)", fontWeight: 600 }}>{t(h)}</th>)}</tr></thead>
                  <tbody>{scanRuns.map((r) => (
                    <tr key={r.scan_run_id}>
                      <td className="air-td">{r.created_at ? new Date(r.created_at).toLocaleString() : "—"}</td>
                      <td className="air-td"><Badge variant={r.trigger === "realtime" ? "warning" : "info"}>{r.trigger}</Badge></td>
                      <td className="air-td">{r.provider}</td>
                      <td className="air-td"><code>{r.model}</code></td>
                      <td className="air-td">{r.requests_scanned}</td>
                      <td className="air-td">{r.findings_count}</td>
                      <td className="air-td">{r.total_tokens.toLocaleString()}</td>
                      <td className="air-td">{formatCost(r.estimated_cost_usd)}</td>
                      <td className="air-td"><Badge variant={r.status === "completed" ? "success" : r.status === "failed" ? "error" : "warning"}>{r.status}</Badge></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            {scanAggregate && <div style={{ padding: "0.75rem 1rem", borderTop: "1px solid var(--color-border-default)", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>{t("obs.air.totalCostLabel")} <strong>{formatCost(scanAggregate.total_cost_usd)}</strong> {t("obs.air.totalCostSuffix", { runs: scanAggregate.total_runs, tokens: scanAggregate.total_tokens.toLocaleString() })}</div>}
          </div>
        ) : loading ? (
          <div style={{ padding: "0.5rem 0.75rem" }}>
            <SkeletonTable rows={6} cols={7} />
          </div>
        ) : visibleFindings.length === 0 ? (
          <div style={{ padding: "2rem", textAlign: "center", color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)" }}>{t("obs.air.noFindingsTab")} {settings?.enabled ? t("obs.air.runScanHint") : t("obs.air.enableFirst")}</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", fontSize: "var(--font-size-xs)", borderCollapse: "collapse" }}>
              <thead><tr>{([
                "obs.air.col.request",
                "obs.air.col.entityType",
                "obs.air.col.risk",
                "obs.air.col.sample",
                "obs.air.col.suggestedDetector",
                "field.status",
                "field.actions",
              ] as DictKey[]).map((h) => <th key={h} style={{ textAlign: "left", padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)", fontWeight: 600 }}>{t(h)}</th>)}</tr></thead>
              <tbody>{visibleFindings.map((f) => (
                <tr key={f.id}>
                  <td className="air-td"><code>{f.request_id.slice(0, 16)}…</code></td>
                  <td className="air-td">{f.entity_type}</td>
                  <td className="air-td"><Badge variant={f.risk_level === "HIGH" ? "error" : f.risk_level === "MEDIUM" ? "warning" : "info"}>{f.risk_level}</Badge></td>
                  <td className="air-td" style={{ maxWidth: "18rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}><code>{f.sample_text}</code></td>
                  <td className="air-td">{f.suggested_detector ? <code>{f.suggested_detector}</code> : "—"}</td>
                  <td className="air-td"><Badge variant={f.status === "dismissed" ? "info" : f.status === "added_to_dlp" ? "success" : f.status === "reviewed" ? "warning" : "info"}>{f.status}</Badge></td>
                  <td className="air-td">
                    <div style={{ display: "flex", gap: "0.25rem", flexWrap: "wrap" }}>
                      <button className="sran-btn sran-btn-secondary" style={{ padding: "0.25rem 0.5rem", fontSize: "var(--font-size-xs)" }} onClick={() => patchFinding(f.id, "reviewed")}>{t("obs.air.act.markReviewed")}</button>
                      <button className="sran-btn sran-btn-secondary" style={{ padding: "0.25rem 0.5rem", fontSize: "var(--font-size-xs)" }} onClick={() => patchFinding(f.id, "added_to_dlp")}>{t("obs.air.act.addToDlp")}</button>
                      <button className="sran-btn sran-btn-secondary" style={{ padding: "0.25rem 0.5rem", fontSize: "var(--font-size-xs)" }} onClick={() => patchFinding(f.id, "dismissed")}>{t("obs.air.act.dismiss")}</button>
                    </div>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

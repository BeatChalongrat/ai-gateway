"use client";

import { useEffect, useMemo, useState } from "react";

import ErrorState from "../../components/ui/ErrorState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { api, ApiError } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import { useT, type DictKey } from "../../lib/i18n";
import { ThemeToggle } from "../../components/ThemeToggle";

type Tab = "general" | "trial" | "security";

// The tier values are backend identifiers; only their LABEL is localized.
const TIER_KEYS: Record<string, DictKey> = {
  budget: "padmin.settings.tierBudget",
  standard: "padmin.settings.tierStandard",
  premium: "padmin.settings.tierPremium",
};

type SettingKey =
  | "free_trial_tokens"
  | "trial_model_tier"
  | "portal_signup_enabled"
  | "credit_low_threshold"
  | "auto_default_model"
  | "auto_default_fallbacks"
  | "auto_routing_tier_default";

interface Setting {
  key: string;
  value: string;
  description: string | null;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Lucide-style SVG icons
// ---------------------------------------------------------------------------

function IconCoins() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="6" />
      <path d="M18.09 10.37A6 6 0 1 1 10.34 18" />
      <path d="M7 6h1v4" />
      <path d="m16.71 13.88.7.71-2.82 2.82" />
    </svg>
  );
}

function IconLayers() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 2 7 12 12 22 7 12 2" />
      <polyline points="2 17 12 22 22 17" />
      <polyline points="2 12 12 17 22 12" />
    </svg>
  );
}

function IconAlertTriangle() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

function IconShield() {
  return (
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function IconLoader() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ animation: "psPin 1s linear infinite" }}>
      <line x1="12" y1="2" x2="12" y2="6" />
      <line x1="12" y1="18" x2="12" y2="22" />
      <line x1="4.93" y1="4.93" x2="7.76" y2="7.76" />
      <line x1="16.24" y1="16.24" x2="19.07" y2="19.07" />
      <line x1="2" y1="12" x2="6" y2="12" />
      <line x1="18" y1="12" x2="22" y2="12" />
      <line x1="4.93" y1="19.07" x2="7.76" y2="16.24" />
      <line x1="16.24" y1="7.76" x2="19.07" y2="4.93" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Scoped styles
// ---------------------------------------------------------------------------

const STYLES = `
@keyframes psFadeUp { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
@keyframes psFadeIn { from{opacity:0} to{opacity:1} }
@keyframes psPin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
.ps-stat { animation: psFadeUp 0.4s ease-out both; }
.ps-tab-content { animation: psFadeIn 0.25s ease-out both; }
.ps-metric-card {
  transition: box-shadow 150ms ease, transform 150ms ease;
}
.ps-metric-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}
.ps-setting-card {
  transition: box-shadow 150ms ease;
}
.ps-setting-card:hover {
  box-shadow: var(--shadow-md);
}
`;

// ---------------------------------------------------------------------------
// Stat card (matches Models page pattern exactly)
// ---------------------------------------------------------------------------

function StatCard({ value, label, icon, accent, bg, delay = 0 }: {
  value: string; label: string; icon: React.ReactNode; accent: string; bg: string; delay?: number;
}) {
  return (
    <div className="ps-stat ps-metric-card" style={{
      animationDelay: `${delay}ms`,
      background: "var(--bg-surface)", borderRadius: 10,
      border: "1px solid var(--border-default)",
      padding: "1rem 1.125rem",
      display: "flex", alignItems: "center", gap: "0.875rem",
      flex: 1, minWidth: 0,
    }}>
      <div style={{
        width: 42, height: 42, borderRadius: 12, flexShrink: 0,
        background: bg,
        display: "flex", alignItems: "center", justifyContent: "center",
        color: accent,
      }}>{icon}</div>
      <div>
        <div style={{
          fontSize: "1.625rem", fontWeight: 700, color: "var(--text-primary)",
          lineHeight: 1, letterSpacing: "-0.03em",
          fontVariantNumeric: "tabular-nums",
        }}>{value}</div>
        <div style={{
          fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)",
          marginTop: "0.25rem", lineHeight: 1,
        }}>{label}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PortalSettingsPage() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("general");
  const { data, loading, error, refetch } = useApi<Setting[]>("/admin/system-settings");
  const settings = data ?? [];

  const byKey = useMemo(() => {
    const m: Partial<Record<SettingKey, Setting>> = {};
    for (const s of settings) {
      m[s.key as SettingKey] = s;
    }
    return m;
  }, [settings]);

  const freeTrialTokens = byKey.free_trial_tokens?.value ?? "150000";
  const trialTier = byKey.trial_model_tier?.value ?? "budget";
  const lowThreshold = byKey.credit_low_threshold?.value ?? "10000";

  return (
    <div style={{ padding: "2rem 2.5rem", maxWidth: 1000, margin: "0 auto" }}>
      <style>{STYLES}</style>

      {/* Page header */}
      <div className="sran-page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 className="sran-page-title">{t("padmin.settings.title")}</h1>
          <p className="sran-page-subtitle">
            {t("padmin.settings.subtitle")}
          </p>
        </div>
        <ThemeToggle />
      </div>

      {/* Metric cards */}
      <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1.25rem", flexWrap: "wrap" }}>
        <StatCard
          icon={<IconCoins />}
          accent="#4f46e5"
          bg="rgba(99,102,241,0.1)"
          value={loading ? "—" : Number(freeTrialTokens).toLocaleString()}
          label={t("padmin.settings.statTokensPerSignup")}
          delay={0}
        />
        <StatCard
          icon={<IconLayers />}
          accent="#059669"
          bg="rgba(5,150,105,0.1)"
          value={
            loading
              ? "—"
              : TIER_KEYS[trialTier]
              ? t(TIER_KEYS[trialTier])
              : trialTier.charAt(0).toUpperCase() + trialTier.slice(1)
          }
          label={t("padmin.settings.statMaxTier")}
          delay={80}
        />
        <StatCard
          icon={<IconAlertTriangle />}
          accent="#d97706"
          bg="rgba(217,119,6,0.1)"
          value={loading ? "—" : Number(lowThreshold).toLocaleString()}
          label={t("padmin.settings.statWarnThreshold")}
          delay={160}
        />
      </div>

      {/* Main card with tabs */}
      <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
        {/* Tab bar */}
        <div style={{
          display: "flex",
          borderBottom: "1px solid var(--border-default)",
          background: "var(--bg-surface-2)",
        }}>
          {([
            { k: "general" as Tab, label: t("padmin.settings.tabGeneral") },
            { k: "trial" as Tab, label: t("padmin.settings.tabTrial") },
            { k: "security" as Tab, label: t("padmin.settings.tabSecurity") },
          ]).map((t) => (
            <button
              key={t.k}
              onClick={() => setTab(t.k)}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: "12px 20px",
                fontSize: "0.8125rem",
                fontFamily: "inherit",
                fontWeight: tab === t.k ? 600 : 500,
                color: tab === t.k ? "var(--accent-primary)" : "var(--text-tertiary)",
                borderBottom: tab === t.k ? "2px solid var(--accent-primary)" : "2px solid transparent",
                marginBottom: "-1px",
                transition: "color 120ms, border-color 120ms",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div style={{ padding: "1.5rem" }}>
          {loading ? (
            <SkeletonTable rows={3} />
          ) : error ? (
            <ErrorState message={error} onRetry={refetch} />
          ) : (
            <div key={tab} className="ps-tab-content">
              {tab === "general" && (
                <GeneralTab
                  setting={byKey.portal_signup_enabled}
                  onSaved={refetch}
                />
              )}
              {tab === "trial" && (
                <>
                  <TrialTab
                    freeTrial={byKey.free_trial_tokens}
                    tier={byKey.trial_model_tier}
                    threshold={byKey.credit_low_threshold}
                    onSaved={refetch}
                  />
                  <AutoRoutingSection
                    defaultModel={byKey.auto_default_model}
                    fallbacks={byKey.auto_default_fallbacks}
                    tierDefault={byKey.auto_routing_tier_default}
                    onSaved={refetch}
                  />
                </>
              )}
              {tab === "security" && <SecurityTab />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

async function patchSetting(key: SettingKey, value: string): Promise<void> {
  await api.patch(`/admin/system-settings/${key}`, { value });
}

function useSaveError() {
  const t = useT();
  return (e: unknown): string => {
    if (e instanceof ApiError) {
      // The backend message is passed through verbatim — it is operator
      // diagnostics, not UI copy.
      return e.body?.error?.message || t("padmin.saveFailedStatus", { status: e.status });
    }
    return t("padmin.saveFailed");
  };
}

// ---------------------------------------------------------------------------
// Tab: General
// ---------------------------------------------------------------------------

function GeneralTab({
  setting,
  onSaved,
}: {
  setting?: Setting;
  onSaved: () => void;
}) {
  const t = useT();
  const { toast } = useToast();
  const formatError = useSaveError();
  const current = (setting?.value || "true").toLowerCase() === "true";
  const [enabled, setEnabled] = useState(current);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setEnabled(current);
  }, [current]);

  async function toggle(next: boolean) {
    const prev = enabled;
    setEnabled(next);
    setSaving(true);
    try {
      await patchSetting("portal_signup_enabled", next ? "true" : "false");
      toast(next ? t("padmin.settings.signupsEnabled") : t("padmin.settings.signupsDisabled"));
      onSaved();
    } catch (e) {
      setEnabled(prev);
      toast(formatError(e), "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <h2 style={{ fontSize: "1.0625rem", fontWeight: 600, margin: "0 0 1rem", color: "var(--text-primary)" }}>
        {t("padmin.settings.registrationHeading")}
      </h2>
      <div className="ps-setting-card" style={{
        display: "flex", alignItems: "center", gap: "1rem",
        padding: "1rem 1.25rem",
        border: "1px solid var(--border-default)",
        borderRadius: 10, background: "var(--bg-surface)",
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: "0.875rem", fontWeight: 600, color: "var(--text-primary)", marginBottom: 2 }}>
            {t("padmin.settings.allowSignups")}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", marginBottom: 8 }}>
            {t("padmin.settings.allowSignupsDesc")}
          </div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: "0.375rem" }}>
            <span style={{
              width: 7, height: 7, borderRadius: "50%",
              background: enabled ? "var(--accent-success)" : "var(--accent-danger)",
            }} />
            <span style={{
              fontSize: "0.6875rem", fontWeight: 600,
              color: enabled ? "var(--accent-success)" : "var(--accent-danger)",
            }}>
              {enabled ? t("field.active") : t("field.disabled")}
            </span>
          </div>
        </div>
        <Toggle checked={enabled} disabled={saving} onChange={toggle} />
      </div>

      {/* Info card */}
      <div style={{
        marginTop: "1rem", padding: "0.875rem 1rem",
        border: "1px solid var(--border-subtle)", borderRadius: 8,
        background: "var(--bg-surface-2)",
        fontSize: "0.75rem", color: "var(--text-tertiary)", lineHeight: 1.5,
      }}>
        <div style={{ fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>
          {t("padmin.settings.aboutRegistration")}
        </div>
        {t("padmin.settings.aboutRegistrationBody")}
      </div>
    </div>
  );
}

function Toggle({
  checked,
  disabled,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      style={{
        width: 44,
        height: 24,
        borderRadius: 12,
        border: "none",
        background: checked ? "var(--accent-success)" : "var(--bg-elevated)",
        position: "relative",
        cursor: disabled ? "wait" : "pointer",
        transition: "background 150ms",
        flexShrink: 0,
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 2,
          left: checked ? 22 : 2,
          width: 20,
          height: 20,
          borderRadius: "50%",
          background: "white",
          boxShadow: "0 1px 3px rgba(0,0,0,0.18)",
          transition: "left 150ms",
        }}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Tab: Trial & Credits
// ---------------------------------------------------------------------------

function TrialTab({
  freeTrial,
  tier,
  threshold,
  onSaved,
}: {
  freeTrial?: Setting;
  tier?: Setting;
  threshold?: Setting;
  onSaved: () => void;
}) {
  const t = useT();
  const { toast } = useToast();
  const formatError = useSaveError();

  const [tokens, setTokens] = useState(freeTrial?.value ?? "100000");
  const [tierValue, setTierValue] = useState<string>(tier?.value ?? "budget");
  const [lowThreshold, setLowThreshold] = useState(threshold?.value ?? "10000");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setTokens(freeTrial?.value ?? "100000");
    setTierValue(tier?.value ?? "budget");
    setLowThreshold(threshold?.value ?? "10000");
  }, [freeTrial?.value, tier?.value, threshold?.value]);

  const dirty =
    tokens !== (freeTrial?.value ?? "") ||
    tierValue !== (tier?.value ?? "") ||
    lowThreshold !== (threshold?.value ?? "");

  async function save() {
    setSaving(true);
    let failed: string | null = null;
    try {
      if (tokens !== freeTrial?.value) {
        await patchSetting("free_trial_tokens", tokens);
      }
      if (tierValue !== tier?.value) {
        await patchSetting("trial_model_tier", tierValue);
      }
      if (lowThreshold !== threshold?.value) {
        await patchSetting("credit_low_threshold", lowThreshold);
      }
    } catch (e) {
      failed = formatError(e);
    }
    setSaving(false);
    if (failed) {
      toast(failed, "error");
    } else {
      toast(t("padmin.settings.settingsSaved"));
    }
    onSaved();
  }

  return (
    <div style={{ maxWidth: 640, display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      <h2 style={{ fontSize: "1.0625rem", fontWeight: 600, margin: 0, color: "var(--text-primary)" }}>
        {t("padmin.settings.trialHeading")}
      </h2>

      {/* Free trial tokens */}
      <div className="ps-setting-card" style={{
        padding: "1rem 1.25rem",
        border: "1px solid var(--border-default)",
        borderRadius: 10, background: "var(--bg-surface)",
      }}>
        <label style={{
          display: "block", fontSize: "0.8125rem", fontWeight: 600,
          color: "var(--text-primary)", marginBottom: 4,
        }}>
          {t("padmin.settings.freeTrialTokens")}
        </label>
        <div style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", marginBottom: 10 }}>
          {t("padmin.settings.freeTrialTokensDesc")}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            display: "flex", alignItems: "center",
            border: "1px solid var(--border-default)", borderRadius: 8,
            overflow: "hidden", background: "var(--bg-base)",
            transition: "border-color 150ms",
          }}>
            <span style={{
              padding: "0.5rem 0.625rem",
              background: "var(--bg-surface-2)",
              borderRight: "1px solid var(--border-default)",
              color: "var(--text-tertiary)",
              display: "flex", alignItems: "center",
            }}>
              <IconCoins />
            </span>
            <input
              type="number"
              min={1}
              value={tokens}
              onChange={(e) => setTokens(e.target.value)}
              style={{
                border: "none", outline: "none", background: "transparent",
                padding: "0.5rem 0.75rem", fontSize: "0.875rem", fontWeight: 500,
                width: 160, color: "var(--text-primary)",
                fontVariantNumeric: "tabular-nums",
              }}
            />
            <span style={{
              padding: "0.5rem 0.75rem", fontSize: "0.75rem",
              color: "var(--text-tertiary)", fontWeight: 500,
            }}>{t("padmin.tokensUnit")}</span>
          </div>
        </div>
      </div>

      {/* Trial model tier */}
      <div className="ps-setting-card" style={{
        padding: "1rem 1.25rem",
        border: "1px solid var(--border-default)",
        borderRadius: 10, background: "var(--bg-surface)",
      }}>
        <label style={{
          display: "block", fontSize: "0.8125rem", fontWeight: 600,
          color: "var(--text-primary)", marginBottom: 4,
        }}>
          {t("padmin.settings.trialTier")}
        </label>
        <div style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", marginBottom: 10 }}>
          {t("padmin.settings.trialTierDesc")}
        </div>
        <div style={{ display: "inline-flex", borderRadius: 8, overflow: "hidden", border: "1px solid var(--border-default)" }}>
          {(["budget", "standard", "premium"] as const).map((opt) => {
            const sel = tierValue === opt;
            return (
              <button
                key={opt}
                type="button"
                onClick={() => setTierValue(opt)}
                style={{
                  padding: "0.4375rem 1rem",
                  border: "none",
                  borderRight: opt !== "premium" ? "1px solid var(--border-default)" : "none",
                  background: sel ? "var(--accent-primary)" : "var(--bg-surface)",
                  color: sel ? "#ffffff" : "var(--text-secondary)",
                  fontSize: "0.8125rem",
                  fontWeight: sel ? 600 : 500,
                  cursor: "pointer",
                  transition: "background 150ms, color 150ms",
                  fontFamily: "inherit",
                }}
              >
                {t(TIER_KEYS[opt])}
              </button>
            );
          })}
        </div>
      </div>

      {/* Low credit threshold */}
      <div className="ps-setting-card" style={{
        padding: "1rem 1.25rem",
        border: "1px solid var(--border-default)",
        borderRadius: 10, background: "var(--bg-surface)",
      }}>
        <label style={{
          display: "block", fontSize: "0.8125rem", fontWeight: 600,
          color: "var(--text-primary)", marginBottom: 4,
        }}>
          {t("padmin.settings.lowThreshold")}
        </label>
        <div style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", marginBottom: 10 }}>
          {t("padmin.settings.lowThresholdDesc")}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            display: "flex", alignItems: "center",
            border: "1px solid var(--border-default)", borderRadius: 8,
            overflow: "hidden", background: "var(--bg-base)",
            transition: "border-color 150ms",
          }}>
            <span style={{
              padding: "0.5rem 0.625rem",
              background: "var(--bg-surface-2)",
              borderRight: "1px solid var(--border-default)",
              color: "var(--text-tertiary)",
              display: "flex", alignItems: "center",
            }}>
              <IconAlertTriangle />
            </span>
            <input
              type="number"
              min={1}
              value={lowThreshold}
              onChange={(e) => setLowThreshold(e.target.value)}
              style={{
                border: "none", outline: "none", background: "transparent",
                padding: "0.5rem 0.75rem", fontSize: "0.875rem", fontWeight: 500,
                width: 160, color: "var(--text-primary)",
                fontVariantNumeric: "tabular-nums",
              }}
            />
            <span style={{
              padding: "0.5rem 0.75rem", fontSize: "0.75rem",
              color: "var(--text-tertiary)", fontWeight: 500,
            }}>{t("padmin.tokensUnit")}</span>
          </div>
        </div>
      </div>

      {/* Save button area */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.875rem", marginTop: "0.25rem" }}>
        <button
          onClick={save}
          disabled={!dirty || saving}
          style={{
            display: "inline-flex", alignItems: "center", gap: "0.5rem",
            padding: "0.625rem 1.5rem",
            fontSize: "0.8125rem", fontWeight: 600, fontFamily: "inherit",
            borderRadius: 8, border: "none", cursor: dirty && !saving ? "pointer" : "not-allowed",
            background: dirty ? "var(--accent-primary)" : "var(--bg-elevated)",
            color: dirty ? "#ffffff" : "var(--text-muted)",
            transition: "background 150ms, opacity 150ms",
            opacity: dirty ? 1 : 0.6,
          }}
        >
          {saving && <IconLoader />}
          {saving ? t("action.saving") : t("padmin.saveChanges")}
        </button>
        {dirty && !saving && (
          <span style={{
            fontSize: "0.6875rem", fontWeight: 500, color: "var(--accent-warning)",
            display: "inline-flex", alignItems: "center", gap: "0.25rem",
          }}>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--accent-warning)" }} />
            {t("padmin.settings.unsavedChanges")}
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Auto model routing
//
// "auto" used to resolve to whatever was cheapest and capable, which in
// this catalog is a budget model — and a budget model handed the memory
// tool set calls it on turns that are not memory requests. The default
// picked here is what Auto reaches for first; the fallback chain covers
// the case where its provider key isn't usable.
// ---------------------------------------------------------------------------

interface ToolModelRow {
  model: string;
  provider: string;
  supports_tools: boolean;
  cost_tier: string;
}

// FIX36 Part B — the five router cost bands, cheapest first. A ceiling,
// not a forced band: Auto still picks the cheapest adequate model below it.
const ROUTING_TIERS = ["low", "medium", "high", "xhigh", "max"] as const;

function AutoRoutingSection({
  defaultModel,
  fallbacks,
  tierDefault,
  onSaved,
}: {
  defaultModel?: Setting;
  fallbacks?: Setting;
  tierDefault?: Setting;
  onSaved: () => void;
}) {
  const t = useT();
  const { toast } = useToast();
  const formatError = useSaveError();
  const { data: modelData, loading: modelsLoading } = useApi<{ items: ToolModelRow[] }>(
    "/admin/models?is_active=true&limit=500",
  );

  const [model, setModel] = useState(defaultModel?.value ?? "");
  const [chain, setChain] = useState(fallbacks?.value ?? "");
  const [tier, setTier] = useState(tierDefault?.value ?? "high");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setModel(defaultModel?.value ?? "");
    setChain(fallbacks?.value ?? "");
    setTier(tierDefault?.value ?? "high");
  }, [defaultModel?.value, fallbacks?.value, tierDefault?.value]);

  // Only tool-capable models may be elected — the backend validator
  // enforces the same rule, this just keeps invalid picks off the menu.
  const options = useMemo(() => {
    const rows = (modelData?.items ?? []).filter((m) => m.supports_tools);
    rows.sort((a, b) => a.model.localeCompare(b.model));
    return rows;
  }, [modelData]);

  const dirty =
    model !== (defaultModel?.value ?? "") ||
    chain !== (fallbacks?.value ?? "") ||
    tier !== (tierDefault?.value ?? "high");

  async function save() {
    setSaving(true);
    let failed: string | null = null;
    try {
      if (model !== defaultModel?.value) {
        await patchSetting("auto_default_model", model);
      }
      if (chain !== fallbacks?.value) {
        await patchSetting("auto_default_fallbacks", chain);
      }
      if (tier !== (tierDefault?.value ?? "high")) {
        await patchSetting("auto_routing_tier_default", tier);
      }
    } catch (e) {
      failed = formatError(e);
    }
    setSaving(false);
    if (failed) toast(failed, "error");
    else toast(t("padmin.settings.settingsSaved"));
    onSaved();
  }

  const cardStyle: React.CSSProperties = {
    padding: "1rem 1.25rem",
    border: "1px solid var(--border-default)",
    borderRadius: 10,
    background: "var(--bg-surface)",
  };
  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "0.8125rem",
    fontWeight: 600,
    color: "var(--text-primary)",
    marginBottom: 4,
  };
  const descStyle: React.CSSProperties = {
    fontSize: "0.75rem",
    color: "var(--text-tertiary)",
    marginBottom: 10,
  };
  const fieldStyle: React.CSSProperties = {
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    background: "var(--bg-base)",
    padding: "0.5rem 0.75rem",
    fontSize: "0.875rem",
    fontFamily: "inherit",
    color: "var(--text-primary)",
    width: "100%",
    maxWidth: 340,
  };

  return (
    <div style={{ maxWidth: 640, display: "flex", flexDirection: "column", gap: "1.25rem", marginTop: "2rem" }}>
      <h2 style={{ fontSize: "1.0625rem", fontWeight: 600, margin: 0, color: "var(--text-primary)" }}>
        {t("padmin.settings.autoHeading")}
      </h2>

      <div className="ps-setting-card" style={cardStyle}>
        <label htmlFor="auto-default-model" style={labelStyle}>
          {t("padmin.settings.autoDefaultModel")}
        </label>
        <div style={descStyle}>{t("padmin.settings.autoDefaultModelDesc")}</div>
        <select
          id="auto-default-model"
          value={model}
          disabled={modelsLoading}
          onChange={(e) => setModel(e.target.value)}
          style={fieldStyle}
        >
          {modelsLoading && <option value="">{t("padmin.settings.autoLoadingModels")}</option>}
          {/* The saved value may sit outside the list (row deactivated
              since it was chosen) — keep it selectable so opening the
              page doesn't silently rewrite the setting. */}
          {!modelsLoading && model && !options.some((o) => o.model === model) && (
            <option value={model}>{model}</option>
          )}
          {options.map((o) => (
            <option key={o.model} value={o.model}>
              {o.model} — {o.provider} ({o.cost_tier})
            </option>
          ))}
        </select>
      </div>

      <div className="ps-setting-card" style={cardStyle}>
        <label htmlFor="auto-fallbacks" style={labelStyle}>
          {t("padmin.settings.autoFallbacks")}
        </label>
        <div style={descStyle}>{t("padmin.settings.autoFallbacksDesc")}</div>
        <input
          id="auto-fallbacks"
          type="text"
          value={chain}
          onChange={(e) => setChain(e.target.value)}
          placeholder="gpt-4.1,gpt-4o-mini"
          style={fieldStyle}
        />
      </div>

      <div className="ps-setting-card" style={cardStyle}>
        <label htmlFor="auto-routing-tier" style={labelStyle}>
          {t("padmin.settings.autoTierDefault")}
        </label>
        <div style={descStyle}>{t("padmin.settings.autoTierDefaultDesc")}</div>
        <select
          id="auto-routing-tier"
          data-testid="auto-routing-tier-default"
          value={tier}
          onChange={(e) => setTier(e.target.value)}
          style={fieldStyle}
        >
          {ROUTING_TIERS.map((v) => (
            <option key={v} value={v}>
              {t(`padmin.settings.autoTier.${v}` as DictKey)}
            </option>
          ))}
        </select>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "0.875rem", marginTop: "0.25rem" }}>
        <button
          onClick={save}
          disabled={!dirty || saving}
          style={{
            display: "inline-flex", alignItems: "center", gap: "0.5rem",
            padding: "0.625rem 1.5rem",
            fontSize: "0.8125rem", fontWeight: 600, fontFamily: "inherit",
            borderRadius: 8, border: "none", cursor: dirty && !saving ? "pointer" : "not-allowed",
            background: dirty ? "var(--accent-primary)" : "var(--bg-elevated)",
            color: dirty ? "#ffffff" : "var(--text-muted)",
            transition: "background 150ms, opacity 150ms",
            opacity: dirty ? 1 : 0.6,
          }}
        >
          {saving && <IconLoader />}
          {saving ? t("action.saving") : t("padmin.saveChanges")}
        </button>
        {dirty && !saving && (
          <span style={{
            fontSize: "0.6875rem", fontWeight: 500, color: "var(--accent-warning)",
            display: "inline-flex", alignItems: "center", gap: "0.25rem",
          }}>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--accent-warning)" }} />
            {t("padmin.settings.unsavedChanges")}
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Security (future)
// ---------------------------------------------------------------------------

function SecurityTab() {
  const t = useT();
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      textAlign: "center", minHeight: 220, padding: "3rem 1.5rem",
      border: "2px dashed var(--border-default)", borderRadius: 12,
      background: "var(--bg-surface-2)",
    }}>
      <div style={{ color: "var(--text-muted)", marginBottom: "0.875rem", opacity: 0.45 }}>
        <IconShield />
      </div>
      <div style={{
        fontSize: "1.0625rem", fontWeight: 600, color: "var(--text-secondary)",
        marginBottom: "0.375rem",
      }}>
        {t("padmin.settings.comingSoon")}
      </div>
      <div style={{ fontSize: "0.8125rem", color: "var(--text-tertiary)", maxWidth: 320 }}>
        {t("padmin.settings.securitySoonBody")}
      </div>
    </div>
  );
}

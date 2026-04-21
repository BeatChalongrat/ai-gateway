"use client";

import { useEffect, useMemo, useState } from "react";

import ErrorState from "../../components/ui/ErrorState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { api, ApiError } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import { ThemeToggle } from "../../components/ThemeToggle";

type Tab = "general" | "trial" | "security";

type SettingKey =
  | "free_trial_tokens"
  | "trial_model_tier"
  | "portal_signup_enabled"
  | "credit_low_threshold";

interface Setting {
  key: string;
  value: string;
  description: string | null;
  updated_at: string;
}

const C = {
  accent: "#3b6cf6",
  borderLight: "#e8eaef",
  textTertiary: "#6b7280",
};

export default function PortalSettingsPage() {
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

  return (
    <div style={{ padding: "2rem 2.5rem", maxWidth: 1000, margin: "0 auto" }}>
      <div className="sran-page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 className="sran-page-title">Portal Settings</h1>
          <p className="sran-page-subtitle">
            Configure portal behavior, free trial, and default values.
          </p>
        </div>
        <ThemeToggle />
      </div>

      <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
        {/* Tabs */}
        <div
          style={{
            display: "flex",
            borderBottom: `1px solid ${C.borderLight}`,
            background: "var(--bg-surface-2)",
          }}
        >
          {[
            { k: "general" as Tab, label: "General" },
            { k: "trial" as Tab, label: "Trial & Credits" },
            { k: "security" as Tab, label: "Security" },
          ].map((t) => (
            <button
              key={t.k}
              onClick={() => setTab(t.k)}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: "12px 20px",
                fontSize: 13,
                fontFamily: "inherit",
                fontWeight: tab === t.k ? 600 : 500,
                color: tab === t.k ? C.accent : C.textTertiary,
                borderBottom:
                  tab === t.k ? `2px solid ${C.accent}` : "2px solid transparent",
                marginBottom: "-1px",
                transition: "color 120ms, border-color 120ms",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div style={{ padding: 24 }}>
          {loading ? (
            <SkeletonTable rows={3} />
          ) : error ? (
            <ErrorState message={error} onRetry={refetch} />
          ) : (
            <>
              {tab === "general" && (
                <GeneralTab
                  setting={byKey.portal_signup_enabled}
                  onSaved={refetch}
                />
              )}
              {tab === "trial" && (
                <TrialTab
                  freeTrial={byKey.free_trial_tokens}
                  tier={byKey.trial_model_tier}
                  threshold={byKey.credit_low_threshold}
                  onSaved={refetch}
                />
              )}
              {tab === "security" && <SecurityTab />}
            </>
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
  return (e: unknown): string => {
    if (e instanceof ApiError) {
      return e.body?.error?.message || `Save failed (${e.status})`;
    }
    return "Save failed";
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
  const { toast } = useToast();
  const formatError = useSaveError();
  const current = (setting?.value || "true").toLowerCase() === "true";
  const [enabled, setEnabled] = useState(current);
  const [saving, setSaving] = useState(false);

  // Keep in sync with refetched data.
  useEffect(() => {
    setEnabled(current);
  }, [current]);

  async function toggle(next: boolean) {
    const prev = enabled;
    setEnabled(next);
    setSaving(true);
    try {
      await patchSetting("portal_signup_enabled", next ? "true" : "false");
      toast(next ? "Portal signups enabled" : "Portal signups disabled");
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
      <h2 style={{ fontSize: 17, fontWeight: 600, margin: 0, marginBottom: 14, fontFamily: "var(--font-outfit)" }}>
        Portal Registration
      </h2>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "14px 16px",
          border: `1px solid ${C.borderLight}`,
          borderRadius: 10,
          background: "var(--bg-surface)",
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
            Allow new user signups
          </div>
          <div style={{ fontSize: 12, color: C.textTertiary, marginTop: 2 }}>
            When disabled, the signup page shows a "Registration is currently closed" message.
          </div>
        </div>
        <Toggle checked={enabled} disabled={saving} onChange={toggle} />
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
        background: checked ? "#10b981" : "#cbd5e1",
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
    // Patch each changed key in sequence so a single validation error
    // doesn't clobber the other fields that were fine.
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
      toast("Settings saved");
    }
    onSaved();
  }

  return (
    <div style={{ maxWidth: 640, display: "flex", flexDirection: "column", gap: 20 }}>
      <h2 style={{ fontSize: 17, fontWeight: 600, margin: 0, fontFamily: "var(--font-outfit)" }}>
        Free Trial Configuration
      </h2>

      <Field
        label="Free trial tokens"
        hint="Tokens granted to each new user on signup."
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="number"
            min={1}
            className="sran-input"
            style={{ width: 220 }}
            value={tokens}
            onChange={(e) => setTokens(e.target.value)}
          />
          <span style={{ fontSize: 12, color: C.textTertiary }}>tokens</span>
        </div>
      </Field>

      <Field
        label="Trial model tier"
        hint="Maximum model tier available to trial users."
      >
        <div style={{ display: "flex", gap: 8 }}>
          {(["budget", "standard", "premium"] as const).map((t) => {
            const sel = tierValue === t;
            return (
              <button
                key={t}
                type="button"
                onClick={() => setTierValue(t)}
                style={{
                  padding: "6px 14px",
                  borderRadius: 20,
                  border: sel ? "1px solid #1e3a5f" : `1px solid ${C.borderLight}`,
                  background: sel ? "#1e3a5f" : "white",
                  color: sel ? "white" : "#374151",
                  fontSize: 12,
                  fontWeight: 500,
                  cursor: "pointer",
                  textTransform: "capitalize",
                }}
              >
                {t}
              </button>
            );
          })}
        </div>
      </Field>

      <Field
        label="Low credit warning threshold"
        hint="Warn user in the portal when their balance drops below this value."
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="number"
            min={1}
            className="sran-input"
            style={{ width: 220 }}
            value={lowThreshold}
            onChange={(e) => setLowThreshold(e.target.value)}
          />
          <span style={{ fontSize: 12, color: C.textTertiary }}>tokens</span>
        </div>
      </Field>

      <div>
        <button
          className="sran-btn sran-btn-primary"
          onClick={save}
          disabled={!dirty || saving}
        >
          {saving ? "Saving…" : "Save Changes"}
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label
        className="sran-label"
        style={{ display: "block", marginBottom: 4 }}
      >
        {label}
      </label>
      {hint ? (
        <div style={{ fontSize: 12, color: C.textTertiary, marginBottom: 8 }}>{hint}</div>
      ) : null}
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Security (future)
// ---------------------------------------------------------------------------

function SecurityTab() {
  return (
    <div
      style={{
        padding: "40px 20px",
        textAlign: "center",
        color: C.textTertiary,
      }}
    >
      <div style={{ fontSize: 32, marginBottom: 8 }}>🔒</div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>
        Coming soon
      </div>
      <div style={{ fontSize: 12 }}>
        Rate limits, IP allowlist, and session controls will live here.
      </div>
    </div>
  );
}

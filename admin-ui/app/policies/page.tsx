"use client";

import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { Outfit } from "next/font/google";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonCards, SkeletonTable } from "../../components/ui/Skeleton";
import { useApi } from "../../lib/useApi";
import { api, ApiError } from "../../lib/api";
import { ThemeToggle } from "../../components/ThemeToggle";
import AgentMarketPointer from "../../components/AgentMarketPointer";
import { useT, type DictKey } from "../../lib/i18n";
import { DLP_CATEGORY, codeWord, profileName } from "../../lib/codeWords";

const outfit = Outfit({ subsets: ["latin"], weight: ["400", "500", "600", "700", "800"] });

type Action = "allow" | "mask" | "block";
type SaveState = "idle" | "saving" | "saved" | "error";

const CATEGORIES: string[] = ["network", "identity", "financial", "pii", "credential", "secret"];
const ACTIONS: Action[] = ["allow", "mask", "block"];

interface Detector { name: string; category: string; severity: string; enabled: boolean; is_ner: boolean; }
interface Profile {
  id: string;
  profile_id: string;
  name: string;
  category_actions: Record<string, Action>;
  // Null on profiles that inherit inbound for outbound (e.g. STRICT).
  // Missing categories inside the dict also inherit, rendered as "(inherited)".
  category_actions_output: Record<string, Action> | null;
}
interface NerStatus { status: "not_loaded" | "loading" | "ready" | "error"; message?: string; ner_enabled: boolean; }
type Tab = "profiles" | "detectors" | "ner";

/* ── Design tokens ────────────────────────────────────────────────── */

const C = {
  bg: "var(--bg-base)", card: "var(--bg-surface)",
  borderLight: "var(--border-default)", borderMed: "var(--border-default)",
  textPrimary: "var(--text-primary)", textSecondary: "var(--text-secondary)",
  textHeader: "var(--text-secondary)", textTertiary: "var(--text-muted)",
  accent: "var(--accent-primary)",
  mono: "'JetBrains Mono', 'Fira Code', monospace",
};

/* ── Category pill palette ────────────────────────────────────────── */

const CAT_PILL: Record<string, { color: string; bg: string }> = {
  network:    { color: "#2563eb", bg: "rgba(59,130,246,0.08)" },
  identity:   { color: "#7c3aed", bg: "rgba(139,92,246,0.08)" },
  financial:  { color: "#059669", bg: "rgba(16,185,129,0.08)" },
  pii:        { color: "#b45309", bg: "rgba(245,158,11,0.08)" },
  credential: { color: "#dc2626", bg: "rgba(239,68,68,0.08)" },
  secret:     { color: "#4b5563", bg: "rgba(100,116,139,0.08)" },
  ner:        { color: "#0d9488", bg: "rgba(20,184,166,0.08)" },
};

function CatPill({ category }: { category: string }) {
  const t = useT();
  const s = CAT_PILL[category] || { color: C.textSecondary, bg: "rgba(100,116,139,0.08)" };
  return (
    <span style={{
      display: "inline-block", padding: "2px 10px",
      fontSize: "10.5px", fontWeight: 700, color: s.color, background: s.bg,
      borderRadius: 100, textTransform: "uppercase", letterSpacing: "0.04em", lineHeight: 1.6,
    }}>{codeWord(DLP_CATEGORY, category, t)}</span>
  );
}

/* ── Severity badge ───────────────────────────────────────────────── */

const SEV: Record<string, { color: string; bg: string }> = {
  critical: { color: "#dc2626", bg: "rgba(239,68,68,0.08)" },
  high:     { color: "#dc2626", bg: "rgba(239,68,68,0.08)" },
  medium:   { color: "#d97706", bg: "rgba(245,158,11,0.08)" },
  low:      { color: "#059669", bg: "rgba(16,185,129,0.08)" },
};

// Severity is a human-facing level, so it is localized; an unrecognized
// value from the API falls through to the raw string.
const SEV_LABEL: Record<string, DictKey> = {
  critical: "infra.severity.critical",
  high: "infra.severity.high",
  medium: "infra.severity.medium",
  low: "infra.severity.low",
};

function SevBadge({ severity }: { severity: string }) {
  const t = useT();
  const s = SEV[severity] || SEV.medium;
  return (
    <span style={{
      display: "inline-block", padding: "2px 10px",
      fontSize: "10.5px", fontWeight: 700, color: s.color, background: s.bg,
      borderRadius: 100, textTransform: "capitalize", lineHeight: 1.6,
    }}>{SEV_LABEL[severity] ? t(SEV_LABEL[severity]) : severity}</span>
  );
}

/* ── Profile accent ───────────────────────────────────────────────── */

const PROF: Record<string, { color: string; bg: string; border: string }> = {
  default: { color: C.accent, bg: `rgba(59,108,246,0.07)`, border: C.accent },
  strict:  { color: "#f59e0b", bg: "rgba(245,158,11,0.07)", border: "#f59e0b" },
};
function profAccent(id: string) { return PROF[id] || PROF.default; }

/* ── Stat card ────────────────────────────────────────────────────── */

function StatCard({ value, label, icon, accent, delay = 0, iconClass }: {
  value: string | number; label: string; icon: React.ReactNode; accent: string; delay?: number; iconClass?: string;
}) {
  return (
    <div className="dlp-stat" style={{
      animationDelay: `${delay}ms`,
      background: C.card, borderRadius: 14, border: `1px solid ${C.borderLight}`,
      boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
      padding: "14px 16px",
      display: "flex", alignItems: "center", gap: "12px",
      flex: 1, minWidth: 0,
    }}>
      <div className={iconClass} style={{
        width: 40, height: 40, borderRadius: 10, flexShrink: 0,
        background: `${accent}14`, display: "flex", alignItems: "center", justifyContent: "center",
        color: accent,
      }}>{icon}</div>
      <div>
        <div style={{
          fontFamily: C.mono, fontSize: "24px", fontWeight: 700, color: C.textPrimary,
          lineHeight: 1, letterSpacing: "-0.02em",
        }}>{value}</div>
        <div style={{
          fontSize: "12px", fontWeight: 500, color: C.textTertiary,
          marginTop: "3px", lineHeight: 1,
        }}>{label}</div>
      </div>
    </div>
  );
}

/* ── Toggle switch (40×22) ────────────────────────────────────────── */

function Toggle({ checked, disabled, onChange }: {
  checked: boolean; disabled?: boolean; onChange?: (v: boolean) => void;
}) {
  return (
    <button
      onClick={() => !disabled && onChange?.(!checked)}
      disabled={disabled}
      style={{
        width: 40, height: 22, borderRadius: 11, padding: 2,
        background: checked ? C.accent : "var(--bg-elevated)",
        border: "none", cursor: disabled ? "not-allowed" : "pointer",
        display: "flex", alignItems: "center",
        transition: "background 0.25s ease",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <span style={{
        width: 18, height: 18, borderRadius: "50%", background: "white",
        boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
        transform: checked ? "translateX(18px)" : "translateX(0)",
        transition: "transform 0.25s ease",
      }} />
    </button>
  );
}

/* ── Action select ────────────────────────────────────────────────── */

function ActionSel({ value, onChange, disabled }: {
  value: Action; onChange: (a: Action) => void; disabled?: boolean;
}) {
  return (
    <select className="dlp-select" value={value} disabled={disabled}
      onChange={(e) => onChange(e.target.value as Action)}
    >
      {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
    </select>
  );
}

/* ── Save indicator ───────────────────────────────────────────────── */

function SaveDot({ state }: { state: SaveState }) {
  const t = useT();
  if (state === "idle") return <span style={{ color: "#d1d5db" }}>—</span>;
  if (state === "saving") return <span style={{ color: C.textTertiary, fontSize: "12px" }}>{t("action.saving")}</span>;
  if (state === "saved") return <span style={{ color: "#059669", fontSize: "12px", fontWeight: 600 }}><Icon name="check" size={12} /> {t("state.saved")}</span>;
  return <span style={{ color: "#dc2626", fontSize: "12px", fontWeight: 600 }}>{t("infra.policies.saveError")}</span>;
}

/* ═══════════════════════════════════════════════════════════════════ */
/* PAGE                                                                */
/* ═══════════════════════════════════════════════════════════════════ */

const iconShield = <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>;
const iconFileCheck = <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M9 15l2 2 4-4"/></svg>;
const iconTag = <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>;

export default function PoliciesPage() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("profiles");
  const { data: detData } = useApi<{ items: Detector[] }>("/admin/dlp/detectors");
  const { data: profData } = useApi<{ items: Profile[] }>("/admin/policy-profiles");

  const detectorCount = detData?.items?.length ?? 0;
  const profileCount = profData?.items?.length ?? 0;
  const categoryCount = useMemo(() => {
    if (!detData?.items) return 0;
    return new Set(detData.items.map((d) => d.category)).size;
  }, [detData]);

  const tabs: { key: Tab; label: DictKey }[] = [
    { key: "profiles", label: "infra.policies.profiles" },
    { key: "detectors", label: "infra.policies.detectors" },
    { key: "ner", label: "infra.policies.ner" },
  ];

  return (
    <div className={outfit.className} style={{ background: C.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      {/* Header */}
      <div style={{ marginBottom: "20px", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 style={{ fontSize: "30px", fontWeight: 800, color: C.textPrimary, letterSpacing: "-0.03em", lineHeight: 1.2, margin: 0 }}>
            {t("infra.policies.title")}
          </h1>
          <p style={{ fontSize: "15px", fontWeight: 500, color: C.textSecondary, margin: "4px 0 0", lineHeight: 1.4 }}>
            {t("infra.policies.subtitle")}
          </p>
        </div>
        <ThemeToggle />
      </div>
      <AgentMarketPointer page="policies" />

      {/* Stats */}
      {(detectorCount > 0 || profileCount > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-3" style={{ gap: "12px", marginBottom: "20px" }}>
          <StatCard value={detectorCount} label={t("infra.dlp.totalDetectors")} icon={iconShield} accent="#3b6cf6" delay={0} iconClass="dlp-icon-pulse" />
          <StatCard value={profileCount} label={t("infra.policies.profiles")} icon={iconFileCheck} accent="#10b981" delay={80} iconClass="dlp-icon-swing" />
          <StatCard value={categoryCount} label={t("infra.policies.categories")} icon={iconTag} accent="#8b5cf6" delay={160} iconClass="dlp-icon-glow" />
        </div>
      )}

      {/* Main card */}
      <div style={{
        background: C.card, borderRadius: 14,
        border: `1px solid ${C.borderLight}`,
        boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
        overflow: "hidden",
      }}>
        {/* Tabs */}
        <div style={{ display: "flex", borderBottom: `1px solid ${C.borderLight}`, background: "var(--bg-surface-2)" }}>
          {tabs.map((item) => (
            <button key={item.key} onClick={() => setTab(item.key)} style={{
              background: "none", border: "none", cursor: "pointer",
              padding: "12px 20px", fontSize: "13px", fontFamily: "inherit",
              fontWeight: tab === item.key ? 600 : 500,
              color: tab === item.key ? C.accent : C.textTertiary,
              borderBottom: tab === item.key ? `2px solid ${C.accent}` : "2px solid transparent",
              marginBottom: "-1px", transition: "color 120ms, border-color 120ms",
            }}>{t(item.label)}</button>
          ))}
        </div>

        {/* Content */}
        <div className="dlp-tab-content" key={tab}>
          {tab === "profiles" && <ProfilesTab />}
          {tab === "detectors" && <DetectorsTab />}
          {tab === "ner" && <NerTab />}
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
/* PROFILES TAB                                                        */
/* ═══════════════════════════════════════════════════════════════════ */

function ProfilesTab() {
  const t = useT();
  const { data, loading, error, refetch } = useApi<{ items: Profile[] }>("/admin/policy-profiles");

  if (loading) return <div style={{ padding: 20 }}><SkeletonCards count={2} /></div>;
  if (error) return <div style={{ padding: 20 }}><ErrorState message={error} onRetry={refetch} /></div>;
  if (!data?.items.length) return <div style={{ padding: 20 }}><EmptyState message={t("infra.policies.noProfiles")} icon={<Icon name="shield" size={22} />} /></div>;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: "20px", padding: "20px" }}>
      {data.items.map((p, i) => <ProfCard key={p.profile_id} profile={p} idx={i} />)}
    </div>
  );
}

function ProfCard({ profile, idx }: { profile: Profile; idx: number }) {
  const t = useT();
  const [actions, setActions] = useState<Record<string, Action>>(profile.category_actions);
  const [saveState, setSaveState] = useState<Record<string, SaveState>>({});
  const a = profAccent(profile.profile_id);

  const onChange = useCallback(async (cat: string, act: Action) => {
    setActions((prev) => ({ ...prev, [cat]: act }));
    setSaveState((prev) => ({ ...prev, [cat]: "saving" }));
    try {
      const res = await api.patch(`/admin/policy-profiles/${profile.profile_id}/category-actions`, { category: cat, action: act });
      setActions(res.category_actions);
      setSaveState((prev) => ({ ...prev, [cat]: "saved" }));
      setTimeout(() => setSaveState((prev) => ({ ...prev, [cat]: "idle" })), 1500);
    } catch {
      setSaveState((prev) => ({ ...prev, [cat]: "error" }));
      setActions((prev) => ({ ...prev, [cat]: profile.category_actions[cat] }));
    }
  }, [profile.profile_id, profile.category_actions]);

  // Outbound column is read-only for now — no PATCH endpoint exists for
  // category_actions_output. The evaluator's contract (see
  // services/policy/evaluator.py _decide) is:
  //   - category_actions_output === null → outbound truly inherits the
  //     inbound action for every category.
  //   - category_actions_output === {...} and cat ∈ dict → explicit value.
  //   - category_actions_output === {...} and cat ∉ dict → falls through
  //     to ALLOW (the _decide "no_matching_rule" branch).
  // Only the first case renders "(inherited)".
  const outboundOverrides = profile.category_actions_output;
  const outboundFor = (cat: string): { value: Action; inherited: boolean } => {
    if (outboundOverrides && cat in outboundOverrides) {
      return { value: outboundOverrides[cat], inherited: false };
    }
    if (!outboundOverrides) {
      return { value: (actions[cat] ?? "allow") as Action, inherited: true };
    }
    return { value: "allow", inherited: false };
  };

  return (
    <div className="dlp-card-anim" style={{
      animationDelay: `${0.1 + idx * 0.08}s`,
      background: C.card, borderRadius: 14,
      border: `1px solid ${C.borderLight}`,
      borderTop: `3px solid ${a.border}`,
      overflow: "hidden",
      transition: "box-shadow 150ms",
    }}
      onMouseEnter={(e) => { e.currentTarget.style.boxShadow = "0 4px 12px rgba(0,0,0,0.06)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.boxShadow = "none"; }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 16px", borderBottom: "1px solid var(--border-default)" }}>
        <h3 style={{ fontSize: "18px", fontWeight: 700, margin: 0, color: C.textPrimary }}>{profileName(profile, t)}</h3>
        <span style={{
          display: "inline-block", padding: "2px 10px",
          fontSize: "11px", fontWeight: 700, color: a.color, background: a.bg,
          borderRadius: 100, lineHeight: 1.6,
        }}>{profile.profile_id}</span>
      </div>
      <table className="dlp-table">
        <thead><tr>
          <th>{t("infra.policies.colCategory")}</th>
          <th style={{ width: 130 }}>{t("infra.policies.colInbound")}</th>
          <th style={{ width: 150 }}>{t("infra.policies.colOutbound")}</th>
          <th style={{ width: 80, textAlign: "center" }}>{t("field.status")}</th>
        </tr></thead>
        <tbody>
          {CATEGORIES.map((cat, i) => {
            const ob = outboundFor(cat);
            return (
              <tr key={cat} className="dlp-row" style={{ animationDelay: `${0.25 + i * 0.03}s` }}>
                <td><CatPill category={cat} /></td>
                <td><ActionSel value={actions[cat] ?? "allow"} onChange={(act) => onChange(cat, act)} /></td>
                <td>
                  <OutboundCell value={ob.value} inherited={ob.inherited} />
                </td>
                <td style={{ textAlign: "center" }}><SaveDot state={saveState[cat] ?? "idle"} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function OutboundCell({ value, inherited }: { value: Action; inherited: boolean }) {
  const t = useT();
  const pillBg =
    value === "block" ? "rgba(220,38,38,0.08)"
    : value === "mask" ? "rgba(59,108,246,0.08)"
    : "rgba(16,185,129,0.08)";
  const pillColor =
    value === "block" ? "#dc2626"
    : value === "mask" ? "#3b6cf6"
    : "#059669";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{
        display: "inline-block", padding: "3px 10px",
        fontSize: 12, fontWeight: 600, color: pillColor, background: pillBg,
        borderRadius: 6, textTransform: "lowercase", letterSpacing: "0.02em",
        fontFamily: C.mono,
      }}>{value}</span>
      {inherited && (
        <span
          title={t("infra.policies.inheritedTitle")}
          style={{ fontSize: 11, fontWeight: 500, color: C.textTertiary }}
        >{t("infra.policies.inherited")}</span>
      )}
    </span>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
/* DETECTORS TAB                                                       */
/* ═══════════════════════════════════════════════════════════════════ */

function DetectorsTab() {
  const t = useT();
  const { data: detectorData, loading: dL, error: dE, refetch: dR } = useApi<{ items: Detector[] }>("/admin/dlp/detectors");
  const { data: profileData, loading: pL } = useApi<{ items: Profile[] }>("/admin/policy-profiles");

  const [search, setSearch] = useState("");

  const defaultProfile = profileData?.items.find((p) => p.profile_id === "default") ?? null;

  if (dL || pL) return <div style={{ padding: 20 }}><SkeletonTable rows={8} cols={6} /></div>;
  if (dE) return <div style={{ padding: 20 }}><ErrorState message={dE} onRetry={dR} /></div>;
  if (!detectorData?.items.length) return <div style={{ padding: 20 }}><EmptyState message={t("infra.policies.noDetectors")} icon={<Icon name="shield" size={22} />} /></div>;

  // Detectors tab is a read-only view of the built-in detector registry.
  // The "Default Action" column shows the action inherited from the default
  // policy profile; it is edited on the Policy Profiles tab, not here.
  const actionFor = (det: Detector): Action =>
    (defaultProfile?.category_actions[det.category] as Action) ?? "allow";

  const filtered = search
    ? detectorData.items.filter((d) => d.name.toLowerCase().includes(search.toLowerCase()) || d.category.toLowerCase().includes(search.toLowerCase()) || codeWord(DLP_CATEGORY, d.category, t).toLowerCase().includes(search.toLowerCase()))
    : detectorData.items;

  return (
    <>
      {/* Toolbar */}
      <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--border-default)", display: "flex", alignItems: "center", gap: "8px" }}>
        <div style={{ position: "relative", flex: "0 1 240px", minWidth: 140 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={C.textTertiary} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("infra.policies.filterDetectors")}
            style={{
              width: "100%", padding: "5px 24px 5px 28px", fontSize: "13px", fontFamily: "inherit",
              border: `1.5px solid ${C.borderLight}`, borderRadius: 6,
              background: "var(--bg-base)", color: C.textPrimary, outline: "none",
              transition: "border-color 120ms, box-shadow 120ms, background 120ms",
            }}
            onFocus={(e) => { e.currentTarget.style.borderColor = C.accent; e.currentTarget.style.boxShadow = `0 0 0 3px rgba(99,102,241,0.18)`; e.currentTarget.style.background = "var(--bg-surface)"; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = C.borderLight; e.currentTarget.style.boxShadow = "none"; e.currentTarget.style.background = "var(--bg-base)"; }}
          />
          {search && <button onClick={() => setSearch("")} style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: C.textTertiary, fontSize: 12, padding: 2 }}><Icon name="x" size={12} /></button>}
        </div>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: "12px", fontWeight: 500, color: C.textTertiary, fontVariantNumeric: "tabular-nums" }}>
          {filtered.length === 1
            ? t("infra.dlp.detectorCountOne")
            : t("infra.dlp.detectorCount", { n: filtered.length })}
        </span>
      </div>

      {/* Table */}
      <div style={{ overflowX: "auto" }}>
        <table className="dlp-table" style={{ minWidth: 740 }}>
          <thead><tr>
            <th style={{ paddingLeft: 18 }}>{t("infra.policies.colDetector")}</th>
            <th>{t("infra.policies.colCategory")}</th><th>{t("infra.policies.colSeverity")}</th>
            <th style={{ textAlign: "center", width: 72 }}>{t("field.enabled")}</th>
            <th style={{ width: 150 }}>{t("infra.policies.colDefaultAction")} <span style={{ fontSize: 11, fontWeight: 400, color: C.textTertiary, textTransform: "none", letterSpacing: 0 }}>{t("infra.policies.readOnly")}</span></th>
          </tr></thead>
          <tbody>
            {filtered.map((det, i) => {
              const isNer = det.is_ner || det.category === "ner";
              return (
                <tr key={det.name} className="dlp-row" style={{ animationDelay: `${0.05 + i * 0.03}s` }}>
                  <td style={{ paddingLeft: 18 }}>
                    <span style={{ fontFamily: C.mono, fontSize: "13px", fontWeight: 600, color: C.textPrimary, letterSpacing: "-0.01em" }}>{det.name}</span>
                    {isNer && <span style={{ marginLeft: 6, fontSize: "9px", fontWeight: 700, color: C.textTertiary, background: "var(--bg-elevated)", padding: "1px 5px", borderRadius: 3, textTransform: "uppercase", letterSpacing: "0.03em" }}>NER</span>}
                  </td>
                  <td><CatPill category={det.category} /></td>
                  <td><SevBadge severity={det.severity} /></td>
                  <td style={{ textAlign: "center" }}>
                    {/* Built-in detector availability is set via server config
                        (settings.dlp_disabled_detectors / DLP_DISABLED_DETECTORS),
                        not editable per-detector here. Render a read-only status
                        indicator instead of an interactive switch so the control
                        doesn't imply it can be toggled from the UI. */}
                    <span
                      title={t("infra.policies.enabledTitle")}
                      style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, fontWeight: 600, color: det.enabled ? "var(--accent-success)" : C.textTertiary }}
                    >
                      <span aria-hidden style={{ width: 6, height: 6, borderRadius: "50%", background: det.enabled ? "var(--accent-success)" : C.textTertiary }} />
                      {det.enabled ? t("field.enabled") : t("field.disabled")}
                    </span>
                  </td>
                  <td><span title={t("infra.policies.defaultActionTitle")} style={{ display: "inline-block" }}><ActionSel value={actionFor(det)} onChange={() => {}} disabled /></span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
/* NER TAB                                                             */
/* ═══════════════════════════════════════════════════════════════════ */

function NerTab() {
  const t = useT();
  const [status, setStatus] = useState<NerStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try { const d = await api.get("/admin/ner/status"); setStatus(d); }
    catch (err) { setError(err instanceof ApiError ? t("infra.errorStatus", { status: err.status }) : String(err)); }
    finally { setLoading(false); }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (status?.ner_enabled && status?.status === "loading") {
      pollRef.current = setInterval(() => load(), 3000);
    }
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [status?.status, status?.ner_enabled, load]);

  const toggle = async (next: boolean) => {
    setToggling(true);
    try { await api.patch("/admin/settings", { ner_enabled: next }); await load(); }
    catch (err) { setError(err instanceof ApiError ? t("infra.errorStatus", { status: err.status }) : String(err)); }
    finally { setToggling(false); }
  };

  if (loading) return <div style={{ padding: 20 }}><SkeletonCards count={1} /></div>;
  if (error) return <div style={{ padding: 20 }}><ErrorState message={error} onRetry={load} /></div>;

  const s = status?.status ?? "not_loaded";
  const enabled = status?.ner_enabled ?? false;

  const isReady = enabled && s === "ready";
  const dotColor = !enabled ? "#9ca3af" : s === "loading" ? "#f59e0b" : s === "ready" ? "#10b981" : "#ef4444";
  const statusText = !enabled ? t("infra.policies.nerNotLoaded")
    : s === "loading" ? t("infra.policies.nerLoading")
    : s === "ready" ? t("infra.policies.nerReady")
    : t("infra.policies.nerError", { message: status?.message ?? t("infra.policies.nerUnknown") });

  return (
    <div style={{ padding: "24px", maxWidth: 640 }}>
      <div className="dlp-card-anim" style={{ animationDelay: "0.1s" }}>
        <h3 style={{ fontSize: "20px", fontWeight: 700, color: C.textPrimary, margin: "0 0 6px" }}>{t("infra.policies.ner")}</h3>
        <p style={{ color: C.textSecondary, fontSize: "14px", margin: "0 0 20px", lineHeight: 1.6 }}>
          {t("infra.policies.nerDesc")}
        </p>

        {/* Toggle row */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <Toggle checked={enabled} disabled={toggling} onChange={toggle} />
          <span style={{ fontSize: "14px", fontWeight: 600, color: C.textPrimary }}>{t("infra.policies.nerEnable")}</span>
          {toggling && <span style={{ fontSize: "12px", color: C.textTertiary }}>{t("infra.policies.nerUpdating")}</span>}
        </div>

        {/* Status indicator */}
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "12px 14px", borderRadius: 10, marginBottom: 20,
          background: isReady ? "rgba(16,185,129,0.06)" : "var(--bg-elevated)",
          border: `1px solid ${isReady ? "rgba(16,185,129,0.15)" : C.borderLight}`,
        }}>
          <span style={{
            width: 9, height: 9, borderRadius: "50%", flexShrink: 0,
            background: dotColor,
            animation: isReady ? "dotGlow 2s ease-in-out infinite" : "none",
          }} />
          <span style={{ fontSize: "13px", color: C.textSecondary, fontWeight: 500 }}>{statusText}</span>
        </div>

        {/* Info callout */}
        <div style={{
          display: "flex", gap: 10, alignItems: "flex-start",
          padding: "12px 14px", borderRadius: 10,
          background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.15)",
          fontSize: "13px", color: "var(--accent-warning)", lineHeight: 1.55,
        }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#d97706" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
            <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <span>{t("infra.policies.nerWarning")}</span>
        </div>
      </div>
    </div>
  );
}

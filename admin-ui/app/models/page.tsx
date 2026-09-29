"use client";

import { useState, useMemo, useCallback, useRef } from "react";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonTable } from "../../components/ui/Skeleton";
import Pagination from "../../components/ui/Pagination";
import { useApi } from "../../lib/useApi";
import { api, getApiErrorMessage } from "../../lib/api";
import { ThemeToggle } from "../../components/ThemeToggle";
import AgentMarketPointer from "../../components/AgentMarketPointer";
import { useT, type DictKey } from "../../lib/i18n";

/* ── Media destinations (FIX67) ───────────────────────────────────── */

interface SettingRow {
  key: string;
  value: string;
}

function MediaDestinations() {
  const t = useT();
  const { data, refetch } = useApi<SettingRow[]>("/admin/system-settings");
  const cur = (k: string) => data?.find((s) => s.key === k)?.value ?? "";
  const [img, setImg] = useState<string | null>(null);
  const [vid, setVid] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const imgVal = img ?? cur("media_order:image");
  const vidVal = vid ?? cur("media_order:video");
  const allowMock = cur("media_allow_mock").toLowerCase() === "true";

  const save = useCallback(async (key: string, value: string) => {
    setStatus("");
    try {
      await api.patch(`/admin/system-settings/${key}`, { value });
      setStatus(t("infra.media.saved"));
      refetch();
    } catch (e) {
      setStatus(getApiErrorMessage(e, "Save failed"));
    }
  }, [t, refetch]);

  const box: React.CSSProperties = {
    flex: 1, minWidth: 220, padding: "0.4rem 0.55rem",
    border: "1px solid var(--border)", borderRadius: 6,
    background: "var(--bg-surface)", color: "var(--text-primary)", fontFamily: "monospace",
  };
  const btn: React.CSSProperties = {
    padding: "0.4rem 0.9rem", borderRadius: 6, border: "1px solid var(--border)",
    background: "var(--bg-elevated)", color: "var(--text-primary)", cursor: "pointer",
  };
  const rowStyle: React.CSSProperties = { display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.5rem", flexWrap: "wrap" };

  return (
    <div style={{ padding: "1rem 1.15rem", border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-base)", marginBottom: "1.25rem" }}>
      <h2 style={{ fontSize: "0.95rem", fontWeight: 600, margin: 0 }}>{t("infra.media.title")}</h2>
      <p style={{ fontSize: "0.8rem", color: "var(--text-muted)", margin: "0.2rem 0 0.75rem" }}>{t("infra.media.subtitle")}</p>
      <label style={rowStyle}>
        <span style={{ minWidth: 90, fontSize: "0.85rem" }}>{t("infra.media.image")}</span>
        <input style={box} value={imgVal} placeholder={t("infra.media.placeholder")} onChange={(e) => setImg(e.target.value)} />
        <button style={btn} onClick={() => save("media_order:image", imgVal)}>{t("infra.media.save")}</button>
      </label>
      <label style={rowStyle}>
        <span style={{ minWidth: 90, fontSize: "0.85rem" }}>{t("infra.media.video")}</span>
        <input style={box} value={vidVal} placeholder={t("infra.media.placeholder")} onChange={(e) => setVid(e.target.value)} />
        <button style={btn} onClick={() => save("media_order:video", vidVal)}>{t("infra.media.save")}</button>
      </label>
      <label style={{ ...rowStyle, marginBottom: 0 }}>
        <input type="checkbox" checked={allowMock} onChange={(e) => save("media_allow_mock", e.target.checked ? "true" : "false")} />
        <span style={{ fontSize: "0.85rem" }}>{t("infra.media.allowMock")}</span>
      </label>
      {status ? <p style={{ fontSize: "0.8rem", color: "var(--text-muted)", margin: "0.5rem 0 0" }}>{status}</p> : null}
    </div>
  );
}

/* ── Types ────────────────────────────────────────────────────────── */

interface ModelItem {
  id: number;
  model: string;
  provider: string;
  supports_streaming: boolean;
  supports_tools?: boolean;
  supports_vision?: boolean;
  supports_reasoning?: boolean;
  // FIX62 — the dedicated media kinds, editable like the other flags.
  supports_video_gen?: boolean;
  supports_audio_gen?: boolean;
  supports_tts?: boolean;
  // How this row's thinking is obtained. Mirrors
  // backend providers/reasoning_strategies.py — the values are a closed
  // set the API validates, so the select below can enumerate them.
  reasoning_strategy?: string;
  default_reasoning_mode?: string;
  is_manual_override?: boolean;
  // FIX64 — where each capability flag came from (admin / provider /
  // inferred / probe / seed) and the last capability-test result per
  // capability. Empty for rows that predate migration 083.
  capability_provenance?: CapabilityProvenance;
  is_active: boolean;
  // FIX47 Part C — the catalogue's second axis: offered in the chat
  // picker. Independent of is_active (API access). Edited right here.
  show_in_chat: boolean;
  cost_tier: string;
  latency_tier: string;
}

/* ── Reasoning capability ─────────────────────────────────────────── */

// The values backend/app/services/providers/reasoning_strategies.py
// accepts, in the order it lists them. "none" is not offered here: the
// switch below turns reasoning off, which is the same thing said once.
const REASONING_STRATEGIES = [
  "openai_compatible_reasoning_content",
  "openai_compatible_reasoning",
  "openai_responses_reasoning",
  "gemini_thinking_level",
] as const;

const STRATEGY_LABEL: Record<string, DictKey> = {
  openai_compatible_reasoning_content: "infra.models.strategy.readOnly",
  openai_compatible_reasoning: "infra.models.strategy.effortKnob",
  openai_responses_reasoning: "infra.models.strategy.openaiResponses",
  gemini_thinking_level: "infra.models.strategy.geminiThinking",
};

// Turning reasoning ON without a strategy would leave the row saying
// "this model thinks" and nothing saying how — so the switch supplies
// the safe one: read whatever the provider streams, send nothing extra.
// That is the only strategy correct for a box we know nothing about.
const DEFAULT_STRATEGY = "openai_compatible_reasoning_content";

function ReasoningCell({
  item,
  onChange,
}: {
  item: ModelItem;
  onChange: (id: number, patch: Record<string, unknown>) => Promise<void>;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const on = !!item.supports_reasoning;
  const strategy = item.reasoning_strategy && item.reasoning_strategy !== "none"
    ? item.reasoning_strategy
    : DEFAULT_STRATEGY;

  async function run(patch: Record<string, unknown>) {
    setBusy(true);
    try { await onChange(item.id, patch); } finally { setBusy(false); }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "0.25rem" }}>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={t("infra.models.reasoningToggle")}
        title={t(on ? "infra.models.reasoningOn" : "infra.models.reasoningOff")}
        disabled={busy}
        onClick={() => run(
          on
            // Off means off on both axes: a row left claiming a strategy
            // it no longer uses is how a stale flag survives a fix.
            ? { supports_reasoning: false, reasoning_strategy: "none" }
            : { supports_reasoning: true, reasoning_strategy: strategy, default_reasoning_mode: "auto" }
        )}
        style={{
          position: "relative", width: 34, height: 18, borderRadius: 9, border: "none", padding: 0,
          cursor: busy ? "wait" : "pointer", opacity: busy ? 0.6 : 1,
          background: on ? "#db2777" : "var(--border-default)", transition: "background 150ms",
        }}
      >
        <span style={{
          position: "absolute", top: 2, left: on ? 18 : 2, width: 14, height: 14, borderRadius: "50%",
          background: "#fff", transition: "left 150ms", boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
        }} />
      </button>
      {on && (
        <select
          value={strategy}
          disabled={busy}
          aria-label={t("infra.models.strategyLabel")}
          title={t("infra.models.strategyHelp")}
          onChange={(e) => run({ reasoning_strategy: e.target.value })}
          style={{
            fontSize: "0.625rem", padding: "0.0625rem 0.125rem", maxWidth: 96,
            border: "1px solid var(--border-default)", borderRadius: 4,
            background: "var(--bg-surface)", color: "var(--text-secondary)",
            cursor: busy ? "wait" : "pointer",
          }}
        >
          {REASONING_STRATEGIES.map((s) => (
            <option key={s} value={s}>{t(STRATEGY_LABEL[s])}</option>
          ))}
        </select>
      )}
    </div>
  );
}

/* ── Show-in-chat switch (FIX47 Part C) ───────────────────────────── */

function ShowInChatSwitch({ item, onChange }: { item: ModelItem; onChange: (id: number, next: boolean) => Promise<void> }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const on = item.show_in_chat;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={t("infra.models.showInChat")}
      title={t(on ? "infra.models.showInChatOn" : "infra.models.showInChatOff")}
      disabled={busy}
      onClick={async () => { setBusy(true); try { await onChange(item.id, !on); } finally { setBusy(false); } }}
      style={{
        position: "relative", width: 34, height: 18, borderRadius: 9, border: "none", padding: 0,
        cursor: busy ? "wait" : "pointer", opacity: busy ? 0.6 : 1,
        background: on ? "#10a37f" : "var(--border-default)", transition: "background 150ms",
      }}
    >
      <span style={{
        position: "absolute", top: 2, left: on ? 18 : 2, width: 14, height: 14, borderRadius: "50%",
        background: "#fff", transition: "left 150ms", boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
      }} />
    </button>
  );
}

/* ── Capability switches (FIX62) ──────────────────────────────────── */

// FIX62 — every capability the catalogue answers for is one click here,
// the same shape as the Thinking switch: the PATCH goes to the same
// route, sets is_manual_override, and the server's row is what gets
// shown afterwards. This is the only route by which a self-hosted model
// gets a capability corrected — never a SQL statement, never a seed.
type CapabilityKey = "supports_tools" | "supports_vision" | "supports_video_gen" | "supports_audio_gen" | "supports_tts";

type CapLabelKey = Parameters<ReturnType<typeof useT>>[0];

const CAPABILITY_SWITCHES: { key: CapabilityKey; label: CapLabelKey; colour: string }[] = [
  { key: "supports_tools", label: "infra.models.cap.tools", colour: "#2563eb" },
  { key: "supports_vision", label: "infra.models.cap.vision", colour: "#7c3aed" },
  { key: "supports_video_gen", label: "infra.models.cap.video", colour: "#0891b2" },
  { key: "supports_audio_gen", label: "infra.models.cap.audio", colour: "#d97706" },
  { key: "supports_tts", label: "infra.models.cap.tts", colour: "#059669" },
];

/* ── FIX64: capability provenance + the per-model capability test ───── */

type CapSource = "admin" | "provider" | "inferred" | "probe" | "seed";
type CapVerdict = "proven_yes" | "proven_no" | "inconclusive" | "unreachable";
interface CapTestRecord { verdict: CapVerdict; evidence?: string; at?: string; applied?: boolean; proposal?: boolean }
interface CapabilityProvenance {
  tools?: { source: CapSource; at?: string; evidence?: string };
  vision?: { source: CapSource; at?: string; evidence?: string };
  streaming?: { source: CapSource; at?: string; evidence?: string };
  reasoning?: { source: CapSource; at?: string; evidence?: string };
  video_gen?: { source: CapSource; at?: string; evidence?: string };
  audio_gen?: { source: CapSource; at?: string; evidence?: string };
  tts?: { source: CapSource; at?: string; evidence?: string };
  tests?: Partial<Record<string, CapTestRecord>>;
}
// The testable capabilities, in the order the test runs them, and the
// row field each one writes. Mirrors backend capability_test.TESTABLE.
const TESTABLE_CAPS = ["tools", "vision", "streaming", "reasoning"] as const;
type TestableCap = (typeof TESTABLE_CAPS)[number];
const CAP_FIELD: Record<TestableCap, keyof ModelItem> = {
  tools: "supports_tools", vision: "supports_vision", streaming: "supports_streaming", reasoning: "supports_reasoning",
};
const CAP_NAME_KEY: Record<TestableCap, CapLabelKey> = {
  tools: "infra.models.capTest.cap.tools", vision: "infra.models.capTest.cap.vision",
  streaming: "infra.models.capTest.cap.streaming", reasoning: "infra.models.capTest.cap.reasoning",
};
const PROVENANCE_CAP: Partial<Record<CapabilityKey, keyof CapabilityProvenance>> = {
  supports_tools: "tools", supports_vision: "vision", supports_video_gen: "video_gen",
  supports_audio_gen: "audio_gen", supports_tts: "tts",
};

interface CapTestPlan {
  capabilities: TestableCap[];
  requests: number;
  requests_per_capability: Record<string, number>;
  max_tokens_total: number;
  paid: boolean;
}
interface CapTestResult {
  capability: TestableCap;
  verdict: CapVerdict;
  evidence: string;
  latency_ms: number;
  stored_before: boolean;
  stored_after: boolean;
  applied: boolean;
  proposal: boolean;
  proposed_value: boolean | null;
  reasoning_strategy?: string;
  provenance?: CapabilityProvenance;
}

function sourceLabel(t: ReturnType<typeof useT>, source: CapSource | undefined): string {
  switch (source) {
    case "admin": return t("infra.models.capSource.admin");
    case "provider": return t("infra.models.capSource.provider");
    case "inferred": return t("infra.models.capSource.inferred");
    case "probe": return t("infra.models.capSource.probe");
    case "seed": return t("infra.models.capSource.seed");
    default: return t("infra.models.capSource.unknown");
  }
}

function verdictLabel(t: ReturnType<typeof useT>, v: CapVerdict): string {
  switch (v) {
    case "proven_yes": return t("infra.models.capTestVerdict.proven_yes");
    case "proven_no": return t("infra.models.capTestVerdict.proven_no");
    case "unreachable": return t("infra.models.capTestVerdict.unreachable");
    default: return t("infra.models.capTestVerdict.inconclusive");
  }
}

const VERDICT_COLOUR: Record<CapVerdict, string> = {
  proven_yes: "#059669", proven_no: "#dc2626", inconclusive: "#d97706", unreachable: "#64748b",
};

/** One request per capability, in order, only while the person has not
 *  pressed Stop; every result lands on the row as the server reports it. */
function CapabilityTest({
  item, onChange, onResult,
}: {
  item: ModelItem;
  onChange: (id: number, patch: Record<string, unknown>) => Promise<void>;
  onResult: (id: number, patch: Partial<ModelItem>) => void;
}) {
  const t = useT();
  const [phase, setPhase] = useState<"idle" | "plan" | "running" | "done">("idle");
  const [plan, setPlan] = useState<CapTestPlan | null>(null);
  const [results, setResults] = useState<CapTestResult[]>([]);
  const [current, setCurrent] = useState<TestableCap | null>(null);
  const [stopped, setStopped] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef(false);

  const loadPlan = async () => {
    setError(null);
    try {
      const p = (await api.get(`/admin/models/${item.id}/capability-test`)) as CapTestPlan;
      setPlan(p);
      setPhase("plan");
    } catch (e) {
      setError(getApiErrorMessage(e, t("infra.models.capTestFailed")));
    }
  };

  const run = async () => {
    if (!plan) return;
    stopRef.current = false;
    setStopped(false);
    setResults([]);
    setPhase("running");
    const out: CapTestResult[] = [];
    for (const cap of plan.capabilities) {
      if (stopRef.current) { setStopped(true); break; }
      setCurrent(cap);
      try {
        const r = (await api.post(`/admin/models/${item.id}/capability-test/${cap}`, { apply: true })) as CapTestResult;
        out.push(r);
        setResults([...out]);
        const patch: Partial<ModelItem> = { [CAP_FIELD[cap]]: r.stored_after } as Partial<ModelItem>;
        if (r.provenance) patch.capability_provenance = r.provenance;
        if (r.reasoning_strategy) patch.reasoning_strategy = r.reasoning_strategy;
        onResult(item.id, patch);
      } catch (e) {
        setError(getApiErrorMessage(e, t("infra.models.capTestFailed")));
        break;
      }
    }
    setCurrent(null);
    setPhase("done");
  };

  const capNames = (caps: readonly TestableCap[]) => caps.map((c) => t(CAP_NAME_KEY[c])).join(", ");
  const onOff = (v: boolean | null | undefined) => t(v ? "infra.models.on" : "infra.models.off");
  const small: React.CSSProperties = { fontSize: "0.625rem", color: "var(--text-tertiary)" };
  const btn = {
    fontSize: "0.625rem", padding: "0.125rem 0.5rem", borderRadius: 4, cursor: "pointer",
    border: "1px solid var(--border-default)", background: "var(--bg-secondary)", color: "var(--text-secondary)",
  };

  if (phase === "idle") {
    return (
      <span style={{ display: "inline-flex", flexDirection: "column", gap: "0.125rem" }}>
        <button type="button" style={btn} data-testid="cap-test" title={t("infra.models.capTestTitle", { provider: item.provider, model: item.model })} onClick={loadPlan}>
          {t("infra.models.capTest")}
        </button>
        {error && <span style={{ ...small, color: "#dc2626" }}>{error}</span>}
      </span>
    );
  }
  if (phase === "plan" && plan) {
    return (
      <span style={{ display: "inline-flex", flexDirection: "column", gap: "0.25rem", maxWidth: 360 }} data-testid="cap-test-plan">
        <span style={small}>{t("infra.models.capTestPlan", { n: String(plan.requests), caps: capNames(plan.capabilities), tokens: String(plan.max_tokens_total) })}</span>
        <span style={{ ...small, color: plan.paid ? "#d97706" : "var(--text-tertiary)" }}>{t(plan.paid ? "infra.models.capTestPaid" : "infra.models.capTestFree")}</span>
        <span style={small}>{t("infra.models.capTestNotTestable")}</span>
        <span style={{ display: "inline-flex", gap: "0.375rem" }}>
          <button type="button" style={{ ...btn, fontWeight: 600 }} data-testid="cap-test-start" onClick={run}>{t("infra.models.capTestStart")}</button>
          <button type="button" style={btn} data-testid="cap-test-cancel" onClick={() => setPhase("idle")}>{t("infra.models.capTestCancel")}</button>
        </span>
      </span>
    );
  }
  const n = plan?.capabilities.length ?? TESTABLE_CAPS.length;
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", gap: "0.25rem", maxWidth: 420 }} data-testid="cap-test-results">
      {phase === "running" && current && (
        <span style={{ display: "inline-flex", gap: "0.375rem", alignItems: "center" }}>
          <span style={small} aria-live="polite">{t("infra.models.capTestRunning", { cap: t(CAP_NAME_KEY[current]), i: String(results.length + 1), n: String(n) })}</span>
          <button type="button" style={btn} data-testid="cap-test-stop" onClick={() => { stopRef.current = true; }}>{t("infra.models.capTestStop")}</button>
        </span>
      )}
      {results.map((r) => (
        <span key={r.capability} style={{ ...small, display: "inline-flex", gap: "0.375rem", alignItems: "baseline", flexWrap: "wrap" }} data-testid={`cap-test-result-${r.capability}`} title={r.evidence}>
          <strong style={{ color: "var(--text-secondary)" }}>{t(CAP_NAME_KEY[r.capability])}</strong>
          <span style={{ color: VERDICT_COLOUR[r.verdict], fontWeight: 600 }}>{verdictLabel(t, r.verdict)}</span>
          <span>
            {r.applied
              ? t("infra.models.capTestApplied", { value: onOff(r.stored_after) })
              : r.proposal
                ? t("infra.models.capTestProposal", { value: onOff(r.proposed_value) })
                : t("infra.models.capTestUnchanged")}
          </span>
          {r.proposal && r.proposed_value !== null && (
            <button type="button" style={btn} data-testid={`cap-test-apply-${r.capability}`} onClick={() => onChange(item.id, { [CAP_FIELD[r.capability]]: r.proposed_value })}>
              {t("infra.models.capTestApply")}
            </button>
          )}
        </span>
      ))}
      {phase === "done" && (
        <span style={{ display: "inline-flex", gap: "0.375rem", alignItems: "center" }}>
          <span style={small}>{stopped ? t("infra.models.capTestStopped", { i: String(results.length), n: String(n) }) : t("infra.models.capTestDone")}</span>
          {error && <span style={{ ...small, color: "#dc2626" }}>{error}</span>}
          <button type="button" style={btn} onClick={() => { setPhase("idle"); setResults([]); setError(null); }}>{t("infra.models.capTest")}</button>
        </span>
      )}
    </span>
  );
}

function CapabilitySwitch({
  item, cap, onChange,
}: {
  item: ModelItem;
  cap: { key: CapabilityKey; label: CapLabelKey; colour: string };
  onChange: (id: number, patch: Record<string, unknown>) => Promise<void>;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const on = !!item[cap.key];
  const name = t(cap.label);
  // FIX64 — the tooltip says where the value came from.
  const provKey = PROVENANCE_CAP[cap.key];
  const prov = provKey ? item.capability_provenance?.[provKey] : undefined;
  const source = prov && "source" in prov ? (prov as { source: CapSource }).source : undefined;
  const title = `${t(on ? "infra.models.capOn" : "infra.models.capOff", { cap: name })} ${t("infra.models.capSource", { source: sourceLabel(t, source) })}`;
  return (
    <label style={{ display: "inline-flex", alignItems: "center", gap: "0.25rem", fontSize: "0.625rem", color: "var(--text-tertiary)" }}>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={t("infra.models.capToggle", { cap: name })}
        title={title}
        data-source={source ?? "unknown"}
        disabled={busy}
        data-testid={`cap-switch-${cap.key}`}
        onClick={async () => {
          setBusy(true);
          try { await onChange(item.id, { [cap.key]: !on }); } finally { setBusy(false); }
        }}
        style={{
          position: "relative", width: 28, height: 16, borderRadius: 8, border: "none", padding: 0,
          cursor: busy ? "wait" : "pointer", opacity: busy ? 0.6 : 1,
          background: on ? cap.colour : "var(--border-default)", transition: "background 150ms",
        }}
      >
        <span style={{
          position: "absolute", top: 2, left: on ? 14 : 2, width: 12, height: 12, borderRadius: "50%",
          background: "#fff", transition: "left 150ms", boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
        }} />
      </button>
      {name}
    </label>
  );
}

function CapabilityCell({
  item, onChange, onResult,
}: {
  item: ModelItem;
  onChange: (id: number, patch: Record<string, unknown>) => Promise<void>;
  onResult: (id: number, patch: Partial<ModelItem>) => void;
}) {
  return (
    <div style={{ display: "flex", gap: "0.375rem 0.625rem", flexWrap: "wrap", alignItems: "center" }}>
      {CAPABILITY_SWITCHES.map((cap) => (
        <CapabilitySwitch key={cap.key} item={item} cap={cap} onChange={onChange} />
      ))}
      {/* FIX64 — measure instead of guess: one click, one request per
          capability, the row shows what was found and what changed. */}
      <CapabilityTest item={item} onChange={onChange} onResult={onResult} />
    </div>
  );
}

/* ── Provider brand colors ────────────────────────────────────────── */

const BRAND: Record<string, string> = {
  openai: "#10a37f", anthropic: "#e07a2f", minimax: "#7c5cbf",
  kimi: "#0891b2", qwen: "#3b6cf6", zai: "#db2777", "z.ai": "#db2777",
  openrouter: "#64748b",
};
function brand(p: string) { return BRAND[p.toLowerCase()] || "#64748b"; }

/* ── Model enrichment (client-side known metadata) ────────────────── */

const META: Record<string, { ctx: string; ctxK: number; caps: string[] }> = {
  "gpt-4o":         { ctx: "128K", ctxK: 128, caps: ["vision","tools","code"] },
  "gpt-4o-mini":    { ctx: "128K", ctxK: 128, caps: ["vision","tools","code"] },
  "gpt-4-turbo":    { ctx: "128K", ctxK: 128, caps: ["vision","tools"] },
  "gpt-3.5-turbo":  { ctx: "16K",  ctxK: 16,  caps: ["tools"] },
  "claude-3-5-sonnet-20241022": { ctx: "200K", ctxK: 200, caps: ["vision","tools","code"] },
  "claude-3-opus-20240229":     { ctx: "200K", ctxK: 200, caps: ["vision","tools","code"] },
  "claude-3-haiku-20240307":    { ctx: "200K", ctxK: 200, caps: ["vision","tools"] },
  "abab6.5s-chat":  { ctx: "245K", ctxK: 245, caps: ["tools"] },
  "abab5.5-chat":   { ctx: "32K",  ctxK: 32,  caps: ["tools"] },
  "qwen-turbo":     { ctx: "128K", ctxK: 128, caps: ["tools","code"] },
  "qwen-plus":      { ctx: "128K", ctxK: 128, caps: ["tools","code"] },
  "qwen-max":       { ctx: "32K",  ctxK: 32,  caps: ["tools","code"] },
  "moonshot-v1-8k":   { ctx: "8K",   ctxK: 8,   caps: ["tools"] },
  "moonshot-v1-32k":  { ctx: "32K",  ctxK: 32,  caps: ["tools"] },
  "moonshot-v1-128k": { ctx: "128K", ctxK: 128, caps: ["tools"] },
  "yi-large":  { ctx: "32K", ctxK: 32, caps: ["tools"] },
  "yi-medium": { ctx: "16K", ctxK: 16, caps: ["tools"] },
};
function getMeta(m: string) {
  if (META[m]) return META[m];
  for (const k of Object.keys(META)) { if (m.startsWith(k)) return META[k]; }
  return null;
}
// FIX47 Part C — the capability flags the API serves (model_configs) win
// over the static map above; the map stays only for context sizes.
function rowCaps(item: ModelItem): string[] {
  const caps: string[] = [];
  if (item.supports_vision) caps.push("vision");
  if (item.supports_tools) caps.push("tools");
  if (item.supports_reasoning) caps.push("reasoning");
  if (caps.length) return caps;
  return getMeta(item.model)?.caps ?? [];
}

/* ── Provider logo with fallback ───────────────────────────────────── */

function ProviderLogo({ provider, size = 36 }: { provider: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const c = brand(provider);
  const src = `/providers/${provider.toLowerCase()}.png`;
  const initials = provider.slice(0, 2).toUpperCase();

  return (
    <div style={{
      width: size, height: size, borderRadius: 6, flexShrink: 0,
      background: `${c}1A`,
      display: "flex", alignItems: "center", justifyContent: "center",
      overflow: "hidden",
    }}>
      {!failed ? (
        <img
          src={src}
          alt={provider}
          onError={() => setFailed(true)}
          style={{
            width: size - 8, height: size - 8,
            objectFit: "contain",
          }}
        />
      ) : (
        <span style={{
          fontSize: size * 0.3, fontWeight: 700,
          color: c, lineHeight: 1,
          textTransform: "uppercase",
        }}>{initials}</span>
      )}
    </div>
  );
}

/* ── Capability tag colors ────────────────────────────────────────── */

const CAP: Record<string, { color: string; bg: string }> = {
  vision: { color: "#7c3aed", bg: "rgba(124,58,237,0.07)" },
  tools:  { color: "#0891b2", bg: "rgba(8,145,178,0.07)" },
  code:   { color: "#ca8a04", bg: "rgba(202,138,4,0.07)" },
  reasoning: { color: "#db2777", bg: "rgba(219,39,119,0.07)" },
};

// Capability + tier VALUES are human-facing labels, so they carry a
// dictionary key. An unknown value from the API falls back to the raw
// string rather than rendering blank.
const CAP_LABEL: Record<string, DictKey> = {
  vision: "infra.models.cap.vision",
  tools: "infra.models.cap.tools",
  code: "infra.models.cap.code",
  reasoning: "infra.models.cap.reasoning",
};
const TIER_LABEL: Record<string, DictKey> = {
  budget: "infra.models.tier.budget",
  standard: "infra.models.tier.standard",
  premium: "infra.models.tier.premium",
  fast: "infra.models.tier.fast",
  medium: "infra.models.tier.medium",
  slow: "infra.models.tier.slow",
};

/* ── Tier badge styles ────────────────────────────────────────────── */

const TIER: Record<string, { color: string; bg: string }> = {
  budget:   { color: "#059669", bg: "rgba(5,150,105,0.07)" },
  standard: { color: "#2563eb", bg: "rgba(37,99,235,0.06)" },
  premium:  { color: "#d97706", bg: "rgba(217,119,6,0.07)" },
  fast:     { color: "#059669", bg: "rgba(5,150,105,0.07)" },
  medium:   { color: "#2563eb", bg: "rgba(37,99,235,0.06)" },
  slow:     { color: "#d97706", bg: "rgba(217,119,6,0.07)" },
};

/* ── Scoped CSS ───────────────────────────────────────────────────── */

const STYLES = `
.mr-table { width:100%; border-collapse:collapse; }
.mr-table th {
  text-align:left; padding:0.5rem 0.875rem; font-weight:500; font-size:0.6875rem;
  text-transform:uppercase; letter-spacing:0.06em; color:var(--text-secondary); font-weight:600;
  border-bottom:1px solid var(--border-default); background:var(--bg-surface-2); white-space:nowrap;
  position:sticky; top:0; z-index:1;
}
.mr-table td { padding:1.25rem 0.875rem; border-bottom:1px solid var(--border-subtle); vertical-align:middle; color:var(--text-primary); }
.mr-table tbody tr { transition: background 100ms ease; }
.mr-table tbody tr:hover { background: rgba(99,102,241,0.05); }
.mr-table tbody tr:last-child td { border-bottom:none; }
@keyframes mrFadeUp { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
@keyframes mrFadeIn { from{opacity:0} to{opacity:1} }
.mr-stat { animation: mrFadeUp 0.4s ease-out both; }
.mr-row { animation: mrFadeUp 0.35s ease-out both; }
.mr-chip-fade { animation: mrFadeIn 0.4s ease-out both; }
@keyframes mrPulse { 0%,100%{transform:scale(1)} 50%{transform:scale(1.08)} }
@keyframes mrRock { 0%,100%{transform:rotate(0deg)} 25%{transform:rotate(8deg)} 75%{transform:rotate(-8deg)} }
@keyframes mrBreathe { 0%,100%{opacity:1} 50%{opacity:0.6} }
/* One-shot on mount (was infinite) — a single attention cue, not perpetual
   motion on a dense data table. */
.mr-icon-pulse svg { animation: mrPulse 2.5s ease-in-out; }
.mr-icon-rock svg { animation: mrRock 3s ease-in-out; }
.mr-icon-breathe svg { animation: mrBreathe 2s ease-in-out; }
`;

/* ── Sub-components ───────────────────────────────────────────────── */

function ContextBar({ ctx, ctxK }: { ctx: string; ctxK: number }) {
  const pct = Math.min(100, (ctxK / 256) * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", minWidth: 100 }}>
      <div style={{ flex: 1, height: 3, borderRadius: 2, background: "var(--bg-elevated)", overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`, height: "100%", borderRadius: 2,
          background: `linear-gradient(90deg, #3b82f6 0%, #60a5fa 100%)`,
        }} />
      </div>
      <span style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        fontSize: "0.625rem", fontWeight: 600, color: "var(--text-tertiary)", whiteSpace: "nowrap",
      }}>{ctx}</span>
    </div>
  );
}

function CapTag({ cap }: { cap: string }) {
  const t = useT();
  const s = CAP[cap] || { color: "#64748b", bg: "rgba(100,116,139,0.06)" };
  return (
    <span style={{
      display: "inline-block", padding: "0.0625rem 0.375rem",
      fontSize: "0.625rem", fontWeight: 600, color: s.color, background: s.bg,
      borderRadius: 4, lineHeight: 1.6, textTransform: "capitalize",
    }}>{CAP_LABEL[cap] ? t(CAP_LABEL[cap]) : cap}</span>
  );
}

function TierBadge({ value }: { value: string }) {
  const t = useT();
  const s = TIER[value] || TIER.standard;
  return (
    <span style={{
      display: "inline-block", padding: "0.0625rem 0.4375rem",
      fontSize: "0.6875rem", fontWeight: 600, color: s.color, background: s.bg,
      borderRadius: 4, lineHeight: 1.5, textTransform: "capitalize",
    }}>{TIER_LABEL[value] ? t(TIER_LABEL[value]) : value}</span>
  );
}

function ProviderChip({ name, active, count, shown, title, onClick }: {
  name: string; active: boolean; count: number; shown?: number; title?: string; onClick: () => void;
}) {
  const c = brand(name);
  return (
    <button onClick={onClick} title={title} style={{
      display: "inline-flex", alignItems: "center", gap: "0.3125rem",
      padding: "0.25rem 0.5rem 0.25rem 0.375rem",
      fontSize: "0.6875rem", fontWeight: active ? 600 : 500,
      borderRadius: 6,
      border: active ? `1.5px solid ${c}` : "1.5px solid transparent",
      background: active ? `${c}22` : "transparent",
      color: active ? c : "var(--text-tertiary)",
      cursor: "pointer", transition: "all 120ms",
      textTransform: "capitalize", whiteSpace: "nowrap",
      fontFamily: "inherit",
    }}>
      <span style={{
        width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
        background: c, opacity: active ? 1 : 0.5,
      }} />
      {name}
      <span style={{
        fontSize: "0.5625rem", fontWeight: 700, color: active ? c : "var(--text-muted)",
        marginLeft: "0.0625rem",
      }}>{
        // FIX47.6 Part A — "shown/total" so the 5-per-provider ceiling
        // is visible at a glance; bare total where shown isn't supplied.
        shown !== undefined ? `${shown}/${count}` : count
      }</span>
    </button>
  );
}

function StatCard({ value, label, icon, accent, delay = 0, iconClass }: {
  value: string | number; label: string; icon: React.ReactNode; accent: string; delay?: number; iconClass?: string;
}) {
  return (
    <div className="mr-stat" style={{
      animationDelay: `${delay}ms`,
      background: "var(--bg-surface)", borderRadius: 10,
      border: "1px solid var(--border-default)",
      padding: "0.875rem 1rem",
      display: "flex", alignItems: "center", gap: "0.75rem",
      flex: 1, minWidth: 0,
    }}>
      <div className={iconClass} style={{
        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
        background: `${accent}0A`, border: `1px solid ${accent}18`,
        display: "flex", alignItems: "center", justifyContent: "center",
        color: accent,
      }}>{icon}</div>
      <div>
        <div style={{
          fontSize: "1.25rem", fontWeight: 700, color: "var(--text-primary)",
          lineHeight: 1, letterSpacing: "-0.03em",
          fontVariantNumeric: "tabular-nums",
        }}>{value}</div>
        <div style={{
          fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)",
          marginTop: "0.125rem", lineHeight: 1,
        }}>{label}</div>
      </div>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

const PAGE_SIZE = 15;
// FIX47.6 Part A — at most this many models per provider may be offered
// in the chat picker. A ceiling, not a target; enforced at the switch.
const CHAT_CEILING = 5;

export default function ModelsPage() {
  const t = useT();
  // The DB-backed listing (model_configs: id, capability flags, is_active,
  // show_in_chat). GET /admin/models is answered by the older registry
  // listing, which has none of those — see admin_model_discovery.py. The
  // listing defaults to 100 rows; ask for the whole catalogue.
  const { data, loading, error, refetch } = useApi<{ items: ModelItem[] }>("/admin/models/configs?limit=500");
  const [localShow, setLocalShow] = useState<Record<number, boolean>>({});
  // Reasoning edits, kept per row like localShow so the table reflects a
  // save without refetching 500 rows.
  const [localReasoning, setLocalReasoning] = useState<Record<number, Partial<ModelItem>>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState<string | null>(null);
  const [streamingOnly, setStreamingOnly] = useState(false);
  const [view, setView] = useState<"list" | "grid">("list");
  const [page, setPage] = useState(1);

  const items = useMemo(
    () => (data?.items ?? []).map((m) => {
      let row = m.id in localShow ? { ...m, show_in_chat: localShow[m.id] } : m;
      if (m.id in localReasoning) row = { ...row, ...localReasoning[m.id] };
      return row;
    }),
    [data, localShow, localReasoning],
  );
  const shownInChat = useMemo(() => items.filter((m) => m.is_active && m.show_in_chat).length, [items]);

  // FIX47.6 Part A — per-provider shown count (active + show_in_chat),
  // driving both the chips' "shown/total" figure and the ceiling guard.
  const providerShown = useMemo(() => {
    const c: Record<string, number> = {};
    items.forEach((m) => { if (m.is_active && m.show_in_chat) c[m.provider] = (c[m.provider] || 0) + 1; });
    return c;
  }, [items]);

  // FIX47.6 Part A — the chat-picker ceiling, enforced at the switch: a
  // 6th "on" for a provider is refused WITH the reason, never silently
  // accepted (the curation script honours the same ceiling on --apply).
  const setShowInChat = useCallback(async (id: number, next: boolean) => {
    setSaveError(null);
    const item = items.find((m) => m.id === id);
    if (next && item && (providerShown[item.provider] || 0) >= CHAT_CEILING) {
      setSaveError(t("infra.models.ceilingRefused", { provider: item.provider, n: String(CHAT_CEILING) }));
      return;
    }
    try {
      await api.patch(`/admin/models/${id}`, { show_in_chat: next });
      setLocalShow((prev) => ({ ...prev, [id]: next }));
    } catch (e) {
      setSaveError(getApiErrorMessage(e, "save failed"));
    }
  }, [items, providerShown, t]);

  // Reasoning capability. Unlike show_in_chat this is a CAPABILITY edit,
  // so the PATCH also sets is_manual_override — the row stops taking
  // discovery updates until the override is cleared. That is the
  // existing contract for every capability flag; the column's tooltip
  // says so rather than letting the operator find out later.
  const setReasoning = useCallback(async (id: number, patch: Record<string, unknown>) => {
    setSaveError(null);
    try {
      const row = await api.patch(`/admin/models/${id}`, patch);
      // Take the SERVER's row, not the optimistic patch: the PATCH also
      // flips is_manual_override/source, and echoing our own guess would
      // show a state the backend never agreed to.
      setLocalReasoning((prev) => ({
        ...prev,
        [id]: {
          supports_reasoning: row.supports_reasoning,
          reasoning_strategy: row.reasoning_strategy,
          default_reasoning_mode: row.default_reasoning_mode,
          is_manual_override: row.is_manual_override,
        },
      }));
    } catch (e) {
      setSaveError(getApiErrorMessage(e, t("infra.models.reasoningSaveFailed")));
    }
  }, [t]);

  // FIX62 — tools / vision / video / audio / tts, same contract as the
  // reasoning switch: one PATCH, the server's row wins, the row becomes a
  // manual override. Kept in the same per-row overlay as reasoning so a
  // fresh list never shows a value the backend has not agreed to.
  const setCapability = useCallback(async (id: number, patch: Record<string, unknown>) => {
    setSaveError(null);
    try {
      const row = await api.patch(`/admin/models/${id}`, patch);
      setLocalReasoning((prev) => ({
        ...prev,
        [id]: {
          ...(prev[id] ?? {}),
          supports_tools: row.supports_tools,
          supports_vision: row.supports_vision,
          supports_video_gen: row.supports_video_gen,
          supports_audio_gen: row.supports_audio_gen,
          supports_tts: row.supports_tts,
          is_manual_override: row.is_manual_override,
        },
      }));
    } catch (e) {
      setSaveError(getApiErrorMessage(e, t("infra.models.capSaveFailed")));
    }
  }, [t]);

  // FIX64 — a capability-test result is the SERVER's word on the row
  // (flag after the verdict, provenance, a filled-in reasoning strategy);
  // it lands in the same overlay so the switches show what was measured.
  const applyTestResult = useCallback((id: number, patch: Partial<ModelItem>) => {
    setLocalReasoning((prev) => ({ ...prev, [id]: { ...(prev[id] ?? {}), ...patch } }));
  }, []);

  const providers = useMemo(() => {
    const c: Record<string, number> = {};
    items.forEach((m) => { c[m.provider] = (c[m.provider] || 0) + 1; });
    return Object.entries(c).sort(([a], [b]) => a.localeCompare(b));
  }, [items]);

  const streamingCount = useMemo(() => items.filter((m) => m.supports_streaming).length, [items]);

  const filtered = useMemo(() => {
    let r = items;
    if (providerFilter) r = r.filter((m) => m.provider === providerFilter);
    if (streamingOnly) r = r.filter((m) => m.supports_streaming);
    if (search) {
      const q = search.toLowerCase();
      r = r.filter((m) =>
        m.model.toLowerCase().includes(q) ||
        m.provider.toLowerCase().includes(q) ||
        m.cost_tier.toLowerCase().includes(q) ||
        m.latency_tier.toLowerCase().includes(q)
      );
    }
    return r;
  }, [items, search, providerFilter, streamingOnly]);

  const paged = useMemo(() => {
    const s = (page - 1) * PAGE_SIZE;
    return filtered.slice(s, s + PAGE_SIZE);
  }, [filtered, page]);

  const isFiltered = !!search || !!providerFilter || streamingOnly;

  function handleSearch(v: string) { setSearch(v); setPage(1); }
  function handleProvider(p: string | null) { setProviderFilter(p); setPage(1); }
  function toggleStream() { setStreamingOnly(!streamingOnly); setPage(1); }

  // SVG icons for stat cards (LayoutGrid, Plug, Radio — distinct from sidebar nav)
  const iconModels = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/><path d="M3.27 6.96L12 12.01l8.73-5.05"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>;
  const iconProviders = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 01-4 4h-4a4 4 0 01-4-4V8z"/></svg>;
  const iconStream = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.4"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.4"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/></svg>;

  return (
    <div style={{ background: "var(--bg-base)", margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      <style>{STYLES}</style>

      {/* ── Header ── */}
      <div className="sran-page-header" style={{ marginBottom: "1.25rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 className="sran-page-title">{t("infra.models.title")}</h1>
          <p className="sran-page-subtitle">{t("infra.models.subtitle")}</p>
        </div>
        <ThemeToggle />
      </div>
      <AgentMarketPointer page="models" />

      {/* ── Media destinations (FIX67) ── */}
      <MediaDestinations />

      {/* ── Stats row ── */}
      {!loading && !error && items.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3" style={{ gap: "0.75rem", marginBottom: "1.25rem" }}>
          <StatCard value={items.length} label={t("infra.models.statTotal")} icon={iconModels} accent="#3b82f6" delay={0} iconClass="mr-icon-pulse" />
          <StatCard value={providers.length} label={t("infra.models.statProviders")} icon={iconProviders} accent="#8b5cf6" delay={80} iconClass="mr-icon-rock" />
          <StatCard value={streamingCount} label={t("infra.models.statStreaming")} icon={iconStream} accent="#10a37f" delay={160} iconClass="mr-icon-breathe" />
        </div>
      )}

      {/* ── Main registry surface ── */}
      <div style={{
        background: "var(--bg-surface)", borderRadius: 12,
        border: "1px solid var(--border-default)",
        boxShadow: "0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02)",
        overflow: "hidden",
      }}>
        {loading ? (
          <SkeletonTable rows={10} cols={6} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : items.length === 0 ? (
          <EmptyState message={t("infra.models.empty")} icon={<Icon name="box" size={22} />} />
        ) : (
          <>
            {saveError ? (
              <div role="alert" style={{ margin: "0.75rem 1rem 0", padding: "0.5rem 0.75rem", borderRadius: 8, fontSize: "0.75rem", color: "#b91c1c", background: "rgba(185,28,28,0.08)" }}>
                {saveError}
              </div>
            ) : null}
            {/* ── Toolbar ── */}
            <div className="mr-chip-fade" style={{
              animationDelay: "0.2s",
              padding: "0.625rem 1rem",
              borderBottom: "1px solid var(--border-default)",
              display: "flex", alignItems: "center", gap: "0.5rem",
              flexWrap: "wrap",
            }}>
              {/* Search */}
              <div style={{ position: "relative", flex: "0 1 200px", minWidth: "140px" }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: "0.5rem", top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}>
                  <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                </svg>
                <input
                  type="text" value={search}
                  onChange={(e) => handleSearch(e.target.value)}
                  placeholder={t("infra.models.searchPlaceholder")}
                  style={{
                    width: "100%",
                    padding: "0.3125rem 1.5rem 0.3125rem 1.75rem",
                    fontSize: "0.8125rem", fontFamily: "inherit",
                    border: "1.5px solid var(--border-default)", borderRadius: 6,
                    background: "var(--bg-base)", color: "var(--text-primary)", outline: "none",
                    transition: "border-color 120ms, box-shadow 120ms, background 120ms",
                  }}
                  onFocus={(e) => {
                    e.currentTarget.style.borderColor = "var(--accent-primary)";
                    e.currentTarget.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.15)";
                    e.currentTarget.style.background = "var(--bg-surface)";
                  }}
                  onBlur={(e) => {
                    e.currentTarget.style.borderColor = "var(--border-default)";
                    e.currentTarget.style.boxShadow = "none";
                    e.currentTarget.style.background = "var(--bg-base)";
                  }}
                />
                {search && (
                  <button onClick={() => handleSearch("")} style={{
                    position: "absolute", right: "0.375rem", top: "50%", transform: "translateY(-50%)",
                    background: "none", border: "none", cursor: "pointer",
                    color: "var(--text-muted)", fontSize: "0.75rem", padding: 2,
                  }}><Icon name="x" size={12} /></button>
                )}
              </div>

              {/* Divider */}
              <span style={{ width: 1, height: 20, background: "var(--border-default)", flexShrink: 0 }} />

              {/* Provider chips */}
              <div style={{ display: "flex", gap: "0.1875rem", flexWrap: "wrap", alignItems: "center" }}>
                <button onClick={() => handleProvider(null)} style={{
                  display: "inline-flex", alignItems: "center", padding: "0.25rem 0.5rem",
                  fontSize: "0.6875rem", fontWeight: providerFilter === null ? 600 : 500,
                  borderRadius: 6, cursor: "pointer", transition: "all 120ms",
                  border: providerFilter === null ? "1.5px solid var(--accent-primary)" : "1.5px solid transparent",
                  background: providerFilter === null ? "rgba(99,102,241,0.12)" : "transparent",
                  color: providerFilter === null ? "var(--accent-primary)" : "var(--text-tertiary)",
                  fontFamily: "inherit",
                }}>{t("field.all")} <span style={{ fontSize: "0.5625rem", fontWeight: 700, marginLeft: 3, color: providerFilter === null ? "var(--accent-primary)" : "var(--text-muted)" }}>{items.length}</span></button>
                {providers.map(([name, count]) => (
                  <ProviderChip
                    key={name} name={name} count={count}
                    shown={providerShown[name] || 0}
                    title={t("infra.models.providerShown", {
                      provider: name,
                      shown: String(providerShown[name] || 0),
                      total: String(count),
                      ceiling: String(CHAT_CEILING),
                    })}
                    active={providerFilter === name}
                    onClick={() => handleProvider(providerFilter === name ? null : name)}
                  />
                ))}
              </div>

              {/* Divider */}
              <span style={{ width: 1, height: 20, background: "var(--border-default)", flexShrink: 0 }} />

              {/* Streaming toggle */}
              <button onClick={toggleStream} style={{
                display: "inline-flex", alignItems: "center", gap: "0.25rem",
                padding: "0.25rem 0.5rem", fontSize: "0.6875rem", fontFamily: "inherit",
                fontWeight: streamingOnly ? 600 : 500, borderRadius: 6, cursor: "pointer",
                border: streamingOnly ? "1.5px solid #10a37f" : "1.5px solid transparent",
                background: streamingOnly ? "rgba(16,163,127,0.06)" : "transparent",
                color: streamingOnly ? "#10a37f" : "#64748b",
                transition: "all 120ms",
              }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                {t("infra.models.stream")}
              </button>

              {/* Spacer + view toggle + count */}
              <span style={{ flex: 1 }} />

              <div style={{ display: "flex", gap: "0.125rem", background: "var(--bg-elevated)", borderRadius: 6, padding: 2 }}>
                {(["list", "grid"] as const).map((v) => (
                  <button key={v} onClick={() => setView(v)} style={{
                    display: "flex", alignItems: "center", justifyContent: "center",
                    width: 28, height: 24, borderRadius: 4, border: "none", cursor: "pointer",
                    background: view === v ? "var(--bg-surface)" : "transparent",
                    boxShadow: view === v ? "0 1px 2px rgba(0,0,0,0.18)" : "none",
                    color: view === v ? "var(--text-primary)" : "var(--text-muted)",
                    transition: "all 100ms",
                  }}>
                    {v === "list" ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
                    )}
                  </button>
                ))}
              </div>

              <span style={{
                fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)",
                whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums",
              }}>
                {isFiltered
                  ? t("infra.models.countFiltered", { n: filtered.length, total: items.length })
                  : t("infra.models.countAll", { n: items.length })}
              </span>
            </div>

            {/* ── Content ── */}
            {filtered.length === 0 ? (
              <div style={{ padding: "3rem 1rem", textAlign: "center" }}>
                <p style={{ fontSize: "0.8125rem", color: "var(--text-tertiary)", margin: "0 0 0.75rem" }}>
                  {t("infra.models.noFilterMatch")}
                </p>
                <button onClick={() => { setSearch(""); setProviderFilter(null); setStreamingOnly(false); setPage(1); }}
                  style={{
                    padding: "0.375rem 0.875rem", fontSize: "0.8125rem", fontWeight: 500,
                    borderRadius: 6, border: "1.5px solid var(--border-default)", background: "var(--bg-surface)",
                    color: "var(--text-primary)", cursor: "pointer", fontFamily: "inherit",
                  }}>{t("infra.models.clearFilters")}</button>
              </div>
            ) : view === "list" ? (
              <>
                <div style={{ overflowX: "auto" }}>
                  <table className="mr-table" style={{ minWidth: 930 }}>
                    <thead>
                      <tr>
                        <th style={{ paddingLeft: "1.125rem", width: "28%" }}>{t("field.model")}</th>
                        <th style={{ width: "15%" }}>{t("infra.models.colContext")}</th>
                        <th>{t("infra.models.colCapabilities")}</th>
                        <th style={{ textAlign: "center", width: 64 }}>{t("infra.models.stream")}</th>
                        <th style={{ textAlign: "center", width: 110 }} title={t("infra.models.reasoningHelp")}>{t("infra.models.colThinking")}</th>
                        <th style={{ textAlign: "center", width: 96 }} title={t("infra.models.showInChatHelp", { n: String(shownInChat) })}>{t("infra.models.showInChat")}</th>
                        <th style={{ width: 84 }}>{t("infra.models.colCost")}</th>
                        <th style={{ width: 84 }}>{t("infra.models.colLatency")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paged.map((m, i) => {
                        const meta = getMeta(m.model);
                        return (
                          <tr key={`${m.provider}/${m.model}`} className="mr-row" title={m.is_active ? undefined : t("infra.models.inactive")} style={{ animationDelay: `${0.3 + i * 0.03}s`, opacity: m.is_active ? 1 : 0.45 }}>
                            {/* Model identity */}
                            <td style={{ paddingLeft: "1.125rem", paddingTop: "1.25rem", paddingBottom: "1.25rem" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
                                <ProviderLogo provider={m.provider} size={36} />
                                <div>
                                  <code style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                                    fontSize: "0.8125rem", fontWeight: 600, color: "var(--text-primary)",
                                    letterSpacing: "-0.01em", lineHeight: 1.3,
                                  }}>{m.model}</code>
                                  <div style={{ display: "flex", alignItems: "center", gap: "0.3125rem", marginTop: "0.1rem" }}>
                                    <span style={{
                                      width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                                      background: brand(m.provider),
                                    }} />
                                    <span style={{
                                      fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)",
                                      textTransform: "capitalize", lineHeight: 1,
                                    }}>{m.provider}</span>
                                  </div>
                                </div>
                              </div>
                            </td>
                            {/* Context window */}
                            <td>
                              {meta?.ctx ? (
                                <ContextBar ctx={meta.ctx} ctxK={meta.ctxK} />
                              ) : (
                                <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>—</span>
                              )}
                            </td>
                            {/* Capabilities — FIX62: switches, not chips.
                                What the catalogue says a model can do is
                                the admin's to correct here. */}
                            <td>
                              <CapabilityCell item={m} onChange={setCapability} onResult={applyTestResult} />
                            </td>
                            {/* Stream */}
                            <td style={{ textAlign: "center" }}>
                              {m.supports_streaming ? (
                                <span style={{
                                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                                  width: 22, height: 22, borderRadius: "50%",
                                  background: "rgba(16,163,127,0.08)",
                                }}>
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10a37f" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                                    <polyline points="20 6 9 17 4 12"/>
                                  </svg>
                                </span>
                              ) : (
                                <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>—</span>
                              )}
                            </td>
                            {/* Thinking — the only route by which a
                                self-hosted reasoning model gets flagged;
                                no seed migration can know an operator's
                                own box. */}
                            <td style={{ textAlign: "center" }}>
                              <ReasoningCell item={m} onChange={setReasoning} />
                            </td>
                            {/* Show in chat (FIX47 Part C) */}
                            <td style={{ textAlign: "center" }}>
                              <ShowInChatSwitch item={m} onChange={setShowInChat} />
                            </td>
                            {/* Cost */}
                            <td><TierBadge value={m.cost_tier} /></td>
                            {/* Latency */}
                            <td><TierBadge value={m.latency_tier} /></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {filtered.length > PAGE_SIZE && (
                  <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setPage} />
                )}
              </>
            ) : (
              /* ── Grid view ── */
              <>
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 280px), 1fr))",
                  gap: "0.75rem", padding: "1rem",
                }}>
                  {paged.map((m, i) => {
                    const meta = getMeta(m.model);
                    return (
                      <div key={`${m.provider}/${m.model}`} className="mr-row" title={m.is_active ? undefined : t("infra.models.inactive")} style={{
                        animationDelay: `${0.3 + i * 0.03}s`, opacity: m.is_active ? 1 : 0.45,
                        background: "var(--bg-surface-2)", borderRadius: 10,
                        border: "1px solid var(--border-subtle)",
                        padding: "0.875rem 1rem",
                        display: "flex", flexDirection: "column", gap: "0.5rem",
                        transition: "border-color 150ms, box-shadow 150ms",
                      }}
                        onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--border-default)"; e.currentTarget.style.boxShadow = "0 2px 8px rgba(0,0,0,0.18)"; }}
                        onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border-subtle)"; e.currentTarget.style.boxShadow = "none"; }}
                        // Keyboard-focus parity with the mouse hover lift (mirrors providers).
                        onFocus={(e) => { e.currentTarget.style.borderColor = "var(--border-default)"; e.currentTarget.style.boxShadow = "0 2px 8px rgba(0,0,0,0.18)"; }}
                        onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border-subtle)"; e.currentTarget.style.boxShadow = "none"; }}
                      >
                        {/* Header */}
                        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                          <ProviderLogo provider={m.provider} size={32} />
                          <div>
                            <code style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                              fontSize: "0.8125rem", fontWeight: 600, color: "var(--text-primary)",
                              letterSpacing: "-0.01em",
                            }}>{m.model}</code>
                            <div style={{ display: "flex", alignItems: "center", gap: "0.3125rem", marginTop: "0.125rem" }}>
                              <span style={{ width: 7, height: 7, borderRadius: "50%", background: brand(m.provider) }} />
                              <span style={{ fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)", textTransform: "capitalize" }}>{m.provider}</span>
                            </div>
                          </div>
                        </div>
                        {/* Context */}
                        {meta?.ctx && <ContextBar ctx={meta.ctx} ctxK={meta.ctxK} />}
                        {/* Caps — FIX62: the same switches as the table view */}
                        <CapabilityCell item={m} onChange={setCapability} onResult={applyTestResult} />
                        {/* Footer badges */}
                        <div style={{ display: "flex", gap: "0.25rem", marginTop: "auto", flexWrap: "wrap" }}>
                          {m.supports_streaming && (
                            <span style={{
                              display: "inline-flex", alignItems: "center", gap: "0.1875rem",
                              padding: "0.0625rem 0.375rem", fontSize: "0.625rem", fontWeight: 600,
                              color: "#10a37f", background: "rgba(16,163,127,0.07)", borderRadius: 4,
                            }}>
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
                              {t("infra.models.stream")}
                            </span>
                          )}
                          <TierBadge value={m.cost_tier} />
                          <TierBadge value={m.latency_tier} />
                          <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: "0.375rem", fontSize: "0.625rem", color: "var(--text-tertiary)" }}>
                            {t("infra.models.showInChat")}
                            <ShowInChatSwitch item={m} onChange={setShowInChat} />
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
                {filtered.length > PAGE_SIZE && (
                  <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setPage} />
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

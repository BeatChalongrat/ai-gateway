"use client";

// Create / edit form for a custom DLP detector (Sprint 6, G).
// Conditional fields by detection_type; inline test-cases with a Run Tests
// button (edit mode) that calls the /test endpoint.

import { useState } from "react";
import { api, ApiError } from "../../lib/api";
import { useT, type DictKey } from "../../lib/i18n";
import { DETECTION_TYPE, DLP_CATEGORY, codeWord } from "../../lib/codeWords";

const BASE = "/admin/dlp/custom-detectors";

const CATEGORIES = ["pii", "identity", "financial", "credential", "secret", "network", "content_policy"];
const TYPES = ["regex", "keyword_list", "semantic"] as const;
const SEVERITIES = ["low", "medium", "high", "critical"];
const ACTIONS = ["mask", "block", "pass", "restore"];
const SEMANTIC_ENTITIES = [
  "PERSON", "LOCATION", "ORGANIZATION", "EMAIL_ADDRESS", "PHONE_NUMBER",
  "URL", "IP_ADDRESS", "DATE_TIME", "CREDIT_CARD", "US_SSN", "IBAN_CODE", "CRYPTO",
];

type DetType = (typeof TYPES)[number];
interface TestCase { input: string; expected_match: boolean; }

// A detector's category and the way it detects are shown as words, the same
// ones every page uses (AM11, lib/codeWords.ts); an option's `value` stays the
// stored code, which is what policy config, audit exports and the API carry.

// Severity and match-action ARE plain UI vocabulary, so they translate.
const SEVERITY_LABELS: Record<string, DictKey> = {
  low: "ui.severity.low", medium: "ui.severity.medium",
  high: "ui.severity.high", critical: "ui.severity.critical",
};
const ACTION_LABELS: Record<string, DictKey> = {
  mask: "ui.dlpAction.mask", block: "ui.dlpAction.block",
  pass: "ui.dlpAction.pass", restore: "ui.dlpAction.restore",
};
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export interface DetectorInitial {
  id?: string;
  name?: string;
  description?: string;
  category?: string;
  detection_type?: string;
  pattern?: Record<string, any>;
  severity?: string;
  default_action?: string;
  status?: string;
}

const lbl: React.CSSProperties = { fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4, display: "block" };
// Inputs sit on `--bg-surface` so they read above the recessed section
// cards (which use `--bg-base`). transition powers the focus ring below.
// Border uses longhand props (not the `border` shorthand) so the focus-ring
// helper can override `borderColor` alone without React warning about mixing
// shorthand and longhand style properties across re-renders.
const inp: React.CSSProperties = { width: "100%", padding: "7px 10px", fontSize: 13, borderWidth: 1, borderStyle: "solid", borderColor: "var(--border-default)", borderRadius: 6, background: "var(--bg-surface)", color: "var(--text-primary)", transition: "border-color 0.15s, box-shadow 0.15s" };
// Regex/pattern input rendered as a distinct "code block": monospace + a
// surface that differs from the plain text inputs so it reads as code.
const mono: React.CSSProperties = {
  ...inp, fontFamily: "'JetBrains Mono', 'Fira Code', ui-monospace, monospace",
  fontSize: 13, letterSpacing: "0.03em", padding: "10px 12px", background: "var(--bg-surface-2)",
};
// Native <select> with the browser arrow replaced by a themed chevron.
// currentColor is inherited from `color`, so the arrow follows the theme.
const CHEVRON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M3 5l3 3 3-3' stroke='%238B96BA' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E";
const sel: React.CSSProperties = {
  ...inp, appearance: "none", WebkitAppearance: "none", MozAppearance: "none",
  backgroundImage: `url("${CHEVRON}")`, backgroundRepeat: "no-repeat",
  backgroundPosition: "right 10px center", paddingRight: 32, cursor: "pointer",
};
// Section grouping — recessed card on the modal surface. Cards replace the
// flat field list, so the old between-section divider is gone.
const sectionCard: React.CSSProperties = {
  background: "var(--bg-base)", border: "1px solid var(--border-subtle)",
  borderRadius: 10, padding: "16px 18px", display: "flex", flexDirection: "column", gap: 12,
};
const sectionTitle: React.CSSProperties = {
  fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em",
  color: "var(--text-muted)", margin: 0,
};

// Connected button group — mirrors the status-filter tabs on the list page.
const segGroup: React.CSSProperties = {
  display: "inline-flex", border: "1px solid var(--border-default)", borderRadius: 6, overflow: "hidden",
};
function segBtn(active: boolean, first: boolean, locked: boolean): React.CSSProperties {
  return {
    padding: "6px 16px", fontSize: 13, cursor: locked ? "not-allowed" : "pointer",
    border: "none", borderLeft: first ? "none" : "1px solid var(--border-default)",
    background: active ? "rgba(30, 58, 95, 0.15)" : "transparent",
    color: active ? "var(--text-primary)" : "var(--text-secondary)",
    fontWeight: active ? 600 : 500,
    opacity: locked && !active ? 0.5 : 1,
    transition: "background 120ms, color 120ms",
  };
}

export default function CustomDetectorForm({
  initial, onSaved, onCancel,
}: {
  initial?: DetectorInitial;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  const editing = Boolean(initial?.id);
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [category, setCategory] = useState(initial?.category ?? "pii");
  const [detType, setDetType] = useState<DetType>((initial?.detection_type as DetType) ?? "regex");
  const [regex, setRegex] = useState(initial?.pattern?.regex ?? "");
  const [keywords, setKeywords] = useState((initial?.pattern?.keywords ?? []).join(", "));
  const [entity, setEntity] = useState(initial?.pattern?.entity_type ?? "PERSON");
  const [severity, setSeverity] = useState(initial?.severity ?? "medium");
  const [action, setAction] = useState(initial?.default_action ?? "mask");
  const [testCases, setTestCases] = useState<TestCase[]>([{ input: "", expected_match: true }]);
  const [testResults, setTestResults] = useState<Record<string, boolean> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [focused, setFocused] = useState("");

  // Inline styles can't express :focus, so we track the focused field and
  // graft a navy border + ring onto its style (matches the navy primary
  // buttons across the admin pages).
  const ring = (fieldName: string, base: React.CSSProperties): React.CSSProperties =>
    focused === fieldName
      ? { ...base, borderColor: "rgb(30, 58, 95)", boxShadow: "0 0 0 3px rgba(30, 58, 95, 0.22)" }
      : base;
  const fx = (fieldName: string) => ({
    onFocus: () => setFocused(fieldName),
    onBlur: () => setFocused((f) => (f === fieldName ? "" : f)),
  });

  function buildPattern(): Record<string, any> {
    if (detType === "regex") return { regex };
    if (detType === "keyword_list")
      return { keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean), case_sensitive: false };
    return { entity_type: entity };
  }

  async function save() {
    setBusy(true); setError(null);
    try {
      const body = {
        name, description: description || null, category,
        detection_type: detType, pattern: buildPattern(),
        severity, default_action: action,
        test_cases: testCases.filter((t) => t.input.trim()),
      };
      if (editing) {
        // `reason` is an audit-log value, not UI copy — stays English so the
        // audit trail reads consistently regardless of the admin's locale.
        await api.put(`${BASE}/${initial!.id}`, { ...body, reason: "edited via admin UI" });
      } else {
        await api.post(BASE, body);
      }
      onSaved();
    } catch (e) {
      const fallback = t("ui.detector.saveFailed");
      setError(e instanceof ApiError ? (e.body?.error?.message ?? fallback) : fallback);
    } finally {
      setBusy(false);
    }
  }

  async function runTests() {
    if (!editing) { setError(t("ui.detector.saveBeforeTest")); return; }
    setBusy(true); setError(null);
    try {
      const inputs = testCases.map((t) => t.input).filter(Boolean);
      const res: any = await api.post(`${BASE}/${initial!.id}/test`, { inputs });
      const map: Record<string, boolean> = {};
      for (const r of res.results) map[r.input] = r.matched;
      setTestResults(map);
    } catch {
      setError(t("ui.detector.testFailed"));
    } finally {
      setBusy(false);
    }
  }

  function setTC(i: number, patch: Partial<TestCase>) {
    setTestCases((list) => list.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  }

  function removeTC(i: number) {
    setTestCases((list) => list.filter((_, j) => j !== i));
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Identity */}
      <div style={sectionCard}>
        <div style={sectionTitle}>{t("ui.detector.section.identity")}</div>
        <div>
          <label style={lbl}>{t("field.name")}</label>
          {/* placeholder is a sample detector id — an API value, not copy */}
          <input style={ring("name", inp)} {...fx("name")} value={name} onChange={(e) => setName(e.target.value)} disabled={editing} placeholder="thai_plate_2026" />
        </div>
        <div>
          <label style={lbl}>{t("field.description")}</label>
          <textarea style={ring("description", { ...inp, minHeight: 48 })} {...fx("description")} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
      </div>

      {/* Classification */}
      <div style={sectionCard}>
        <div style={sectionTitle}>{t("ui.detector.section.classification")}</div>
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <label style={lbl}>{t("ui.detector.category")}</label>
            <select style={ring("category", sel)} {...fx("category")} value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => <option key={c} value={c}>{codeWord(DLP_CATEGORY, c, t)}</option>)}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label style={lbl}>{t("ui.detector.severity")}</label>
            <select style={ring("severity", sel)} {...fx("severity")} value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {SEVERITIES.map((s) => {
                const k = SEVERITY_LABELS[s];
                return <option key={s} value={s}>{k ? t(k) : cap(s)}</option>;
              })}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label style={lbl}>{t("ui.detector.defaultAction")}</label>
            <select style={ring("action", sel)} {...fx("action")} value={action} onChange={(e) => setAction(e.target.value)}>
              {ACTIONS.map((a) => {
                const k = ACTION_LABELS[a];
                return <option key={a} value={a}>{k ? t(k) : cap(a)}</option>;
              })}
            </select>
          </div>
        </div>
      </div>

      {/* Detection */}
      <div style={sectionCard}>
        <div style={sectionTitle}>{t("ui.detector.section.detection")}</div>
        <div>
          <label style={lbl}>{t("ui.detector.detectionType")}</label>
          <div style={segGroup} role="group" aria-label={t("ui.detector.detectionType")}>
            {TYPES.map((type, i) => (
              <button
                key={type}
                type="button"
                aria-pressed={detType === type}
                disabled={editing}
                title={editing ? t("ui.detector.typeLocked") : undefined}
                style={segBtn(detType === type, i === 0, editing)}
                onClick={() => { if (!editing) setDetType(type); }}
              >
                {codeWord(DETECTION_TYPE, type, t)}
              </button>
            ))}
          </div>
        </div>

        {detType === "regex" && (
          <div>
            <label style={lbl}>{t("ui.detector.regexPattern")}</label>
            <input style={ring("regex", mono)} {...fx("regex")} value={regex} onChange={(e) => setRegex(e.target.value)} placeholder="^[ก-ฮ]{2}[0-9]{4}$" />
          </div>
        )}
        {detType === "keyword_list" && (
          <div>
            <label style={lbl}>{t("ui.detector.keywords")}</label>
            <input style={ring("keywords", inp)} {...fx("keywords")} value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="confidential, internal-only" />
          </div>
        )}
        {detType === "semantic" && (
          <div>
            <label style={lbl}>{t("ui.detector.entityType")}</label>
            {/* Entity names are Presidio identifiers — never translated. */}
            <select style={ring("entity", sel)} {...fx("entity")} value={entity} onChange={(e) => setEntity(e.target.value)}>
              {SEMANTIC_ENTITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              {t("ui.detector.semanticNote")}
            </div>
          </div>
        )}
      </div>

      {/* Test cases */}
      <div style={sectionCard}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={sectionTitle}>{t("ui.detector.section.testCases")}</div>
          <div style={{ display: "flex", gap: 8 }}>
            {editing && <button style={btn("ghost")} onClick={runTests} disabled={busy}>{t("ui.detector.runTests")}</button>}
            <button style={btn("ghost")} onClick={() => setTestCases((tc) => [...tc, { input: "", expected_match: true }])}>+ {t("ui.detector.addCase")}</button>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {testCases.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "4px 2px" }}>{t("ui.detector.noTestCases")}</div>
          )}
          {testCases.map((tc, i) => (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "6px 8px", background: "var(--bg-surface)", border: "1px solid var(--border-default)", borderRadius: 6 }}>
              <input
                style={{ ...mono, flex: 1, background: "transparent", borderWidth: 0, padding: "2px 4px" }}
                value={tc.input} placeholder={t("ui.detector.sampleInput")}
                onChange={(e) => setTC(i, { input: e.target.value })}
              />
              <label style={{ fontSize: 12, display: "flex", gap: 4, alignItems: "center", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                <input type="checkbox" checked={tc.expected_match} onChange={(e) => setTC(i, { expected_match: e.target.checked })} />
                {t("ui.detector.shouldMatch")}
              </label>
              {testResults && tc.input in testResults && (
                <span style={{ fontSize: 12, fontWeight: 700, whiteSpace: "nowrap", color: testResults[tc.input] === tc.expected_match ? "var(--color-status-success)" : "var(--color-status-error)" }}>
                  {testResults[tc.input] === tc.expected_match
                    ? `✓ ${t("ui.detector.pass")}`
                    : `✗ ${t("ui.detector.fail")}`}
                </span>
              )}
              <button
                type="button"
                title={t("ui.detector.removeCase")}
                aria-label={t("ui.detector.removeCase")}
                onClick={() => removeTC(i)}
                style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 16, lineHeight: 1, padding: "2px 6px", borderRadius: 4 }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </div>

      {error && <div style={{ color: "var(--color-status-error)", fontSize: 13 }}>{error}</div>}

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", borderTop: "1px solid var(--border-subtle)", paddingTop: 16, marginTop: 2 }}>
        <button style={btn("ghost")} onClick={onCancel} disabled={busy}>{t("action.cancel")}</button>
        <button style={btn("primary")} onClick={save} disabled={busy || !name}>
          {editing ? t("ui.detector.saveChanges") : t("action.create")}
        </button>
      </div>
    </div>
  );
}

function btn(kind: "primary" | "ghost"): React.CSSProperties {
  return {
    padding: "7px 16px", fontSize: 13, fontWeight: 600, borderRadius: 6, cursor: "pointer",
    border: kind === "primary" ? "none" : "1px solid var(--border-default)",
    background: kind === "primary" ? "rgb(30, 58, 95)" : "transparent",
    color: kind === "primary" ? "#fff" : "var(--text-secondary)",
  };
}

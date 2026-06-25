"use client";

// Create / edit form for a custom DLP detector (Sprint 6, G).
// Conditional fields by detection_type; inline test-cases with a Run Tests
// button (edit mode) that calls the /test endpoint.

import { useState } from "react";
import { api, ApiError } from "../../lib/api";

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
const inp: React.CSSProperties = { width: "100%", padding: "7px 10px", fontSize: 13, border: "1px solid var(--border-default)", borderRadius: 6, background: "var(--bg-base)", color: "var(--text-primary)" };
const mono: React.CSSProperties = { ...inp, fontFamily: "'JetBrains Mono', monospace" };

export default function CustomDetectorForm({
  initial, onSaved, onCancel,
}: {
  initial?: DetectorInitial;
  onSaved: () => void;
  onCancel: () => void;
}) {
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
        await api.put(`${BASE}/${initial!.id}`, { ...body, reason: "edited via admin UI" });
      } else {
        await api.post(BASE, body);
      }
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? (e.body?.error?.message ?? "save failed") : "save failed");
    } finally {
      setBusy(false);
    }
  }

  async function runTests() {
    if (!editing) { setError("Save the detector first to run tests against the server."); return; }
    setBusy(true); setError(null);
    try {
      const inputs = testCases.map((t) => t.input).filter(Boolean);
      const res: any = await api.post(`${BASE}/${initial!.id}/test`, { inputs });
      const map: Record<string, boolean> = {};
      for (const r of res.results) map[r.input] = r.matched;
      setTestResults(map);
    } catch {
      setError("test run failed");
    } finally {
      setBusy(false);
    }
  }

  function setTC(i: number, patch: Partial<TestCase>) {
    setTestCases((tc) => tc.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <label style={lbl}>Name</label>
        <input style={inp} value={name} onChange={(e) => setName(e.target.value)} disabled={editing} placeholder="thai_plate_2026" />
      </div>
      <div>
        <label style={lbl}>Description</label>
        <textarea style={{ ...inp, minHeight: 48 }} value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div style={{ display: "flex", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <label style={lbl}>Category</label>
          <select style={inp} value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div style={{ flex: 1 }}>
          <label style={lbl}>Severity</label>
          <select style={inp} value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div style={{ flex: 1 }}>
          <label style={lbl}>Default action</label>
          <select style={inp} value={action} onChange={(e) => setAction(e.target.value)}>
            {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
      </div>

      <div>
        <label style={lbl}>Detection type</label>
        <div style={{ display: "flex", gap: 14 }}>
          {TYPES.map((t) => (
            <label key={t} style={{ fontSize: 13, display: "flex", gap: 5, alignItems: "center", color: "var(--text-primary)" }}>
              <input type="radio" checked={detType === t} onChange={() => setDetType(t)} disabled={editing} />
              {t}
            </label>
          ))}
        </div>
      </div>

      {detType === "regex" && (
        <div>
          <label style={lbl}>Regex pattern</label>
          <input style={mono} value={regex} onChange={(e) => setRegex(e.target.value)} placeholder="^[ก-ฮ]{2}[0-9]{4}$" />
        </div>
      )}
      {detType === "keyword_list" && (
        <div>
          <label style={lbl}>Keywords (comma-separated)</label>
          <input style={inp} value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="confidential, internal-only" />
        </div>
      )}
      {detType === "semantic" && (
        <div>
          <label style={lbl}>Entity type</label>
          <select style={inp} value={entity} onChange={(e) => setEntity(e.target.value)}>
            {SEMANTIC_ENTITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            Semantic detectors are stored but not yet executed at runtime in this release.
          </div>
        </div>
      )}

      <div>
        <label style={lbl}>Test cases</label>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {testCases.map((tc, i) => (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input style={{ ...mono, flex: 1 }} value={tc.input} placeholder="sample input"
                onChange={(e) => setTC(i, { input: e.target.value })} />
              <label style={{ fontSize: 12, display: "flex", gap: 4, alignItems: "center", color: "var(--text-secondary)" }}>
                <input type="checkbox" checked={tc.expected_match} onChange={(e) => setTC(i, { expected_match: e.target.checked })} />
                match
              </label>
              {testResults && tc.input in testResults && (
                <span style={{ fontSize: 12, fontWeight: 700, color: testResults[tc.input] === tc.expected_match ? "#059669" : "#dc2626" }}>
                  {testResults[tc.input] ? "matched" : "no match"}
                </span>
              )}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
          <button style={btn("ghost")} onClick={() => setTestCases((t) => [...t, { input: "", expected_match: true }])}>+ Add case</button>
          {editing && <button style={btn("ghost")} onClick={runTests} disabled={busy}>Run tests</button>}
        </div>
      </div>

      {error && <div style={{ color: "#dc2626", fontSize: 13 }}>{error}</div>}

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button style={btn("ghost")} onClick={onCancel} disabled={busy}>Cancel</button>
        <button style={btn("primary")} onClick={save} disabled={busy || !name}>
          {editing ? "Save changes" : "Create"}
        </button>
      </div>
    </div>
  );
}

function btn(kind: "primary" | "ghost"): React.CSSProperties {
  return {
    padding: "7px 16px", fontSize: 13, fontWeight: 600, borderRadius: 6, cursor: "pointer",
    border: kind === "primary" ? "none" : "1px solid var(--border-default)",
    background: kind === "primary" ? "var(--accent-primary)" : "transparent",
    color: kind === "primary" ? "#fff" : "var(--text-secondary)",
  };
}

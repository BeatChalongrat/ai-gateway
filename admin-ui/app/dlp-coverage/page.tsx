"use client";

import { useState, useMemo } from "react";
import { Outfit } from "next/font/google";
import { useApi } from "../../lib/useApi";
import { api, ApiError } from "../../lib/api";
import { ThemeToggle } from "../../components/ThemeToggle";

const outfit = Outfit({ subsets: ["latin"], weight: ["400", "500", "600", "700", "800"] });

/* ── Types ────────────────────────────────────────────────────────── */

type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | string;
type Method = "regex" | "ner" | "checksum" | string;

interface DetectorEntry { name: string; category: string; severity: Severity; mask_token: string; enabled: boolean; method: Method; coverage_note: string; example_patterns: string[]; gaps: string[]; }
interface CategorySummary { count: number; detectors: string[]; }
interface KnownGap { category: string; description: string; reason: string; priority: "high" | "medium" | "low" | string; }
interface CoverageResponse { summary: { total: number; by_category: Record<string, CategorySummary> }; detectors: DetectorEntry[]; known_gaps: KnownGap[]; }
interface TestFinding { detector: string; entity_type: string; category: string; severity: Severity; start: number; end: number; matched_text: string; }
interface TestResponse { findings: TestFinding[]; masked_text: string; detector_count: number; }

/* ── Tokens ───────────────────────────────────────────────────────── */

const T = {
  bg: "var(--bg-base)", card: "var(--bg-surface)",
  bl: "var(--border-default)", bm: "var(--border-default)",
  tp: "var(--text-primary)", ts: "var(--text-secondary)",
  th: "var(--text-secondary)", tt: "var(--text-muted)",
  accent: "var(--accent-primary)",
  mono: "'JetBrains Mono','Fira Code',monospace",
};

/* ── Category colors ──────────────────────────────────────────────── */

const CAT: Record<string, { color: string; bg: string; border: string }> = {
  pii:        { color: "#a16207", bg: "rgba(234,179,8,0.08)",   border: "#eab308" },
  identity:   { color: "#8b5cf6", bg: "rgba(139,92,246,0.08)",  border: "#8b5cf6" },
  financial:  { color: "#059669", bg: "rgba(16,185,129,0.08)",  border: "#10b981" },
  secret:     { color: "#4b5563", bg: "rgba(107,114,128,0.08)", border: "#6b7280" },
  credential: { color: "#dc2626", bg: "rgba(239,68,68,0.08)",   border: "#ef4444" },
  network:    { color: "#3b6cf6", bg: "rgba(59,108,246,0.08)",  border: "#3b6cf6" },
  ner:        { color: "#0d9488", bg: "rgba(20,184,166,0.08)",  border: "#14b8a6" },
};
function catStyle(c: string) { return CAT[c] || { color: T.ts, bg: "rgba(107,114,128,0.08)", border: "#6b7280" }; }

/* ── Severity / Method / Priority colors ──────────────────────────── */

const SEV: Record<string, { color: string; bg: string }> = {
  CRITICAL: { color: "#db2777", bg: "rgba(219,39,119,0.08)" },
  HIGH:     { color: "#dc2626", bg: "rgba(239,68,68,0.08)" },
  MEDIUM:   { color: "#d97706", bg: "rgba(245,158,11,0.08)" },
  LOW:      { color: "#059669", bg: "rgba(16,185,129,0.08)" },
};
const METH: Record<string, { color: string; bg: string }> = {
  regex:    { color: "#06b6d4", bg: "rgba(6,182,212,0.08)" },
  checksum: { color: "#3b6cf6", bg: "rgba(59,108,246,0.08)" },
  ner:      { color: "#d97706", bg: "rgba(245,158,11,0.08)" },
};
const PRI: Record<string, { color: string; bg: string }> = {
  high:   { color: "#dc2626", bg: "rgba(239,68,68,0.08)" },
  medium: { color: "#d97706", bg: "rgba(245,158,11,0.08)" },
  low:    { color: "#059669", bg: "rgba(16,185,129,0.08)" },
};

function Pill({ text, color, bg }: { text: string; color: string; bg: string }) {
  return <span style={{ display: "inline-block", padding: "2px 10px", fontSize: "10px", fontWeight: 700, color, background: bg, borderRadius: 100, textTransform: "uppercase", letterSpacing: "0.04em", lineHeight: 1.6 }}>{text}</span>;
}

// presidio_ner (the only ML-based detector) has category="pii"; there is
// no "ner" category in the backend. Categories here must match the
// ``category`` attribute on each detector class in
// ``backend/app/services/dlp/detectors/*.py``.
const CATEGORY_ORDER = ["identity", "financial", "credential", "secret", "pii", "network"];

/* ── CSS ──────────────────────────────────────────────────────────── */

const CSS = `
@keyframes dcFadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes dcPop{from{opacity:0;transform:scale(0.9) translateY(6px)}to{opacity:1;transform:scale(1) translateY(0)}}
@keyframes dcFadeIn{from{opacity:0}to{opacity:1}}
.dc-anim{animation:dcFadeUp 0.4s ease-out both}
.dc-pop{animation:dcPop 0.35s ease-out both}
.dc-row{animation:dcFadeUp 0.35s ease-out both}
.dc-table{width:100%;border-collapse:collapse}
.dc-table th{text-align:left;padding:0 14px;height:36px;font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:1.2px;color:${T.th};border-bottom:1px solid ${T.bl};background:var(--bg-elevated);white-space:nowrap}
.dc-table td{padding:12px 14px;border-bottom:1px solid ${T.bl};vertical-align:middle;color:${T.tp}}
.dc-table tbody tr{transition:background 100ms}
.dc-table tbody tr:hover{background:color-mix(in srgb, var(--bg-elevated) 60%, transparent)}
.dc-table tbody tr:last-child td{border-bottom:none}
`;

/* ═══════════════════════════════════════════════════════════════════ */
/* PAGE                                                                */
/* ═══════════════════════════════════════════════════════════════════ */

export default function DlpCoveragePage() {
  const { data, loading, error, refetch } = useApi<CoverageResponse>("/admin/dlp/coverage");
  const [testInput, setTestInput] = useState("");
  const [testResult, setTestResult] = useState<TestResponse | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [search, setSearch] = useState("");
  const [catFilter, setCatFilter] = useState<string | null>(null);

  async function runTest() {
    setTestError(null); setTestResult(null);
    if (!testInput.trim()) { setTestError("Enter some text to scan."); return; }
    setTesting(true);
    try { const r = await api.post("/admin/dlp/test", { text: testInput }); setTestResult(r as TestResponse); }
    catch (err) { setTestError(err instanceof ApiError ? err.body?.error?.message || `Error ${err.status}` : err instanceof Error ? err.message : "Test failed"); }
    finally { setTesting(false); }
  }

  const sortedCats = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.summary.by_category).sort(([a], [b]) => {
      const ai = CATEGORY_ORDER.indexOf(a); const bi = CATEGORY_ORDER.indexOf(b);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
  }, [data]);

  const detectorByName = useMemo(() => {
    if (!data) return new Map<string, DetectorEntry>();
    return new Map(data.detectors.map((d) => [d.name, d]));
  }, [data]);

  // Filter categories. Search matches detector name, category, severity,
  // or method — so users can search "critical" to find all critical
  // detectors, or "checksum" to find all checksum-validated ones.
  const filteredCats = useMemo(() => {
    let cats = sortedCats;
    if (catFilter) cats = cats.filter(([c]) => c === catFilter);
    if (search) {
      const q = search.toLowerCase();
      cats = cats.map(([c, info]) => {
        const filtered = info.detectors.filter((n) => {
          if (n.toLowerCase().includes(q)) return true;
          if (c.toLowerCase().includes(q)) return true;
          const d = detectorByName.get(n);
          if (!d) return false;
          return (
            d.severity.toLowerCase().includes(q) ||
            d.method.toLowerCase().includes(q)
          );
        });
        return [c, { ...info, detectors: filtered, count: filtered.length }] as [string, CategorySummary];
      }).filter(([, info]) => info.count > 0);
    }
    return cats;
  }, [sortedCats, catFilter, search, detectorByName]);

  const totalFiltered = filteredCats.reduce((s, [, i]) => s + i.count, 0);

  if (loading) {
    return (
      <div className={outfit.className} style={{ background: T.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", margin: 0 }}>DLP Coverage</h1>
          <p style={{ fontSize: 15, color: T.ts, margin: "4px 0 0" }}>Detector catalog and known gaps</p>
        </div>
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 24 }}>Loading…</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className={outfit.className} style={{ background: T.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", margin: 0 }}>DLP Coverage</h1>
          <p style={{ fontSize: 15, color: T.ts, margin: "4px 0 0" }}>Detector catalog and known gaps</p>
        </div>
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 24, color: "#dc2626" }}>
          {error || "Failed to load."} <button onClick={refetch} style={{ marginLeft: 8, padding: "4px 12px", borderRadius: 6, border: `1px solid ${T.bl}`, background: T.card, cursor: "pointer", fontSize: 13, fontFamily: "inherit" }}>Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className={outfit.className} style={{ background: T.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      <style>{CSS}</style>

      {/* ── Header ── */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", lineHeight: 1.2, margin: 0 }}>DLP Coverage</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: T.ts, margin: "4px 0 0" }}>Detector catalog and known gaps</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button onClick={refetch} style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "7px 14px", fontSize: 13, fontWeight: 500, fontFamily: "inherit",
            borderRadius: 8, border: `1px solid ${T.bl}`, background: T.card,
            color: T.ts, cursor: "pointer", transition: "all 120ms",
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
            Refresh
          </button>
          <ThemeToggle />
        </div>
      </div>

      {/* ── Summary bar ── */}
      <div className="dc-anim" style={{ animationDelay: "0.05s", background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, boxShadow: "0 1px 2px rgba(0,0,0,0.04)", padding: "22px 32px", marginBottom: 16, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        {/* Total */}
        <div style={{ paddingRight: 32, borderRight: `1px solid ${T.bl}`, marginRight: 28 }}>
          <div style={{ fontSize: "10.5px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "1.2px", color: T.th, marginBottom: 4 }}>Total Detectors</div>
          <div style={{ fontFamily: T.mono, fontSize: 32, fontWeight: 800, color: T.tp, lineHeight: 1, letterSpacing: "-0.02em" }}>{data.summary.total}</div>
        </div>
        {/* Per-category */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, flex: 1, minWidth: 0 }}>
          {sortedCats.map(([cat, info], i) => {
            const cs = catStyle(cat);
            return (
              <div key={cat} className="dc-pop" style={{
                animationDelay: `${0.1 + i * 0.06}s`,
                padding: "8px 14px", borderRadius: 10, cursor: "default",
                transition: "background 100ms", textAlign: "center", minWidth: 70,
              }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "#f3f4f6"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              >
                <Pill text={cat} color={cs.color} bg={cs.bg} />
                <div style={{ fontFamily: T.mono, fontSize: 18, fontWeight: 700, color: T.tp, marginTop: 6, lineHeight: 1 }}>{info.count}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Toolbar ── */}
      <div className="dc-anim" style={{ animationDelay: "0.15s", display: "flex", alignItems: "center", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
        {/* Search */}
        <div style={{ position: "relative", flex: "0 1 280px", minWidth: 160 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.tt} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search detectors..."
            style={{ width: "100%", padding: "6px 24px 6px 30px", fontSize: 13, fontFamily: "inherit", border: `1.5px solid ${T.bl}`, borderRadius: 6, background: "var(--bg-base)", color: T.tp, outline: "none", transition: "border-color 120ms, box-shadow 120ms, background 120ms" }}
            onFocus={(e) => { e.currentTarget.style.borderColor = T.accent; e.currentTarget.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.18)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = T.bl; e.currentTarget.style.boxShadow = "none"; e.currentTarget.style.background = "var(--bg-base)"; }}
          />
          {search && <button onClick={() => setSearch("")} style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: T.tt, fontSize: 12, padding: 2 }}>✕</button>}
        </div>

        {/* Filter chips */}
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}>
          <button onClick={() => setCatFilter(null)} style={{
            padding: "4px 10px", fontSize: "11px", fontWeight: catFilter === null ? 600 : 500, borderRadius: 6, cursor: "pointer", fontFamily: "inherit",
            border: catFilter === null ? `1.5px solid ${T.accent}` : "1.5px solid transparent",
            background: catFilter === null ? "rgba(59,108,246,0.06)" : "transparent",
            color: catFilter === null ? T.accent : T.tt, transition: "all 120ms",
          }}>All <span style={{ fontWeight: 700, fontSize: 10, marginLeft: 2 }}>{data.summary.total}</span></button>
          {sortedCats.map(([cat, info]) => {
            const cs = catStyle(cat);
            const active = catFilter === cat;
            return (
              <button key={cat} onClick={() => setCatFilter(active ? null : cat)} style={{
                display: "inline-flex", alignItems: "center", gap: 5,
                padding: "4px 10px 4px 7px", fontSize: "11px", fontWeight: active ? 600 : 500,
                borderRadius: 6, cursor: "pointer", fontFamily: "inherit",
                border: active ? `1.5px solid ${cs.border}` : "1.5px solid transparent",
                background: active ? cs.bg : "transparent",
                color: active ? cs.color : T.tt, transition: "all 120ms",
                textTransform: "capitalize",
              }}>
                <span style={{ width: 7, height: 7, borderRadius: "50%", background: cs.border, opacity: active ? 1 : 0.5, flexShrink: 0 }} />
                {cat} <span style={{ fontWeight: 700, fontSize: 10, color: active ? cs.color : T.tt }}>{info.count}</span>
              </button>
            );
          })}
        </div>

        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 12, fontWeight: 500, color: T.tt }}>{filteredCats.length} categor{filteredCats.length === 1 ? "y" : "ies"}</span>
      </div>

      {/* ── Category cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 18, marginBottom: 40 }}>
        {filteredCats.map(([cat, info], ci) => {
          const cs = catStyle(cat);
          return (
            <div key={cat} className="dc-anim" style={{
              animationDelay: `${0.2 + ci * 0.05}s`,
              background: T.card, borderRadius: 14,
              border: `1px solid ${T.bl}`, borderTop: `3px solid ${cs.border}`,
              boxShadow: "0 1px 2px rgba(0,0,0,0.04)", overflow: "hidden",
              transition: "box-shadow 150ms",
            }}
              onMouseEnter={(e) => { e.currentTarget.style.boxShadow = "0 4px 12px rgba(0,0,0,0.06)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.boxShadow = "0 1px 2px rgba(0,0,0,0.04)"; }}
            >
              {/* Header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 20px", borderBottom: "1px solid #f3f4f6" }}>
                <Pill text={cat} color={cs.color} bg={cs.bg} />
                <span style={{ fontSize: 12, fontWeight: 500, color: T.tt }}>{info.count} detector{info.count !== 1 ? "s" : ""}</span>
              </div>
              {/* Rows */}
              {info.detectors.map((name, ri) => {
                const d = detectorByName.get(name);
                if (!d) return null;
                const sv = SEV[d.severity] || SEV.MEDIUM;
                const mt = METH[d.method] || { color: T.ts, bg: "rgba(107,114,128,0.08)" };
                const isDisabled = d.enabled === false;
                return (
                  <div key={name} className="dc-row" style={{
                    animationDelay: `${0.3 + ci * 0.05 + ri * 0.02}s`,
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "12px 20px",
                    borderBottom: ri < info.detectors.length - 1 ? "1px solid #f8f9fa" : "none",
                    transition: "background 80ms",
                    opacity: isDisabled ? 0.55 : 1,
                  }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(59,108,246,0.04)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                  >
                    <span style={{
                      fontFamily: T.mono, fontSize: "12.5px", fontWeight: 600,
                      color: T.tp,
                      textDecoration: isDisabled ? "line-through" : "none",
                    }}>{name}</span>
                    <div style={{ display: "flex", gap: 5, flexShrink: 0 }}>
                      {isDisabled && (
                        <Pill text="disabled" color="#6b7280" bg="rgba(107,114,128,0.12)" />
                      )}
                      <Pill text={d.severity.toLowerCase()} color={sv.color} bg={sv.bg} />
                      <Pill text={d.method} color={mt.color} bg={mt.bg} />
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {/* ── Known gaps ── */}
      {data.known_gaps.length > 0 && (
        <div style={{ marginBottom: 32 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <h2 style={{ fontSize: 20, fontWeight: 700, color: T.tp, margin: 0 }}>Known Gaps</h2>
            <Pill text={`${data.known_gaps.length} gaps`} color="#d97706" bg="rgba(245,158,11,0.08)" />
          </div>
          <p style={{ fontSize: "13.5px", color: T.tt, margin: "0 0 14px" }}>Areas the detector catalog does not cover yet. Contributions welcome.</p>

          <div className="dc-anim" style={{
            animationDelay: "0.4s",
            background: T.card, borderRadius: 14,
            border: `1px solid ${T.bl}`, borderLeft: "3px solid #f59e0b",
            boxShadow: "0 1px 2px rgba(0,0,0,0.04)", overflow: "hidden",
          }}>
            <div className="overflow-x-auto">
            <table className="dc-table">
              <thead><tr>
                <th style={{ paddingLeft: 18 }}>Category</th>
                <th>Description</th>
                <th>Why Not Implemented</th>
                <th style={{ width: 80 }}>Priority</th>
              </tr></thead>
              <tbody>
                {data.known_gaps.map((g, i) => {
                  const cs = catStyle(g.category);
                  const pr = PRI[g.priority] || PRI.medium;
                  return (
                    <tr key={i} className="dc-row" style={{ animationDelay: `${0.45 + i * 0.03}s` }}>
                      <td style={{ paddingLeft: 18 }}><Pill text={g.category} color={cs.color} bg={cs.bg} /></td>
                      <td style={{ fontSize: 13, color: T.ts }}>{g.description}</td>
                      <td style={{ fontSize: "12.5px", color: T.tt }}>{g.reason}</td>
                      <td><Pill text={g.priority} color={pr.color} bg={pr.bg} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          </div>
        </div>
      )}

      {/* ── Quick test ── */}
      <div className="dc-anim" style={{
        animationDelay: "0.6s",
        background: T.card, borderRadius: 14,
        border: `1px solid ${T.bl}`, boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
        padding: "28px 32px",
      }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: T.tp, margin: "0 0 4px" }}>Quick Test</h2>
        <p style={{ fontSize: 13, color: T.ts, margin: "0 0 18px", lineHeight: 1.5 }}>
          Scan a message against every detector. Output is masked for review — no gateway request is made.
        </p>

        <textarea
          value={testInput} onChange={(e) => setTestInput(e.target.value)}
          placeholder="Paste or type text containing PII to test detection…"
          rows={5}
          style={{
            width: "100%", fontFamily: T.mono, fontSize: 13, padding: "14px 16px",
            borderRadius: 10, border: `1.5px solid ${T.bl}`, background: "var(--bg-elevated)",
            color: T.tp, outline: "none", resize: "vertical", lineHeight: 1.6,
            transition: "border-color 120ms, box-shadow 120ms, background 120ms",
          }}
          onFocus={(e) => { e.currentTarget.style.borderColor = T.accent; e.currentTarget.style.boxShadow = "0 0 0 3px rgba(59,108,246,0.08)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = T.bl; e.currentTarget.style.boxShadow = "none"; e.currentTarget.style.background = "var(--bg-elevated)"; }}
        />

        <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
          <button onClick={runTest} disabled={testing} style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "8px 18px", fontSize: 13, fontWeight: 600, fontFamily: "inherit",
            borderRadius: 8, border: "none", background: T.accent, color: "white",
            cursor: testing ? "wait" : "pointer", opacity: testing ? 0.7 : 1, transition: "opacity 120ms",
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            {testing ? "Scanning…" : "Run Detection"}
          </button>
          <button onClick={() => { setTestInput(""); setTestResult(null); setTestError(null); }} disabled={testing} style={{
            padding: "8px 18px", fontSize: 13, fontWeight: 500, fontFamily: "inherit",
            borderRadius: 8, border: `1.5px solid ${T.bl}`, background: T.card,
            color: T.ts, cursor: "pointer", transition: "all 120ms",
          }}>Clear</button>
        </div>

        {testError && (
          <div style={{ marginTop: 14, padding: "10px 14px", borderRadius: 8, background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.15)", color: "#dc2626", fontSize: 13 }}>
            {testError}
          </div>
        )}

        {testResult && (
          <div style={{ marginTop: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: T.tp }}>Detections found:</span>
              <span style={{ fontFamily: T.mono, fontSize: 14, fontWeight: 700, color: T.accent }}>{testResult.findings.length}</span>
            </div>

            {testResult.findings.length === 0 ? (
              <p style={{ fontSize: 13, color: T.tt, margin: 0 }}>No detectors matched.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {testResult.findings.map((f, i) => {
                  const cs = catStyle(f.category);
                  return (
                    <div key={i} className="dc-row" style={{
                      animationDelay: `${i * 0.04}s`,
                      display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
                      padding: "10px 14px", borderRadius: 8, background: "var(--bg-elevated)",
                    }}>
                      <Pill text={f.category} color={cs.color} bg={cs.bg} />
                      <code style={{ fontFamily: T.mono, fontSize: 12, fontWeight: 600, padding: "2px 8px", borderRadius: 4, background: "rgba(239,68,68,0.08)", color: "#dc2626" }}>
                        {f.matched_text.length > 40 ? `${f.matched_text.slice(0, 37)}…` : f.matched_text}
                      </code>
                      <span style={{ color: T.tt, fontSize: 12 }}>→</span>
                      <code style={{ fontFamily: T.mono, fontSize: 12, fontWeight: 600, padding: "2px 8px", borderRadius: 4, background: "rgba(16,185,129,0.08)", color: "#059669" }}>
                        {f.entity_type}
                      </code>
                    </div>
                  );
                })}
              </div>
            )}

            {testResult.masked_text && (
              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: T.th, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>Masked Output</div>
                <div style={{ fontFamily: T.mono, fontSize: 13, padding: "14px 16px", borderRadius: 10, background: "var(--bg-elevated)", border: `1px solid ${T.bl}`, whiteSpace: "pre-wrap", lineHeight: 1.6, color: T.tp }}>
                  {testResult.masked_text}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

"use client";

/**
 * /audit-log — Admin Actions page.
 *
 * Surfaces ``admin_audit_log`` — who did what to which resource, when,
 * from where, with what outcome. Distinct from /requests, which shows
 * gateway traffic.
 *
 * Data model: the backend returns a flat list of AuditRow records.
 * This page fetches up to 500 at a time (filtered server-side) and does
 * everything else client-side: stat cards, the stacked activity
 * timeline, quick/stat/search refinement, day grouping, inline
 * expansion. CSV export hits the same endpoint with format=csv so the
 * column set stays in one place.
 *
 * Visual language matches the Routing / Usage & Cost redesigns: white
 * cards on the light base, top accent lines, navy #1E3A5F primary
 * buttons and pills (no accent-primary / violet anywhere).
 */

import {
  FormEvent,
  KeyboardEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { ThemeToggle } from "../../components/ThemeToggle";
import { Icon } from "../../components/ui/Icon";

// -----------------------------------------------------------------------
// Constants + palettes
// -----------------------------------------------------------------------

const NAVY = "#1E3A5F";
const MONO = "'JetBrains Mono','Fira Code',monospace";

const PAGE_SIZE = 50;
const FETCH_LIMIT = 500; // stats + timeline + table share one fetch

function severityOf(outcome: string | null): "success" | "failure" | "neutral" {
  if (!outcome) return "neutral";
  const v = outcome.toLowerCase();
  if (v === "success" || v === "ok") return "success";
  if (v === "denied" || v === "error" || v === "fail" || v === "failed") return "failure";
  return "neutral";
}

// Table category pills.
type TableCat = "other" | "auth" | "key" | "dlp";
const TABLE_CATS: Record<TableCat, { label: string; bg: string; fg: string }> = {
  other: { label: "Other", bg: "var(--bg-subtle)", fg: "var(--text-secondary)" },
  auth: { label: "Auth", bg: "#ECFDF5", fg: "#059669" },
  key: { label: "Key", bg: "#FFFBEB", fg: "#92600A" },
  dlp: { label: "DLP", bg: "#EEF4FF", fg: "#3B82F6" },
};

function tableCatOf(action: string): TableCat {
  const a = action.toLowerCase();
  if (a.includes("key")) return "key";
  if (a.includes("dlp") || a.includes("trace")) return "dlp";
  if (a.includes("session") || a.includes("login") || a.includes("logout") || a.includes("auth")) return "auth";
  return "other";
}

// Timeline stacking categories (spec order: key → dlp/trace → failed → auth).
type TimelineCat = "dlp" | "auth" | "key" | "failed";
const TIMELINE_CATS: { key: TimelineCat; label: string; color: string }[] = [
  { key: "dlp", label: "DLP / Trace", color: "#3B82F6" },
  { key: "auth", label: "Auth", color: "#10B981" },
  { key: "key", label: "Key ops", color: "#F59E0B" },
  { key: "failed", label: "Failed", color: "#EF4444" },
];

function timelineCatOf(r: AuditRow): TimelineCat {
  const a = r.action.toLowerCase();
  if (a.includes("key")) return "key";
  if (a.includes("dlp") || a.includes("trace")) return "dlp";
  if (severityOf(r.outcome) === "failure") return "failed";
  return "auth";
}

// key_create → Key Create; cross_tenant_access → Cross-tenant Access
function formatActionLabel(action: string): string {
  return action
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/Cross Tenant/i, "Cross-tenant");
}

// -----------------------------------------------------------------------
// Types & filter state
// -----------------------------------------------------------------------

interface AuditRow {
  id: number;
  timestamp: string | null;
  actor_key_id: string;
  tenant_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: string | null;
  ip_address: string | null;
  user_agent: string | null;
  session_id_prefix: string | null;
  details: string | null;
}

interface AuditListResponse {
  items: AuditRow[];
  total: number;
  limit: number;
  offset: number;
}

interface Filters {
  date_from: string;
  date_to: string;
  actor_key_id: string;
  tenant_id: string;
  action: string;
  outcome: string;
  q: string;
}

const EMPTY_FILTERS: Filters = {
  date_from: "",
  date_to: "",
  actor_key_id: "",
  tenant_id: "",
  action: "",
  outcome: "",
  q: "",
};

type TimeWindow = "1h" | "24h" | "7d" | "30d" | "all";
type QuickCat = "" | "failed" | "key" | "cross-tenant";
type StatFilter = "" | "success" | "failed" | "key";

const TIME_WINDOW_LABELS: Record<TimeWindow, string> = {
  "1h": "Last hour",
  "24h": "Last 24h",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  all: "All time",
};

function windowToDateFrom(w: TimeWindow): string {
  if (w === "all") return "";
  const now = Date.now();
  const ms =
    w === "1h"
      ? 60 * 60 * 1000
      : w === "24h"
        ? 24 * 60 * 60 * 1000
        : w === "7d"
          ? 7 * 24 * 60 * 60 * 1000
          : 30 * 24 * 60 * 60 * 1000;
  return new Date(now - ms).toISOString();
}

// How many day-bars the timeline shows for each period.
function timelineDayCount(w: TimeWindow): number {
  if (w === "30d") return 30;
  if (w === "7d") return 7;
  if (w === "all") return 14;
  return 2; // 1h / 24h still span up to two calendar days
}

// -----------------------------------------------------------------------
// Shared small styles (advanced filter fields)
// -----------------------------------------------------------------------

const fieldLabel: React.CSSProperties = {
  display: "block",
  fontSize: 10,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.3px",
  color: "var(--text-muted)",
  marginBottom: 3,
};

const fieldInput: React.CSSProperties = {
  width: "100%",
  fontSize: 13,
  padding: "6px 10px",
  border: "1px solid var(--border-color)",
  borderRadius: 6,
  background: "var(--bg-subtle)",
  color: "var(--text-primary)",
  fontFamily: "inherit",
  boxSizing: "border-box",
};

const whiteCard: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border-color)",
  borderRadius: 10,
};

// -----------------------------------------------------------------------
// Mini icons
// -----------------------------------------------------------------------

function MiniIcon({ d, size = 14 }: { d: ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {d}
    </svg>
  );
}

const ICON_LIST = <><line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" /><line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" /></>;
const ICON_CHECK = <polyline points="20 6 9 17 4 12" />;
const ICON_ALERT = <><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" /></>;
const ICON_KEY = <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />;
const ICON_DOWNLOAD = <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></>;
const ICON_CHEVRON = <polyline points="9 18 15 12 9 6" />;

// -----------------------------------------------------------------------
// Presentational pieces
// -----------------------------------------------------------------------

function StatCard({
  accent,
  iconBg,
  iconColor,
  icon,
  label,
  value,
  sub,
  active,
  onClick,
}: {
  accent: string;
  iconBg: string;
  iconColor: string;
  icon: ReactNode;
  label: string;
  value: string;
  sub: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`audit-stat-card${active ? " active" : ""}`}
      onClick={onClick}
      style={{ ["--stat-accent" as string]: accent }}
    >
      <span
        aria-hidden
        style={{
          position: "absolute",
          top: 12,
          right: 12,
          width: 28,
          height: 28,
          borderRadius: 6,
          background: iconBg,
          color: iconColor,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {icon}
      </span>
      <span style={{ display: "block", fontSize: 11, fontWeight: 400, textTransform: "uppercase", letterSpacing: "0.5px", color: "var(--text-muted)" }}>
        {label}
      </span>
      <span style={{ display: "block", fontSize: 24, fontWeight: 700, color: "var(--text-primary)", marginTop: 4, lineHeight: 1.1, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </span>
      <span style={{ display: "block", fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>{sub}</span>
    </button>
  );
}

function CatBadge({ cat }: { cat: TableCat }) {
  const c = TABLE_CATS[cat];
  return (
    <span style={{ display: "inline-block", padding: "3px 8px", borderRadius: 10, fontSize: 10, fontWeight: 600, background: c.bg, color: c.fg }}>
      {c.label}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span style={{ color: "var(--text-muted)", fontSize: 14 }}>—</span>;
  const sev = severityOf(outcome);
  const [label, bg, fg] =
    sev === "success"
      ? ["Success", "#ECFDF5", "#059669"]
      : sev === "failure"
        ? ["Denied", "#FEF2F2", "#DC2626"]
        : [outcome, "var(--bg-subtle)", "var(--text-secondary)"];
  return (
    <span style={{ display: "inline-block", padding: "3px 8px", borderRadius: 10, fontSize: 10, fontWeight: 600, background: bg, color: fg }}>
      {label}
    </span>
  );
}

// JSON rendering for the expanded row panel.
function JsonBlock({ data }: { data: unknown }) {
  const json = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  const html = json
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"([^"]+)":/g, '<span style="color:rgba(255,255,255,0.35)">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span style="color:#10b981">"$1"</span>')
    .replace(/: (\d+\.?\d*)/g, ': <span style="color:#06b6d4">$1</span>')
    .replace(/: (true|false|null)/g, ': <span style="color:#f59e0b">$1</span>');
  return (
    <pre
      style={{
        background: "#1a1d27",
        borderRadius: 8,
        padding: "12px 14px",
        margin: 0,
        fontFamily: MONO,
        fontSize: 11,
        lineHeight: 1.65,
        color: "rgba(255,255,255,0.8)",
        overflowX: "auto",
        maxHeight: 360,
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

// -----------------------------------------------------------------------
// Main page
// -----------------------------------------------------------------------

export default function AuditLogPage() {
  const { toast } = useToast();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(EMPTY_FILTERS);
  const [timeWindow, setTimeWindow] = useState<TimeWindow>("7d");
  const [quickCat, setQuickCat] = useState<QuickCat>("");
  const [statFilter, setStatFilter] = useState<StatFilter>("");
  const [search, setSearch] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [groupByDay, setGroupByDay] = useState(true);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState<AuditRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  // ---------------------------------------------------------------------
  // The request the backend sees: appliedFilters + time-window date_from
  // + quick-category's outcome hint. "key"/"cross-tenant" quick pills
  // are refined client-side below (no single backend param maps to them).
  // ---------------------------------------------------------------------
  const effectiveFilters = useMemo<Filters>(() => {
    const eff = { ...appliedFilters };
    if (!eff.date_from && timeWindow !== "all") {
      eff.date_from = windowToDateFrom(timeWindow);
    }
    if (quickCat === "failed" && !eff.outcome) {
      eff.outcome = "denied";
    }
    return eff;
  }, [appliedFilters, timeWindow, quickCat]);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(effectiveFilters)) {
      if (v) params.set(k, v);
    }
    params.set("limit", String(FETCH_LIMIT));
    params.set("offset", "0");
    return params.toString();
  }, [effectiveFilters]);

  const reqRef = useRef(0);

  const load = useCallback(async () => {
    const reqId = ++reqRef.current;
    setLoading(true);
    setError(null);
    try {
      const body = (await api.get(`/admin/audit-log?${queryString}`)) as AuditListResponse;
      if (reqRef.current !== reqId) return; // stale response — drop
      setRows(body.items ?? []);
      setTotal(body.total ?? 0);
    } catch (err) {
      if (reqRef.current !== reqId) return;
      setError(
        err instanceof ApiError
          ? err.body?.error?.message || `Error ${err.status}`
          : err instanceof Error
            ? err.message
            : "Failed to load audit log.",
      );
    } finally {
      if (reqRef.current === reqId) setLoading(false);
    }
  }, [queryString]);

  useEffect(() => {
    load();
  }, [load]);

  // Reset pagination + expansion whenever any refinement changes.
  useEffect(() => {
    setPage(1);
    setExpanded(new Set());
  }, [queryString, quickCat, statFilter, search]);

  // ---------------------------------------------------------------------
  // Refinement chain:
  //   rows (server-filtered)
  //   → quick-category pills (key / cross-tenant need client filtering)
  //   → live search box
  //   =: scopeRows — feeds stat cards + timeline
  //   → stat-card subset
  //   =: tableRows — feeds the table
  // ---------------------------------------------------------------------
  const scopeRows = useMemo(() => {
    let out = rows;
    if (quickCat === "key") {
      out = out.filter((r) => r.action.toLowerCase().includes("key"));
    } else if (quickCat === "cross-tenant") {
      out = out.filter((r) => r.action.toLowerCase().includes("cross_tenant") || r.action.toLowerCase().includes("cross-tenant"));
    }
    if (search.trim()) {
      const s = search.trim().toLowerCase();
      out = out.filter((r) =>
        `${r.action} ${r.actor_key_id} ${r.tenant_id} ${r.resource_type}/${r.resource_id}`.toLowerCase().includes(s),
      );
    }
    return out;
  }, [rows, quickCat, search]);

  const stats = useMemo(() => {
    let success = 0;
    let failed = 0;
    let key = 0;
    scopeRows.forEach((r) => {
      const sev = severityOf(r.outcome);
      if (sev === "success") success++;
      else if (sev === "failure") failed++;
      if (r.action.toLowerCase().includes("key")) key++;
    });
    return { total: scopeRows.length, success, failed, key };
  }, [scopeRows]);

  const successRate = stats.total > 0 ? Math.round((stats.success / stats.total) * 100) : 0;

  const tableRows = useMemo(
    () =>
      scopeRows.filter((r) =>
        statFilter === "success"
          ? severityOf(r.outcome) === "success"
          : statFilter === "failed"
            ? severityOf(r.outcome) === "failure"
            : statFilter === "key"
              ? r.action.toLowerCase().includes("key")
              : true,
      ),
    [scopeRows, statFilter],
  );

  // ---------------------------------------------------------------------
  // Activity timeline buckets — one bar per day, stacked by category.
  // ---------------------------------------------------------------------
  const timeline = useMemo(() => {
    const n = timelineDayCount(timeWindow);
    const days = Array.from({ length: n }, (_, i) => {
      const d = new Date();
      d.setHours(0, 0, 0, 0);
      d.setDate(d.getDate() - (n - 1 - i));
      return d;
    });
    return days.map((d) => {
      const counts: Record<TimelineCat, number> = { dlp: 0, auth: 0, key: 0, failed: 0 };
      scopeRows.forEach((r) => {
        if (!r.timestamp) return;
        const t = new Date(r.timestamp);
        if (t.getFullYear() === d.getFullYear() && t.getMonth() === d.getMonth() && t.getDate() === d.getDate()) {
          counts[timelineCatOf(r)]++;
        }
      });
      return { day: d, counts, total: 0 };
    }).map((b) => ({ ...b, total: b.counts.dlp + b.counts.auth + b.counts.key + b.counts.failed }));
  }, [scopeRows, timeWindow]);

  const timelineMax = Math.max(1, ...timeline.map((b) => b.total));
  const labelEvery = Math.max(1, Math.ceil(timeline.length / 10));

  // ---------------------------------------------------------------------
  // Table pagination + day grouping
  // ---------------------------------------------------------------------
  const totalFiltered = tableRows.length;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE));
  const pageStart = (page - 1) * PAGE_SIZE;
  const pageRows = tableRows.slice(pageStart, pageStart + PAGE_SIZE);

  type RenderRow = { kind: "header"; key: string; day: string; count: number } | { kind: "row"; row: AuditRow };
  const grouped: RenderRow[] = useMemo(() => {
    if (!groupByDay) return pageRows.map((row) => ({ kind: "row" as const, row }));
    const out: RenderRow[] = [];
    let lastDay = "";
    const dayCount = new Map<string, number>();
    for (const r of pageRows) {
      const day = r.timestamp ? r.timestamp.slice(0, 10) : "—";
      dayCount.set(day, (dayCount.get(day) ?? 0) + 1);
    }
    for (const r of pageRows) {
      const day = r.timestamp ? r.timestamp.slice(0, 10) : "—";
      if (day !== lastDay) {
        out.push({ kind: "header", key: `h-${day}`, day, count: dayCount.get(day) ?? 0 });
        lastDay = day;
      }
      out.push({ kind: "row", row: r });
    }
    return out;
  }, [pageRows, groupByDay]);

  // ---------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------

  function handleApply(e: FormEvent) {
    e.preventDefault();
    setAppliedFilters(filters);
  }

  function handleClearAdvanced() {
    setFilters(EMPTY_FILTERS);
    setAppliedFilters(EMPTY_FILTERS);
  }

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleCopyJson(row: AuditRow) {
    try {
      await navigator.clipboard.writeText(JSON.stringify(row, null, 2));
      toast("Copied JSON to clipboard", "success");
    } catch {
      toast("Copy failed — clipboard unavailable", "error");
    }
  }

  async function handleExport() {
    setExporting(true);
    try {
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(effectiveFilters)) {
        if (v) params.set(k, v);
      }
      params.set("format", "csv");
      const base = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";
      const res = await fetch(`${base}/admin/audit-log?${params.toString()}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `admin_audit_log-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Export failed", "error");
    } finally {
      setExporting(false);
    }
  }

  function parseDetails(details: string | null): unknown {
    if (!details) return null;
    try {
      return JSON.parse(details);
    } catch {
      return details;
    }
  }

  // ---------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------

  return (
    <div className="p-4 sm:p-6 lg:p-10" style={{ maxWidth: 1400, margin: "0 auto" }}>
      {/* 1. Page header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: "var(--text-primary)", margin: 0 }}>Admin actions</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: "var(--text-secondary)", margin: "3px 0 0" }}>
            Who did what, when, and where. Click any row for full context.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            onClick={handleExport}
            disabled={exporting || totalFiltered === 0}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 6,
              border: "1px solid var(--border-color)",
              background: "var(--bg-card)",
              color: "var(--text-secondary)",
              cursor: exporting || totalFiltered === 0 ? "not-allowed" : "pointer",
              opacity: exporting || totalFiltered === 0 ? 0.6 : 1,
              fontFamily: "inherit",
            }}
          >
            <MiniIcon d={ICON_DOWNLOAD} size={13} />
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
          <ThemeToggle />
        </div>
      </div>

      {/* 2. Stat cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 12 }}>
        <StatCard
          accent="#3B82F6"
          iconBg="var(--icon-bg-blue)"
          iconColor="#3B82F6"
          icon={<MiniIcon d={ICON_LIST} />}
          label="Total events"
          value={stats.total.toLocaleString()}
          sub={TIME_WINDOW_LABELS[timeWindow]}
          active={statFilter === ""}
          onClick={() => setStatFilter("")}
        />
        <StatCard
          accent="#10B981"
          iconBg="var(--icon-bg-green)"
          iconColor="#10B981"
          icon={<MiniIcon d={ICON_CHECK} />}
          label="Success"
          value={stats.success.toLocaleString()}
          sub={`${successRate}% success rate`}
          active={statFilter === "success"}
          onClick={() => setStatFilter(statFilter === "success" ? "" : "success")}
        />
        <StatCard
          accent="#EF4444"
          iconBg="var(--icon-bg-red)"
          iconColor="#EF4444"
          icon={<MiniIcon d={ICON_ALERT} />}
          label="Failed / denied"
          value={stats.failed.toLocaleString()}
          sub={stats.failed === 0 ? "No failures" : `${stats.failed.toLocaleString()} failures`}
          active={statFilter === "failed"}
          onClick={() => setStatFilter(statFilter === "failed" ? "" : "failed")}
        />
        <StatCard
          accent="#F59E0B"
          iconBg="var(--icon-bg-amber)"
          iconColor="#F59E0B"
          icon={<MiniIcon d={ICON_KEY} />}
          label="Key operations"
          value={stats.key.toLocaleString()}
          sub="API key changes"
          active={statFilter === "key"}
          onClick={() => setStatFilter(statFilter === "key" ? "" : "key")}
        />
      </div>

      {/* 3. Activity timeline */}
      <div style={{ ...whiteCard, padding: "14px 16px", marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>Activity timeline</div>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 80 }}>
          {timeline.map((b, i) => (
            <div
              key={i}
              title={`${b.day.toLocaleDateString("en-US", { month: "short", day: "numeric" })} · ${b.total} events`}
              style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", height: "100%", minWidth: 4 }}
            >
              {b.total === 0 ? (
                <div style={{ height: 2, borderRadius: 1, background: "var(--bg-subtle)" }} />
              ) : (
                TIMELINE_CATS.map((c) =>
                  b.counts[c.key] > 0 ? (
                    <div
                      key={c.key}
                      style={{
                        height: Math.max(2, Math.round((b.counts[c.key] / timelineMax) * 76)),
                        background: c.color,
                        borderRadius: 1,
                        marginTop: 1,
                      }}
                    />
                  ) : null,
                )
              )}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 3, marginTop: 3 }}>
          {timeline.map((b, i) => (
            <div key={i} style={{ flex: 1, fontSize: 10, color: "var(--text-muted)", textAlign: "center", minWidth: 4, whiteSpace: "nowrap", overflow: "hidden" }}>
              {i % labelEvery === 0 ? b.day.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : ""}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 8, paddingTop: 6, borderTop: "1px solid var(--border-color)" }}>
          {TIMELINE_CATS.map((c) => (
            <span key={c.key} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--text-secondary)" }}>
              <span style={{ width: 7, height: 7, background: c.color, borderRadius: 1, flexShrink: 0 }} />
              {c.label}
            </span>
          ))}
        </div>
      </div>

      {/* 4. Filter bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px", color: "var(--text-muted)" }}>Period:</span>
        {(["1h", "24h", "7d", "30d", "all"] as TimeWindow[]).map((w) => {
          const active = timeWindow === w;
          return (
            <button
              key={w}
              onClick={() => setTimeWindow(w)}
              style={{
                padding: "5px 12px",
                borderRadius: 12,
                fontSize: 11,
                fontWeight: active ? 600 : 500,
                border: "none",
                cursor: "pointer",
                fontFamily: "inherit",
                background: active ? NAVY : "var(--bg-subtle)",
                color: active ? "#fff" : "var(--text-secondary)",
              }}
            >
              {w === "all" ? "All" : w}
            </button>
          );
        })}
        <span style={{ width: 6 }} />
        <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px", color: "var(--text-muted)" }}>Quick:</span>
        {(
          [
            ["failed", "Failed only", "chip-danger"],
            ["key", "Key ops", "chip-warning"],
            ["cross-tenant", "Cross-tenant", "chip-neutral"],
          ] as [QuickCat, string, string][]
        ).map(([key, label, chipClass]) => {
          const active = quickCat === key;
          return (
            <button
              key={key}
              className={chipClass}
              onClick={() => setQuickCat(active ? "" : key)}
              style={{
                padding: "5px 12px",
                borderRadius: 12,
                fontSize: 11,
                fontWeight: active ? 700 : 500,
                border: `1px solid ${active ? "currentColor" : "transparent"}`,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              {label}
            </button>
          );
        })}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search actions, actors..."
          style={{
            marginLeft: "auto",
            fontSize: 13,
            padding: "6px 12px",
            border: "1px solid var(--border-color)",
            borderRadius: 6,
            width: 220,
            background: "var(--bg-subtle)",
            color: "var(--text-primary)",
            fontFamily: "inherit",
          }}
        />
      </div>

      {/* 5. Advanced filters — collapsible */}
      <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-color)", borderRadius: 8, padding: "14px 16px", marginBottom: 12 }}>
        <button
          onClick={() => setAdvancedOpen((o) => !o)}
          aria-expanded={advancedOpen}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            fontSize: 12,
            fontWeight: 500,
            color: "var(--text-secondary)",
            background: "none",
            border: "none",
            cursor: "pointer",
            padding: 0,
            fontFamily: "inherit",
          }}
        >
          <span style={{ display: "inline-flex", transform: advancedOpen ? "rotate(90deg)" : "none", transition: "transform 120ms" }}>
            <MiniIcon d={ICON_CHEVRON} size={14} />
          </span>
          Advanced filters
        </button>
        {advancedOpen ? (
          <form onSubmit={handleApply} style={{ marginTop: 8 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr 1fr 0.8fr 1.2fr", gap: 6 }}>
              <label>
                <span style={fieldLabel}>Search</span>
                <input style={fieldInput} placeholder="resource id, action..." value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
              </label>
              <label>
                <span style={fieldLabel}>Action</span>
                <input style={fieldInput} placeholder="e.g. key_create" value={filters.action} onChange={(e) => setFilters({ ...filters, action: e.target.value })} />
              </label>
              <label>
                <span style={fieldLabel}>Tenant</span>
                <input style={fieldInput} placeholder="tenant name" value={filters.tenant_id} onChange={(e) => setFilters({ ...filters, tenant_id: e.target.value })} />
              </label>
              <label>
                <span style={fieldLabel}>Actor</span>
                <input style={fieldInput} placeholder="actor id" value={filters.actor_key_id} onChange={(e) => setFilters({ ...filters, actor_key_id: e.target.value })} />
              </label>
              <label>
                <span style={fieldLabel}>Outcome</span>
                <select style={fieldInput} value={filters.outcome} onChange={(e) => setFilters({ ...filters, outcome: e.target.value })}>
                  <option value="">All</option>
                  <option value="success">Success</option>
                  <option value="denied">Denied</option>
                  <option value="error">Error</option>
                </select>
              </label>
              <label>
                <span style={fieldLabel}>From</span>
                <input style={fieldInput} type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} />
              </label>
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 6, alignItems: "flex-end" }}>
              <label>
                <span style={fieldLabel}>To</span>
                <input style={{ ...fieldInput, width: 160 }} type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} />
              </label>
              <button
                type="submit"
                style={{ padding: "6px 16px", borderRadius: 6, fontSize: 13, fontWeight: 600, border: "none", background: NAVY, color: "#fff", cursor: "pointer", fontFamily: "inherit" }}
              >
                Apply
              </button>
              <button
                type="button"
                onClick={handleClearAdvanced}
                style={{ padding: "6px 16px", borderRadius: 6, fontSize: 13, fontWeight: 500, border: "1px solid var(--border-color)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontFamily: "inherit" }}
              >
                Clear
              </button>
            </div>
          </form>
        ) : null}
      </div>

      {/* 6. Event table */}
      {loading ? (
        <div style={{ ...whiteCard, padding: 20 }}>
          <SkeletonTable rows={10} cols={6} />
        </div>
      ) : error ? (
        <div style={{ ...whiteCard, padding: 20 }}>
          <ErrorState message={error} onRetry={load} />
        </div>
      ) : totalFiltered === 0 ? (
        <div style={{ ...whiteCard, padding: 20 }}>
          <EmptyState
            message={
              rows.length === 0 && timeWindow === "all"
                ? "No admin actions recorded yet."
                : "No actions match the current filter."
            }
            icon={<Icon name="shield" size={22} />}
          />
        </div>
      ) : (
        <div style={{ ...whiteCard, overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 16px", borderBottom: "1px solid var(--border-color)" }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
              {totalFiltered.toLocaleString()} event{totalFiltered === 1 ? "" : "s"} in scope
              {total > FETCH_LIMIT ? (
                <span style={{ fontWeight: 400, color: "#B45309", marginLeft: 6 }}>
                  (first {FETCH_LIMIT} of {total.toLocaleString()} loaded — narrow filters for full view)
                </span>
              ) : null}
            </span>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--text-secondary)", cursor: "pointer" }}>
              <input type="checkbox" checked={groupByDay} onChange={(e) => setGroupByDay(e.target.checked)} style={{ width: 16, height: 16 }} />
              Group by day
            </label>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, color: "var(--text-primary)" }}>
              <thead>
                <tr>
                  {(
                    [
                      ["Time", 65],
                      ["Category", 55],
                      ["Action", undefined],
                      ["Actor", undefined],
                      ["Tenant", 65],
                      ["Resource", undefined],
                      ["Outcome", 50],
                    ] as [string, number | undefined][]
                  ).map(([h, w]) => (
                    <th
                      key={h}
                      style={{
                        width: w,
                        textAlign: h === "Outcome" ? "right" : "left",
                        padding: "6px 16px",
                        fontSize: 9,
                        textTransform: "uppercase",
                        letterSpacing: "0.54px",
                        color: "var(--text-secondary)",
                        fontWeight: 600,
                        background: "var(--bg-elevated)",
                        borderBottom: "1px solid var(--border-color)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {grouped.map((g) =>
                  g.kind === "header" ? (
                    <tr key={g.key} style={{ background: "var(--bg-subtle)" }}>
                      <td colSpan={7} style={{ padding: "8px 16px", borderTop: "1px solid var(--border-color)", fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                        {formatDayHeader(g.day)}
                        <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
                          {g.count} event{g.count === 1 ? "" : "s"}
                        </span>
                      </td>
                    </tr>
                  ) : (
                    <AuditRowView
                      key={g.row.id}
                      row={g.row}
                      grouped={groupByDay}
                      expanded={expanded.has(g.row.id)}
                      onToggle={() => toggleExpand(g.row.id)}
                      onCopyJson={() => handleCopyJson(g.row)}
                      parseDetails={parseDetails}
                    />
                  ),
                )}
              </tbody>
            </table>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 16px", borderTop: "1px solid var(--border-color)" }}>
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
              {pageStart + 1}-{Math.min(pageStart + PAGE_SIZE, totalFiltered)} of {totalFiltered.toLocaleString()}
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <PaginationButton disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</PaginationButton>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{page}/{totalPages}</span>
              <PaginationButton disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</PaginationButton>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// -----------------------------------------------------------------------
// Row view (collapsed or expanded)
// -----------------------------------------------------------------------

function AuditRowView({
  row,
  grouped,
  expanded,
  onToggle,
  onCopyJson,
  parseDetails,
}: {
  row: AuditRow;
  grouped: boolean;
  expanded: boolean;
  onToggle: () => void;
  onCopyJson: () => void;
  parseDetails: (d: string | null) => unknown;
}) {
  const handleKey = (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggle();
    }
  };

  const ts = row.timestamp ? new Date(row.timestamp) : null;
  const timeLabel = !ts
    ? "—"
    : grouped
      ? ts.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
      : `${ts.toLocaleDateString("en-US", { month: "short", day: "numeric" })}, ${ts.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;

  const actorKind = row.session_id_prefix ? "Session" : "API key";
  const td: React.CSSProperties = { padding: "8px 16px", borderBottom: "1px solid var(--border-color)", verticalAlign: "middle" };

  return (
    <>
      <tr
        onClick={onToggle}
        onKeyDown={handleKey}
        tabIndex={0}
        role="button"
        aria-expanded={expanded}
        style={{ cursor: "pointer", background: expanded ? "rgba(30,58,95,0.04)" : undefined }}
      >
        <td style={{ ...td, fontSize: 11, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>{timeLabel}</td>
        <td style={td}>
          <CatBadge cat={tableCatOf(row.action)} />
        </td>
        <td style={{ ...td, fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>{formatActionLabel(row.action)}</td>
        <td style={td}>
          <span style={{ display: "block", fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>{actorKind}</span>
          <span
            title={row.actor_key_id}
            style={{ display: "block", fontSize: 10, fontFamily: MONO, color: "var(--text-muted)", maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {row.actor_key_id}
          </span>
        </td>
        <td style={{ ...td, fontSize: 11, color: "var(--text-primary)" }}>{row.tenant_id || "—"}</td>
        <td style={td}>
          <span
            title={`${row.resource_type}/${row.resource_id}`}
            style={{ display: "block", fontSize: 10, fontFamily: MONO, color: "var(--text-secondary)", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {row.resource_type}/{row.resource_id}
          </span>
        </td>
        <td style={{ ...td, textAlign: "right" }}>
          <OutcomeBadge outcome={row.outcome} />
        </td>
      </tr>

      {expanded ? (
        <tr style={{ background: "rgba(30,58,95,0.03)" }}>
          <td colSpan={7} style={{ padding: "12px 14px", borderBottom: "1px solid var(--border-color)" }}>
            <ExpandedDetail row={row} parseDetails={parseDetails} onCopyJson={onCopyJson} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ExpandedDetail({
  row,
  parseDetails,
  onCopyJson,
}: {
  row: AuditRow;
  parseDetails: (d: string | null) => unknown;
  onCopyJson: () => void;
}) {
  const parsed = parseDetails(row.details);
  const fullRow = {
    id: row.id,
    timestamp: row.timestamp,
    actor_key_id: row.actor_key_id,
    tenant_id: row.tenant_id,
    action: row.action,
    resource_type: row.resource_type,
    resource_id: row.resource_id,
    outcome: row.outcome,
    ip_address: row.ip_address,
    user_agent: row.user_agent,
    session_id_prefix: row.session_id_prefix,
    details: parsed,
  };

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "6px 20px", marginBottom: 10 }}>
        <DetailField label="User-Agent" value={row.user_agent || "—"} mono />
        <DetailField label="Session prefix" value={row.session_id_prefix || "—"} mono />
        <DetailField label="Resource type" value={row.resource_type} mono />
        <DetailField label="IP address" value={row.ip_address || "—"} mono />
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>
          Full event JSON
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onCopyJson();
          }}
          style={{ background: "none", border: "none", cursor: "pointer", fontSize: 12, fontWeight: 600, color: NAVY, padding: 0, fontFamily: "inherit" }}
        >
          Copy JSON
        </button>
      </div>
      <JsonBlock data={fullRow} />
    </div>
  );
}

function DetailField({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.3px", textTransform: "uppercase", color: "var(--text-muted)" }}>{label}</div>
      <div style={{ fontSize: 12, color: "var(--text-primary)", fontFamily: mono ? MONO : "inherit", marginTop: 2, wordBreak: "break-all" }}>
        {value}
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Misc helpers
// -----------------------------------------------------------------------

// "2026-06-29" → "Mon, Jun 29"
function formatDayHeader(iso: string): string {
  if (iso === "—") return "Unknown date";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function PaginationButton({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      style={{
        padding: "4px 10px",
        fontSize: 11,
        fontWeight: 500,
        borderRadius: 5,
        border: "1px solid var(--border-color)",
        background: "var(--bg-card)",
        color: "var(--text-secondary)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        fontFamily: "inherit",
      }}
    >
      {children}
    </button>
  );
}

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
import { useT, type DictKey } from "../../lib/i18n";

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

// Table category pills. `labelKey` is resolved at render time so switching
// language re-labels the pills; `dlp` carries a literal instead because the
// acronym is the same word in Thai and translating it would only obscure
// which DLP subsystem the row came from.
type TableCat = "other" | "auth" | "key" | "dlp";
const TABLE_CATS: Record<TableCat, { labelKey: DictKey | null; label?: string; bg: string; fg: string }> = {
  other: { labelKey: "obs.audit.cat.other", bg: "var(--bg-subtle)", fg: "var(--text-secondary)" },
  auth: { labelKey: "obs.audit.cat.auth", bg: "#ECFDF5", fg: "#059669" },
  key: { labelKey: "obs.audit.cat.key", bg: "#FFFBEB", fg: "#92600A" },
  dlp: { labelKey: null, label: "DLP", bg: "#EEF4FF", fg: "#3B82F6" },
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
const TIMELINE_CATS: { key: TimelineCat; label: DictKey; color: string }[] = [
  { key: "dlp", label: "obs.audit.timeline.dlp", color: "#3B82F6" },
  { key: "auth", label: "obs.audit.timeline.auth", color: "#10B981" },
  { key: "key", label: "obs.audit.timeline.key", color: "#F59E0B" },
  { key: "failed", label: "obs.audit.timeline.failed", color: "#EF4444" },
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

const TIME_WINDOW_LABELS: Record<TimeWindow, DictKey> = {
  "1h": "obs.audit.window.lastHour",
  "24h": "time.last24h",
  "7d": "time.last7d",
  "30d": "time.last30d",
  all: "time.allTime",
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
  const t = useT();
  const c = TABLE_CATS[cat];
  return (
    <span style={{ display: "inline-block", padding: "3px 8px", borderRadius: 10, fontSize: 10, fontWeight: 600, background: c.bg, color: c.fg }}>
      {c.labelKey ? t(c.labelKey) : c.label}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  const t = useT();
  if (!outcome) return <span style={{ color: "var(--text-muted)", fontSize: 14 }}>—</span>;
  const sev = severityOf(outcome);
  // An unrecognised outcome renders the backend's own value verbatim.
  const [label, bg, fg] =
    sev === "success"
      ? [t("obs.audit.outcome.success"), "#ECFDF5", "#059669"]
      : sev === "failure"
        ? [t("obs.audit.outcome.denied"), "#FEF2F2", "#DC2626"]
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
  const t = useT();
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
          ? err.body?.error?.message || t("obs.audit.errorStatus", { status: err.status })
          : err instanceof Error
            ? err.message
            : t("obs.audit.errorLoad"),
      );
    } finally {
      if (reqRef.current === reqId) setLoading(false);
    }
  }, [queryString, t]);

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
      toast(t("obs.audit.copied"), "success");
    } catch {
      toast(t("obs.audit.copyFailed"), "error");
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
      if (!res.ok) throw new Error(t("obs.audit.exportFailedStatus", { status: res.status }));
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
      toast(err instanceof Error ? err.message : t("obs.audit.exportFailed"), "error");
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
          <h1 style={{ fontSize: 30, fontWeight: 800, color: "var(--text-primary)", margin: 0 }}>{t("obs.audit.title")}</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: "var(--text-secondary)", margin: "3px 0 0" }}>
            {t("obs.audit.subtitle")}
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
            {exporting ? t("obs.audit.exporting") : t("action.exportCsv")}
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
          label={t("obs.audit.stat.total")}
          value={stats.total.toLocaleString()}
          sub={t(TIME_WINDOW_LABELS[timeWindow])}
          active={statFilter === ""}
          onClick={() => setStatFilter("")}
        />
        <StatCard
          accent="#10B981"
          iconBg="var(--icon-bg-green)"
          iconColor="#10B981"
          icon={<MiniIcon d={ICON_CHECK} />}
          label={t("obs.audit.stat.success")}
          value={stats.success.toLocaleString()}
          sub={t("obs.audit.stat.successSub", { rate: successRate })}
          active={statFilter === "success"}
          onClick={() => setStatFilter(statFilter === "success" ? "" : "success")}
        />
        <StatCard
          accent="#EF4444"
          iconBg="var(--icon-bg-red)"
          iconColor="#EF4444"
          icon={<MiniIcon d={ICON_ALERT} />}
          label={t("obs.audit.stat.failed")}
          value={stats.failed.toLocaleString()}
          sub={stats.failed === 0 ? t("obs.audit.stat.noFailures") : t("obs.audit.stat.failures", { n: stats.failed.toLocaleString() })}
          active={statFilter === "failed"}
          onClick={() => setStatFilter(statFilter === "failed" ? "" : "failed")}
        />
        <StatCard
          accent="#F59E0B"
          iconBg="var(--icon-bg-amber)"
          iconColor="#F59E0B"
          icon={<MiniIcon d={ICON_KEY} />}
          label={t("obs.audit.stat.keyOps")}
          value={stats.key.toLocaleString()}
          sub={t("obs.audit.stat.keyOpsSub")}
          active={statFilter === "key"}
          onClick={() => setStatFilter(statFilter === "key" ? "" : "key")}
        />
      </div>

      {/* 3. Activity timeline */}
      <div style={{ ...whiteCard, padding: "14px 16px", marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>{t("obs.audit.timeline.title")}</div>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 80 }}>
          {timeline.map((b, i) => (
            <div
              key={i}
              title={t("obs.audit.timeline.tooltip", {
                day: b.day.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
                count: b.total,
              })}
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
              {t(c.label)}
            </span>
          ))}
        </div>
      </div>

      {/* 4. Filter bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px", color: "var(--text-muted)" }}>{t("obs.audit.period")}</span>
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
              {w === "all" ? t("field.all") : w}
            </button>
          );
        })}
        <span style={{ width: 6 }} />
        <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px", color: "var(--text-muted)" }}>{t("obs.audit.quick")}</span>
        {(
          [
            ["failed", "obs.audit.quick.failed", "chip-danger"],
            ["key", "obs.audit.quick.key", "chip-warning"],
            ["cross-tenant", "obs.audit.quick.crossTenant", "chip-neutral"],
          ] as [QuickCat, DictKey, string][]
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
              {t(label)}
            </button>
          );
        })}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("obs.audit.searchPlaceholder")}
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
          {t("obs.audit.advanced")}
        </button>
        {advancedOpen ? (
          <form onSubmit={handleApply} style={{ marginTop: 8 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr 1fr 0.8fr 1.2fr", gap: 6 }}>
              <label>
                <span style={fieldLabel}>{t("action.search")}</span>
                <input style={fieldInput} placeholder={t("obs.audit.ph.q")} value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
              </label>
              <label>
                <span style={fieldLabel}>{t("obs.audit.field.action")}</span>
                <input style={fieldInput} placeholder={t("obs.audit.ph.action")} value={filters.action} onChange={(e) => setFilters({ ...filters, action: e.target.value })} />
              </label>
              <label>
                <span style={fieldLabel}>{t("field.tenant")}</span>
                <input style={fieldInput} placeholder={t("obs.audit.ph.tenant")} value={filters.tenant_id} onChange={(e) => setFilters({ ...filters, tenant_id: e.target.value })} />
              </label>
              <label>
                <span style={fieldLabel}>{t("obs.audit.field.actor")}</span>
                <input style={fieldInput} placeholder={t("obs.audit.ph.actor")} value={filters.actor_key_id} onChange={(e) => setFilters({ ...filters, actor_key_id: e.target.value })} />
              </label>
              <label>
                <span style={fieldLabel}>{t("obs.audit.field.outcome")}</span>
                {/* option VALUES are the backend's outcome identifiers — only the labels are translated. */}
                <select style={fieldInput} value={filters.outcome} onChange={(e) => setFilters({ ...filters, outcome: e.target.value })}>
                  <option value="">{t("field.all")}</option>
                  <option value="success">{t("obs.audit.outcome.success")}</option>
                  <option value="denied">{t("obs.audit.outcome.denied")}</option>
                  <option value="error">{t("obs.audit.outcome.error")}</option>
                </select>
              </label>
              <label>
                <span style={fieldLabel}>{t("obs.audit.field.from")}</span>
                <input style={fieldInput} type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} />
              </label>
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 6, alignItems: "flex-end" }}>
              <label>
                <span style={fieldLabel}>{t("obs.audit.field.to")}</span>
                <input style={{ ...fieldInput, width: 160 }} type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} />
              </label>
              <button
                type="submit"
                style={{ padding: "6px 16px", borderRadius: 6, fontSize: 13, fontWeight: 600, border: "none", background: NAVY, color: "#fff", cursor: "pointer", fontFamily: "inherit" }}
              >
                {t("action.apply")}
              </button>
              <button
                type="button"
                onClick={handleClearAdvanced}
                style={{ padding: "6px 16px", borderRadius: 6, fontSize: 13, fontWeight: 500, border: "1px solid var(--border-color)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontFamily: "inherit" }}
              >
                {t("action.clear")}
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
                ? t("obs.audit.emptyAll")
                : t("obs.audit.emptyFiltered")
            }
            icon={<Icon name="shield" size={22} />}
          />
        </div>
      ) : (
        <div style={{ ...whiteCard, overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 16px", borderBottom: "1px solid var(--border-color)" }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
              {t("obs.audit.inScope", { n: totalFiltered.toLocaleString() })}
              {total > FETCH_LIMIT ? (
                <span style={{ fontWeight: 400, color: "#B45309", marginLeft: 6 }}>
                  {t("obs.audit.truncated", { limit: FETCH_LIMIT, total: total.toLocaleString() })}
                </span>
              ) : null}
            </span>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--text-secondary)", cursor: "pointer" }}>
              <input type="checkbox" checked={groupByDay} onChange={(e) => setGroupByDay(e.target.checked)} style={{ width: 16, height: 16 }} />
              {t("obs.audit.groupByDay")}
            </label>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, color: "var(--text-primary)" }}>
              <thead>
                <tr>
                  {(
                    [
                      ["obs.audit.col.time", 65],
                      ["obs.audit.col.category", 55],
                      ["obs.audit.col.action", undefined],
                      ["obs.audit.col.actor", undefined],
                      ["field.tenant", 65],
                      ["obs.audit.col.resource", undefined],
                      ["obs.audit.col.outcome", 50],
                    ] as [DictKey, number | undefined][]
                  ).map(([h, w]) => (
                    <th
                      key={h}
                      style={{
                        width: w,
                        textAlign: h === "obs.audit.col.outcome" ? "right" : "left",
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
                      {t(h)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {grouped.map((g) =>
                  g.kind === "header" ? (
                    <tr key={g.key} style={{ background: "var(--bg-subtle)" }}>
                      <td colSpan={7} style={{ padding: "8px 16px", borderTop: "1px solid var(--border-color)", fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                        {formatDayHeader(g.day, t("obs.audit.unknownDate"))}
                        <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
                          {t("obs.audit.dayEvents", { n: g.count })}
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
              {t("table.showing", {
                from: pageStart + 1,
                to: Math.min(pageStart + PAGE_SIZE, totalFiltered),
                total: totalFiltered.toLocaleString(),
              })}
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <PaginationButton disabled={page <= 1} onClick={() => setPage(page - 1)}>{t("table.previous")}</PaginationButton>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{page}/{totalPages}</span>
              <PaginationButton disabled={page >= totalPages} onClick={() => setPage(page + 1)}>{t("table.next")}</PaginationButton>
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
  const t = useT();
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

  const actorKind = row.session_id_prefix ? t("obs.audit.actor.session") : t("obs.audit.actor.apiKey");
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
  const t = useT();
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
        {/* Labels are translated; the VALUES stay raw — they are what an
            operator copies into an incident ticket. */}
        <DetailField label={t("obs.audit.detail.userAgent")} value={row.user_agent || "—"} mono />
        <DetailField label={t("obs.audit.detail.sessionPrefix")} value={row.session_id_prefix || "—"} mono />
        <DetailField label={t("obs.audit.detail.resourceType")} value={row.resource_type} mono />
        <DetailField label={t("obs.audit.detail.ip")} value={row.ip_address || "—"} mono />
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>
          {t("obs.audit.detail.fullJson")}
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onCopyJson();
          }}
          style={{ background: "none", border: "none", cursor: "pointer", fontSize: 12, fontWeight: 600, color: NAVY, padding: 0, fontFamily: "inherit" }}
        >
          {t("obs.copyJson")}
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
function formatDayHeader(iso: string, unknownLabel: string): string {
  if (iso === "—") return unknownLabel;
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

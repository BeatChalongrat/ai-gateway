"use client";

/**
 * /audit-log — Admin Actions page (GCP / Datadog-style audit viewer).
 *
 * Surfaces ``admin_audit_log`` — who did what to which resource, when,
 * from where, with what outcome. Distinct from /requests, which shows
 * gateway traffic.
 *
 * Data model: the backend returns a flat list of AuditRow records.
 * This page fetches up to 500 at a time (filtered server-side) and
 * does everything else client-side: summary KPIs, severity stripes,
 * day grouping, inline expansion, linked-entity click-to-filter,
 * quick-chip time windows. CSV export hits the same endpoint with
 * format=csv so the column set stays in one place.
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

// -----------------------------------------------------------------------
// Theme tokens
// -----------------------------------------------------------------------

const T = {
  bg: "var(--bg-base)",
  card: "var(--bg-surface)",
  elev: "var(--bg-elevated)",
  bl: "var(--border-default)",
  tp: "var(--text-primary)",
  ts: "var(--text-secondary)",
  tt: "var(--text-muted)",
  accent: "var(--accent-primary)",
  green: "var(--accent-success)",
  red: "var(--accent-danger)",
  amber: "var(--accent-warning, #d97706)",
  mono: "'JetBrains Mono','Fira Code',monospace",
  dark: "#1a1d27",
};

// Severity palette — each lookup returns (fg, bg, stripe). Kept in
// one place so the pill, the row stripe, and the summary KPI all
// stay in sync. Any action with outcome NULL gets neutral — we only
// color when we know the outcome.
const SEVERITY: Record<
  "success" | "failure" | "warning" | "neutral",
  { fg: string; bg: string; stripe: string }
> = {
  success: { fg: "#059669", bg: "rgba(16,185,129,0.08)", stripe: "#10b981" },
  failure: { fg: "#dc2626", bg: "rgba(239,68,68,0.08)", stripe: "#ef4444" },
  warning: { fg: "#b45309", bg: "rgba(217,119,6,0.08)",  stripe: "#d97706" },
  neutral: { fg: "var(--text-muted)", bg: "rgba(107,114,128,0.08)", stripe: "#9ca3af" },
};

function severityOf(outcome: string | null): keyof typeof SEVERITY {
  if (!outcome) return "neutral";
  const v = outcome.toLowerCase();
  if (v === "success" || v === "ok") return "success";
  if (v === "denied" || v === "error" || v === "fail" || v === "failed") return "failure";
  if (v === "warning" || v === "warn") return "warning";
  return "neutral";
}

// -----------------------------------------------------------------------
// Action categorization (icon + category color + label).
// Action names are free-form strings from the backend, so we match
// prefixes rather than maintaining a hard-coded registry. New action
// types the backend emits will fall into "other" until explicitly
// categorized here — safer than breaking on unknown actions.
// -----------------------------------------------------------------------

type Category = "key" | "tenant" | "cross-tenant" | "session" | "policy" | "other";

const CATEGORIES: Record<
  Category,
  { label: string; color: string; Icon: (p: { size?: number }) => ReactNode }
> = {
  key: {
    label: "Key ops",
    color: "#06b6d4",
    Icon: ({ size = 13 }) => (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
      </svg>
    ),
  },
  tenant: {
    label: "Tenant",
    color: "#6366f1",
    Icon: ({ size = 13 }) => (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
        <polyline points="9 22 9 12 15 12 15 22" />
      </svg>
    ),
  },
  "cross-tenant": {
    label: "Cross-tenant",
    color: "#d97706",
    Icon: ({ size = 13 }) => (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 00-3-3.87" />
        <path d="M16 3.13a4 4 0 010 7.75" />
      </svg>
    ),
  },
  session: {
    label: "Session",
    color: "#8b5cf6",
    Icon: ({ size = 13 }) => (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4" />
        <polyline points="10 17 15 12 10 7" />
        <line x1="15" y1="12" x2="3" y2="12" />
      </svg>
    ),
  },
  policy: {
    label: "Policy",
    color: "#10b981",
    Icon: ({ size = 13 }) => (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      </svg>
    ),
  },
  other: {
    label: "Other",
    color: "#6b7280",
    Icon: ({ size = 13 }) => (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <circle cx="12" cy="12" r="9" />
      </svg>
    ),
  },
};

function categoryOf(action: string): Category {
  const a = action.toLowerCase();
  if (a.includes("cross_tenant") || a.includes("cross-tenant")) return "cross-tenant";
  if (a.startsWith("key_") || a.includes("api_key")) return "key";
  if (a.startsWith("tenant_")) return "tenant";
  if (a.startsWith("session_") || a.startsWith("login") || a.startsWith("logout")) return "session";
  if (a.startsWith("policy_") || a.startsWith("keyword_")) return "policy";
  return "other";
}

// Pretty-print an action name for display:
//   key_create             → Key · Create
//   cross_tenant_access    → Cross-tenant access
//   session_login_success  → Session · Login success
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

const PAGE_SIZE = 50;
const FETCH_LIMIT = 500;  // summary + table share one fetch

// -----------------------------------------------------------------------
// Small presentational helpers
// -----------------------------------------------------------------------

function KpiCard({
  label,
  value,
  accent,
  subtitle,
}: {
  label: string;
  value: string | number;
  accent: string;
  subtitle?: string;
}) {
  return (
    <div
      style={{
        background: T.card,
        border: `1px solid ${T.bl}`,
        borderRadius: 12,
        padding: "14px 16px",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        aria-hidden
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: 3,
          height: "100%",
          background: accent,
          borderRadius: "0 2px 2px 0",
        }}
      />
      <div
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          color: T.tt,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 24,
          fontWeight: 700,
          color: T.tp,
          marginTop: 4,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
      {subtitle ? (
        <div style={{ fontSize: 11, color: T.tt, marginTop: 2 }}>{subtitle}</div>
      ) : null}
    </div>
  );
}

// 24-bucket sparkline of event counts over the last 24h.
function Sparkline24h({ rows }: { rows: AuditRow[] }) {
  const now = Date.now();
  const buckets = useMemo(() => {
    const b = Array.from({ length: 24 }, () => ({ success: 0, failure: 0, other: 0 }));
    rows.forEach((r) => {
      if (!r.timestamp) return;
      const age = now - new Date(r.timestamp).getTime();
      const hour = Math.floor(age / (60 * 60 * 1000));
      if (hour < 0 || hour >= 24) return;
      const idx = 23 - hour;
      const sev = severityOf(r.outcome);
      if (sev === "success") b[idx].success++;
      else if (sev === "failure") b[idx].failure++;
      else b[idx].other++;
    });
    return b;
  }, [rows, now]);

  const max = Math.max(1, ...buckets.map((b) => b.success + b.failure + b.other));

  return (
    <div
      style={{
        background: T.card,
        border: `1px solid ${T.bl}`,
        borderRadius: 12,
        padding: "14px 16px",
        display: "flex",
        alignItems: "center",
        gap: 14,
        minHeight: 72,
      }}
    >
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          color: T.tt,
          flexShrink: 0,
        }}
      >
        24h
      </span>
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 3,
          height: 40,
          flex: 1,
        }}
      >
        {buckets.map((b, i) => {
          const total = b.success + b.failure + b.other;
          const h = total > 0 ? Math.max(4, Math.round((total / max) * 40)) : 2;
          const hasFailure = b.failure > 0;
          const onlyNeutral = total > 0 && b.success === 0 && b.failure === 0;
          const color = hasFailure
            ? SEVERITY.failure.stripe
            : onlyNeutral
              ? SEVERITY.neutral.stripe
              : SEVERITY.success.stripe;
          return (
            <div
              key={i}
              title={`${23 - i}h ago · ${total} events`}
              style={{
                flex: 1,
                minWidth: 3,
                height: h,
                borderRadius: "3px 3px 0 0",
                background: total > 0 ? color : "rgba(156,163,175,0.15)",
                opacity: 0.85,
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

// A small action-category chip: icon + label, colored by category.
function CategoryBadge({ category }: { category: Category }) {
  const c = CATEGORIES[category];
  const Icon = c.Icon;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 100,
        background: `${c.color}18`,
        color: c.color,
        fontSize: 11,
        fontWeight: 600,
      }}
    >
      <Icon />
      {c.label}
    </span>
  );
}

function OutcomePill({ outcome }: { outcome: string | null }) {
  const sev = severityOf(outcome);
  const p = SEVERITY[sev];
  const label = outcome || "—";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 12,
        fontFamily: T.mono,
        fontWeight: 500,
        color: p.fg,
        background: p.bg,
      }}
    >
      {label}
    </span>
  );
}

// Active filter chips: the currently-applied filters rendered as
// removable tags. ×  clears just that one filter and re-applies.
function ActiveFilterChip({
  label,
  value,
  onRemove,
}: {
  label: string;
  value: string;
  onRemove: () => void;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 4px 4px 10px",
        borderRadius: 100,
        background: "rgba(99,102,241,0.10)",
        color: "var(--accent-primary)",
        border: `1px solid rgba(99,102,241,0.25)`,
        fontSize: 12,
        fontWeight: 500,
      }}
    >
      <span style={{ opacity: 0.7 }}>{label}:</span> {value}
      <button
        onClick={onRemove}
        aria-label={`Remove ${label} filter`}
        style={{
          border: "none",
          background: "transparent",
          color: "inherit",
          cursor: "pointer",
          fontSize: 14,
          lineHeight: 1,
          padding: "0 4px",
          fontFamily: "inherit",
        }}
      >
        ×
      </button>
    </span>
  );
}

function QuickChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "5px 11px",
        borderRadius: 100,
        fontSize: 12,
        fontWeight: 500,
        border: `1px solid ${active ? "var(--accent-primary)" : T.bl}`,
        background: active ? "rgba(99,102,241,0.12)" : T.card,
        color: active ? "var(--accent-primary)" : T.ts,
        cursor: "pointer",
        fontFamily: "inherit",
        transition: "background 120ms, border-color 120ms",
      }}
    >
      {children}
    </button>
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
        background: T.dark,
        borderRadius: 8,
        padding: "12px 14px",
        margin: 0,
        fontFamily: T.mono,
        fontSize: "11px",
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

type TimeWindow = "1h" | "24h" | "7d" | "30d" | "all";
type QuickCategoryFilter = "" | "failed" | "key" | "cross-tenant";

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

export default function AuditLogPage() {
  const { toast } = useToast();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(EMPTY_FILTERS);
  const [timeWindow, setTimeWindow] = useState<TimeWindow>("7d");
  const [quickCat, setQuickCat] = useState<QuickCategoryFilter>("");
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
  // + quick-category's outcome/action hint. Quick filters translate to
  // concrete backend params so server-side counts stay accurate.
  // ---------------------------------------------------------------------
  const effectiveFilters = useMemo<Filters>(() => {
    const eff = { ...appliedFilters };
    // Time window override — the chip always wins over any blank
    // date_from; a manually-entered date_from in the advanced form
    // overrides the chip.
    if (!eff.date_from && timeWindow !== "all") {
      eff.date_from = windowToDateFrom(timeWindow);
    }
    // Quick-category chips map to concrete params.
    if (quickCat === "failed" && !eff.outcome) {
      eff.outcome = "denied";
    }
    // "key" and "cross-tenant" map to action substrings. The backend
    // expects exact action match; for those we filter client-side by
    // leaving eff.action empty and post-filtering the result.
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
      const body = (await api.get(
        `/admin/audit-log?${queryString}`,
      )) as AuditListResponse;
      if (reqRef.current !== reqId) return;  // stale response — drop
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

  // Reset pagination whenever the query changes.
  useEffect(() => {
    setPage(1);
    setExpanded(new Set());
  }, [queryString]);

  // ---------------------------------------------------------------------
  // Client-side post-filter for quick-category chips that don't map to
  // a single backend param (key ops, cross-tenant), plus free-text
  // refinement when quick-category isn't set.
  // ---------------------------------------------------------------------
  const refinedRows = useMemo(() => {
    if (quickCat === "key") {
      return rows.filter((r) => categoryOf(r.action) === "key");
    }
    if (quickCat === "cross-tenant") {
      return rows.filter((r) => categoryOf(r.action) === "cross-tenant");
    }
    return rows;
  }, [rows, quickCat]);

  const totalFiltered = refinedRows.length;

  // Summary stats computed over the full filtered fetch (not just the
  // visible page) so the KPIs reflect the scope the user has selected.
  const summary = useMemo(() => {
    const s = {
      total: refinedRows.length,
      success: 0,
      failure: 0,
      warning: 0,
      neutral: 0,
      categories: { key: 0, tenant: 0, "cross-tenant": 0, session: 0, policy: 0, other: 0 } as Record<Category, number>,
    };
    refinedRows.forEach((r) => {
      const sev = severityOf(r.outcome);
      s[sev]++;
      s.categories[categoryOf(r.action)]++;
    });
    return s;
  }, [refinedRows]);

  const totalPages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE));
  const pageStart = (page - 1) * PAGE_SIZE;
  const pageRows = refinedRows.slice(pageStart, pageStart + PAGE_SIZE);

  // Day-grouping the visible page. Returns a list of either a "header"
  // marker or a row, so the render loop can emit day headers inline.
  type Row = { kind: "header"; key: string; day: string; count: number }
           | { kind: "row"; row: AuditRow };
  const grouped: Row[] = useMemo(() => {
    if (!groupByDay) return pageRows.map((row) => ({ kind: "row" as const, row }));
    const out: Row[] = [];
    let lastDay = "";
    // Tally counts per day for the header.
    const dayCount = new Map<string, number>();
    for (const r of pageRows) {
      const day = r.timestamp ? r.timestamp.slice(0, 10) : "—";
      dayCount.set(day, (dayCount.get(day) ?? 0) + 1);
    }
    for (const r of pageRows) {
      const day = r.timestamp ? r.timestamp.slice(0, 10) : "—";
      if (day !== lastDay) {
        out.push({
          kind: "header",
          key: `h-${day}`,
          day,
          count: dayCount.get(day) ?? 0,
        });
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

  function handleClearAll() {
    setFilters(EMPTY_FILTERS);
    setAppliedFilters(EMPTY_FILTERS);
    setTimeWindow("all");
    setQuickCat("");
  }

  function removeFilter(key: keyof Filters) {
    const next = { ...appliedFilters, [key]: "" };
    setAppliedFilters(next);
    setFilters(next);
  }

  function setFilterAndApply(patch: Partial<Filters>) {
    const next = { ...appliedFilters, ...patch };
    setAppliedFilters(next);
    setFilters(next);
    // Scroll to top of the page on filter-by-click so the user sees
    // the summary bar update in place instead of the click vanishing
    // mid-scroll.
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
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
      // The admin-UI reads backend via process.env.NEXT_PUBLIC_API_BASE_URL
      // on the browser; the original code path used a relative URL
      // which 404'd against the admin-UI's own origin. Match the
      // lib/api.ts convention so CSV actually hits the backend.
      const base =
        process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";
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

  // Parse details column into structured JSON when possible — the
  // backend stores it as a text blob; expanding treats it like JSON
  // if it parses, else shows the raw string.
  function parseDetails(details: string | null): unknown {
    if (!details) return null;
    try {
      return JSON.parse(details);
    } catch {
      return details;
    }
  }

  const activeFilterEntries = useMemo(() => {
    const e: { key: keyof Filters; label: string; value: string }[] = [];
    if (appliedFilters.q) e.push({ key: "q", label: "Search", value: appliedFilters.q });
    if (appliedFilters.action) e.push({ key: "action", label: "Action", value: appliedFilters.action });
    if (appliedFilters.tenant_id) e.push({ key: "tenant_id", label: "Tenant", value: appliedFilters.tenant_id });
    if (appliedFilters.actor_key_id) e.push({ key: "actor_key_id", label: "Actor", value: appliedFilters.actor_key_id });
    if (appliedFilters.outcome) e.push({ key: "outcome", label: "Outcome", value: appliedFilters.outcome });
    if (appliedFilters.date_from) e.push({ key: "date_from", label: "From", value: appliedFilters.date_from });
    if (appliedFilters.date_to) e.push({ key: "date_to", label: "To", value: appliedFilters.date_to });
    return e;
  }, [appliedFilters]);

  // ---------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------

  return (
    <div style={{ padding: "2rem 2.5rem", maxWidth: 1400, margin: "0 auto" }}>
      {/* Header */}
      <div
        className="sran-page-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div>
          <h1 className="sran-page-title">Admin Actions</h1>
          <p className="sran-page-subtitle">
            Who did what, when, and where. Click any row for full context.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            onClick={handleExport}
            disabled={exporting || totalFiltered === 0}
            style={{
              padding: "7px 13px",
              fontSize: 12.5,
              fontWeight: 500,
              borderRadius: 6,
              border: `1px solid ${T.bl}`,
              background: T.card,
              color: exporting || totalFiltered === 0 ? T.tt : T.ts,
              cursor: exporting || totalFiltered === 0 ? "not-allowed" : "pointer",
              fontFamily: "inherit",
            }}
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
          <ThemeToggle />
        </div>
      </div>

      {/* Summary KPIs + sparkline */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 10,
          marginTop: 14,
        }}
      >
        <KpiCard
          label="Total events"
          value={summary.total.toLocaleString()}
          accent={SEVERITY.neutral.stripe}
          subtitle={TIME_WINDOW_LABELS[timeWindow]}
        />
        <KpiCard
          label="Success"
          value={summary.success.toLocaleString()}
          accent={SEVERITY.success.stripe}
        />
        <KpiCard
          label="Failure / denied"
          value={summary.failure.toLocaleString()}
          accent={SEVERITY.failure.stripe}
        />
        <KpiCard
          label="Key ops"
          value={summary.categories.key.toLocaleString()}
          accent={CATEGORIES.key.color}
        />
        <KpiCard
          label="Cross-tenant"
          value={summary.categories["cross-tenant"].toLocaleString()}
          accent={CATEGORIES["cross-tenant"].color}
        />
        <KpiCard
          label="Session events"
          value={summary.categories.session.toLocaleString()}
          accent={CATEGORIES.session.color}
        />
      </div>

      <div style={{ marginTop: 10 }}>
        <Sparkline24h rows={refinedRows} />
      </div>

      {/* Quick-filter chip strip */}
      <div
        style={{
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
          marginTop: 14,
          alignItems: "center",
        }}
      >
        <span
          style={{
            fontSize: 10.5,
            fontWeight: 700,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            color: T.tt,
            marginRight: 4,
          }}
        >
          Period
        </span>
        {(["1h", "24h", "7d", "30d", "all"] as TimeWindow[]).map((w) => (
          <QuickChip
            key={w}
            active={timeWindow === w}
            onClick={() => setTimeWindow(w)}
          >
            {TIME_WINDOW_LABELS[w]}
          </QuickChip>
        ))}
        <span
          style={{
            width: 1,
            height: 20,
            background: T.bl,
            margin: "0 4px",
          }}
        />
        <QuickChip active={quickCat === "failed"} onClick={() => setQuickCat(quickCat === "failed" ? "" : "failed")}>
          Failed only
        </QuickChip>
        <QuickChip active={quickCat === "key"} onClick={() => setQuickCat(quickCat === "key" ? "" : "key")}>
          Key operations
        </QuickChip>
        <QuickChip active={quickCat === "cross-tenant"} onClick={() => setQuickCat(quickCat === "cross-tenant" ? "" : "cross-tenant")}>
          Cross-tenant
        </QuickChip>
      </div>

      {/* Active filter chips (only when any advanced filter is set) */}
      {activeFilterEntries.length > 0 ? (
        <div
          style={{
            display: "flex",
            gap: 6,
            flexWrap: "wrap",
            marginTop: 10,
            alignItems: "center",
          }}
        >
          {activeFilterEntries.map((f) => (
            <ActiveFilterChip
              key={f.key}
              label={f.label}
              value={f.value}
              onRemove={() => removeFilter(f.key)}
            />
          ))}
          <button
            onClick={handleClearAll}
            style={{
              fontSize: 11,
              fontWeight: 500,
              color: T.ts,
              background: "none",
              border: "none",
              cursor: "pointer",
              textDecoration: "underline",
              padding: "0 4px",
              fontFamily: "inherit",
            }}
          >
            Clear all
          </button>
        </div>
      ) : null}

      {/* Advanced filter form — collapsible */}
      <details
        open={advancedOpen}
        onToggle={(e) => setAdvancedOpen((e.target as HTMLDetailsElement).open)}
        style={{
          marginTop: 14,
          background: T.card,
          border: `1px solid ${T.bl}`,
          borderRadius: 12,
        }}
      >
        <summary
          style={{
            padding: "10px 14px",
            fontSize: 12,
            fontWeight: 500,
            color: T.ts,
            cursor: "pointer",
            listStyle: "none",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <svg
            width="10"
            height="10"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            style={{
              transform: advancedOpen ? "rotate(90deg)" : "rotate(0)",
              transition: "transform 120ms",
            }}
          >
            <polyline points="9 18 15 12 9 6" />
          </svg>
          Advanced filters
        </summary>
        <form
          onSubmit={handleApply}
          style={{
            padding: "0 14px 14px",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 10,
          }}
        >
          <FilterInput
            label="Search"
            placeholder="resource id, action…"
            value={filters.q}
            onChange={(v) => setFilters({ ...filters, q: v })}
          />
          <FilterInput
            label="Action"
            placeholder="e.g. key_create"
            value={filters.action}
            onChange={(v) => setFilters({ ...filters, action: v })}
          />
          <FilterInput
            label="Tenant"
            value={filters.tenant_id}
            onChange={(v) => setFilters({ ...filters, tenant_id: v })}
          />
          <FilterInput
            label="Actor"
            value={filters.actor_key_id}
            onChange={(v) => setFilters({ ...filters, actor_key_id: v })}
          />
          <FilterSelect
            label="Outcome"
            value={filters.outcome}
            options={[
              ["", "All"],
              ["success", "Success"],
              ["denied", "Denied"],
              ["error", "Error"],
            ]}
            onChange={(v) => setFilters({ ...filters, outcome: v })}
          />
          <FilterInput
            label="From"
            type="datetime-local"
            value={filters.date_from}
            onChange={(v) => setFilters({ ...filters, date_from: v })}
          />
          <FilterInput
            label="To"
            type="datetime-local"
            value={filters.date_to}
            onChange={(v) => setFilters({ ...filters, date_to: v })}
          />
          <div style={{ display: "flex", alignItems: "flex-end", gap: 6 }}>
            <button
              type="submit"
              style={{
                padding: "8px 14px",
                fontSize: 13,
                fontWeight: 500,
                borderRadius: 6,
                border: "none",
                background: T.accent,
                color: "white",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              Apply
            </button>
            <button
              type="button"
              onClick={() => {
                setFilters(EMPTY_FILTERS);
                setAppliedFilters(EMPTY_FILTERS);
              }}
              style={{
                padding: "8px 14px",
                fontSize: 13,
                fontWeight: 500,
                borderRadius: 6,
                border: `1px solid ${T.bl}`,
                background: T.card,
                color: T.ts,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              Clear
            </button>
          </div>
        </form>
      </details>

      {/* View-mode toggle */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: 14,
        }}
      >
        <div style={{ fontSize: 12, color: T.tt }}>
          {loading
            ? "Loading…"
            : totalFiltered > 0
              ? `${totalFiltered.toLocaleString()} event${totalFiltered === 1 ? "" : "s"} in scope`
              : ""}
          {total > FETCH_LIMIT ? (
            <span style={{ color: T.amber, marginLeft: 6 }}>
              · showing first {FETCH_LIMIT.toLocaleString()} of {total.toLocaleString()} — narrow filters for full view
            </span>
          ) : null}
        </div>
        <label
          style={{
            fontSize: 12,
            color: T.ts,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={groupByDay}
            onChange={(e) => setGroupByDay(e.target.checked)}
          />
          Group by day
        </label>
      </div>

      {/* Results */}
      <div style={{ marginTop: 10 }}>
        {loading ? (
          <div
            style={{
              background: T.card,
              borderRadius: 12,
              border: `1px solid ${T.bl}`,
              padding: 20,
            }}
          >
            <SkeletonTable rows={10} cols={6} />
          </div>
        ) : error ? (
          <div
            style={{
              background: T.card,
              borderRadius: 12,
              border: `1px solid ${T.bl}`,
              padding: 20,
            }}
          >
            <ErrorState message={error} onRetry={load} />
          </div>
        ) : totalFiltered === 0 ? (
          <div
            style={{
              background: T.card,
              borderRadius: 12,
              border: `1px solid ${T.bl}`,
              padding: 20,
            }}
          >
            <EmptyState
              message={
                activeFilterEntries.length === 0 && quickCat === "" && timeWindow === "all"
                  ? "No admin actions recorded yet."
                  : "No actions match the current filter."
              }
              icon="🛡"
            />
          </div>
        ) : (
          <div
            style={{
              background: T.card,
              borderRadius: 12,
              border: `1px solid ${T.bl}`,
              overflow: "hidden",
            }}
          >
            <div style={{ overflowX: "auto" }}>
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: 13,
                }}
              >
                <thead style={{ background: T.elev }}>
                  <tr>
                    <Th style={{ width: 8 }} />{/* severity stripe column */}
                    <Th>Timestamp</Th>
                    <Th>Category</Th>
                    <Th>Action</Th>
                    <Th>Actor</Th>
                    <Th>Tenant</Th>
                    <Th>Resource</Th>
                    <Th>Outcome</Th>
                    <Th>IP</Th>
                  </tr>
                </thead>
                <tbody>
                  {grouped.map((g) =>
                    g.kind === "header" ? (
                      <tr key={g.key} style={{ background: "var(--bg-elevated)" }}>
                        <td
                          colSpan={9}
                          style={{
                            padding: "8px 14px",
                            fontSize: 11,
                            fontWeight: 700,
                            letterSpacing: "0.08em",
                            textTransform: "uppercase",
                            color: T.tt,
                            borderTop: `1px solid ${T.bl}`,
                            borderBottom: `1px solid ${T.bl}`,
                          }}
                        >
                          {formatDayHeader(g.day)}
                          <span style={{ fontWeight: 500, marginLeft: 8, color: T.tt }}>
                            {g.count} event{g.count === 1 ? "" : "s"}
                          </span>
                        </td>
                      </tr>
                    ) : (
                      <AuditRowView
                        key={g.row.id}
                        row={g.row}
                        expanded={expanded.has(g.row.id)}
                        onToggle={() => toggleExpand(g.row.id)}
                        onFilterBy={setFilterAndApply}
                        onCopyJson={() => handleCopyJson(g.row)}
                        parseDetails={parseDetails}
                      />
                    ),
                  )}
                </tbody>
              </table>
            </div>

            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "10px 14px",
                borderTop: `1px solid ${T.bl}`,
                fontSize: 12,
                color: T.tt,
                fontFamily: T.mono,
              }}
            >
              <span>
                {totalFiltered > 0
                  ? `${pageStart + 1}-${Math.min(pageStart + PAGE_SIZE, totalFiltered)} of ${totalFiltered.toLocaleString()}`
                  : "0 results"}
              </span>
              <div style={{ display: "flex", gap: 6 }}>
                <PaginationButton
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                >
                  Prev
                </PaginationButton>
                <span style={{ padding: "4px 6px" }}>
                  {page}/{totalPages}
                </span>
                <PaginationButton
                  disabled={page >= totalPages}
                  onClick={() => setPage(page + 1)}
                >
                  Next
                </PaginationButton>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Row view (collapsed or expanded)
// -----------------------------------------------------------------------

function AuditRowView({
  row,
  expanded,
  onToggle,
  onFilterBy,
  onCopyJson,
  parseDetails,
}: {
  row: AuditRow;
  expanded: boolean;
  onToggle: () => void;
  onFilterBy: (patch: Partial<Filters>) => void;
  onCopyJson: () => void;
  parseDetails: (d: string | null) => unknown;
}) {
  const sev = severityOf(row.outcome);
  const cat = categoryOf(row.action);
  const sevPalette = SEVERITY[sev];

  const handleKey = (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggle();
    }
  };

  return (
    <>
      <tr
        onClick={onToggle}
        onKeyDown={handleKey}
        tabIndex={0}
        role="button"
        aria-expanded={expanded}
        style={{
          borderBottom: `1px solid ${T.bl}`,
          cursor: "pointer",
          background: expanded ? "rgba(99,102,241,0.04)" : undefined,
        }}
      >
        {/* Severity stripe column — 4px colored bar */}
        <td
          style={{
            padding: 0,
            borderLeft: `4px solid ${sevPalette.stripe}`,
            width: 8,
          }}
        />
        <Td mono>
          {row.timestamp ? new Date(row.timestamp).toLocaleString() : "—"}
        </Td>
        <Td>
          <CategoryBadge category={cat} />
        </Td>
        <Td>
          <span style={{ fontWeight: 500, color: T.tp }}>
            {formatActionLabel(row.action)}
          </span>
        </Td>
        <Td>
          <LinkedEntity
            value={row.actor_key_id}
            onClick={() => onFilterBy({ actor_key_id: row.actor_key_id })}
            title="Filter by this actor"
          />
        </Td>
        <Td>
          <LinkedEntity
            value={row.tenant_id}
            onClick={() => onFilterBy({ tenant_id: row.tenant_id })}
            title="Filter by this tenant"
          />
        </Td>
        <Td>
          <span style={{ color: T.ts, marginRight: 2 }}>
            {row.resource_type}/
          </span>
          <LinkedEntity
            value={row.resource_id}
            onClick={() =>
              onFilterBy({ q: row.resource_id })
            }
            title="Filter by this resource"
          />
        </Td>
        <Td>
          <OutcomePill outcome={row.outcome} />
        </Td>
        <Td mono>{row.ip_address || "—"}</Td>
      </tr>

      {expanded ? (
        <tr style={{ background: "rgba(99,102,241,0.03)" }}>
          <td
            style={{
              padding: 0,
              borderLeft: `4px solid ${sevPalette.stripe}`,
              width: 8,
            }}
          />
          <td colSpan={8} style={{ padding: "16px 18px" }}>
            <ExpandedDetail
              row={row}
              parseDetails={parseDetails}
              onCopyJson={onCopyJson}
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function LinkedEntity({
  value,
  onClick,
  title,
}: {
  value: string;
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={title}
      style={{
        background: "none",
        border: "none",
        padding: 0,
        fontFamily: T.mono,
        fontSize: 12,
        color: T.tp,
        cursor: "pointer",
        textAlign: "left",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.textDecoration = "underline";
        e.currentTarget.style.color = "var(--accent-primary)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.textDecoration = "none";
        e.currentTarget.style.color = T.tp;
      }}
    >
      {value}
    </button>
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
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: "8px 24px",
          marginBottom: 14,
        }}
      >
        <DetailField label="User-Agent" value={row.user_agent || "—"} mono />
        <DetailField
          label="Session prefix"
          value={row.session_id_prefix || "—"}
          mono
        />
        <DetailField label="Resource type" value={row.resource_type} mono />
        <DetailField label="IP address" value={row.ip_address || "—"} mono />
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 6,
        }}
      >
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            color: T.tt,
          }}
        >
          Full event JSON
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onCopyJson();
          }}
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            fontSize: 11,
            fontWeight: 600,
            color: T.accent,
            padding: 0,
            fontFamily: "inherit",
          }}
        >
          Copy JSON
        </button>
      </div>
      <JsonBlock data={fullRow} />
    </div>
  );
}

function DetailField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: T.tt,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 12.5,
          color: T.tp,
          fontFamily: mono ? T.mono : "inherit",
          marginTop: 2,
          wordBreak: "break-all",
        }}
      >
        {value}
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Misc helpers
// -----------------------------------------------------------------------

function formatDayHeader(iso: string): string {
  if (iso === "—") return "Unknown date";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const today = new Date();
  const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000);
  const iso2 = (x: Date) => x.toISOString().slice(0, 10);
  if (iso === iso2(today)) return "Today";
  if (iso === iso2(yesterday)) return "Yesterday";
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: d.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
}

function FilterInput({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <label style={{ display: "block" }}>
      <span
        style={{
          display: "block",
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: T.tt,
          marginBottom: 4,
        }}
      >
        {label}
      </span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="sran-input"
        style={{ width: "100%" }}
      />
    </label>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
}) {
  return (
    <label style={{ display: "block" }}>
      <span
        style={{
          display: "block",
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: T.tt,
          marginBottom: 4,
        }}
      >
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="sran-input"
        style={{ width: "100%" }}
      >
        {options.map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
    </label>
  );
}

function Th({
  children,
  style,
}: {
  children?: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <th
      style={{
        padding: "10px 12px",
        fontSize: 11,
        fontWeight: 600,
        textAlign: "left",
        textTransform: "uppercase",
        letterSpacing: "0.06em",
        color: T.ts,
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  mono = false,
}: {
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <td
      style={{
        padding: "10px 12px",
        color: T.tp,
        fontFamily: mono ? T.mono : "inherit",
        fontSize: mono ? 12 : 13,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </td>
  );
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
        fontSize: 12,
        fontWeight: 500,
        borderRadius: 6,
        border: `1px solid ${T.bl}`,
        background: T.card,
        color: disabled ? T.tt : T.ts,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        fontFamily: "inherit",
      }}
    >
      {children}
    </button>
  );
}

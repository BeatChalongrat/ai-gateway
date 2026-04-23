"use client";

/**
 * /admin/audit-log — Admin Actions audit viewer (Phase 4 F4).
 *
 * Distinct from /audit (which shows request logs / the /v1 gateway
 * trace). This page surfaces ``admin_audit_log`` — who did what to
 * which resource, when, from where.
 *
 * Filters: date range, actor, tenant, action, outcome, free-text
 * search. Pagination 50 rows/page. CSV export applies the current
 * filter set.
 */

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { ThemeToggle } from "../../components/ThemeToggle";

const T = {
  bg: "var(--bg-base)",
  card: "var(--bg-surface)",
  bl: "var(--border-default)",
  tp: "var(--text-primary)",
  ts: "var(--text-secondary)",
  tt: "var(--text-muted)",
  accent: "var(--accent-primary)",
  mono: "'JetBrains Mono','Fira Code',monospace",
};

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


function OutcomePill({ value }: { value: string | null }) {
  if (!value) return <span style={{ color: T.tt, fontSize: 12 }}>—</span>;
  const v = value.toLowerCase();
  const palette =
    v === "success"
      ? { color: "#059669", bg: "rgba(16,185,129,0.08)" }
      : v === "denied" || v === "error" || v === "fail"
        ? { color: "#dc2626", bg: "rgba(239,68,68,0.08)" }
        : { color: T.ts, bg: "rgba(107,114,128,0.08)" };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 12,
        fontFamily: T.mono,
        fontWeight: 500,
        color: palette.color,
        background: palette.bg,
      }}
    >
      {value}
    </span>
  );
}

export default function AuditLogPage() {
  const { toast } = useToast();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);

  const [data, setData] = useState<AuditListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(appliedFilters)) {
      if (v) params.set(k, v);
    }
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String((page - 1) * PAGE_SIZE));
    return params.toString();
  }, [appliedFilters, page]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = (await api.get(
        `/admin/audit-log?${queryString}`,
      )) as AuditListResponse;
      setData(body);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.body?.error?.message || `Error ${err.status}`
          : err instanceof Error
            ? err.message
            : "Failed to load audit log.",
      );
    } finally {
      setLoading(false);
    }
  }, [queryString]);

  useEffect(() => {
    load();
  }, [load]);

  function handleApply(e: FormEvent) {
    e.preventDefault();
    setAppliedFilters(filters);
    setPage(1);
  }

  function handleClear() {
    setFilters(EMPTY_FILTERS);
    setAppliedFilters(EMPTY_FILTERS);
    setPage(1);
  }

  async function handleExport() {
    setExporting(true);
    try {
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(appliedFilters)) {
        if (v) params.set(k, v);
      }
      params.set("format", "csv");
      const res = await fetch(`/admin/audit-log?${params.toString()}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "admin_audit_log.csv";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      toast(
        err instanceof Error ? err.message : "Export failed",
        "error",
      );
    } finally {
      setExporting(false);
    }
  }

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const rows = data?.items ?? [];

  return (
    <div style={{ padding: "2rem 2.5rem", maxWidth: 1400, margin: "0 auto" }}>
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
            Who did what, when, and where. Scope: create / revoke / rotate /
            cross-tenant access / session events.
          </p>
        </div>
        <ThemeToggle />
      </div>

      {/* Filter bar */}
      <form
        onSubmit={handleApply}
        style={{
          background: T.card,
          border: `1px solid ${T.bl}`,
          borderRadius: 12,
          padding: 16,
          marginBottom: 16,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 12,
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
          placeholder="e.g. create_api_key"
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
        <div
          style={{
            display: "flex",
            alignItems: "flex-end",
            gap: 6,
            minWidth: 0,
          }}
        >
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
            onClick={handleClear}
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
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting || total === 0}
            style={{
              padding: "8px 14px",
              fontSize: 13,
              fontWeight: 500,
              borderRadius: 6,
              border: `1px solid ${T.bl}`,
              background: T.card,
              color: exporting || total === 0 ? T.tt : T.ts,
              cursor: exporting || total === 0 ? "not-allowed" : "pointer",
              fontFamily: "inherit",
            }}
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
        </div>
      </form>

      {/* Results */}
      {loading ? (
        <div
          style={{
            background: T.card,
            borderRadius: 14,
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
            borderRadius: 14,
            border: `1px solid ${T.bl}`,
            padding: 20,
          }}
        >
          <ErrorState message={error} onRetry={load} />
        </div>
      ) : rows.length === 0 ? (
        <div
          style={{
            background: T.card,
            borderRadius: 14,
            border: `1px solid ${T.bl}`,
            padding: 20,
          }}
        >
          <EmptyState
            message={
              appliedFilters === EMPTY_FILTERS
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
              <thead style={{ background: "var(--bg-elevated)" }}>
                <tr>
                  <Th>Timestamp</Th>
                  <Th>Actor</Th>
                  <Th>Tenant</Th>
                  <Th>Action</Th>
                  <Th>Resource</Th>
                  <Th>Outcome</Th>
                  <Th>IP</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    style={{ borderBottom: `1px solid ${T.bl}` }}
                  >
                    <Td mono>
                      {r.timestamp
                        ? new Date(r.timestamp).toLocaleString()
                        : "—"}
                    </Td>
                    <Td mono>{r.actor_key_id}</Td>
                    <Td mono>{r.tenant_id}</Td>
                    <Td>{r.action}</Td>
                    <Td>
                      <span style={{ color: T.ts }}>{r.resource_type}/</span>
                      <span style={{ fontFamily: T.mono }}>
                        {r.resource_id}
                      </span>
                    </Td>
                    <Td>
                      <OutcomePill value={r.outcome} />
                    </Td>
                    <Td mono>{r.ip_address || "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "10px 16px",
              borderTop: `1px solid ${T.bl}`,
              fontSize: 12,
              color: T.tt,
              fontFamily: T.mono,
            }}
          >
            <span>
              {total > 0
                ? `${(page - 1) * PAGE_SIZE + 1}-${Math.min(
                    page * PAGE_SIZE,
                    total,
                  )} of ${total}`
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
  );
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

function Th({ children }: { children: React.ReactNode }) {
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
  children: React.ReactNode;
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
  children: React.ReactNode;
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

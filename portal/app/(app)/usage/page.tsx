"use client";

import { useEffect, useMemo, useState } from "react";
import { Hash, TrendingUp, Zap } from "lucide-react";

import { PageSpinner } from "@/components/Spinner";
import { api, PortalApiError } from "@/lib/api";
import { formatDate, formatTokens } from "@/lib/format";
import type { UsageRequest } from "@/lib/types";
import PageHeader from "../_components/PageHeader";
import StatCard from "../_components/StatCard";
import RequestLogTable, {
  type LogRow,
} from "./_components/RequestLogTable";
import UsageChart from "./_components/UsageChart";

interface UsageResp {
  requests: UsageRequest[];
  total: number;
}

type Range = "today" | "7d" | "30d" | "all";

const RANGE_MS: Record<Exclude<Range, "all">, number> = {
  today: 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
};

export default function UsagePage() {
  const [rows, setRows] = useState<UsageRequest[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<Range>("7d");

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const r = await api.get<UsageResp>("/portal/me/usage?limit=200");
        if (!alive) return;
        setRows(r.requests);
        setTotal(r.total);
      } catch (err) {
        if (err instanceof PortalApiError) setError(err.message);
        else setError("Failed to load usage.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const filtered = useMemo(() => {
    if (range === "all") return rows;
    const windowMs = RANGE_MS[range];
    const cutoff = Date.now() - windowMs;
    return rows.filter((r) => {
      if (!r.created_at) return false;
      return new Date(r.created_at).getTime() >= cutoff;
    });
  }, [rows, range]);

  const totals = useMemo(() => {
    let tokens = 0;
    let requests = 0;
    for (const r of filtered) {
      if (r.total_tokens != null) tokens += r.total_tokens;
      requests += 1;
    }
    return {
      requests,
      tokens,
      avg: requests > 0 ? Math.round(tokens / requests) : 0,
    };
  }, [filtered]);

  // Map UsageRequest → LogRow. RequestLogTable uses a 3-state status;
  // we bucket the backend's raw status string into success / error /
  // rate_limited. Unknowns bucket as error so they stand out.
  const logRows = useMemo<LogRow[]>(() => {
    return filtered.map((r, i) => {
      const raw = (r.status ?? "").toLowerCase();
      let status: LogRow["status"] = "error";
      if (raw === "success" || raw.startsWith("http_2") || raw === "200") {
        status = "success";
      } else if (
        raw === "rate_limited" ||
        raw === "429" ||
        raw.startsWith("http_429")
      ) {
        status = "rate_limited";
      }
      return {
        id: r.id ?? String(i),
        timestamp: formatDate(r.created_at),
        model: r.model ?? "—",
        project: r.provider ?? "—",
        tokens: r.total_tokens ?? 0,
        latencyMs: r.latency_ms ?? 0,
        status,
      };
    });
  }, [filtered]);

  if (loading) return <PageSpinner />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Usage"
        subtitle="Recent gateway requests from your API keys."
        actions={
          <div
            className="inline-flex p-0.5"
            style={{
              background: "var(--bg-surface)",
              border: "1px solid var(--border-default)",
              borderRadius: 8,
            }}
          >
            {(["today", "7d", "30d", "all"] as Range[]).map((r) => {
              const active = range === r;
              return (
                <button
                  key={r}
                  onClick={() => setRange(r)}
                  type="button"
                  className="px-3 py-1.5 text-sm font-medium rounded-md"
                  style={{
                    background: active ? "var(--bg-elevated)" : "transparent",
                    color: active ? "var(--text-primary)" : "var(--text-secondary)",
                    border: "none",
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  {r === "today"
                    ? "Today"
                    : r === "all"
                    ? "All"
                    : r === "7d"
                    ? "7 days"
                    : "30 days"}
                </button>
              );
            })}
          </div>
        }
      />

      {error ? (
        <div
          style={{
            background: "rgba(220, 38, 38, 0.08)",
            border: "1px solid rgba(220, 38, 38, 0.25)",
            color: "var(--accent-danger)",
            fontSize: 13,
            padding: "8px 12px",
            borderRadius: 8,
          }}
        >
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          tone="violet"
          icon={<Hash className="w-5 h-5" strokeWidth={1.75} />}
          label="Total Requests"
          value={totals.requests.toLocaleString()}
          caption="Selected period"
        />
        <StatCard
          tone="cyan"
          icon={<Zap className="w-5 h-5" strokeWidth={1.75} />}
          label="Total Tokens"
          value={formatTokens(totals.tokens)}
          caption="Selected period"
        />
        <StatCard
          tone="green"
          icon={<TrendingUp className="w-5 h-5" strokeWidth={1.75} />}
          label="Avg / Request"
          value={formatTokens(totals.avg)}
          caption="Tokens per request"
        />
      </div>

      <UsageChart totalRequests={totals.requests} range={range} />

      <RequestLogTable rows={logRows} />

      {filtered.length > 0 ? (
        <p
          className="text-right"
          style={{ fontSize: 11, color: "var(--text-tertiary)" }}
        >
          Showing {filtered.length} of {total} request
          {total === 1 ? "" : "s"}
        </p>
      ) : null}
    </div>
  );
}


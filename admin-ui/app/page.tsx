"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import PageHeader from "../components/ui/PageHeader";
import { api } from "../lib/api";

/* ── Types ───────────────────────────────────────────────────────── */

type Period = "24h" | "7d" | "30d";
type Direction = "up" | "down" | "flat";

interface KpiMetric {
  value: number;
  delta_pct: number | null;
  direction: Direction;
}

interface Overview {
  period: Period;
  requests: KpiMetric;
  success_rate: KpiMetric;
  avg_latency_ms: KpiMetric;
  spend_usd: KpiMetric;
  resources: { tenants: number; projects: number; models: number };
  requests_series: number[];
  success_series: number[];
  latency_series: number[];
  spend_series: number[];
}

interface VolumeBucket {
  label: string;
  openai: number;
  anthropic: number;
  google: number;
  other: number;
}
interface VolumeResp {
  period: Period;
  granularity: "hour" | "day";
  buckets: VolumeBucket[];
  providers: string[];
}

interface TopModel {
  model: string;
  provider: string;
  count: number;
}
interface TopModelsResp {
  period: Period;
  total: number;
  items: TopModel[];
}

interface RecentRequest {
  request_id: string;
  status: string;
  http_class: "2xx" | "4xx" | "5xx" | "unknown";
  provider: string;
  model: string;
  latency_ms: number;
  error_code: string | null;
  created_at: string | null;
}

/* ── Constants ───────────────────────────────────────────────────── */

const PROVIDER_COLOR: Record<string, string> = {
  openai: "#6366f1",
  anthropic: "#d97757",
  google: "#4285f4",
  other: "#cbd5e1",
};

const PROVIDER_LABEL: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  other: "Others",
};

/* ── Helpers ─────────────────────────────────────────────────────── */

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 5000) return "just now";
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(0)}k`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/* ── Counter animation (initial mount only) ─────────────────────── */

function useAnimatedNumber(target: number, duration = 800): number {
  const [val, setVal] = useState(0);
  const hasStartedRef = useRef(false);
  useEffect(() => {
    // Only animate on the first time we get a real target. After that,
    // snap to the new value so refetches don't replay the countup.
    if (hasStartedRef.current) {
      setVal(target);
      return;
    }
    if (target === 0) {
      setVal(0);
      return;
    }
    hasStartedRef.current = true;
    const start = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setVal(target * eased);
      if (t < 1) raf = requestAnimationFrame(step);
      else setVal(target);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return val;
}

/* ── Icons ───────────────────────────────────────────────────────── */

function LightningIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
    </svg>
  );
}
function CheckIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 11-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}
function ClockIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}
function DollarIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="1" x2="12" y2="23" />
      <path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6" />
    </svg>
  );
}

/* ── Sparkline (SVG, absolute-anchored to card bottom) ──────────── */

function Sparkline({ series, color, gradId }: { series: number[]; color: string; gradId: string }) {
  if (!series || series.length < 2) return null;
  const max = Math.max(...series);
  const min = Math.min(...series);
  const range = max - min;
  const w = 100;
  const h = 40;
  const step = w / (series.length - 1);

  // When all values are equal (typical on an empty deployment where every
  // bucket is 0), mapping to y=h pins the line to the card's bottom edge
  // and it reads as "missing". Draw a faint baseline at ~70% height so
  // the sparkline is visually present but obviously empty.
  const allFlat = range === 0;
  const pts = series.map((v, i) => {
    const x = i * step;
    const y = allFlat ? h * 0.7 : h - ((v - min) / range) * h;
    return `${x},${y}`;
  });
  const linePath = `M ${pts.join(" L ")}`;
  const areaPath = `${linePath} L ${w},${h} L 0,${h} Z`;
  return (
    <svg
      data-sparkline={gradId}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        width: "100%",
        height: 44,
        opacity: allFlat ? 0.18 : 0.3,
        pointerEvents: "none",
      }}
      aria-hidden
    >
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.55" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeDasharray={allFlat ? "3 3" : undefined}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/* ── Delta chip ──────────────────────────────────────────────────── */

function DeltaChip({
  delta,
  direction,
  compareLabel,
  invertDeltaColor,
}: {
  delta: number | null;
  direction: Direction;
  compareLabel: string;
  invertDeltaColor?: boolean;
}) {
  if (delta === null) {
    return <span style={{ fontSize: 11, color: "#94a3b8" }}>No prior data</span>;
  }

  const good = invertDeltaColor ? direction === "down" : direction === "up";
  const bad = invertDeltaColor ? direction === "up" : direction === "down";
  const flat = direction === "flat";
  const color = flat ? "#64748b" : good ? "#16a34a" : bad ? "#dc2626" : "#64748b";
  const bg = flat
    ? "rgba(100,116,139,0.1)"
    : good
    ? "rgba(34,197,94,0.12)"
    : bad
    ? "rgba(220,38,38,0.1)"
    : "rgba(100,116,139,0.1)";
  const arrow = direction === "up" ? "▲" : direction === "down" ? "▼" : "—";

  return (
    <>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 2,
          background: bg,
          color,
          padding: "1px 5px",
          borderRadius: 3,
          fontSize: 10.5,
          fontWeight: 600,
          marginRight: 6,
        }}
      >
        <span style={{ fontSize: 8 }}>{arrow}</span>
        {direction === "flat" ? "—" : `${Math.abs(delta).toFixed(1)}%`}
      </span>
      <span style={{ fontSize: 11, color: "#94a3b8" }}>{compareLabel}</span>
    </>
  );
}

/* ── KPI Card ────────────────────────────────────────────────────── */

interface KpiProps {
  label: string;
  delta: number | null;
  direction: Direction;
  compareLabel: string;
  gradient: string;
  border: string;
  accent: string;
  accentRgb: string; // "99,102,241"
  icon: React.ReactNode;
  series: number[];
  sparkId: string;
  /** Raw numeric target; the component formats it for display. */
  rawValue: number;
  formatValue: (n: number) => string;
  /** For latency / spend, an "up" delta is bad. */
  invertDeltaColor?: boolean;
  animationDelay?: number;
}

function KpiCard({
  label,
  delta,
  direction,
  compareLabel,
  gradient,
  border,
  accent,
  accentRgb,
  icon,
  series,
  sparkId,
  rawValue,
  formatValue,
  invertDeltaColor,
  animationDelay = 0,
}: KpiProps) {
  const animated = useAnimatedNumber(rawValue);

  return (
    <div
      className="dash-kpi"
      style={{
        background: gradient,
        border: `1px solid ${border}`,
        borderRadius: 10,
        padding: "14px 16px",
        position: "relative",
        overflow: "hidden",
        transition: "transform 200ms ease, box-shadow 200ms ease",
        animation: `fadeInUp 0.5s ease-out ${animationDelay}s both`,
      }}
    >
      {/* Sparkline bottom layer */}
      <Sparkline series={series} color={accent} gradId={sparkId} />

      {/* Small icon tile */}
      <div
        style={{
          position: "absolute",
          top: 12,
          right: 12,
          width: 22,
          height: 22,
          borderRadius: 6,
          background: `rgba(${accentRgb}, 0.12)`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: accent,
        }}
      >
        {icon}
      </div>

      <div
        style={{
          position: "relative",
          fontSize: 10,
          fontWeight: 600,
          color: "#64748b",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div
        style={{
          position: "relative",
          fontSize: 22,
          fontWeight: 700,
          color: "#0f172a",
          letterSpacing: "-0.02em",
          lineHeight: 1.1,
          marginBottom: 6,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {formatValue(animated)}
      </div>
      <div style={{ position: "relative" }}>
        <DeltaChip
          delta={delta}
          direction={direction}
          compareLabel={compareLabel}
          invertDeltaColor={invertDeltaColor}
        />
      </div>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function DashboardPage() {
  const [period, setPeriod] = useState<Period>("24h");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [volume, setVolume] = useState<VolumeResp | null>(null);
  const [topModels, setTopModels] = useState<TopModelsResp | null>(null);
  const [recent, setRecent] = useState<RecentRequest[]>([]);
  const [loading, setLoading] = useState(true);

  // Initial load + reload on period change
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.get(`/admin/dashboard/overview?period=${period}`),
      api.get(`/admin/dashboard/volume?period=${period}`),
      api.get(`/admin/dashboard/top-models?period=${period}&limit=5`),
    ])
      .then(([o, v, t]) => {
        if (cancelled) return;
        setOverview(o);
        setVolume(v);
        setTopModels(t);
      })
      .catch(() => {
        /* handled per-section */
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period]);

  const loadRecent = useCallback(async () => {
    try {
      const r = await api.get(`/admin/dashboard/recent-requests?limit=10`);
      setRecent(r.items || []);
    } catch {
      /* keep previous data */
    }
  }, []);

  useEffect(() => {
    loadRecent();
    const id = setInterval(loadRecent, 5000);
    return () => clearInterval(id);
  }, [loadRecent]);

  const compareLabel = useMemo(() => {
    if (period === "24h") return "vs. prior 24h";
    if (period === "7d") return "vs. prior week";
    return "vs. prior 30d";
  }, [period]);

  return (
    <div className="dashboard-root">
      <PageStyles />

      {/* Header with period switcher */}
      <div
        className="dash-header"
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          marginBottom: 16,
          animation: "fadeInDown 0.4s ease-out both",
        }}
      >
        <div>
          <PageHeader
            title="Dashboard"
            subtitle={`Platform overview · last ${
              period === "24h" ? "24 hours" : period === "7d" ? "7 days" : "30 days"
            }`}
          />
        </div>
        <div
          role="tablist"
          style={{
            display: "inline-flex",
            background: "#f1f5f9",
            borderRadius: 8,
            padding: 3,
            gap: 2,
            height: "fit-content",
            animation: "fadeInDown 0.4s ease-out 0.1s both",
          }}
        >
          {(["24h", "7d", "30d"] as Period[]).map((p) => {
            const active = period === p;
            return (
              <button
                key={p}
                role="tab"
                aria-selected={active}
                onClick={() => setPeriod(p)}
                style={{
                  background: active ? "#ffffff" : "transparent",
                  color: active ? "#0f172a" : "#64748b",
                  border: "none",
                  borderRadius: 6,
                  padding: "5px 14px",
                  fontSize: 12,
                  fontWeight: active ? 600 : 500,
                  cursor: "pointer",
                  boxShadow: active ? "0 1px 2px rgba(15,23,42,0.06)" : "none",
                  transition: "background 120ms, color 120ms, box-shadow 120ms",
                }}
              >
                {p}
              </button>
            );
          })}
        </div>
      </div>

      {/* Row 1: 4 KPI cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 10,
          marginBottom: 10,
        }}
      >
        <KpiCard
          label="Requests"
          rawValue={overview?.requests.value ?? 0}
          formatValue={(n) => (overview ? fmt(n) : "—")}
          delta={overview?.requests.delta_pct ?? null}
          direction={overview?.requests.direction ?? "flat"}
          compareLabel={compareLabel}
          gradient="linear-gradient(135deg,#eef2ff,#fdf4ff)"
          border="rgba(99,102,241,0.15)"
          accent="#6366f1"
          accentRgb="99,102,241"
          icon={<LightningIcon color="#6366f1" />}
          series={overview?.requests_series ?? []}
          sparkId="spk-requests"
          animationDelay={0.15}
        />
        <KpiCard
          label="Success rate"
          rawValue={overview?.success_rate.value ?? 0}
          formatValue={(n) => (overview ? `${n.toFixed(1)}%` : "—")}
          delta={overview?.success_rate.delta_pct ?? null}
          direction={overview?.success_rate.direction ?? "flat"}
          compareLabel={compareLabel}
          gradient="linear-gradient(135deg,#f0fdf4,#f0fdfa)"
          border="rgba(16,185,129,0.15)"
          accent="#10b981"
          accentRgb="16,185,129"
          icon={<CheckIcon color="#10b981" />}
          series={overview?.success_series ?? []}
          sparkId="spk-success"
          animationDelay={0.2}
        />
        <KpiCard
          label="Avg latency"
          rawValue={overview?.avg_latency_ms.value ?? 0}
          formatValue={(n) => (overview ? `${Math.round(n)}ms` : "—")}
          delta={overview?.avg_latency_ms.delta_pct ?? null}
          direction={overview?.avg_latency_ms.direction ?? "flat"}
          compareLabel={compareLabel}
          gradient="linear-gradient(135deg,#fff7ed,#fef3c7)"
          border="rgba(245,158,11,0.15)"
          accent="#f59e0b"
          accentRgb="245,158,11"
          icon={<ClockIcon color="#f59e0b" />}
          series={overview?.latency_series ?? []}
          sparkId="spk-latency"
          invertDeltaColor
          animationDelay={0.25}
        />
        <KpiCard
          label="Spend"
          rawValue={overview?.spend_usd.value ?? 0}
          formatValue={(n) => (overview ? `$${n.toFixed(2)}` : "—")}
          delta={overview?.spend_usd.delta_pct ?? null}
          direction={overview?.spend_usd.direction ?? "flat"}
          compareLabel={compareLabel}
          gradient="linear-gradient(135deg,#faf5ff,#fdf2f8)"
          border="rgba(168,85,247,0.15)"
          accent="#a855f7"
          accentRgb="168,85,247"
          icon={<DollarIcon color="#a855f7" />}
          series={overview?.spend_series ?? []}
          sparkId="spk-spend"
          invertDeltaColor
          animationDelay={0.3}
        />
      </div>

      {/* Row 2: Volume chart + Top models */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.6fr 1fr",
          gap: 10,
          marginBottom: 10,
        }}
      >
        <VolumeChart data={volume} loading={loading} />
        <TopModelsList data={topModels} loading={loading} />
      </div>

      {/* Row 3: Platform health + Recent requests */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1.4fr",
          gap: 10,
        }}
      >
        <PlatformHealth resources={overview?.resources ?? null} />
        <RecentRequests items={recent} />
      </div>
    </div>
  );
}

/* ── Volume chart ────────────────────────────────────────────────── */

function VolumeChart({ data, loading }: { data: VolumeResp | null; loading: boolean }) {
  const buckets = data?.buckets ?? [];
  const total = useMemo(
    () => buckets.reduce((acc, b) => acc + b.openai + b.anthropic + b.google + b.other, 0),
    [buckets]
  );
  const tickInterval = buckets.length > 0 ? Math.floor((buckets.length - 1) / 2) : 0;

  return (
    <section
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 12,
        padding: "16px 18px",
        animation: "fadeInUp 0.5s ease-out 0.35s both",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#0f172a" }}>Request volume</h3>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
            {total.toLocaleString()} requests total · by provider
          </div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          {["openai", "anthropic", "google", "other"].map((p) => (
            <div key={p} style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: PROVIDER_COLOR[p], display: "inline-block" }} />
              <span style={{ fontSize: 11, color: "#64748b" }}>{PROVIDER_LABEL[p]}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ width: "100%", height: 220, marginTop: 8 }}>
        {loading && !data ? (
          <div style={{ display: "grid", placeItems: "center", height: "100%", color: "#94a3b8", fontSize: 12 }}>
            Loading…
          </div>
        ) : total === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "#94a3b8",
              fontSize: 12,
            }}
          >
            <svg
              width="32"
              height="32"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ marginBottom: 8, opacity: 0.5 }}
            >
              <path d="M3 3v18h18" />
              <path d="M7 14l4-4 4 4 5-5" />
            </svg>
            <div style={{ fontWeight: 500, color: "#64748b" }}>No requests yet</div>
            <div style={{ fontSize: 11, marginTop: 2 }}>
              Send a request to <code>/v1/chat/completions</code> to see data
            </div>
          </div>
        ) : (
          <ResponsiveContainer>
            <BarChart data={buckets} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 10, fill: "#94a3b8" }}
                tickLine={false}
                axisLine={{ stroke: "#e2e8f0" }}
                interval={tickInterval || "preserveStartEnd"}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "#94a3b8" }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ fill: "rgba(99,102,241,0.06)" }}
                contentStyle={{
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  fontSize: 12,
                  padding: "6px 10px",
                }}
                labelStyle={{ color: "#0f172a", fontWeight: 600 }}
              />
              <Bar
                dataKey="openai"
                stackId="a"
                fill={PROVIDER_COLOR.openai}
                name="OpenAI"
                isAnimationActive
                animationDuration={600}
                animationBegin={400}
              />
              <Bar
                dataKey="anthropic"
                stackId="a"
                fill={PROVIDER_COLOR.anthropic}
                name="Anthropic"
                isAnimationActive
                animationDuration={600}
                animationBegin={450}
              />
              <Bar
                dataKey="google"
                stackId="a"
                fill={PROVIDER_COLOR.google}
                name="Google"
                isAnimationActive
                animationDuration={600}
                animationBegin={500}
              />
              <Bar
                dataKey="other"
                stackId="a"
                fill={PROVIDER_COLOR.other}
                name="Others"
                radius={[3, 3, 0, 0]}
                isAnimationActive
                animationDuration={600}
                animationBegin={550}
              />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

/* ── Top models list ─────────────────────────────────────────────── */

function TopModelsList({ data, loading }: { data: TopModelsResp | null; loading: boolean }) {
  const items = data?.items ?? [];
  const max = items.length ? items[0].count : 1;
  return (
    <section
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 12,
        padding: "16px 18px",
        animation: "fadeInUp 0.5s ease-out 0.4s both",
      }}
    >
      <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#0f172a" }}>Top models</h3>
      <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2, marginBottom: 10 }}>By request count</div>

      {loading && !data ? (
        <div style={{ color: "#94a3b8", fontSize: 12 }}>Loading…</div>
      ) : items.length === 0 ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "24px 8px",
            color: "#94a3b8",
            fontSize: 12,
          }}
        >
          <svg
            width="32"
            height="32"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ marginBottom: 8, opacity: 0.5 }}
          >
            <rect x="3" y="10" width="4" height="10" rx="1" />
            <rect x="10" y="6" width="4" height="14" rx="1" />
            <rect x="17" y="13" width="4" height="7" rx="1" />
          </svg>
          <div style={{ fontWeight: 500, color: "#64748b" }}>No models yet</div>
          <div style={{ fontSize: 11, marginTop: 2 }}>No requests in this period.</div>
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          {items.map((m, i) => {
            const color = PROVIDER_COLOR[m.provider] ?? PROVIDER_COLOR.other;
            const pct = Math.max(4, (m.count / max) * 100);
            return (
              <li
                key={`${m.provider}:${m.model}`}
                style={{ animation: `fadeInRight 0.4s ease-out ${0.5 + i * 0.04}s both` }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    marginBottom: 4,
                    gap: 8,
                  }}
                >
                  <span
                    style={{
                      fontSize: 12,
                      color: "#0f172a",
                      fontWeight: 500,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      minWidth: 0,
                    }}
                    title={m.model}
                  >
                    {m.model}
                  </span>
                  <span style={{ fontSize: 11, color: "#64748b", fontVariantNumeric: "tabular-nums" }}>
                    {m.count.toLocaleString()}
                  </span>
                </div>
                <div style={{ height: 4, background: "#f1f5f9", borderRadius: 2, overflow: "hidden" }}>
                  <div
                    style={{
                      width: `${pct}%`,
                      height: "100%",
                      background: color,
                      animation: `growWidth 0.8s ease-out ${0.7 + i * 0.04}s both`,
                      transformOrigin: "left",
                    }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/* ── Platform health ─────────────────────────────────────────────── */

function PlatformHealth({ resources }: { resources: Overview["resources"] | null }) {
  const components = [
    { label: "Gateway API" },
    { label: "DLP engine" },
    { label: "Routing" },
    { label: "Adapters" },
  ];
  return (
    <section
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 12,
        padding: "16px 18px",
        animation: "fadeInUp 0.5s ease-out 0.45s both",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#0f172a" }}>Platform health</h3>
        <span
          style={{
            fontSize: 10,
            fontWeight: 600,
            color: "#14532d",
            background: "#f0fdf4",
            border: "1px solid rgba(16,185,129,0.2)",
            borderRadius: 999,
            padding: "2px 8px",
            letterSpacing: "0.04em",
          }}
        >
          ● OPERATIONAL
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 14 }}>
        {components.map((c, i) => (
          <div
            key={c.label}
            style={{
              background: "#f0fdf4",
              color: "#14532d",
              border: "1px solid rgba(16,185,129,0.15)",
              borderRadius: 8,
              padding: "6px 10px",
              fontSize: 12,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span
              className="health-dot"
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "#10b981",
                animationDelay: `${i * 0.3}s`,
              }}
            />
            {c.label}
          </div>
        ))}
      </div>

      <div style={{ height: 1, background: "#e2e8f0", marginBottom: 12 }} />

      <div
        style={{
          fontSize: 10,
          fontWeight: 600,
          color: "#94a3b8",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 8,
        }}
      >
        Resources
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {[
          ["Tenants", resources?.tenants ?? 0],
          ["Projects", resources?.projects ?? 0],
          ["Models", resources?.models ?? 0],
        ].map(([label, value]) => (
          <div
            key={label as string}
            style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}
          >
            <span style={{ color: "#64748b" }}>{label}</span>
            <span style={{ color: "#0f172a", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
              {value as number}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── Recent requests ─────────────────────────────────────────────── */

function RecentRequests({ items }: { items: RecentRequest[] }) {
  // Track which request_ids we've already seen so we only animate brand-new
  // rows on refetch. Rows present on initial mount share a staggered load-in.
  const seenRef = useRef<Set<string> | null>(null);
  const mountedAtRef = useRef<number>(Date.now());

  const annotatedItems = useMemo(() => {
    if (seenRef.current === null) {
      seenRef.current = new Set(items.map((i) => i.request_id));
      return items.map((r, i) => ({ r, isNew: false, index: i }));
    }
    const seen = seenRef.current;
    const out = items.map((r, i) => {
      const isNew = !seen.has(r.request_id);
      if (isNew) seen.add(r.request_id);
      return { r, isNew, index: i };
    });
    return out;
  }, [items]);

  const initialMount = Date.now() - mountedAtRef.current < 50;

  return (
    <section
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 12,
        padding: "16px 18px",
        animation: "fadeInUp 0.5s ease-out 0.5s both",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#0f172a" }}>Recent requests</h3>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
            Last {items.length} · auto-refresh 5s
          </div>
        </div>
        <span
          style={{
            fontSize: 10,
            fontWeight: 600,
            color: "#065f46",
            background: "#d1fae5",
            border: "1px solid rgba(16,185,129,0.25)",
            borderRadius: 999,
            padding: "2px 8px",
            letterSpacing: "0.04em",
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
          }}
        >
          <span className="live-dot" style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981" }} />
          LIVE
        </span>
      </div>

      {items.length === 0 ? (
        <div style={{ padding: 20, textAlign: "center", color: "#94a3b8", fontSize: 12 }}>No recent requests.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr
                style={{
                  textAlign: "left",
                  color: "#94a3b8",
                  fontSize: 10,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                <th style={{ padding: "6px 8px 6px 0", fontWeight: 600 }}>Status</th>
                <th style={{ padding: "6px 8px", fontWeight: 600 }}>Provider</th>
                <th style={{ padding: "6px 8px", fontWeight: 600 }}>Model</th>
                <th style={{ padding: "6px 8px", fontWeight: 600, textAlign: "right" }}>Latency</th>
                <th style={{ padding: "6px 0 6px 8px", fontWeight: 600, textAlign: "right" }}>When</th>
              </tr>
            </thead>
            <tbody>
              {annotatedItems.map(({ r, isNew, index }) => {
                const rowBg =
                  r.http_class === "2xx"
                    ? "#f0fdf4"
                    : r.http_class === "4xx"
                    ? "#fff7ed"
                    : r.http_class === "5xx"
                    ? "#fef2f2"
                    : "#ffffff";
                const statusColor =
                  r.http_class === "2xx"
                    ? "#16a34a"
                    : r.http_class === "4xx"
                    ? "#d97706"
                    : r.http_class === "5xx"
                    ? "#dc2626"
                    : "#64748b";

                const delay = initialMount ? 0.6 + Math.min(index, 10) * 0.02 : 0;
                const animation = isNew
                  ? "slideInRow 0.35s ease-out both"
                  : `slideInRow 0.35s ease-out ${delay}s both`;

                return (
                  <tr
                    key={r.request_id}
                    style={{
                      background: rowBg,
                      borderTop: "1px solid #f1f5f9",
                      animation,
                    }}
                  >
                    <td style={{ padding: "6px 8px 6px 0" }}>
                      <span
                        style={{
                          color: statusColor,
                          fontSize: 10,
                          fontWeight: 600,
                          background: "#ffffff",
                          border: `1px solid ${statusColor}33`,
                          borderRadius: 4,
                          padding: "2px 6px",
                          textTransform: "uppercase",
                          letterSpacing: "0.04em",
                        }}
                      >
                        {r.http_class === "unknown" ? r.status || "—" : r.http_class.toUpperCase()}
                      </span>
                    </td>
                    <td style={{ padding: "6px 8px", color: "#64748b", textTransform: "capitalize" }}>
                      {r.provider || "—"}
                    </td>
                    <td
                      style={{
                        padding: "6px 8px",
                        color: "#0f172a",
                        fontFamily: "ui-monospace, SFMono-Regular, monospace",
                        fontSize: 11,
                      }}
                      title={r.model}
                    >
                      {r.model || "—"}
                    </td>
                    <td
                      style={{
                        padding: "6px 8px",
                        color: "#0f172a",
                        textAlign: "right",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {r.latency_ms ? `${r.latency_ms}ms` : "—"}
                    </td>
                    <td
                      style={{
                        padding: "6px 0 6px 8px",
                        color: "#94a3b8",
                        textAlign: "right",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {timeAgo(r.created_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ── Page-level styles (keyframes + hover + reduced-motion) ─────── */

function PageStyles() {
  return (
    <style jsx global>{`
      .dashboard-root .dash-kpi:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px -8px rgba(15, 23, 42, 0.1);
      }

      .dashboard-root .live-dot {
        animation: pulseDot 1.5s ease-in-out infinite;
      }
      .dashboard-root .health-dot {
        animation: pulseDot 2s ease-in-out infinite;
      }

      @keyframes fadeInUp {
        from {
          opacity: 0;
          transform: translateY(8px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
      @keyframes fadeInDown {
        from {
          opacity: 0;
          transform: translateY(-8px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
      @keyframes fadeInRight {
        from {
          opacity: 0;
          transform: translateX(-10px);
        }
        to {
          opacity: 1;
          transform: translateX(0);
        }
      }
      @keyframes growWidth {
        from {
          transform: scaleX(0);
        }
        to {
          transform: scaleX(1);
        }
      }
      @keyframes pulseDot {
        0%,
        100% {
          opacity: 1;
          box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4);
        }
        50% {
          opacity: 0.7;
          box-shadow: 0 0 0 6px rgba(16, 185, 129, 0);
        }
      }
      @keyframes slideInRow {
        from {
          opacity: 0;
          transform: translateX(-6px);
        }
        to {
          opacity: 1;
          transform: translateX(0);
        }
      }

      @media (prefers-reduced-motion: reduce) {
        .dashboard-root,
        .dashboard-root *,
        .dashboard-root *::before,
        .dashboard-root *::after {
          animation: none !important;
          transition: none !important;
        }
      }
    `}</style>
  );
}

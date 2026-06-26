"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  FolderOpen,
  FolderPlus,
  Key,
  MessageSquare,
  ScrollText,
  Sparkles,
  Zap,
} from "lucide-react";

import { Avatar } from "@/components/Avatar";
import { HeroSkeleton } from "@/components/skeletons/HeroSkeleton";
import { StatCardRowSkeleton } from "@/components/skeletons/StatCardRowSkeleton";
import { TableSkeleton } from "@/components/skeletons/TableSkeleton";
import { api, PortalApiError } from "@/lib/api";
import { formatTokens, relativeTime } from "@/lib/format";
import { useCountUp } from "@/lib/hooks/useCountUp";
import type { PortalUser, Project, UsageRequest } from "@/lib/types";

interface ProjectsResp {
  projects: Project[];
}
interface UsageResp {
  requests: UsageRequest[];
  total: number;
}

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<PortalUser | null>(null);
  const [projectCount, setProjectCount] = useState(0);
  const [keyCount, setKeyCount] = useState(0);
  const [keyCountPartial, setKeyCountPartial] = useState(false);
  const [recent, setRecent] = useState<UsageRequest[]>([]);
  const [totalRequests, setTotalRequests] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [me, projs, usage] = await Promise.all([
          api.get<PortalUser>("/portal/me"),
          api.get<ProjectsResp>("/portal/projects"),
          api.get<UsageResp>("/portal/me/usage?limit=5"),
        ]);
        if (!alive) return;
        setUser(me);
        setProjectCount(projs.projects.length);
        // allSettled (not all+catch→0) so a failed per-project /keys call is
        // recorded as partial data, not silently counted as zero keys.
        const keyResults = await Promise.allSettled(
          projs.projects.map((p) =>
            api
              .get<{ keys: { key_id: string }[] }>(`/portal/projects/${p.id}/keys`)
              .then((r) => r.keys.length)
          )
        );
        if (!alive) return;
        const counts = keyResults.map((r) => (r.status === "fulfilled" ? r.value : 0));
        setKeyCount(counts.reduce((a, b) => a + b, 0));
        setKeyCountPartial(keyResults.some((r) => r.status === "rejected"));
        setRecent(usage.requests || []);
        setTotalRequests(usage.total ?? 0);
      } catch (err) {
        if (err instanceof PortalApiError) setError(err.message);
        else setError("Failed to load dashboard.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (loading)
    return (
      <div className="space-y-5">
        <HeroSkeleton />
        <StatCardRowSkeleton />
        <TableSkeleton rows={5} />
      </div>
    );

  const balance = user?.credit_balance ?? 0;
  const used = user?.credit_used ?? 0;
  const total = balance + used;
  const percent = total > 0 ? Math.round((balance / total) * 100) : 100;
  const planLabel = user?.plan
    ? user.plan.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
    : "Free Trial";

  return (
    <div className="space-y-5">
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <Avatar
          url={user?.avatar_url}
          name={user?.display_name}
          email={user?.email}
          id={user?.id}
          size={44}
          radius={12}
        />
        <div>
          <h1
            className="font-heading"
            style={{
              fontSize: 24,
              fontWeight: 600,
              letterSpacing: "-0.02em",
              margin: "0 0 4px",
              color: "var(--text-primary)",
            }}
          >
            Dashboard
          </h1>
          <p style={{ fontSize: 13, color: "var(--text-tertiary)", margin: 0 }}>
            Welcome back, {user?.display_name || user?.email || "there"}
          </p>
        </div>
      </div>

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

      {user?.is_low_credit ? (
        <div
          style={{
            background: "rgba(217, 119, 6, 0.08)",
            border: "1px solid rgba(217, 119, 6, 0.25)",
            padding: "12px 16px",
            borderRadius: 10,
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            color: "var(--accent-warning)",
          }}
        >
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} />
            Your credit balance is running low. Top up to keep using the gateway.
          </span>
          <button
            onClick={() => router.push("/billing")}
            style={{
              fontWeight: 600,
              background: "none",
              border: "none",
              color: "inherit",
              cursor: "pointer",
              fontSize: 13,
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
            type="button"
          >
            View plans <ArrowRight size={14} />
          </button>
        </div>
      ) : null}

      <CreditHero
        balance={balance}
        used={used}
        total={total}
        percent={percent}
        planLabel={planLabel}
      />

      <QuickstartGrid />

      <ChatLaunchPanel />

      <StatsGrid
        used={used}
        projectCount={projectCount}
        keyCount={keyCount}
        keyCountPartial={keyCountPartial}
      />

      <RecentActivitySection recent={recent} totalRequests={totalRequests} />
    </div>
  );
}

/* -------------------------------------------------------------------- */
/* Credit Hero                                                           */
/* -------------------------------------------------------------------- */

function CreditHero({
  balance,
  used,
  total,
  percent,
  planLabel,
}: {
  balance: number;
  used: number;
  total: number;
  percent: number;
  planLabel: string;
}) {
  const r = 42;
  const circumference = 2 * Math.PI * r;
  const dashOffset = circumference * (1 - percent / 100);
  const displayBalance = useCountUp(balance, 1000);

  return (
    <div
      style={{
        position: "relative",
        overflow: "hidden",
        background: "var(--hero-gradient)",
        border: "1px solid var(--border-default)",
        borderRadius: 16,
        padding: "28px 32px",
        boxShadow: "0 20px 40px -20px var(--hero-glow)",
        color: "#FFFFFF",
      }}
    >
      {/* Grid pattern overlay — slow drift, imperceptible "alive" feel */}
      <div
        aria-hidden
        className="hero-grid"
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage:
            "linear-gradient(rgba(99,102,241,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(99,102,241,0.05) 1px, transparent 1px)",
          backgroundSize: "40px 40px",
          pointerEvents: "none",
        }}
      />
      {/* Radial glow — breathes on 6s loop */}
      <div
        aria-hidden
        className="hero-glow"
        style={{
          position: "absolute",
          top: -80,
          right: -80,
          width: 280,
          height: 280,
          background:
            "radial-gradient(circle, rgba(99,102,241,0.25) 0%, transparent 70%)",
          pointerEvents: "none",
        }}
      />

      <div
        style={{
          position: "relative",
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 24,
          flexWrap: "wrap",
        }}
      >
        {/* Left side */}
        <div style={{ minWidth: 0, flex: "1 1 320px" }}>
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: "rgba(255,255,255,0.7)",
              marginBottom: 8,
            }}
          >
            Available Credits
          </div>
          <div
            className="font-heading"
            style={{
              fontSize: 48,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              lineHeight: 1,
              color: "#FFFFFF",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {displayBalance.toLocaleString()}
          </div>
          <div
            style={{
              fontSize: 12,
              color: "rgba(255,255,255,0.55)",
              marginTop: 6,
            }}
          >
            of {total.toLocaleString()} lifetime allotment
          </div>

          {/* Stats row */}
          <div
            style={{
              display: "flex",
              gap: 32,
              marginTop: 20,
              flexWrap: "wrap",
            }}
          >
            <HeroStat label="Used" value={used.toLocaleString()} />
            <HeroStat label="Remaining" value={balance.toLocaleString()} />
            {/* "Next Reset" removed (F6): value was a permanent "—". Re-add
                when the backend exposes a real reset date. */}
          </div>
        </div>

        {/* Right side */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            gap: 16,
            flexShrink: 0,
          }}
        >
          {/* Plan pill */}
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              borderRadius: 999,
              background: "rgba(99,102,241,0.15)",
              color: "#A5B4FC",
              border: "1px solid rgba(99,102,241,0.3)",
              fontSize: 11,
              fontWeight: 500,
            }}
          >
            <Sparkles size={11} />
            {planLabel}
          </span>

          {/* Donut */}
          <div style={{ position: "relative", width: 110, height: 110 }}>
            <svg
              viewBox="0 0 100 100"
              width="110"
              height="110"
              style={{ transform: "rotate(-90deg)" }}
              aria-hidden
            >
              <circle
                cx="50"
                cy="50"
                r={r}
                fill="none"
                stroke="rgba(255,255,255,0.1)"
                strokeWidth="6"
              />
              <circle
                className="hero-circle"
                cx="50"
                cy="50"
                r={r}
                fill="none"
                stroke="url(#heroDonutGrad)"
                strokeWidth="6"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                style={
                  {
                    ["--circle-circumference" as string]: String(circumference),
                    ["--circle-target-offset" as string]: String(dashOffset),
                  } as React.CSSProperties
                }
              />
              <defs>
                <linearGradient id="heroDonutGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#6366F1" />
                  <stop offset="100%" stopColor="#06B6D4" />
                </linearGradient>
              </defs>
            </svg>
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                className="font-heading"
                style={{ fontSize: 20, fontWeight: 700, color: "#FFFFFF", lineHeight: 1 }}
              >
                {percent}%
              </div>
              <div
                style={{
                  fontSize: 9,
                  letterSpacing: "0.18em",
                  textTransform: "uppercase",
                  color: "rgba(255,255,255,0.55)",
                  marginTop: 4,
                }}
              >
                Available
              </div>
            </div>
          </div>

          {/* Actions row */}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Link
              href="/billing"
              style={{
                fontSize: 12,
                color: "rgba(255,255,255,0.7)",
                textDecoration: "none",
                whiteSpace: "nowrap",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              View transactions <ArrowRight size={14} />
            </Link>
            <Link
              href="/billing"
              className="btn-premium"
              style={{
                background: "#FFFFFF",
                color: "#0A1230",
                borderRadius: 8,
                padding: "6px 14px",
                fontSize: 12,
                fontWeight: 600,
                textDecoration: "none",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              Upgrade
              <ArrowUpRight size={13} />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function HeroStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          color: "rgba(255,255,255,0.5)",
          marginBottom: 4,
        }}
      >
        {label}
      </div>
      <div style={{ fontSize: 18, fontWeight: 600, color: "#FFFFFF" }}>{value}</div>
    </div>
  );
}

/* -------------------------------------------------------------------- */
/* Quickstart cards                                                      */
/* -------------------------------------------------------------------- */

function QuickstartGrid() {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
        gap: 12,
      }}
    >
      <QuickstartCard
        rgb="99,102,241"
        icon={<FolderPlus size={16} strokeWidth={1.75} />}
        title="Create a project"
        description="Group API keys by app or environment"
        href="/projects"
      />
      <QuickstartCard
        rgb="139,92,246"
        icon={<Key size={16} strokeWidth={1.75} />}
        title="Get your first API key"
        description="Start sending requests in under a minute"
        href="/projects"
      />
      {/* TODO: point to /docs when docs page is built */}
      <QuickstartCard
        rgb="245,158,11"
        icon={<BookOpen size={16} strokeWidth={1.75} />}
        title="Read the quickstart"
        description="OpenAI-compatible — swap one line"
        href="/projects"
      />
    </div>
  );
}

/* -------------------------------------------------------------------- */
/* Web Chat launch panel — opens the SRAN Chat surface on port 3002      */
/* using the shared portal_access_token cookie. No second login.         */
/* -------------------------------------------------------------------- */

function ChatLaunchPanel() {
  const baseChatUrl =
    process.env.NEXT_PUBLIC_WEB_CHAT_URL || "http://localhost:3002/chat";

  // No more token-in-URL handoff. The portal access token is an HttpOnly
  // cookie, host-only on localhost (shared across :3001/:3002) and set on
  // the shared parent domain in prod, so Web Chat already holds the
  // session when it opens. Plain link — no JS token plumbing, no JWT in
  // the URL / history / Referer.

  return (
    <a
      href={baseChatUrl}
      // ``target="_blank"`` so the new tab is sandboxed; ``rel`` per
      // OWASP guidance for off-origin new-tab links.
      target="_blank"
      rel="noopener noreferrer"
      className="card-hover"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        background:
          "linear-gradient(135deg, rgba(99,102,241,0.16), rgba(6,182,212,0.10))",
        border: "1px solid rgba(99,102,241,0.28)",
        borderRadius: 12,
        padding: "16px 20px",
        textDecoration: "none",
        color: "var(--text-primary)",
      }}
    >
      <div
        style={{
          width: 44,
          height: 44,
          borderRadius: 11,
          background: "rgba(99,102,241,0.22)",
          color: "#A5B4FC",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <MessageSquare size={20} strokeWidth={1.8} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          className="font-heading"
          style={{
            fontSize: 14.5,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          Open SRAN Chat
        </div>
        <div
          style={{
            fontSize: 12,
            color: "var(--text-secondary)",
            marginTop: 2,
          }}
        >
          Your private AI chat surface — uses your Portal sign-in,
          no second login.
        </div>
      </div>
      <ArrowUpRight
        size={16}
        style={{ color: "var(--accent-primary)", flexShrink: 0 }}
      />
    </a>
  );
}

function QuickstartCard({
  rgb,
  icon,
  title,
  description,
  href,
}: {
  rgb: string;
  icon: React.ReactNode;
  title: string;
  description: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="card-hover"
      style={{
        display: "block",
        background: "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
        border: "1px solid var(--border-default)",
        borderRadius: 10,
        padding: 16,
        textDecoration: "none",
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <div
          style={{
            width: 36,
            height: 36,
            background: `rgba(${rgb}, 0.12)`,
            border: `1px solid rgba(${rgb}, 0.2)`,
            borderRadius: 9,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: `rgb(${rgb})`,
            flexShrink: 0,
          }}
        >
          {icon}
        </div>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 13.5,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          {title}
          <ArrowRight size={12} style={{ color: `rgb(${rgb})` }} />
        </div>
      </div>
      <div
        style={{
          paddingLeft: 48,
          marginTop: 6,
          fontSize: 11.5,
          color: "var(--text-secondary)",
        }}
      >
        {description}
      </div>
    </Link>
  );
}

/* -------------------------------------------------------------------- */
/* Stats cards                                                           */
/* -------------------------------------------------------------------- */

function StatsGrid({
  used,
  projectCount,
  keyCount,
  keyCountPartial,
}: {
  used: number;
  projectCount: number;
  keyCount: number;
  keyCountPartial: boolean;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
        gap: 12,
      }}
    >
      <StatPanel
        rgb="99,102,241"
        label="Tokens Used"
        value={formatTokens(used)}
        caption="Lifetime"
        icon={<Zap size={12} strokeWidth={2} />}
      />
      <StatPanel
        rgb="6,182,212"
        label="Projects"
        value={projectCount.toString()}
        caption={`${projectCount} project${projectCount === 1 ? "" : "s"}`}
        icon={<FolderOpen size={12} strokeWidth={2} />}
        href="/projects"
      />
      <StatPanel
        rgb="245,158,11"
        label="API Keys"
        value={keyCountPartial ? `${keyCount}+` : keyCount.toString()}
        caption={keyCountPartial ? "Some counts unavailable" : "Across all projects"}
        icon={<Key size={12} strokeWidth={2} />}
        href="/projects"
      />
    </div>
  );
}

function StatPanel({
  rgb,
  label,
  value,
  caption,
  icon,
  href,
}: {
  rgb: string;
  label: string;
  value: string;
  caption: string;
  icon: React.ReactNode;
  href?: string;
}) {
  const body = (
    <div
      className="card-hover"
      style={{
        position: "relative",
        background: "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
        border: "1px solid var(--border-default)",
        borderRadius: 10,
        padding: 16,
        overflow: "hidden",
      }}
    >
      <div
        aria-hidden
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 1.5,
          background: `linear-gradient(90deg, transparent, rgba(${rgb}, 0.65), transparent)`,
        }}
      />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <span
          style={{
            fontSize: 10.5,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            color: "var(--text-tertiary)",
            fontWeight: 600,
          }}
        >
          {label}
        </span>
        <div
          style={{
            width: 22,
            height: 22,
            background: `rgba(${rgb}, 0.14)`,
            borderRadius: 6,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: `rgb(${rgb})`,
          }}
        >
          {icon}
        </div>
      </div>
      <div
        className="font-heading"
        style={{
          fontSize: 28,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          color: "var(--text-primary)",
          lineHeight: 1.05,
        }}
      >
        {value}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
        {caption}
      </div>
    </div>
  );

  if (href) {
    return (
      <Link href={href} style={{ display: "block", textDecoration: "none" }}>
        {body}
      </Link>
    );
  }
  return body;
}

/* -------------------------------------------------------------------- */
/* Recent activity                                                       */
/* -------------------------------------------------------------------- */

function RecentActivitySection({
  recent,
  totalRequests,
}: {
  recent: UsageRequest[];
  totalRequests: number;
}) {
  return (
    <div
      style={{
        background: "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
        border: "1px solid var(--border-default)",
        borderRadius: 10,
        padding: 20,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <h2
          className="font-heading"
          style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}
        >
          Recent Activity
        </h2>
        <Link
          href="/usage"
          style={{
            fontSize: 12,
            color: "var(--accent-primary)",
            textDecoration: "none",
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
          }}
        >
          View all <ArrowRight size={12} />
        </Link>
      </div>

      {recent.length === 0 ? (
        <div
          style={{
            padding: "32px 0",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 10,
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              background: "rgba(99,102,241,0.08)",
              border: "1px solid rgba(99,102,241,0.2)",
              borderRadius: 10,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--accent-primary)",
            }}
          >
            <ScrollText size={18} strokeWidth={1.5} />
          </div>
          <div style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>
            Send your first request to see activity here.
          </div>
        </div>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                  <Th>When</Th>
                  <Th>Model</Th>
                  <Th>Tokens</Th>
                  <Th>Status</Th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r, i) => (
                  <tr
                    key={r.id ?? i}
                    style={{ borderBottom: "1px solid var(--border-subtle)" }}
                  >
                    <Td color="var(--text-secondary)">{relativeTime(r.created_at)}</Td>
                    <Td color="var(--text-primary)" mono>
                      {r.model ?? "—"}
                    </Td>
                    <Td color="var(--text-primary)">
                      {r.total_tokens != null ? formatTokens(r.total_tokens) : "—"}
                    </Td>
                    <Td>
                      <StatusChip status={r.status} />
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div
            style={{
              marginTop: 14,
              paddingTop: 12,
              borderTop: "1px solid var(--border-subtle)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              fontSize: 11,
              color: "var(--text-tertiary)",
            }}
          >
            <span>
              Showing {recent.length} of {totalRequests.toLocaleString()}
            </span>
            <Link
              href="/usage"
              style={{
                color: "var(--accent-primary)",
                textDecoration: "none",
                fontWeight: 500,
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              View all in Usage <ArrowRight size={12} />
            </Link>
          </div>
        </>
      )}
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th
      style={{
        padding: "8px 16px 8px 0",
        textAlign: "left",
        fontSize: 10.5,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        color: "var(--text-muted)",
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  color = "var(--text-primary)",
  mono = false,
}: {
  children: React.ReactNode;
  color?: string;
  mono?: boolean;
}) {
  return (
    <td
      style={{
        padding: "10px 16px 10px 0",
        color,
        fontFamily: mono ? "var(--ff-mono)" : "inherit",
        fontSize: mono ? 12 : 13,
      }}
    >
      {children}
    </td>
  );
}

function StatusChip({ status }: { status: string | null }) {
  const s = (status || "").toLowerCase();
  const isError = s === "error" || s === "failed" || s.startsWith("http_5");
  const isSuccess = s === "success";
  const bg = isSuccess
    ? "rgba(16,185,129,0.12)"
    : isError
    ? "rgba(248,113,113,0.12)"
    : "var(--bg-elevated)";
  const color = isSuccess
    ? "var(--accent-success)"
    : isError
    ? "var(--accent-danger)"
    : "var(--text-secondary)";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 500,
        background: bg,
        color,
      }}
    >
      {status || "—"}
    </span>
  );
}

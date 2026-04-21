"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  BookOpen,
  FolderOpen,
  FolderPlus,
  Key,
  ScrollText,
  Zap,
} from "lucide-react";

import { Card } from "@/components/Card";
import { CreditBar } from "@/components/CreditBar";
import { PageSpinner } from "@/components/Spinner";
import { api, PortalApiError } from "@/lib/api";
import { formatTokens, relativeTime } from "@/lib/format";
import type { PortalUser, Project, UsageRequest } from "@/lib/types";
import EmptyState from "../_components/EmptyState";
import PageHeader from "../_components/PageHeader";
import QuickAction from "../_components/QuickAction";
import StatCard from "../_components/StatCard";

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
        const counts = await Promise.all(
          projs.projects.map((p) =>
            api
              .get<{ keys: { key_id: string }[] }>(`/portal/projects/${p.id}/keys`)
              .then((r) => r.keys.length)
              .catch(() => 0)
          )
        );
        if (!alive) return;
        setKeyCount(counts.reduce((a, b) => a + b, 0));
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

  if (loading) return <PageSpinner />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        subtitle={`Welcome back, ${user?.display_name || user?.email || "there"}`}
      />

      {error ? (
        <div className="rounded-lg bg-red-50 border border-red-200 text-brand-red text-sm px-3 py-2">
          {error}
        </div>
      ) : null}

      {user?.is_low_credit ? (
        <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800 flex items-center justify-between">
          <span className="inline-flex items-center gap-2">
            <AlertCircle size={16} className="text-amber-600" />
            Your credit balance is running low. Top up to keep using the gateway.
          </span>
          <button
            onClick={() => router.push("/billing")}
            className="font-semibold text-amber-900 hover:underline"
          >
            View plans →
          </button>
        </div>
      ) : null}

      {user ? (
        <CreditBar
          balance={user.credit_balance}
          used={user.credit_used}
          plan={user.plan}
          onUpgrade={() => router.push("/billing")}
          viewTransactionsHref="/billing"
        />
      ) : null}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <QuickAction
          icon={<FolderPlus className="w-5 h-5" strokeWidth={1.75} />}
          title="Create a project"
          description="Group API keys by app or environment"
          href="/projects"
        />
        <QuickAction
          icon={<Key className="w-5 h-5" strokeWidth={1.75} />}
          title="Get your first API key"
          description="Start sending requests in under a minute"
          href="/projects"
        />
        <QuickAction
          icon={<BookOpen className="w-5 h-5" strokeWidth={1.75} />}
          title="Read the quickstart"
          description="OpenAI-compatible — swap one line"
          href="/docs"
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          tone="violet"
          icon={<Zap className="w-5 h-5" strokeWidth={1.75} />}
          label="Tokens Used"
          value={formatTokens(user?.credit_used ?? 0)}
          caption="All time"
        />
        <StatCard
          tone="cyan"
          icon={<FolderOpen className="w-5 h-5" strokeWidth={1.75} />}
          label="Projects"
          value={projectCount.toString()}
          caption={`${projectCount} project${projectCount === 1 ? "" : "s"}`}
          href="/projects"
        />
        <StatCard
          tone="amber"
          icon={<Key className="w-5 h-5" strokeWidth={1.75} />}
          label="API Keys"
          value={keyCount.toString()}
          caption="Across all projects"
          href="/projects"
        />
      </div>

      <Card padding="md">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-heading font-semibold">Recent Activity</h2>
          <Link
            href="/usage"
            className="text-sm text-brand-navy hover:underline inline-flex items-center gap-1"
          >
            View all <ArrowRight size={14} />
          </Link>
        </div>
        {recent.length === 0 ? (
          <EmptyState
            icon={<ScrollText className="w-7 h-7" strokeWidth={1.5} />}
            title="No requests yet"
            description="Create an API key and send your first request to see it appear here."
            action={{ label: "Go to Projects →", href: "/projects" }}
            secondaryAction={{ label: "View docs", href: "/docs" }}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wider text-gray-500 border-b border-gray-200">
                  <th className="py-2 pr-4 font-medium">When</th>
                  <th className="py-2 pr-4 font-medium">Model</th>
                  <th className="py-2 pr-4 font-medium">Tokens</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r, i) => (
                  <tr key={r.id ?? i} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                    <td className="py-2 pr-4 text-gray-600">{relativeTime(r.created_at)}</td>
                    <td className="py-2 pr-4 text-gray-900 font-mono text-xs">{r.model ?? "—"}</td>
                    <td className="py-2 pr-4 text-gray-900">
                      {r.total_tokens != null ? formatTokens(r.total_tokens) : "—"}
                    </td>
                    <td className="py-2 pr-4">
                      <StatusChip status={r.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {recent.length > 0 ? (
          <div className="mt-4 pt-3 border-t border-gray-100 flex items-center justify-between text-xs text-gray-500">
            <span>
              Showing {recent.length} of {totalRequests.toLocaleString()}
            </span>
            <Link
              href="/usage"
              className="inline-flex items-center gap-1 text-violet-600 font-medium hover:underline"
            >
              View all in Usage <ArrowRight size={12} />
            </Link>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function StatusChip({ status }: { status: string | null }) {
  const s = (status || "").toLowerCase();
  const color =
    s === "success"
      ? "bg-green-100 text-green-800"
      : s === "error" || s === "failed" || s.startsWith("http_5")
      ? "bg-red-100 text-red-800"
      : "bg-gray-100 text-gray-700";
  return (
    <span className={["inline-block px-2 py-0.5 rounded-full text-xs font-medium", color].join(" ")}>
      {status || "—"}
    </span>
  );
}

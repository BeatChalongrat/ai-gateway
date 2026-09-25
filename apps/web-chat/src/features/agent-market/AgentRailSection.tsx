"use client";

// The Agent Market entry in the app sidebar: a link to the market and the
// agents this person installed (with a dot on one that waits for an answer).
//
// Flag off = absent: renders null until the availability check says the
// feature is available, and never throws — the sidebar mounts this on every
// chat page, including in tests where fetch is missing or fails.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Store } from "lucide-react";

import { amApi } from "./api";
import { useAmT } from "./i18n";
import type { InstallRow } from "./types";
import { AgentTile, cls } from "./ui";
import { onInstallsChanged, useAgentMarket } from "./useAgentMarket";

export function AgentRailSection({ onNavigate }: { onNavigate?: () => void }) {
  const { state } = useAgentMarket();
  const t = useAmT();
  const pathname = usePathname() ?? "";
  const [installs, setInstalls] = useState<InstallRow[]>([]);
  const available = state === "available";

  useEffect(() => {
    if (!available) return;
    let alive = true;
    const load = () => {
      amApi.installs().then(
        (r) => {
          if (alive) setInstalls(r?.installs ?? []);
        },
        () => {
          /* the section simply shows no agents */
        },
      );
    };
    load();
    const off = onInstallsChanged(load);
    return () => {
      alive = false;
      off();
    };
  }, [available]);

  if (!available) return null;

  // The market, an agent's page and its install wizard; a workspace or a run
  // belongs to the installed agent below.
  const marketActive =
    pathname === "/chat/agents" ||
    (pathname.startsWith("/chat/agents/") && !pathname.startsWith("/chat/agents/i/") && !pathname.startsWith("/chat/agents/runs/"));

  return (
    <nav aria-label={t("rail.aria")} className="flex flex-col gap-0.5" data-testid="agent-rail-section">
      <Link
        href="/chat/agents"
        onClick={onNavigate}
        aria-current={marketActive ? "page" : undefined}
        className={cls(
          "focus-ring tap-44 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-bg-elevated hover:text-text-primary",
          marketActive ? "bg-bg-elevated text-text-primary" : "text-text-secondary",
        )}
      >
        <Store width={16} height={16} aria-hidden />
        <span>{t("rail.market")}</span>
      </Link>
      {installs.map((i) => {
        const href = `/chat/agents/i/${i.id}`;
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={i.id}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            data-testid="agent-rail-install"
            className={cls(
              "focus-ring tap-44 flex w-full items-center gap-2.5 rounded-lg py-1.5 pl-5 pr-3 text-sm transition-colors hover:bg-bg-elevated hover:text-text-primary",
              active ? "bg-bg-elevated text-text-primary" : "text-text-secondary",
            )}
          >
            <AgentTile icon={i.icon} size={20} />
            <span className="min-w-0 flex-1 truncate">{i.title}</span>
            {i.attention ? (
              <span className="flex h-2 w-2 shrink-0 rounded-full bg-status-warning" title={t("rail.attention")}>
                <span className="sr-only">{t("rail.attention")}</span>
              </span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}

"use client";

// AM12 — an agent that works on the web, and whether it may now.
//
// Off until an admin turns it on for this agent (admin app › Agent Market ›
// Models): the workspace says so before anything is typed, in the server's
// own sentence — who can turn it on and where — and a request sent anyway is
// answered with the same reason, and nothing starts. With no search provider
// the agent still opens the links a person pastes, and says that.

import { Globe } from "lucide-react";

import { useAmT } from "../i18n";
import type { WebState } from "../types";
import { cls } from "../ui";

export function WebBanner({ web }: { web: WebState | null | undefined }) {
  const t = useAmT();
  if (!web?.uses || (web.open && web.search)) return null;
  const closed = !web.open;
  return (
    <div
      role="status"
      className={cls(
        "mx-auto mb-2 flex w-full max-w-chat items-start gap-2 rounded-lg border px-3 py-2 text-xs",
        closed ? "border-status-warning/40 bg-status-warning/10 text-text-primary" : "border-border bg-bg-elevated text-text-secondary",
      )}
      data-testid="am-web-banner"
      data-web={closed ? "closed" : "no-search"}
    >
      <Globe width={15} height={15} className={cls("mt-0.5 shrink-0", closed ? "text-status-warning" : "text-text-muted")} aria-hidden />
      <div className="flex min-w-0 flex-col gap-0.5">
        {/* the server's sentence says it whole (what, who can change it, where); the short line stands in when there is none */}
        {web.words ? <span>{web.words}</span> : <span className="font-medium">{closed ? t("web.closed.title") : t("web.noSearch.title")}</span>}
      </div>
    </div>
  );
}

"use client";

// Phase 4 (desktop shell) — the in-thread tool-consent card, the visible half
// of the bridge's consent.request. Mirrors the WPF ToolConsentDialog
// (category icon, tool name, description, arguments box, danger banner,
// Deny / Allow once / Allow for session). The host owns the decision; this
// only collects it and posts consent.respond via resolveConsent.
//
// allow-once and allow-session both map to the .NET session grant (the
// codebase has no single-use grant); allow-session is offered as the
// no-reprompt choice, allow-once as the same-run choice — both are honest
// given the backend, and the report states this.

import { AlertTriangle, ShieldQuestion } from "lucide-react";

import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import type { PendingConsent } from "@/lib/types";

// AM10 F — the sentences live in the dictionary; these tables only say
// which one a danger class / a forced-prompt reason reads as. A class this
// build does not know reads as the generic one.
const DANGER_KEY: Record<string, DictKey> = {
  "danger.shell": "consent.danger.shell",
  "danger.computerUse": "consent.danger.computerUse",
  "danger.code": "consent.danger.code",
  "danger.fileWrite": "consent.danger.fileWrite",
  "danger.fileUpload": "consent.danger.fileUpload",
  "danger.clipboard": "consent.danger.clipboard",
  "danger.browser": "consent.danger.browser",
  "danger.connector": "consent.danger.connector",
  "danger.generic": "consent.danger.generic",
};

const ALWAYS_PROMPT_KEY: Record<string, DictKey> = {
  download: "consent.always.download",
  "sensitive-info": "consent.always.sensitiveInfo",
  authorization: "consent.always.authorization",
};

export function ConsentCard({
  consent,
  onDecision,
}: {
  consent: PendingConsent;
  onDecision: (decision: "allow" | "deny", scope: "once" | "session" | "always") => void;
}) {
  const t = useT();
  const dangerous = consent.dangerClass !== "safe";
  // FIX31 §1.1: a sub-agent's request is NOT the assistant's own. The user is
  // approving something they did not directly ask for; the card says so, names
  // the sub-agent, and shows the task the assistant wrote for it — labelled as
  // the assistant's words. The command itself (below) cannot lie.
  const sub = consent.requestedBy?.kind === "subagent" ? consent.requestedBy : null;
  // FIX6: a browser-site prompt is Anthropic's "New permissions required" — it
  // names the site and offers exactly "Allow this action" + "Always allow actions
  // on this site". A forced prompt on an already-approved site says why.
  const site = consent.site;
  const alwaysPromptKey = consent.alwaysPromptReason ? ALWAYS_PROMPT_KEY[consent.alwaysPromptReason] : undefined;
  return (
    <div
      className="my-2 rounded-lg border border-amber-500/50 bg-amber-500/5 px-3 py-3"
      data-testid="consent-card"
      data-tool={consent.tool}
      data-site={site ?? undefined}
      data-requested-by={sub ? "subagent" : undefined}
    >
      <div className="flex items-center gap-2">
        <ShieldQuestion className="h-5 w-5 shrink-0 text-amber-500" />
        <div className="text-sm font-semibold text-text-primary">
          {sub ? t("consent.subagentTitle") : site ? t("consent.site.title") : t("consent.tool.title")}
        </div>
      </div>
      {sub ? (
        <div className="mt-1 rounded border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-xs text-text-secondary" data-testid="consent-requester">
          <div>
            {sub.label
              ? t("consent.subagentFrom", { label: sub.label })
              : t("consent.subagentFromUnnamed")}
          </div>
          <div className="mt-1 text-text-muted">{t("consent.subagentTaskLabel")}</div>
          <blockquote className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap border-l-2 border-amber-500/50 pl-2 text-text-primary">
            {sub.task}
          </blockquote>
        </div>
      ) : null}
      {site ? (
        <div className="mt-1 text-sm text-text-secondary">
          {t("consent.site.actOn")}{" "}
          <span className="font-medium text-text-primary">{site}</span>
          {alwaysPromptKey ? (
            <>
              {" "}{t("consent.site.always.pre")}<span className="font-medium text-status-danger">{t(alwaysPromptKey)}</span>{t("consent.site.always.post")}
            </>
          ) : null}
        </div>
      ) : (
        <div className="mt-1 text-sm text-text-secondary">
          <span className="font-medium text-text-primary">
            {consent.tool.replace(/_/g, " ")}
          </span>
          {consent.description ? ` — ${consent.description}` : null}
        </div>
      )}

      {consent.argumentDetail ? (
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-bg-base px-3 py-2 font-mono text-xs text-text-secondary">
          {consent.argumentDetail}
        </pre>
      ) : consent.argumentSummary ? (
        <div className="mt-2 truncate text-xs text-text-muted" title={consent.argumentSummary}>
          {consent.argumentSummary}
        </div>
      ) : null}

      {dangerous ? (
        <div className="mt-2 flex items-start gap-2 rounded border border-status-danger/40 bg-status-danger/5 px-2 py-1.5 text-xs text-status-danger">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {t(DANGER_KEY[consent.dangerClass] ?? "consent.danger.generic")} {t("consent.danger.review")}
          </span>
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={() => onDecision("deny", "once")}
          className="rounded-md border border-status-danger/50 px-3 py-1.5 text-sm font-medium text-status-danger hover:bg-status-danger/10 focus-ring"
          data-testid="consent-deny"
        >
          {site ? t("consent.decline") : t("consent.deny")}
        </button>
        <button
          type="button"
          onClick={() => onDecision("allow", "once")}
          className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text-primary hover:bg-bg-elevated focus-ring"
          data-testid="consent-allow-once"
        >
          {site ? t("consent.allowAction") : t("consent.allowOnce")}
        </button>
        {site ? (
          // "Always allow actions on this site" — persists (approved sites list).
          <button
            type="button"
            onClick={() => onDecision("allow", "always")}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 focus-ring"
            data-testid="consent-allow-always"
          >
            {t("consent.allowAlwaysSite")}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onDecision("allow", "session")}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 focus-ring"
            data-testid="consent-allow-session"
          >
            {t("consent.allowSession")}
          </button>
        )}
      </div>
    </div>
  );
}

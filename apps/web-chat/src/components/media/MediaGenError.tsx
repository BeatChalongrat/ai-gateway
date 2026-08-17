"use client";

// Sprint 3 — inline error banner for the direct-dispatch media forms.
// Maps the gateway's HTTP status / error code to a human-readable state and
// the right recovery action (retry / add key / contact admin / dismiss).

import { AlertTriangle, KeyRound, RefreshCw, X } from "lucide-react";

import { useT } from "@/lib/i18n";

export type MediaGenErrorCode =
  | "provider_unavailable"
  | "budget_exceeded"
  | "content_policy"
  | "timeout"
  | "unknown";

const TITLES: Record<MediaGenErrorCode, string> = {
  provider_unavailable: "Provider not configured",
  budget_exceeded: "Budget limit reached",
  content_policy: "Content policy violation",
  timeout: "Generation timed out",
  unknown: "Something went wrong",
};

/** Map an ApiError's HTTP status + error code to a MediaGenErrorCode. */
export function mediaErrorCodeFromStatus(
  status: number | undefined,
  code: string | undefined,
): MediaGenErrorCode {
  const c = (code || "").toLowerCase();
  if (
    status === 503 ||
    c.includes("unavailable") ||
    c.includes("provider_key_missing") ||
    c.includes("no_adapter")
  ) {
    return "provider_unavailable";
  }
  if (status === 402 || c.includes("budget") || c.includes("spend") || c.includes("quota")) {
    return "budget_exceeded";
  }
  if (status === 422 || c.includes("content_policy") || c.includes("policy")) {
    return "content_policy";
  }
  if (status === 504 || c.includes("timeout")) {
    return "timeout";
  }
  return "unknown";
}

interface Props {
  errorCode: MediaGenErrorCode;
  errorMessage: string;
  /** Admins get an "Add key" link to the provider console. */
  isAdmin?: boolean;
  onRetry?: () => void;
  onDismiss: () => void;
}

export function MediaGenError({
  errorCode,
  errorMessage,
  isAdmin,
  onRetry,
  onDismiss,
}: Props) {
  const t = useT();
  const showAddKey = errorCode === "provider_unavailable" && isAdmin;
  // Budget + provider issues aren't fixed by an immediate retry; content/timeout/
  // unknown are transient enough to offer one.
  const showRetry =
    Boolean(onRetry) &&
    (errorCode === "content_policy" ||
      errorCode === "timeout" ||
      errorCode === "unknown");

  return (
    <div
      role="alert"
      className="flex flex-col gap-2 rounded-lg border border-status-danger/40 bg-status-danger/10 px-3 py-2.5 text-sm"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-status-danger" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-text-primary">{TITLES[errorCode]}</p>
          {errorMessage ? (
            <p className="mt-0.5 break-words text-xs text-text-secondary">{errorMessage}</p>
          ) : null}
          {errorCode === "provider_unavailable" && !isAdmin ? (
            <p className="mt-0.5 text-xs text-text-muted">
              Contact your administrator to enable this provider.
            </p>
          ) : null}
        </div>
        <button
          type="button"
          aria-label={t("a11y.dismiss")}
          onClick={onDismiss}
          className="focus-ring -mr-1 rounded p-1 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      {showAddKey || showRetry ? (
        <div className="flex items-center justify-end gap-2">
          {showAddKey ? (
            <a
              href="/providers"
              target="_blank"
              rel="noopener noreferrer"
              className="focus-ring inline-flex items-center gap-1 rounded-full bg-rose-500 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-rose-600"
            >
              <KeyRound className="h-3 w-3" aria-hidden />
              Add key
            </a>
          ) : null}
          {showRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="focus-ring inline-flex items-center gap-1 rounded-full border border-border px-3 py-1 text-xs text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
            >
              <RefreshCw className="h-3 w-3" aria-hidden />
              Retry
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

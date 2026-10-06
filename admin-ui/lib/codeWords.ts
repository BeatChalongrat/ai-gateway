// AM11 — a stored code is never what a person reads.
//
// The gateway stores an alert's severity, what became of the alert, and a
// DLP detector's category as short codes ("log_only", "pii"). A page shows
// the word for the code, in its language; the code stays what the page
// filters, sorts and keys by. A code this file does not know yet reads as
// "Other (code)" rather than as a bare code, and the test beside the pages
// (app/codes.am11.test.tsx) lists every code the backend can store, so a new
// one arrives with its word.

import type { DictKey } from "./i18n";

type Translate = (key: DictKey, vars?: Record<string, string | number>) => string;

/** backend/app/services/alert_channels.py: low | info | warning | critical */
export const ALERT_SEVERITY: Record<string, DictKey> = {
  critical: "obs.alerts.severity.critical",
  warning: "obs.alerts.severity.warning",
  info: "obs.alerts.severity.info",
  low: "obs.alerts.severity.low",
};

/**
 * What became of an alert (alert_service.py, alert_pager.py): sent, not sent,
 * or deliberately not sent — the pager's grouped / hold / maintenance / log_only.
 */
export const ALERT_DELIVERY: Record<string, DictKey> = {
  pending: "obs.alerts.delivery.pending",
  delivered: "obs.alerts.delivery.delivered",
  failed: "obs.alerts.delivery.failed",
  log_only: "obs.alerts.delivery.log_only",
  grouped: "obs.alerts.delivery.grouped",
  hold: "obs.alerts.delivery.hold",
  maintenance: "obs.alerts.delivery.maintenance",
};

/** The `category` of a detector class (backend/app/services/dlp/detectors/*.py). */
export const DLP_CATEGORY: Record<string, DictKey> = {
  identity: "infra.category.identity",
  financial: "infra.category.financial",
  credential: "infra.category.credential",
  secret: "infra.category.secret",
  pii: "infra.category.pii",
  network: "infra.category.network",
  content_policy: "infra.category.content_policy",
};

/** How a custom detector detects (backend/app/models/custom_detector.py). */
export const DETECTION_TYPE: Record<string, DictKey> = {
  regex: "infra.detection.regex",
  keyword_list: "infra.detection.keyword_list",
  semantic: "infra.detection.semantic",
};

/**
 * The built-in policy profiles (backend/app/services/policy/profiles.py), by id, with the name
 * the backend seeds them under. A profile is named by this app only while it still carries that
 * name: one an operator renamed, or made, is theirs and shows as written.
 */
const BUILT_IN_PROFILE: Record<string, { seeded: string; key: DictKey }> = {
  default: { seeded: "Default", key: "infra.profile.default" },
  strict: { seeded: "Strict", key: "infra.profile.strict" },
  media_gen: { seeded: "Media Generation", key: "infra.profile.media_gen" },
};

export function profileName(profile: { profile_id: string; name: string }, t: Translate): string {
  const builtIn = BUILT_IN_PROFILE[profile.profile_id];
  return builtIn && builtIn.seeded === profile.name ? t(builtIn.key) : profile.name;
}

/** The word for a stored code; "Other (code)" for one this app does not know yet. */
export function codeWord(words: Record<string, DictKey>, code: string | null | undefined, t: Translate): string {
  const c = (code ?? "").trim();
  if (!c) return "—";
  const key = words[c];
  return key ? t(key) : t("code.other", { code: c });
}

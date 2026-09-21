// B1 (Layer 3) — map machine-readable gateway error codes to
// user-friendly messages. The gateway/chat-edge emit a stable
// ``code`` plus an English ``message``; the frontend owns
// localization so the wire stays language-neutral.
//
// Phase-2 i18n sweep: the copy now lives in the EN/TH dictionary and
// follows the ACTIVE locale at the moment the error is localized
// (tOutside). These strings are captured onto message objects at
// stream time, so an already-rendered error keeps the language it was
// created in — same behavior as before, minus the always-Thai bug.
//
// FIX47 Part A — every code the gateway can emit has a row here, and
// the rows are split by whether sending the same request again can
// succeed. The old table knew nine codes; everything else showed the
// gateway's default sentence, which was "Model unavailable, please try
// again" for a model that can never use tools. A code missing from
// this table is NOT told to retry: the dictionary sentence for an
// unknown code is neutral, and the gateway's own ``retryable`` flag
// (when the frame carries one) decides whether the retry hint appears.
// ``errorMessages.test.ts`` reads the backend catalog and fails when
// the two tables drift.

import { tOutside } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";

/** Codes for which a retry of the same request may succeed. */
export const RETRYABLE_CODES: ReadonlySet<string> = new Set([
  "provider_error",
  "provider_unavailable",
  "provider_timeout",
  // FIX80 — the provider went silent mid-stream and the client watchdog
  // ended the turn (measured: 108 chunks in 2.4 s, then 60 s of nothing).
  // A retry of the same request usually streams fine.
  "stream_stalled",
  "provider_at_capacity",
  "provider_rate_limited",
  "rate_limit_exceeded",
  "provider_model_not_found",
  "all_models_failed",
  "internal_error",
  "stream_interrupted",
  "health_marker_skip",
  // FIX49 Part A — the turn "completed" with no text and no tool call;
  // a retry (or another model) is exactly the way out.
  "empty_completion",
  "output_limit_reached",
  // chat-edge's own codes
  "send_failed",
  "INTERNAL_ERROR",
  "PROVIDER_UNAVAILABLE",
  // image pipeline
  "image_rate_limited",
  "image_timeout",
  "image_provider_unavailable",
  "image_provider_error",
  "image_empty_response",
  "image_bad_response",
  "image_attachment_save_failed",
  // stream resume
  "GENERATION_INTERRUPTED",
]);

const ERROR_CODE_KEYS: Record<string, DictKey> = {
  // ── transient — the message may say "try again" ───────────────────
  provider_error: "error.provider_error",
  provider_unavailable: "error.provider_unavailable",
  provider_timeout: "error.provider_timeout",
  provider_at_capacity: "error.provider_at_capacity",
  provider_rate_limited: "error.provider_rate_limited",
  rate_limit_exceeded: "error.rate_limit_exceeded",
  provider_model_not_found: "error.provider_model_not_found",
  all_models_failed: "error.all_models_failed",
  internal_error: "error.internal_error",
  stream_interrupted: "error.stream_interrupted",
  health_marker_skip: "error.health_marker_skip",
  empty_completion: "error.empty_completion",
  output_limit_reached: "error.output_limit_reached",
  send_failed: "error.send_failed",
  INTERNAL_ERROR: "error.internal_error",
  PROVIDER_UNAVAILABLE: "error.provider_unavailable",
  // ── permanent for this request — the message says what to change ──
  validation_error: "error.validation_error",
  VALIDATION_ERROR: "error.validation_error",
  model_no_tool_support: "error.model_no_tool_support",
  model_no_vision_support: "error.model_no_vision_support",
  no_capable_model: "error.no_capable_model",
  // FIX126 O4 — an allow-list that matches no provider is refused.
  no_allowed_provider: "error.no_allowed_provider",
  context_length_exceeded: "error.context_length_exceeded",
  // FIX105 — ONE message is over the budget on its own (chat-edge
  // message_too_long.go; the desktop host names the same case). The advice
  // is about this message, not the conversation, so it has its own sentence.
  message_too_long: "error.message_too_long",
  upstream_bad_response: "error.upstream_bad_response",
  quota_exceeded: "error.quota_exceeded",
  INSUFFICIENT_CREDITS: "error.insufficient_credits",
  insufficient_credits: "error.insufficient_credits",
  // FIX88 Part A — an admin-side refusal (a top-up beyond a user's credit
  // ceiling). It never reaches a chat turn; the sentence exists because
  // every catalogue code must be sayable in both languages.
  credit_ceiling: "error.credit_ceiling",
  // FIX51 Part 4 — the PROVIDER account ran dry (bare 402 upstream),
  // distinct from the user's own credit balance above.
  provider_no_credit: "error.provider_no_credit",
  provider_key_missing: "error.provider_key_missing",
  provider_not_implemented: "error.provider_not_implemented",
  authentication_error: "error.authentication_error",
  authorization_error: "error.authorization_error",
  policy_violation: "error.policy_violation",
  dlp_violation: "error.dlp_violation",
  guardrail_blocked: "error.guardrail_blocked",
  moderation_flagged: "error.moderation_flagged",
  content_policy_blocked: "error.content_policy_blocked",
  provider_auth_error: "error.provider_auth_error",
  provider_keys_stale: "error.provider_keys_stale",
  model_not_served: "error.model_not_served",
  raw_egress_blocked: "error.raw_egress_blocked",
  search_not_enabled: "error.search_not_enabled",
  search_unavailable: "error.search_unavailable",
  search_query_blocked: "error.search_query_blocked",
  not_found: "error.not_found",
  NOT_FOUND: "error.not_found",
  conflict: "error.conflict",
  gateway_error: "error.gateway_error",
  // FIX100 Part C — the turn died on an exception nobody classified.
  // Deliberately NOT in RETRYABLE_CODES: what lands here ranges from a
  // transport blip to a bug in our own parser and the code exists exactly
  // because we cannot tell them apart, so the sentence names the one way
  // out we can promise — write a new message — and Part B's gate replaces
  // Regenerate with it.
  unclassified_error: "error.unclassified_error",
  // FIX80 — the desktop watchdog's mid-stream silence abort, named
  // honestly instead of "connection stalled" with no next step.
  stream_stalled: "error.stream_stalled",
  // ── image pipeline ─────────────────────────────────────────────────
  image_invalid_request: "error.image_invalid_request",
  image_auth_error: "error.image_auth_error",
  image_rate_limited: "error.image_rate_limited",
  // FIX66 Part 4 — the image backend's account is out of credits. NOT
  // retryable (kept out of RETRYABLE_CODES): waiting doesn't refill it.
  image_no_credit: "error.image_no_credit",
  image_timeout: "error.image_timeout",
  image_provider_unavailable: "error.image_provider_unavailable",
  image_provider_error: "error.image_provider_error",
  image_service_not_configured: "error.image_service_not_configured",
  image_empty_response: "error.image_empty_response",
  image_bad_response: "error.image_bad_response",
  image_attachment_save_failed: "error.image_attachment_save_failed",
  image_cancelled: "error.image_cancelled",
  // Phase 5 — stream resume.
  GENERATION_INTERRUPTED: "error.generationInterrupted",
};

/** The dictionary key for a code, or undefined when the code is unknown. */
export function errorDictKey(code?: string): DictKey | undefined {
  return code ? ERROR_CODE_KEYS[code] : undefined;
}

/** Every code this table knows (for the parity test). */
export function knownErrorCodes(): string[] {
  return Object.keys(ERROR_CODE_KEYS);
}

/** Whether a retry of the same request may succeed. The gateway's own
 *  ``retryable`` flag wins when the frame carries one; otherwise the
 *  local table decides; an unknown code is not retryable. */
export function isRetryable(code?: string, retryable?: boolean): boolean {
  if (typeof retryable === "boolean") return retryable;
  return !!code && RETRYABLE_CODES.has(code);
}

/** FIX100 Part B — do we KNOW that sending this same request again cannot
 *  work? Only then is the Regenerate button wrong to offer.
 *
 *  Regenerate re-sends the identical context. The owner's conversation
 *  e493ca28 failed six times because the button was there whatever the
 *  failure was: deepseek answered upstream_bad_response, retryable:false,
 *  with a sentence that says "change the request before sending it again",
 *  and directly under that sentence sat a button that sent it again.
 *
 *  Deliberately three-valued, and the default is to LEAVE THE BUTTON:
 *
 *  · the gateway's own flag wins when the frame carried one;
 *  · else a code this table knows decides, by RETRYABLE_CODES;
 *  · a code we do not know, or no code at all, returns false.
 *
 *  That last line is the one that matters. A message read back from the
 *  server after a reload has no code — the error frame is live-only — so
 *  every failed turn in a reloaded thread would otherwise lose its retry.
 *  Removing a working affordance is a worse failure than offering one that
 *  will not help, so silence here means "keep it".
 *
 *  This is the same input localizeErrorCode() gets, so the sentence and the
 *  button cannot contradict each other. */
export function knownNotRetryable(code?: string, retryable?: boolean): boolean {
  if (typeof retryable === "boolean") return !retryable;
  if (!code) return false;
  if (RETRYABLE_CODES.has(code)) return false;
  return errorDictKey(code) !== undefined;
}

/** Localized message for a gateway error code (active locale).
 *
 *  ``serverMessage`` is used only for a code this table does not know
 *  and only when it is a real sentence (the gateway's catalog message,
 *  never provider details). An unknown code with no usable sentence
 *  gets the neutral line — "try again" is never implied for a failure
 *  whose nature we do not know. */
export function localizeErrorCode(
  code?: string,
  serverMessage?: string,
  retryable?: boolean,
): string {
  const key = errorDictKey(code);
  if (key) {
    return tOutside(key);
  }
  const server = serverMessage?.trim();
  if (server && !looksRaw(server)) {
    return server;
  }
  return tOutside(isRetryable(code, retryable) ? "error.generic" : "error.unknownNoRetry");
}

// A message that carries a JSON body, an HTTP status or a stack is the
// transport talking, not a sentence for a person.
function looksRaw(msg: string): boolean {
  return /[{}\[\]]|status=\d|body=|traceback|exception/i.test(msg);
}

/** Short notice shown under a message that a fallback model answered.
 *  e.g. "Answered by GPT-4o (claude-sonnet-4 temporarily unavailable)". */
export function fallbackNotice(
  requestedModel: string,
  actualModel: string,
): string {
  return tOutside("error.fallbackNotice", {
    actual: actualModel,
    requested: requestedModel,
  });
}

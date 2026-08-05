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

import { tOutside } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";

const ERROR_CODE_KEYS: Record<string, DictKey> = {
  provider_error: "error.provider_error",
  provider_unavailable: "error.provider_unavailable",
  provider_timeout: "error.provider_timeout",
  all_models_failed: "error.all_models_failed",
  rate_limit_exceeded: "error.rate_limit_exceeded",
  INSUFFICIENT_CREDITS: "error.insufficient_credits",
  insufficient_credits: "error.insufficient_credits",
  internal_error: "error.internal_error",
  image_invalid_request: "error.image_invalid_request",
  image_auth_error: "error.image_auth_error",
  image_rate_limited: "error.image_rate_limited",
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

/** Localized message for a gateway error code (active locale). */
export function localizeErrorCode(
  code?: string,
  serverMessage?: string,
): string {
  if (code && ERROR_CODE_KEYS[code]) {
    return tOutside(ERROR_CODE_KEYS[code]);
  }
  return serverMessage?.trim() || tOutside("error.generic");
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

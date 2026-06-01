// B1 (Layer 3) — map machine-readable gateway error codes to
// user-friendly Thai messages. The gateway/chat-edge emit a stable
// ``code`` plus an English ``message``; the frontend owns localization
// so the wire stays language-neutral. Unknown codes fall back to the
// server-provided message, then a generic Thai default.

const ERROR_MESSAGES_TH: Record<string, string> = {
  provider_error: "ไม่สามารถเชื่อมต่อกับผู้ให้บริการ AI ได้ กรุณาลองใหม่อีกครั้ง",
  provider_unavailable: "บริการ AI ไม่พร้อมใช้งานชั่วคราว กรุณาลองใหม่อีกครั้ง",
  provider_timeout: "ผู้ให้บริการ AI ตอบสนองช้าเกินไป กรุณาลองใหม่อีกครั้ง",
  all_models_failed:
    "ไม่สามารถตอบได้ในขณะนี้ กรุณาลองใหม่ หรือเลือกโมเดลอื่น",
  rate_limit_exceeded:
    "มีคำขอมากเกินไป กรุณารอสักครู่แล้วลองใหม่อีกครั้ง",
  internal_error: "เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่อีกครั้ง",
};

const GENERIC_TH = "เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง";

/** Localized Thai message for a gateway error code. */
export function localizeErrorCode(
  code?: string,
  serverMessage?: string,
): string {
  if (code && ERROR_MESSAGES_TH[code]) {
    return ERROR_MESSAGES_TH[code];
  }
  return serverMessage?.trim() || GENERIC_TH;
}

/** Short Thai notice shown under a message that a fallback answered.
 *  e.g. "ตอบโดย GPT-4o (claude-sonnet-4 ไม่พร้อมใช้งานชั่วคราว)". */
export function fallbackNotice(
  requestedModel: string,
  actualModel: string,
): string {
  return `ตอบโดย ${actualModel} (${requestedModel} ไม่พร้อมใช้งานชั่วคราว)`;
}

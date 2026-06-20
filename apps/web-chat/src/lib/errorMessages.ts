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
  INSUFFICIENT_CREDITS:
    "เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต",
  insufficient_credits:
    "เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต",
  internal_error: "เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่อีกครั้ง",
  image_invalid_request:
    "คำขอสร้างรูปไม่ถูกต้อง หรือโมเดล/ขนาดรูปไม่รองรับ",
  image_auth_error:
    "ระบบยังไม่มีสิทธิ์ใช้บริการสร้างรูป กรุณาตรวจสอบ OpenAI API key หรือสิทธิ์ของโมเดล",
  image_rate_limited:
    "โควต้าหรือ rate limit สำหรับสร้างรูปเต็มแล้ว กรุณารอสักครู่แล้วลองใหม่",
  image_timeout:
    "ระบบสร้างรูปใช้เวลานานเกินไป กรุณาลองใหม่อีกครั้ง",
  image_provider_unavailable:
    "บริการสร้างรูปไม่พร้อมใช้งานชั่วคราว กรุณาลองใหม่อีกครั้ง",
  image_provider_error:
    "ไม่สามารถสร้างรูปได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
  image_service_not_configured:
    "ระบบสร้างรูปยังไม่ได้ตั้งค่า กรุณาติดต่อผู้ดูแลระบบ",
  image_empty_response:
    "ระบบสร้างรูปไม่ส่งไฟล์รูปกลับมา กรุณาลองใหม่อีกครั้ง",
  image_bad_response:
    "ไฟล์รูปที่ได้รับไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง",
  image_attachment_save_failed:
    "สร้างรูปแล้วแต่บันทึกไฟล์ไม่สำเร็จ กรุณาลองใหม่อีกครั้ง",
  image_cancelled: "ยกเลิกการสร้างรูปแล้ว",
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

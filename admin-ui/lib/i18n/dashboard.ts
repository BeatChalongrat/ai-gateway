// Dashboard + login.
//
// Key prefix for this module: `dash.` — the parity test rejects a key
// claimed by two modules, so stay inside the prefix.
// `th` is typed against `en`, so a missing Thai twin is a COMPILE error.
//
// NOT in here on purpose: provider brand names (OpenAI / Anthropic /
// Google), model ids, HTTP status codes and DLP category identifiers.
// Those render verbatim in both locales so a screenshot from a Thai admin
// still matches what an operator greps for in the logs.

export const en = {
  // ── Page header ──────────────────────────────────────────
  "dash.header.subtitle": "Platform overview · updated {n}s ago",

  // ── Period selector ──────────────────────────────────────
  "dash.period.24h": "24h",
  "dash.period.7d": "7d",
  "dash.period.30d": "30d",

  // ── KPI cards ────────────────────────────────────────────
  "dash.kpi.requests": "Requests",
  "dash.kpi.successRate": "Success rate",
  "dash.kpi.avgLatency": "Avg latency",
  "dash.kpi.spend": "Spend",
  "dash.kpi.noPriorData": "No prior data",
  "dash.kpi.vs24h": "vs. prior 24h",
  "dash.kpi.vs7d": "vs. prior week",
  "dash.kpi.vs30d": "vs. prior 30d",

  // ── Request volume chart ─────────────────────────────────
  "dash.volume.title": "Request volume",
  "dash.volume.subtitle": "{total} requests total · by provider",
  "dash.volume.error": "Failed to load volume data",
  "dash.volume.emptyTitle": "No requests yet",
  // Split around the <code>/v1/chat/completions</code> element, which is
  // an endpoint path and must not be translated or reordered into the
  // middle of a substituted string.
  "dash.volume.emptyHintPrefix": "Send a request to",
  "dash.volume.emptyHintSuffix": "to see data",
  // Legend/tooltip bucket for every provider outside the named three.
  "dash.provider.other": "Others",

  // ── Top models ───────────────────────────────────────────
  "dash.topModels.title": "Top models",
  "dash.topModels.subtitle": "By request count",
  "dash.topModels.error": "Failed to load top models",
  "dash.topModels.emptyTitle": "No models yet",
  "dash.topModels.emptyHint": "No requests in this period.",

  // ── Recent requests ──────────────────────────────────────
  "dash.recent.title": "Recent requests",
  "dash.recent.live": "LIVE",
  "dash.recent.meta": "Last {n} · auto-refresh 5s",
  "dash.recent.filterPlaceholder": "Filter by model, provider, status…",
  "dash.recent.noMatch": "No matching requests.",
  "dash.recent.empty": "No recent requests.",
  "dash.recent.latency": "Latency",
  "dash.recent.when": "When",

  // Seconds-granularity relative time; minutes/hours/days reuse `time.*`
  // from common.ts.
  "dash.time.secondsAgo": "{n}s ago",

  // ── Data protection (DLP) ────────────────────────────────
  "dash.dlp.title": "Data protection",
  "dash.dlp.subtitle24h": "DLP findings from the last 24 hours",
  "dash.dlp.subtitle7d": "DLP findings from the last 7 days",
  "dash.dlp.subtitle30d": "DLP findings from the last 30 days",
  "dash.dlp.error": "Failed to load DLP stats.",
  "dash.dlp.totalFindings": "Total findings",
  "dash.dlp.requestsProtected": "Requests protected",
  "dash.dlp.blocked": "Blocked",
  "dash.dlp.masked": "Masked",
  "dash.dlp.byCategory": "By category",
  "dash.dlp.byProject": "By project",
  "dash.dlp.empty": "No findings in this period",

  // ── Login ────────────────────────────────────────────────
  "dash.login.identifier": "Username or email",
  "dash.login.identifierPlaceholder": "Username or e-mail",
  "dash.login.password": "Password",
  "dash.login.passwordPlaceholder": "Enter password",
  "dash.login.submit": "Sign In",
  "dash.login.submitting": "Authenticating…",
  "dash.login.errorInvalid": "Invalid credentials",
  "dash.login.errorRateLimited": "Too many attempts. Try again later.",
  "dash.login.errorNetwork": "Unable to reach the backend. Try again.",
};

export const th: Record<keyof typeof en, string> = {
  // ── Page header ──────────────────────────────────────────
  "dash.header.subtitle": "ภาพรวมระบบ · อัปเดตเมื่อ {n} วินาทีที่แล้ว",

  // ── Period selector ──────────────────────────────────────
  "dash.period.24h": "24 ชม.",
  "dash.period.7d": "7 วัน",
  "dash.period.30d": "30 วัน",

  // ── KPI cards ────────────────────────────────────────────
  "dash.kpi.requests": "คำขอทั้งหมด",
  "dash.kpi.successRate": "อัตราสำเร็จ",
  "dash.kpi.avgLatency": "เวลาตอบสนองเฉลี่ย",
  "dash.kpi.spend": "ค่าใช้จ่าย",
  "dash.kpi.noPriorData": "ไม่มีข้อมูลช่วงก่อนหน้า",
  "dash.kpi.vs24h": "เทียบกับ 24 ชม. ก่อนหน้า",
  "dash.kpi.vs7d": "เทียบกับสัปดาห์ก่อนหน้า",
  "dash.kpi.vs30d": "เทียบกับ 30 วันก่อนหน้า",

  // ── Request volume chart ─────────────────────────────────
  "dash.volume.title": "ปริมาณคำขอ",
  "dash.volume.subtitle": "รวม {total} คำขอ · แยกตามผู้ให้บริการ",
  "dash.volume.error": "โหลดข้อมูลปริมาณคำขอไม่สำเร็จ",
  "dash.volume.emptyTitle": "ยังไม่มีคำขอเข้ามา",
  "dash.volume.emptyHintPrefix": "ลองส่งคำขอไปที่",
  "dash.volume.emptyHintSuffix": "เพื่อดูข้อมูล",
  "dash.provider.other": "อื่น ๆ",

  // ── Top models ───────────────────────────────────────────
  "dash.topModels.title": "โมเดลที่ใช้งานมากที่สุด",
  "dash.topModels.subtitle": "เรียงตามจำนวนคำขอ",
  "dash.topModels.error": "โหลดข้อมูลโมเดลยอดนิยมไม่สำเร็จ",
  "dash.topModels.emptyTitle": "ยังไม่มีการใช้งานโมเดล",
  "dash.topModels.emptyHint": "ไม่มีคำขอในช่วงเวลานี้",

  // ── Recent requests ──────────────────────────────────────
  "dash.recent.title": "คำขอล่าสุด",
  "dash.recent.live": "เรียลไทม์",
  "dash.recent.meta": "{n} รายการล่าสุด · รีเฟรชอัตโนมัติทุก 5 วินาที",
  "dash.recent.filterPlaceholder": "กรองตามโมเดล ผู้ให้บริการ หรือสถานะ…",
  "dash.recent.noMatch": "ไม่พบคำขอที่ตรงกับเงื่อนไข",
  "dash.recent.empty": "ยังไม่มีคำขอล่าสุด",
  "dash.recent.latency": "เวลาตอบสนอง",
  "dash.recent.when": "เมื่อ",

  "dash.time.secondsAgo": "{n} วินาทีที่แล้ว",

  // ── Data protection (DLP) ────────────────────────────────
  "dash.dlp.title": "การปกป้องข้อมูล",
  "dash.dlp.subtitle24h": "ข้อมูลอ่อนไหวที่ DLP ตรวจพบใน 24 ชั่วโมงล่าสุด",
  "dash.dlp.subtitle7d": "ข้อมูลอ่อนไหวที่ DLP ตรวจพบใน 7 วันล่าสุด",
  "dash.dlp.subtitle30d": "ข้อมูลอ่อนไหวที่ DLP ตรวจพบใน 30 วันล่าสุด",
  "dash.dlp.error": "โหลดสถิติ DLP ไม่สำเร็จ",
  "dash.dlp.totalFindings": "รายการที่ตรวจพบทั้งหมด",
  "dash.dlp.requestsProtected": "คำขอที่ได้รับการป้องกัน",
  "dash.dlp.blocked": "ถูกบล็อก",
  "dash.dlp.masked": "ถูกปิดบังข้อมูล",
  "dash.dlp.byCategory": "แยกตามประเภทข้อมูล",
  "dash.dlp.byProject": "แยกตามโปรเจกต์",
  "dash.dlp.empty": "ไม่พบข้อมูลอ่อนไหวในช่วงเวลานี้",

  // ── Login ────────────────────────────────────────────────
  "dash.login.identifier": "ชื่อผู้ใช้หรืออีเมล",
  "dash.login.identifierPlaceholder": "ชื่อผู้ใช้หรืออีเมล",
  "dash.login.password": "รหัสผ่าน",
  "dash.login.passwordPlaceholder": "กรอกรหัสผ่าน",
  "dash.login.submit": "เข้าสู่ระบบ",
  "dash.login.submitting": "กำลังตรวจสอบสิทธิ์…",
  "dash.login.errorInvalid": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง",
  "dash.login.errorRateLimited": "พยายามเข้าสู่ระบบบ่อยเกินไป กรุณาลองใหม่ในภายหลัง",
  "dash.login.errorNetwork": "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ กรุณาลองใหม่อีกครั้ง",
};

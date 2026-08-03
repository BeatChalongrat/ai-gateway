// Shared UI components.
//
// Key prefix for this module: `ui.` — the parity test rejects a key
// claimed by two modules, so stay inside the prefix.
// `th` is typed against `en`, so a missing Thai twin is a COMPILE error.
//
// Scope: components/ui/*, components/dlp/*, the app shell, and the one
// user-facing string in lib/useApi.ts. Anything a page owns lives in that
// page's module instead.
//
// Deliberately NOT translated here (see the rule in common.ts): DLP
// category identifiers (PII / Network / Credential …), detection-type
// identifiers (Regex / Keywords / Semantic), Presidio entity names
// (PERSON, IP_ADDRESS …), HTTP status codes, and the sample values used as
// input placeholders — an operator pastes those straight into a bug report
// or a detector definition, so they must read the same in every locale.

export const en = {
  // ── Modal / dialog ───────────────────────────────────────
  "ui.modal.processing": "Processing…",

  // ── Table ────────────────────────────────────────────────
  "ui.table.noResultsFor": 'No results for "{query}"',

  // ── Search ───────────────────────────────────────────────
  "ui.search.placeholder": "Search…",

  // ── Stat card ────────────────────────────────────────────
  "ui.stat.live": "live",

  // ── Audit timeline (DLP detector history) ────────────────
  "ui.audit.noHistory": "No history yet.",
  "ui.audit.by": "by {user}",
  "ui.audit.statusChange": "status: {from} → {to}",
  "ui.audit.reason": "reason: {reason}",
  "ui.audit.action.create": "Created",
  "ui.audit.action.update": "Updated",
  "ui.audit.action.activate": "Activated",
  "ui.audit.action.moveToTesting": "Moved to testing",
  "ui.audit.action.disable": "Disabled",
  "ui.audit.action.delete": "Deleted",

  // ── Custom detector form ─────────────────────────────────
  "ui.detector.section.identity": "Identity",
  "ui.detector.section.classification": "Classification",
  "ui.detector.section.detection": "Detection",
  "ui.detector.section.testCases": "Test cases",
  "ui.detector.category": "Category",
  "ui.detector.severity": "Severity",
  "ui.detector.defaultAction": "Default action",
  "ui.detector.detectionType": "Detection type",
  "ui.detector.typeLocked": "Detection type can't change after creation",
  "ui.detector.regexPattern": "Regex pattern",
  "ui.detector.keywords": "Keywords (comma-separated)",
  "ui.detector.entityType": "Entity type",
  "ui.detector.semanticNote":
    "Semantic detectors are stored but not yet executed at runtime in this release.",
  "ui.detector.runTests": "Run tests",
  "ui.detector.addCase": "Add case",
  "ui.detector.noTestCases": "No test cases — add one to validate the pattern.",
  "ui.detector.sampleInput": "sample input",
  "ui.detector.shouldMatch": "Should match",
  "ui.detector.pass": "pass",
  "ui.detector.fail": "fail",
  "ui.detector.removeCase": "Remove test case",
  "ui.detector.saveChanges": "Save changes",
  "ui.detector.saveFailed": "Could not save the detector.",
  "ui.detector.testFailed": "Test run failed.",
  "ui.detector.saveBeforeTest":
    "Save the detector first to run tests against the server.",

  // ── Severity levels ──────────────────────────────────────
  "ui.severity.low": "Low",
  "ui.severity.medium": "Medium",
  "ui.severity.high": "High",
  "ui.severity.critical": "Critical",

  // ── DLP actions (what the engine does on a match) ────────
  "ui.dlpAction.mask": "Mask",
  "ui.dlpAction.block": "Block",
  "ui.dlpAction.pass": "Pass",
  "ui.dlpAction.restore": "Restore",

  // ── API errors surfaced by useApi ────────────────────────
  // {status} stays an HTTP status code / the literal below.
  "ui.api.error": "Error {status}",
  "ui.api.timeout": "timeout",
};

export const th: Record<keyof typeof en, string> = {
  // ── Modal / dialog ───────────────────────────────────────
  "ui.modal.processing": "กำลังดำเนินการ…",

  // ── Table ────────────────────────────────────────────────
  "ui.table.noResultsFor": 'ไม่พบผลลัพธ์สำหรับ "{query}"',

  // ── Search ───────────────────────────────────────────────
  "ui.search.placeholder": "ค้นหา…",

  // ── Stat card ────────────────────────────────────────────
  "ui.stat.live": "ออนไลน์",

  // ── Audit timeline (DLP detector history) ────────────────
  "ui.audit.noHistory": "ยังไม่มีประวัติการเปลี่ยนแปลง",
  "ui.audit.by": "โดย {user}",
  "ui.audit.statusChange": "สถานะ: {from} → {to}",
  "ui.audit.reason": "เหตุผล: {reason}",
  "ui.audit.action.create": "สร้าง",
  "ui.audit.action.update": "แก้ไข",
  "ui.audit.action.activate": "เปิดใช้งาน",
  "ui.audit.action.moveToTesting": "ย้ายไปสถานะทดสอบ",
  "ui.audit.action.disable": "ปิดใช้งาน",
  "ui.audit.action.delete": "ลบ",

  // ── Custom detector form ─────────────────────────────────
  "ui.detector.section.identity": "ข้อมูลทั่วไป",
  "ui.detector.section.classification": "การจัดประเภท",
  "ui.detector.section.detection": "การตรวจจับ",
  "ui.detector.section.testCases": "กรณีทดสอบ",
  "ui.detector.category": "หมวดหมู่",
  "ui.detector.severity": "ระดับความรุนแรง",
  "ui.detector.defaultAction": "การดำเนินการเริ่มต้น",
  "ui.detector.detectionType": "รูปแบบการตรวจจับ",
  "ui.detector.typeLocked": "เปลี่ยนรูปแบบการตรวจจับหลังสร้างแล้วไม่ได้",
  "ui.detector.regexPattern": "รูปแบบ Regex",
  "ui.detector.keywords": "คำสำคัญ (คั่นด้วยเครื่องหมายจุลภาค)",
  "ui.detector.entityType": "ประเภทเอนทิตี",
  "ui.detector.semanticNote":
    "ตัวตรวจจับแบบ Semantic จะถูกบันทึกไว้ แต่ยังไม่ทำงานจริงในเวอร์ชันนี้",
  "ui.detector.runTests": "รันการทดสอบ",
  "ui.detector.addCase": "เพิ่มกรณีทดสอบ",
  "ui.detector.noTestCases": "ยังไม่มีกรณีทดสอบ — เพิ่มอย่างน้อยหนึ่งรายการเพื่อตรวจสอบรูปแบบ",
  "ui.detector.sampleInput": "ข้อความตัวอย่าง",
  "ui.detector.shouldMatch": "ควรตรวจพบ",
  "ui.detector.pass": "ผ่าน",
  "ui.detector.fail": "ไม่ผ่าน",
  "ui.detector.removeCase": "ลบกรณีทดสอบ",
  "ui.detector.saveChanges": "บันทึกการเปลี่ยนแปลง",
  "ui.detector.saveFailed": "บันทึกตัวตรวจจับไม่สำเร็จ",
  "ui.detector.testFailed": "รันการทดสอบไม่สำเร็จ",
  "ui.detector.saveBeforeTest": "กรุณาบันทึกตัวตรวจจับก่อน จึงจะทดสอบกับเซิร์ฟเวอร์ได้",

  // ── Severity levels ──────────────────────────────────────
  "ui.severity.low": "ต่ำ",
  "ui.severity.medium": "ปานกลาง",
  "ui.severity.high": "สูง",
  "ui.severity.critical": "วิกฤต",

  // ── DLP actions (what the engine does on a match) ────────
  "ui.dlpAction.mask": "ปิดบัง",
  "ui.dlpAction.block": "บล็อก",
  "ui.dlpAction.pass": "ปล่อยผ่าน",
  "ui.dlpAction.restore": "คืนค่าเดิม",

  // ── API errors surfaced by useApi ────────────────────────
  "ui.api.error": "เกิดข้อผิดพลาด {status}",
  "ui.api.timeout": "หมดเวลาเชื่อมต่อ",
};

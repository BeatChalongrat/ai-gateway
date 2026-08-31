// FIX33 A-D6 — what would the DESKTOP store after an extraction call on the
// gateway's PUBLIC path (shared API key, no internal-service secret — the
// desktop's path)? The default policy RESTORES pii/identity/financial/network/
// thai_name in responses, so the summary JSON may carry raw values. This sends
// the exact desktop extraction request (shared prompt as system, no tools)
// over a PII-laden chain and reports whether the raw values came back.
const fs = require("fs");
const path = require("path");
const REPO = path.resolve(__dirname, "..", "..");
const dist = path.join(REPO, "apps", "desktop", "dist", "main");
const { COMPACTION_EXTRACTION_PROMPT } = require(path.join(dist, "compaction-prompt.js"));
const { buildExtractionUserBody, parseExtraction } = require(path.join(dist, "conversation-state.js"));
const env = fs.readFileSync(path.join(REPO, ".env"), "utf8");
const KEY = env.match(/^GATEWAY_API_KEY=(.+)$/m)[1].trim();
const MODEL = process.env.PROBE_MODEL || "gpt-5.6-terra";

const PHONE = "081-234-5678", EMAIL = "somchai.jaidee@example.co.th", NAME = "สมชาย ใจดี";
const chain = [
  { role: "user", content: `สวัสดี ผมชื่อ${NAME} เบอร์โทร ${PHONE} อีเมล ${EMAIL} ช่วยจำไว้สำหรับติดต่อเรื่องโปรเจกต์` },
  { role: "assistant", content: "รับทราบครับ บันทึกข้อมูลติดต่อไว้แล้ว" },
  { role: "user", content: "โปรเจกต์คือทำระบบจองห้องประชุม เดดไลน์ 15 ก.ย." },
  { role: "assistant", content: "เข้าใจครับ ระบบจองห้องประชุม ส่ง 15 ก.ย." },
];
(async () => {
  // FIX36 Part D — mirror the desktop client: the state extractor now sends
  // metadata.dlp_response_restore="off" so the summary comes back MASKED.
  const body = { model: MODEL, stream: false, max_tokens: 800,
    metadata: { dlp_response_restore: "off" },
    messages: [
    { role: "system", content: COMPACTION_EXTRACTION_PROMPT },
    { role: "user", content: buildExtractionUserBody(null, chain, "") },
  ] };
  const res = await fetch((process.env.GATEWAY_URL || "http://localhost:8080") + "/v1/chat/completions", { method: "POST", headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await res.json();
  const text = j.choices?.[0]?.message?.content ?? "";
  const parsed = parseExtraction(text);
  const out = {
    http: res.status,
    policy_action: j.policy_action ?? null,
    parsed_ok: !!parsed,
    raw_phone_in_summary: text.includes(PHONE) || text.includes(PHONE.replace(/-/g, "")),
    raw_email_in_summary: text.includes(EMAIL),
    raw_name_in_summary: text.includes(NAME),
    placeholders_seen: (text.match(/<<[A-Z_]+_[A-Z0-9]+>>/g) || []).slice(0, 8),
    excerpt: text.slice(0, 500),
  };
  console.log(JSON.stringify(out, null, 2));
  fs.writeFileSync(path.join(__dirname, "dlp-check.json"), JSON.stringify(out, null, 2));
})().catch((e) => { console.error("FATAL", e); process.exit(1); });

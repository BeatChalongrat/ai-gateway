// User-facing UI strings, keyed by dotted-path identifiers.
//
// We DO NOT translate technical terms (model ids, error codes,
// developer-facing copy in toasts), keyboard shortcut hints, or
// API status messages. The user's rule: focus on visible UI labels.
//
// Adding a new language: copy the `en` shape, fill every key, then
// add the locale to `Locale` + the `dictionaries` map below. The
// `t()` function falls back to `en` for any missing key so a
// partial translation degrades gracefully.

export type Locale = "en" | "th";

// Single TypeScript-typed dictionary shape — exporting the EN
// dictionary as the canonical type means new keys force a compile
// error in any non-EN dictionary that hasn't filled them in.
export const en = {
  // ── Sidebar ──────────────────────────────────────────────
  "sidebar.newChat": "New chat",
  "sidebar.searchChats": "Search chats",
  "sidebar.empty": "No conversations yet.",
  "sidebar.group.today": "Today",
  "sidebar.group.yesterday": "Yesterday",
  "sidebar.group.last7": "Previous 7 days",
  "sidebar.group.last30": "Previous 30 days",
  "sidebar.group.older": "Older",

  // ── Sidebar user menu ────────────────────────────────────
  "menu.theme.dark": "Dark mode",
  "menu.theme.light": "Light mode",
  "menu.language": "Language",
  "menu.knowledgeBase": "Knowledge base",
  "menu.voiceSettings": "Voice settings",
  "menu.customInstructions": "Custom instructions",
  "menu.logout": "Log out",

  // ── Empty state / greeting ───────────────────────────────
  "empty.greet.morning": "Good morning",
  "empty.greet.afternoon": "Good afternoon",
  "empty.greet.evening": "Good evening",
  "empty.greet.lateNight": "Still up",
  "empty.greet.midnight": "Burning the midnight oil",
  "empty.subtitle": "How can I help you today?",
  "empty.chip.write": "Write",
  "empty.chip.learn": "Learn",
  "empty.chip.code": "Code",
  "empty.chip.analyze": "Analyze",
  "empty.chip.write.prompt": "Help me draft ",
  "empty.chip.learn.prompt": "Explain ",
  "empty.chip.code.prompt": "Write a ",
  "empty.chip.analyze.prompt": "Analyze this ",

  // ── Chat input ───────────────────────────────────────────
  "input.placeholder": "Message SRAN Chat…",
  "input.placeholder.streaming": "Streaming response…",
  "input.placeholder.uploading": "Uploading…",
  "input.attach": "Attach files",
  "input.voice": "Voice input",
  "input.send": "Send",
  "input.stop": "Stop generation",
  "input.footer": "SRAN Chat can make mistakes. Check important info.",

  // ── Conversation item ────────────────────────────────────
  "conv.rename": "Rename",
  "conv.delete": "Delete",

  // ── Export menu ──────────────────────────────────────────
  "export.markdown": "Export as Markdown",
  "export.json": "Export as JSON",
  "export.copy": "Copy to clipboard",
  "export.share": "Share…",

  // ── Voice settings ───────────────────────────────────────
  "voice.title": "Voice settings",
  "voice.subtitle": "Configure speech-to-text and text-to-speech",
  "voice.section.input": "Voice input",
  "voice.section.output": "Voice output",
  "voice.section.behaviour": "Behaviour",
  "voice.field.method": "Method",
  "voice.field.language": "Language",
  "voice.field.source": "Source",
  "voice.field.voice": "Voice",
  "voice.field.speed": "Speed",
  "voice.method.browser": "Browser",
  "voice.method.browser.hint": "Free, real-time. Works in Chrome, Edge, Safari.",
  "voice.method.whisper": "Whisper",
  "voice.method.whisper.hint": "Higher accuracy. Requires server key.",
  "voice.source.browser": "Browser",
  "voice.source.browser.hint": "System voices. No server round-trip.",
  "voice.source.openai": "OpenAI TTS",
  "voice.source.openai.hint": "Premium voices. Requires server key.",
  "voice.voice.systemDefault": "System default",
  "voice.autoRead": "Auto-read AI responses",
  "voice.autoRead.hint": "Automatically read new replies aloud.",
  "voice.preview.play": "Preview voice",
  "voice.preview.stop": "Stop preview",
  "voice.done": "Done",

  // ── Custom instructions ──────────────────────────────────
  "instr.title": "Custom instructions",
  "instr.subtitle": "Shape how the AI responds in every conversation",
  "instr.about.label": "What would you like the AI to know about you?",
  "instr.about.placeholder":
    "e.g. I'm a software developer working with Python and Go. I prefer concise answers with code examples.",
  "instr.respond.label": "How would you like the AI to respond?",
  "instr.respond.placeholder":
    "e.g. Be concise and technical. Use code blocks. Explain trade-offs.",
  "instr.cancel": "Cancel",
  "instr.save": "Save",
  "instr.saving": "Saving…",

  // ── Knowledge bases page ─────────────────────────────────
  "kb.back": "Back to chat",
  "kb.title": "Knowledge bases",
  "kb.subtitle":
    "Upload documents that the AI can retrieve from when answering questions.",
  "kb.create": "Create",
  "kb.create.full": "Create knowledge base",
  "kb.empty.title": "No knowledge bases yet",
  "kb.empty.body":
    "Create a knowledge base to give the AI context from your own documents.",
  "kb.card.manage": "Manage",
  "kb.card.close": "Close",
  "kb.status.indexed": "Indexed",
  "kb.status.inactive": "Inactive",
  "kb.status.empty": "Empty",
  "kb.unit.document": "document",
  "kb.unit.documents": "documents",
  "kb.unit.chunk": "chunk",
  "kb.unit.chunks": "chunks",
} as const;

export type DictKey = keyof typeof en;

// ── Thai ─────────────────────────────────────────────────────
// Coverage matches `en` 1:1. The compile-time `Record<DictKey, string>`
// guarantees no key is silently missing — a typo or forgotten field
// blocks the build instead of shipping a half-translated UI.
const th: Record<DictKey, string> = {
  // Sidebar
  "sidebar.newChat": "แชทใหม่",
  "sidebar.searchChats": "ค้นหาแชท",
  "sidebar.empty": "ยังไม่มีบทสนทนา",
  "sidebar.group.today": "วันนี้",
  "sidebar.group.yesterday": "เมื่อวาน",
  "sidebar.group.last7": "7 วันที่ผ่านมา",
  "sidebar.group.last30": "30 วันที่ผ่านมา",
  "sidebar.group.older": "ก่อนหน้านั้น",

  // Menu
  "menu.theme.dark": "โหมดมืด",
  "menu.theme.light": "โหมดสว่าง",
  "menu.language": "ภาษา",
  "menu.knowledgeBase": "ฐานความรู้",
  "menu.voiceSettings": "ตั้งค่าเสียง",
  "menu.customInstructions": "คำสั่งแบบกำหนดเอง",
  "menu.logout": "ออกจากระบบ",

  // Empty
  "empty.greet.morning": "สวัสดีตอนเช้า",
  "empty.greet.afternoon": "สวัสดีตอนบ่าย",
  "empty.greet.evening": "สวัสดีตอนเย็น",
  "empty.greet.lateNight": "ยังตื่นอยู่",
  "empty.greet.midnight": "ทำงานดึกอีกแล้ว",
  "empty.subtitle": "วันนี้ให้ช่วยอะไรดี?",
  "empty.chip.write": "เขียน",
  "empty.chip.learn": "เรียนรู้",
  "empty.chip.code": "โค้ด",
  "empty.chip.analyze": "วิเคราะห์",
  "empty.chip.write.prompt": "ช่วยร่าง ",
  "empty.chip.learn.prompt": "อธิบาย ",
  "empty.chip.code.prompt": "เขียน ",
  "empty.chip.analyze.prompt": "วิเคราะห์ ",

  // Input
  "input.placeholder": "ส่งข้อความถึง SRAN Chat…",
  "input.placeholder.streaming": "กำลังตอบ…",
  "input.placeholder.uploading": "กำลังอัปโหลด…",
  "input.attach": "แนบไฟล์",
  "input.voice": "ป้อนเสียง",
  "input.send": "ส่ง",
  "input.stop": "หยุด",
  "input.footer": "SRAN Chat อาจตอบผิดได้ ตรวจสอบข้อมูลสำคัญด้วย",

  // Conv item
  "conv.rename": "เปลี่ยนชื่อ",
  "conv.delete": "ลบ",

  // Export
  "export.markdown": "ส่งออกเป็น Markdown",
  "export.json": "ส่งออกเป็น JSON",
  "export.copy": "คัดลอกไปยังคลิปบอร์ด",
  "export.share": "แชร์…",

  // Voice
  "voice.title": "ตั้งค่าเสียง",
  "voice.subtitle": "ตั้งค่าการแปลงเสียงเป็นข้อความและข้อความเป็นเสียง",
  "voice.section.input": "การป้อนด้วยเสียง",
  "voice.section.output": "เสียงตอบกลับ",
  "voice.section.behaviour": "พฤติกรรม",
  "voice.field.method": "วิธีการ",
  "voice.field.language": "ภาษา",
  "voice.field.source": "แหล่งเสียง",
  "voice.field.voice": "เสียง",
  "voice.field.speed": "ความเร็ว",
  "voice.method.browser": "เบราว์เซอร์",
  "voice.method.browser.hint":
    "ฟรี ทันที ใช้ได้กับ Chrome, Edge, Safari",
  "voice.method.whisper": "Whisper",
  "voice.method.whisper.hint": "แม่นยำกว่า ต้องตั้งค่า server key",
  "voice.source.browser": "เบราว์เซอร์",
  "voice.source.browser.hint": "เสียงระบบ ไม่ต้องเรียก server",
  "voice.source.openai": "OpenAI TTS",
  "voice.source.openai.hint": "เสียงคุณภาพสูง ต้องตั้งค่า server key",
  "voice.voice.systemDefault": "เสียงเริ่มต้นของระบบ",
  "voice.autoRead": "อ่านคำตอบของ AI อัตโนมัติ",
  "voice.autoRead.hint": "อ่านคำตอบใหม่ออกเสียงให้อัตโนมัติ",
  "voice.preview.play": "ฟังตัวอย่าง",
  "voice.preview.stop": "หยุดฟัง",
  "voice.done": "เสร็จสิ้น",

  // Instructions
  "instr.title": "คำสั่งแบบกำหนดเอง",
  "instr.subtitle": "กำหนดวิธีตอบของ AI ในทุกบทสนทนา",
  "instr.about.label": "อยากให้ AI รู้เกี่ยวกับคุณอย่างไร?",
  "instr.about.placeholder":
    "เช่น ผมเป็นนักพัฒนาที่ใช้ Python และ Go อยากได้คำตอบกระชับพร้อมตัวอย่างโค้ด",
  "instr.respond.label": "อยากให้ AI ตอบอย่างไร?",
  "instr.respond.placeholder":
    "เช่น ตอบให้กระชับ ใช้บล็อกโค้ด อธิบายข้อแลกเปลี่ยน",
  "instr.cancel": "ยกเลิก",
  "instr.save": "บันทึก",
  "instr.saving": "กำลังบันทึก…",

  // KB
  "kb.back": "กลับไปที่แชท",
  "kb.title": "ฐานความรู้",
  "kb.subtitle":
    "อัปโหลดเอกสารให้ AI ใช้ค้นหาข้อมูลเวลาตอบคำถาม",
  "kb.create": "สร้าง",
  "kb.create.full": "สร้างฐานความรู้",
  "kb.empty.title": "ยังไม่มีฐานความรู้",
  "kb.empty.body":
    "สร้างฐานความรู้เพื่อให้ AI ใช้ข้อมูลจากเอกสารของคุณ",
  "kb.card.manage": "จัดการ",
  "kb.card.close": "ปิด",
  "kb.status.indexed": "พร้อมใช้งาน",
  "kb.status.inactive": "ปิดใช้งาน",
  "kb.status.empty": "ว่างเปล่า",
  "kb.unit.document": "เอกสาร",
  "kb.unit.documents": "เอกสาร",
  "kb.unit.chunk": "ชิ้น",
  "kb.unit.chunks": "ชิ้น",
};

export const dictionaries: Record<Locale, Record<DictKey, string>> = {
  en,
  th,
};

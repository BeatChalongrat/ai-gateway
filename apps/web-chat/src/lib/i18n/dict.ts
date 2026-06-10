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
  "sidebar.group.pinned": "Pinned",
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
  "input.imageMode": "Generate an image",
  "input.placeholder.image": "Describe the image to generate…",
  "input.send": "Send",
  "input.stop": "Stop generation",
  "input.footer": "SRAN Chat can make mistakes. Check important info.",

  // ── Conversation item ────────────────────────────────────
  "conv.rename": "Rename",
  "conv.delete": "Delete",
  "conv.pin": "Pin",
  "conv.unpin": "Unpin",

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

  // ── Model selector ───────────────────────────────────────
  "model.auto.name": "Auto",
  "model.auto.description": "Selects the best model automatically",
  "model.selectModel": "Select model",
  "model.searchPlaceholder": "Search models…",
  "model.popular": "Popular",
  "model.recent": "Recently used",
  "model.reasoningBadge": "Thinks",
  "model.imageBadge": "Image",

  // ── Memory page ──────────────────────────────────────────
  "memory.back": "Back to chat",
  "memory.title": "Memory",
  "memory.description":
    "SRAN Chat remembers facts from your conversations to personalize future responses.",
  "memory.savedCount.zero": "0 saved memories",
  "memory.savedCount.one": "1 saved memory",
  "memory.savedCount.other": "{count} saved memories",
  "memory.addMemory": "Add memory",
  "memory.searchPlaceholder": "Search memories…",
  "memory.noResults": "No memories match “{query}”.",

  "memory.empty.title": "No memories yet",
  "memory.empty.description":
    "Just chat naturally — the AI will automatically remember important facts about you, like your name, preferences, and interests. You can also tell it directly: “remember that…” or “จำไว้ว่า…”",
  "memory.empty.hint":
    "Try saying: “My name is Beat and I prefer concise Thai responses.”",

  "memory.suggested.title": "Suggested preferences ({count})",
  "memory.suggested.description":
    "AI noticed these patterns. Confirm to activate; reject to dismiss.",

  // ── Memory card ──────────────────────────────────────────
  "memory.type.fact": "fact",
  "memory.type.preference": "preference",
  "memory.type.interest": "interest",
  "memory.type.instruction": "instruction",
  "memory.type.suggested": "suggested",
  "memory.type.fileReference": "file reference",
  "memory.type.inferred": "inferred",
  "memory.card.pinned": "Pinned",
  "memory.card.savedAgo": "saved {time}",
  "memory.card.sourceLabel": "Source:",
  "memory.card.source.conv": "“{title}” conversation",
  "memory.card.source.userExplicitConv": "saved from a conversation",
  "memory.card.source.userExplicitDirect": "direct input",
  "memory.card.source.assistantInferred": "AI detected from conversation",
  "memory.card.source.summary": "conversation summary",
  "memory.card.source.artifact": "file event",
  "memory.card.source.detectedAgo": "detected {time}",
  "memory.card.source.fromConvAgo": "from a conversation {time}",
  "memory.card.usedOne": "Used 1 time",
  "memory.card.usedMany": "Used {count} times",
  "memory.card.lastUsed": "last used {time}",
  "memory.card.edit": "Edit",
  "memory.card.pin": "Pin",
  "memory.card.unpin": "Unpin",
  "memory.card.delete": "Delete",

  // ── Pending preference card ──────────────────────────────
  "memory.pending.confirm": "Confirm",
  "memory.pending.reject": "Reject",

  // ── Memory form dialog ───────────────────────────────────
  "memory.form.titleAdd": "Add memory",
  "memory.form.titleEdit": "Edit memory",
  "memory.form.descriptionAdd":
    "Save a fact or preference so it persists across all chats.",
  "memory.form.descriptionEdit":
    "Update the saved fact, preference, or instruction.",
  "memory.form.contentLabel": "What should I remember?",
  "memory.form.contentPlaceholder": "e.g. My name is Beat",
  "memory.form.typeLabel": "Type",
  "memory.form.categoryLabel": "Category (optional)",
  "memory.form.categoryPlaceholder":
    "identity, language, coding, work…",
  "memory.form.cancel": "Cancel",
  "memory.form.saving": "Saving…",
  "memory.form.saveNew": "Save memory",
  "memory.form.saveEdit": "Save changes",
  "memory.form.errorEmpty": "Please enter what should be remembered.",
  "memory.form.errorTooLong":
    "Memory text is limited to {max} characters.",
  "memory.form.toastSaved": "Memory saved.",
  "memory.form.toastUpdated": "Memory updated.",
  "memory.form.toastError": "Couldn’t save the memory.",

  "memory.form.opt.fact": "Fact",
  "memory.form.opt.preference": "Preference",
  "memory.form.opt.interest": "Interest",
  "memory.form.opt.instruction": "Instruction",
  "memory.form.opt.fileReference": "File reference",
  "memory.form.describe.fact":
    "A personal fact — name, role, identity, etc.",
  "memory.form.describe.preference":
    "How you want the AI to respond (tone, length, language).",
  "memory.form.describe.interest":
    "What you’re currently working on or interested in.",
  "memory.form.describe.instruction":
    "A standing rule to follow in every conversation.",
  "memory.form.describe.fileReference":
    "Something about a file the AI should remember.",

  // ── Temporary chat section ───────────────────────────────
  "memory.temporary.title": "Temporary chat",
  "memory.temporary.description":
    "Start a conversation that doesn’t use or create memory. Useful when you don’t want a particular topic feeding into your saved facts.",
  "memory.temporary.button": "Start temporary chat",
  "memory.temporary.toast":
    "Starting a temporary chat — memory is off for this one.",

  // ── Data management ──────────────────────────────────────
  "memory.export.title": "Data management",
  "memory.export.description":
    "Download a JSON snapshot of every memory the AI has stored about you. The export expires after 24 hours.",
  "memory.export.button": "Export my memories",
  "memory.export.busy": "Preparing export…",
  "memory.export.ready": "Export ready ({size})",
  "memory.export.error": "Couldn’t export memories.",

  // ── Danger zone ──────────────────────────────────────────
  "memory.danger.title": "Danger zone",
  "memory.danger.clearDescription":
    "Clear every saved memory. The chat history stays — only the cross-conversation facts the AI remembers about you are removed.",
  "memory.danger.clearButton": "Clear all memories",
  "memory.danger.purgeDescription":
    "Permanently delete all data. Irreversibly remove every memory, event, conversation state, and source attribution. Past export files are also unlinked.",
  "memory.danger.purgeDescriptionPrefix": "Permanently delete all data.",
  "memory.danger.purgeButton": "Permanently delete everything",
  "memory.danger.purgeBusy": "Deleting…",
  "memory.danger.purgePromptPhrase": "DELETE ALL MY MEMORIES",
  "memory.danger.purgePromptBody":
    "To permanently delete ALL memories, events, conversation states, and source attributions, type exactly:\n\nDELETE ALL MY MEMORIES",
  "memory.danger.purgeMismatch":
    "Confirmation did not match. Deletion cancelled.",
  "memory.danger.purgeError": "Permanent delete failed.",
  "memory.danger.purgeDoneOne":
    "Permanently deleted 1 memory + related data.",
  "memory.danger.purgeDoneMany":
    "Permanently deleted {count} memories + related data.",

  // ── Confirm dialogs ──────────────────────────────────────
  "memory.confirm.cancel": "Cancel",
  "memory.confirm.clearTitle": "Clear all memories?",
  "memory.confirm.clearBody":
    "This will remove every saved memory for your account. This action cannot be undone.",
  "memory.confirm.clearAction": "Clear all",
  "memory.confirm.deleteTitle": "Delete this memory?",
  "memory.confirm.deleteAction": "Delete",
  "memory.confirm.purgeTitle": "Permanently delete all data?",
  "memory.confirm.purgeBody":
    "This will irreversibly delete every memory, memory event, conversation state, and source attribution. It also unlinks any past export files. You’ll need to type the confirmation phrase next.",
  "memory.confirm.purgeAction": "Continue",
  "memory.clear.toastNone": "No memories to clear.",
  "memory.clear.toastOne": "Cleared 1 memory.",
  "memory.clear.toastMany": "Cleared {count} memories.",
  "memory.clear.toastError": "Couldn’t clear memories.",

  // ── Suggested-preference toasts ──────────────────────────
  "memory.toast.preferenceConfirmed": "Preference confirmed.",
  "memory.toast.preferenceConfirmError": "Couldn’t confirm preference.",
  "memory.toast.suggestionDismissed": "Suggestion dismissed.",
  "memory.toast.suggestionDismissError": "Couldn’t dismiss suggestion.",
  "memory.toast.memoryDeleted": "Memory deleted.",
  "memory.toast.memoryDeleteError": "Couldn’t delete memory.",
  "memory.toast.pinError": "Couldn’t update pin.",

  // ── Generic states ───────────────────────────────────────
  "memory.error.loadFailed": "Couldn’t load memories.",
  "memory.error.tryAgain": "Try again",

  // ── Reasoning / Thinking Mode (T4) ──────────────────────
  "reasoning.label": "Thinking",
  "reasoning.toggle": "Toggle reasoning mode",
  "reasoning.effort": "Reasoning effort",
  "reasoning.thinking": "Thinking…",
  "reasoning.analyzing": "Analyzing",
  "reasoning.analyzed": "Analyzed",
  "reasoning.duration": "{seconds}s",
  "reasoning.mode.auto": "Auto",
  "reasoning.mode.low": "Quick",
  "reasoning.mode.medium": "Balanced",
  "reasoning.mode.high": "Deep",
  "reasoning.showSummary": "Show analysis",
  "reasoning.error": "Couldn't update reasoning mode",
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
  "sidebar.group.pinned": "ปักหมุด",
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
  "input.imageMode": "สร้างรูปภาพ",
  "input.placeholder.image": "อธิบายรูปที่ต้องการสร้าง…",
  "input.send": "ส่ง",
  "input.stop": "หยุด",
  "input.footer": "SRAN Chat อาจตอบผิดได้ ตรวจสอบข้อมูลสำคัญด้วย",

  // Conv item
  "conv.rename": "เปลี่ยนชื่อ",
  "conv.delete": "ลบ",
  "conv.pin": "ปักหมุด",
  "conv.unpin": "เลิกปักหมุด",

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

  // Model selector
  "model.auto.name": "อัตโนมัติ",
  "model.auto.description": "เลือก model ให้อัตโนมัติ",
  "model.selectModel": "เลือกโมเดล",
  "model.searchPlaceholder": "ค้นหาโมเดล…",
  "model.popular": "ยอดนิยม",
  "model.recent": "ใช้ล่าสุด",
  "model.reasoningBadge": "คิดวิเคราะห์",
  "model.imageBadge": "สร้างรูป",

  // Memory page
  "memory.back": "กลับไปแชท",
  "memory.title": "ความจำ",
  "memory.description":
    "SRAN Chat จดจำข้อมูลจากการสนทนาเพื่อปรับคำตอบให้เหมาะกับคุณ",
  "memory.savedCount.zero": "ยังไม่มีความจำที่บันทึก",
  "memory.savedCount.one": "บันทึกไว้ 1 รายการ",
  "memory.savedCount.other": "บันทึกไว้ {count} รายการ",
  "memory.addMemory": "เพิ่มความจำ",
  "memory.searchPlaceholder": "ค้นหาความจำ…",
  "memory.noResults": "ไม่พบความจำที่ตรงกับ “{query}”",

  "memory.empty.title": "ยังไม่มีความจำ",
  "memory.empty.description":
    "แค่คุยไปตามปกติ AI จะจดจำข้อมูลสำคัญเกี่ยวกับคุณเอง เช่น ชื่อ ความชอบ และสิ่งที่สนใจ หรือจะบอกตรง ๆ ก็ได้ เช่น “จำไว้ว่า…”",
  "memory.empty.hint":
    "ลองพูดว่า: “ผมชื่อ Beat ชอบคำตอบภาษาไทยแบบกระชับ”",

  "memory.suggested.title": "ความชอบที่แนะนำ ({count})",
  "memory.suggested.description":
    "AI สังเกตพฤติกรรมเหล่านี้ กดยืนยันเพื่อเปิดใช้งาน หรือไม่ใช่เพื่อปฏิเสธ",

  // Memory card
  "memory.type.fact": "ข้อเท็จจริง",
  "memory.type.preference": "ความชอบ",
  "memory.type.interest": "สิ่งที่สนใจ",
  "memory.type.instruction": "คำสั่งถาวร",
  "memory.type.suggested": "แนะนำ",
  "memory.type.fileReference": "อ้างอิงไฟล์",
  "memory.type.inferred": "อนุมาน",
  "memory.card.pinned": "ปักหมุดไว้",
  "memory.card.savedAgo": "บันทึกเมื่อ {time}",
  "memory.card.sourceLabel": "ที่มา:",
  "memory.card.source.conv": "สนทนา “{title}”",
  "memory.card.source.userExplicitConv": "บันทึกจากการสนทนา",
  "memory.card.source.userExplicitDirect": "เพิ่มโดยตรง",
  "memory.card.source.assistantInferred": "AI ตรวจพบจากการสนทนา",
  "memory.card.source.summary": "สรุปการสนทนา",
  "memory.card.source.artifact": "เหตุการณ์ไฟล์",
  "memory.card.source.detectedAgo": "ตรวจพบเมื่อ {time}",
  "memory.card.source.fromConvAgo": "จากการสนทนาเมื่อ {time}",
  "memory.card.usedOne": "ใช้ไปแล้ว 1 ครั้ง",
  "memory.card.usedMany": "ใช้ไปแล้ว {count} ครั้ง",
  "memory.card.lastUsed": "ครั้งล่าสุดเมื่อ {time}",
  "memory.card.edit": "แก้ไข",
  "memory.card.pin": "ปักหมุด",
  "memory.card.unpin": "เลิกปักหมุด",
  "memory.card.delete": "ลบ",

  // Pending preference card
  "memory.pending.confirm": "ยืนยัน",
  "memory.pending.reject": "ไม่ใช่",

  // Memory form dialog
  "memory.form.titleAdd": "เพิ่มความจำ",
  "memory.form.titleEdit": "แก้ไขความจำ",
  "memory.form.descriptionAdd":
    "บันทึกข้อเท็จจริงหรือความชอบเพื่อใช้ได้ในทุกบทสนทนา",
  "memory.form.descriptionEdit":
    "แก้ไขข้อเท็จจริง ความชอบ หรือคำสั่งที่บันทึกไว้",
  "memory.form.contentLabel": "ต้องการให้จำอะไร?",
  "memory.form.contentPlaceholder": "เช่น ผมชื่อ Beat",
  "memory.form.typeLabel": "ประเภท",
  "memory.form.categoryLabel": "หมวดหมู่ (ไม่บังคับ)",
  "memory.form.categoryPlaceholder":
    "เช่น ตัวตน, ภาษา, เขียนโค้ด, งาน…",
  "memory.form.cancel": "ยกเลิก",
  "memory.form.saving": "กำลังบันทึก…",
  "memory.form.saveNew": "บันทึก",
  "memory.form.saveEdit": "บันทึกการแก้ไข",
  "memory.form.errorEmpty": "กรุณาระบุสิ่งที่ต้องการให้จำ",
  "memory.form.errorTooLong":
    "ข้อความความจำจำกัด {max} ตัวอักษร",
  "memory.form.toastSaved": "บันทึกความจำแล้ว",
  "memory.form.toastUpdated": "อัปเดตความจำแล้ว",
  "memory.form.toastError": "บันทึกความจำไม่สำเร็จ",

  "memory.form.opt.fact": "ข้อเท็จจริง",
  "memory.form.opt.preference": "ความชอบ",
  "memory.form.opt.interest": "สิ่งที่สนใจ",
  "memory.form.opt.instruction": "คำสั่งถาวร",
  "memory.form.opt.fileReference": "อ้างอิงไฟล์",
  "memory.form.describe.fact":
    "ข้อมูลส่วนตัว เช่น ชื่อ บทบาท ตัวตน",
  "memory.form.describe.preference":
    "วิธีที่อยากให้ AI ตอบ (โทน ความยาว ภาษา)",
  "memory.form.describe.interest":
    "สิ่งที่กำลังทำหรือสนใจอยู่ในตอนนี้",
  "memory.form.describe.instruction":
    "กฎที่ต้องปฏิบัติทุกบทสนทนา",
  "memory.form.describe.fileReference":
    "สิ่งที่ AI ควรจำเกี่ยวกับไฟล์",

  // Temporary chat
  "memory.temporary.title": "แชทชั่วคราว",
  "memory.temporary.description":
    "เริ่มสนทนาที่ไม่ใช้และไม่สร้างความจำ เหมาะเมื่อไม่ต้องการให้หัวข้อบางอย่างถูกบันทึก",
  "memory.temporary.button": "เริ่มแชทชั่วคราว",
  "memory.temporary.toast":
    "เริ่มแชทชั่วคราว — ปิดความจำเฉพาะแชทนี้",

  // Data management
  "memory.export.title": "จัดการข้อมูล",
  "memory.export.description":
    "ดาวน์โหลดข้อมูลความจำทั้งหมดในรูปแบบ JSON ไฟล์จะหมดอายุใน 24 ชั่วโมง",
  "memory.export.button": "ส่งออกความจำ",
  "memory.export.busy": "กำลังเตรียมไฟล์…",
  "memory.export.ready": "ไฟล์พร้อมแล้ว ({size})",
  "memory.export.error": "ส่งออกความจำไม่สำเร็จ",

  // Danger zone
  "memory.danger.title": "โซนอันตราย",
  "memory.danger.clearDescription":
    "ล้างความจำทั้งหมด ประวัติการสนทนาจะยังอยู่ — ลบเฉพาะข้อมูลที่ AI จดจำข้ามบทสนทนา",
  "memory.danger.clearButton": "ล้างความจำทั้งหมด",
  "memory.danger.purgeDescription":
    "ลบข้อมูลทั้งหมดถาวร ลบความจำ เหตุการณ์ สถานะการสนทนา และแหล่งอ้างอิงทั้งหมดอย่างถาวร ไฟล์ส่งออกในอดีตจะถูกตัดการเชื่อมโยงด้วย",
  "memory.danger.purgeDescriptionPrefix": "ลบข้อมูลทั้งหมดถาวร",
  "memory.danger.purgeButton": "ลบทุกอย่างถาวร",
  "memory.danger.purgeBusy": "กำลังลบ…",
  "memory.danger.purgePromptPhrase": "DELETE ALL MY MEMORIES",
  "memory.danger.purgePromptBody":
    "เพื่อยืนยันการลบความจำ เหตุการณ์ สถานะการสนทนา และแหล่งอ้างอิงทั้งหมดอย่างถาวร พิมพ์ข้อความนี้ตรงตัว:\n\nDELETE ALL MY MEMORIES",
  "memory.danger.purgeMismatch":
    "ข้อความยืนยันไม่ตรงกัน ยกเลิกการลบ",
  "memory.danger.purgeError": "ลบถาวรไม่สำเร็จ",
  "memory.danger.purgeDoneOne":
    "ลบความจำ 1 รายการ พร้อมข้อมูลที่เกี่ยวข้องถาวรแล้ว",
  "memory.danger.purgeDoneMany":
    "ลบความจำ {count} รายการ พร้อมข้อมูลที่เกี่ยวข้องถาวรแล้ว",

  // Confirm dialogs
  "memory.confirm.cancel": "ยกเลิก",
  "memory.confirm.clearTitle": "ล้างความจำทั้งหมด?",
  "memory.confirm.clearBody":
    "การดำเนินการนี้จะลบความจำทั้งหมดของบัญชีคุณ ไม่สามารถย้อนกลับได้",
  "memory.confirm.clearAction": "ล้างทั้งหมด",
  "memory.confirm.deleteTitle": "ลบความจำนี้?",
  "memory.confirm.deleteAction": "ลบ",
  "memory.confirm.purgeTitle": "ลบข้อมูลทั้งหมดถาวร?",
  "memory.confirm.purgeBody":
    "การดำเนินการนี้จะลบความจำ เหตุการณ์ สถานะการสนทนา และแหล่งอ้างอิงทั้งหมดอย่างถาวร ไฟล์ส่งออกในอดีตจะถูกตัดการเชื่อมโยงด้วย ขั้นต่อไปต้องพิมพ์ข้อความยืนยัน",
  "memory.confirm.purgeAction": "ดำเนินการต่อ",
  "memory.clear.toastNone": "ไม่มีความจำให้ล้าง",
  "memory.clear.toastOne": "ล้างความจำ 1 รายการ",
  "memory.clear.toastMany": "ล้างความจำ {count} รายการ",
  "memory.clear.toastError": "ล้างความจำไม่สำเร็จ",

  // Suggested-preference toasts
  "memory.toast.preferenceConfirmed": "ยืนยันความชอบแล้ว",
  "memory.toast.preferenceConfirmError": "ยืนยันความชอบไม่สำเร็จ",
  "memory.toast.suggestionDismissed": "ปิดคำแนะนำแล้ว",
  "memory.toast.suggestionDismissError": "ปิดคำแนะนำไม่สำเร็จ",
  "memory.toast.memoryDeleted": "ลบความจำแล้ว",
  "memory.toast.memoryDeleteError": "ลบความจำไม่สำเร็จ",
  "memory.toast.pinError": "อัปเดตการปักหมุดไม่สำเร็จ",

  // Generic states
  "memory.error.loadFailed": "โหลดความจำไม่สำเร็จ",
  "memory.error.tryAgain": "ลองใหม่",

  // Reasoning / Thinking Mode (T4)
  "reasoning.label": "วิเคราะห์",
  "reasoning.toggle": "เปิด/ปิดโหมดวิเคราะห์",
  "reasoning.effort": "ระดับการวิเคราะห์",
  "reasoning.thinking": "กำลังคิด…",
  "reasoning.analyzing": "กำลังวิเคราะห์",
  "reasoning.analyzed": "วิเคราะห์แล้ว",
  "reasoning.duration": "{seconds} วิ",
  "reasoning.mode.auto": "อัตโนมัติ",
  "reasoning.mode.low": "เร็ว",
  "reasoning.mode.medium": "สมดุล",
  "reasoning.mode.high": "ละเอียด",
  "reasoning.showSummary": "แสดงการวิเคราะห์",
  "reasoning.error": "อัปเดตโหมดวิเคราะห์ไม่สำเร็จ",
};

export const dictionaries: Record<Locale, Record<DictKey, string>> = {
  en,
  th,
};

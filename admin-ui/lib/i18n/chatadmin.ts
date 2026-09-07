// Chat analytics / Conversations / Chat users / Knowledge bases.
//
// Key prefix for this module: `chat.` — the parity test rejects a key
// claimed by two modules, so stay inside the prefix.
// `th` is typed against `en`, so a missing Thai twin is a COMPILE error.
//
// NOT translated here (deliberately): model ids, conversation/message ids,
// tenant uuids, user emails, file names, knowledge-base document titles,
// embedding model names, and message CONTENT — that is user data, or a
// value an operator pastes into a bug report. Only the chrome around it
// (headers, labels, banners, confirmations) is localized.
//
// Shared vocabulary reused from common.ts rather than redefined:
// field.user / field.model / field.role / field.status / field.name /
// field.tenant / field.type / field.id / field.created / field.updated /
// field.active / field.inactive / field.disabled, action.apply /
// action.reset / action.search / action.clear / action.enable /
// action.disable / action.delete / action.deleting, state.loading /
// state.errorLoad / state.noResults, time.last7d / time.last30d /
// time.last90d / time.justNow / time.minutesAgo / time.hoursAgo /
// time.daysAgo.

export const en = {
  // ── Shared across the chat-admin pages ───────────────────
  "chat.untitled": "Untitled",

  // ── Table column headers ─────────────────────────────────
  "chat.col.title": "Title",
  "chat.col.messages": "Messages",
  "chat.col.msgs": "Msgs",
  "chat.col.tokens": "Tokens",
  "chat.col.lastActive": "Last Active",
  "chat.col.files": "Files",
  "chat.col.shared": "Shared",
  "chat.col.conversations": "Conversations",
  "chat.col.documents": "Documents",
  "chat.col.chunks": "Chunks",
  "chat.col.filename": "Filename",
  "chat.col.size": "Size",
  "chat.col.uploaded": "Uploaded",

  // ── Chat analytics ───────────────────────────────────────
  "chat.analytics.title": "Chat Analytics",
  "chat.analytics.subtitle":
    "Web Chat usage, model breakdown, and recent conversations",
  "chat.analytics.loadError": "Failed to load analytics",
  "chat.analytics.periodToday": "Today",
  "chat.analytics.statActiveUsers": "Active Users",
  "chat.analytics.statTokensUsed": "Tokens Used",
  "chat.analytics.statEstCost": "Est. Cost",
  "chat.analytics.messagesCount": "{n} messages",
  "chat.analytics.filesUploaded": "{n} files uploaded",
  "chat.analytics.inputOutput": "input + output",
  "chat.analytics.listPricing": "provider list pricing",
  "chat.analytics.messagesOverTime": "Messages Over Time",
  "chat.analytics.daily": "daily",
  "chat.analytics.noChatData": "No chat data yet",
  "chat.analytics.usageByModel": "Usage by Model",
  "chat.analytics.noModelUsage": "No model usage yet",
  "chat.analytics.recentConversations": "Recent Conversations",
  "chat.analytics.viewAll": "View all",
  "chat.analytics.noConversations": "No conversations yet",

  // ── Conversation logs (list) ─────────────────────────────
  "chat.conversations.title": "Conversation Logs",
  "chat.conversations.subtitle":
    "Filter and inspect chat conversations across the workspace",
  "chat.conversations.searchTitleLabel": "Search title",
  "chat.conversations.searchPlaceholder": "Search conversations...",
  "chat.conversations.modelIdLabel": "Model id",
  "chat.conversations.modelIdPlaceholder": "e.g. gpt-4o",
  "chat.conversations.from": "From",
  "chat.conversations.to": "To",

  // ── Conversation detail ──────────────────────────────────
  "chat.conversation.title": "Conversation",
  "chat.conversation.backToLogs": "Back to logs",
  "chat.conversation.maskedBanner":
    "Viewing masked content — PII has been redacted by the DLP pipeline. Raw user prompts are never stored in this database.",
  "chat.conversation.userName": "User name",
  "chat.conversation.tokenSummary": "{input} in · {output} out tokens",
  "chat.conversation.attachments": "Attachments",
  "chat.conversation.downloadsDisabled":
    "File downloads are intentionally disabled on the admin surface — admins see metadata only.",
  "chat.role.user": "User",
  "chat.role.assistant": "Assistant",

  // ── Chat users ───────────────────────────────────────────
  "chat.users.title": "Chat Users",
  "chat.users.subtitle": "Per-user chat usage and access controls",
  "chat.users.searchPlaceholder": "Search by email or name...",
  "chat.users.updateFailed": "Failed to update user",
  "chat.users.enableUser": "Enable user",
  "chat.users.disableUser": "Disable user",
  "chat.users.disableTitle": "Disable this user?",
  "chat.users.disableBody": "Disable {name}? They lose access to chat immediately; their conversations are kept. You can enable them again from this page.",
  "chat.users.disableConfirm": "Disable user",

  // ── Knowledge bases (list) ───────────────────────────────
  "chat.kb.title": "Knowledge Bases",
  "chat.kb.subtitle":
    "Inspect retrieval-augmented knowledge bases across every tenant",
  "chat.kb.filterName": "Search by name",
  "chat.kb.searchPlaceholder": "Search knowledge bases...",
  "chat.kb.filterTenantId": "Tenant ID (uuid)",
  "chat.kb.emptyFilteredTitle": "No knowledge bases match these filters",
  "chat.kb.emptyFilteredBody":
    "Try a broader search term or a different tenant id.",
  "chat.kb.clearFilters": "Clear filters",
  "chat.kb.emptyTitle": "No knowledge bases yet",
  "chat.kb.emptyBody":
    "Knowledge bases created by users in SRAN Chat will appear here, with their per-tenant documents and chunk counts.",

  // ── Knowledge base detail ────────────────────────────────
  "chat.kb.detailTitle": "Knowledge Base",
  "chat.kb.detailLoadError": "Could not load this knowledge base",
  "chat.kb.notFound": "Not found",
  "chat.kb.noDescription": "No description",
  "chat.kb.allKbs": "All KBs",
  "chat.kb.chunkSize": "Chunk size",
  "chat.kb.overlap": "Overlap",
  "chat.kb.chars": "{n} chars",
  "chat.kb.embeddingModel": "Embedding model",
  "chat.kb.documentsCount": "Documents ({n})",
  "chat.kb.noDocuments": "No documents indexed.",
  "chat.kb.sampleChunks": "Sample chunks",
  "chat.kb.noChunks": "No chunks yet — upload a document to populate this list.",
  "chat.kb.section": "section {n}",
  "chat.kb.backToList": "Back to list",

  // Danger zone
  "chat.kb.adminActions": "Admin actions",
  "chat.kb.adminActionsBody":
    "Reindex re-embeds every document — useful after a chunk-size config change. Delete cascades to documents, chunks, and on-disk / S3 blobs.",
  "chat.kb.reindex": "Reindex",
  "chat.kb.reindexAll": "Reindex all documents",
  "chat.kb.reindexing": "Reindexing…",
  "chat.kb.deleteKb": "Delete knowledge base",
  "chat.kb.confirmReindexTitle": "Reindex documents?",
  "chat.kb.confirmReindexBody":
    "Re-index all documents in “{name}”? This re-embeds {count} documents and may take a while.",
  "chat.kb.confirmReindexBodyOne":
    "Re-index all documents in “{name}”? This re-embeds 1 document and may take a while.",
  "chat.kb.confirmDeleteTitle": "Delete knowledge base?",
  "chat.kb.confirmDeleteBody":
    "Delete “{name}”? {documents} documents and {chunks} chunks will be permanently removed.",
  "chat.kb.reindexResult": "Reindexed {done} of {total} documents",
  "chat.kb.reindexFailedSuffix": "({failed} failed)",
  "chat.kb.reindexError": "Reindex failed: {message}",
  "chat.kb.reindexErrorPlain": "Reindex failed",
  "chat.kb.deleteError": "Delete failed: {message}",
  "chat.kb.deleteErrorPlain": "Delete failed",

  // Document indexing status
  "chat.kb.docStatus.pending": "Pending",
  "chat.kb.docStatus.processing": "Processing",
  "chat.kb.docStatus.indexed": "Indexed",
  "chat.kb.docStatus.failed": "Failed",

  // Retention & deletion
  "chat.retention.title": "Data Retention",
  "chat.retention.subtitle": "Per-tenant transcript retention, deletion audit, and dry-run preview. Changes take effect at the next scheduled run (00:00 UTC), not immediately.",
  "chat.retention.tenantLabel": "Tenant ID",
  "chat.retention.load": "Load",
  "chat.retention.enabled": "Transcript retention enabled",
  "chat.retention.days": "Retention window (days)",
  "chat.retention.daysHint": "Minimum {n} days. Empty = keep forever. Conversations with no activity beyond this window are permanently deleted.",
  "chat.retention.purgeDays": "User-deleted purge window (days)",
  "chat.retention.purgeDaysHint": "A chat the user deletes disappears immediately and is permanently erased from the server this many days later.",
  "chat.retention.dryRun": "Dry run (count only, delete nothing)",
  "chat.retention.dryRunHint": "Leave ON for the first nightly pass, read the numbers below, then turn it off to let deletion actually run.",
  "chat.retention.save": "Save settings",
  "chat.retention.saved": "Saved. Takes effect {at}.",
  "chat.retention.effectiveAt": "Next scheduled run: {at}",
  "chat.retention.preview": "Preview what would be deleted",
  "chat.retention.previewTitle": "Dry-run numbers (nothing was deleted)",
  "chat.retention.audit.title": "Deletion audit",
  "chat.retention.audit.empty": "No deletion-audit entries for this tenant yet.",
  "chat.retention.audit.when": "When",
  "chat.retention.audit.action": "Action",
  "chat.retention.audit.rule": "Rule",
  "chat.retention.audit.actor": "Actor",
  "chat.retention.audit.counts": "Counts",
  "chat.retention.floorError": "The retention window cannot be below {n} days.",
};

export const th: Record<keyof typeof en, string> = {
  // ── Shared across the chat-admin pages ───────────────────
  "chat.untitled": "ไม่มีชื่อ",

  // ── Table column headers ─────────────────────────────────
  "chat.col.title": "ชื่อบทสนทนา",
  "chat.col.messages": "ข้อความ",
  "chat.col.msgs": "ข้อความ",
  "chat.col.tokens": "โทเคน",
  "chat.col.lastActive": "ใช้งานล่าสุด",
  "chat.col.files": "ไฟล์",
  "chat.col.shared": "แชร์",
  "chat.col.conversations": "บทสนทนา",
  "chat.col.documents": "เอกสาร",
  "chat.col.chunks": "ชิ้นส่วนข้อมูล",
  "chat.col.filename": "ชื่อไฟล์",
  "chat.col.size": "ขนาด",
  "chat.col.uploaded": "อัปโหลดเมื่อ",

  // ── Chat analytics ───────────────────────────────────────
  "chat.analytics.title": "วิเคราะห์การใช้งานแชท",
  "chat.analytics.subtitle":
    "การใช้งาน Web Chat สัดส่วนการใช้โมเดล และบทสนทนาล่าสุด",
  "chat.analytics.loadError": "โหลดข้อมูลวิเคราะห์ไม่สำเร็จ",
  "chat.analytics.periodToday": "วันนี้",
  "chat.analytics.statActiveUsers": "ผู้ใช้ที่ใช้งานอยู่",
  "chat.analytics.statTokensUsed": "โทเคนที่ใช้",
  "chat.analytics.statEstCost": "ค่าใช้จ่ายโดยประมาณ",
  "chat.analytics.messagesCount": "{n} ข้อความ",
  "chat.analytics.filesUploaded": "อัปโหลดไฟล์ {n} รายการ",
  "chat.analytics.inputOutput": "ขาเข้า + ขาออก",
  "chat.analytics.listPricing": "ราคาตามที่ผู้ให้บริการประกาศ",
  "chat.analytics.messagesOverTime": "จำนวนข้อความตามช่วงเวลา",
  "chat.analytics.daily": "รายวัน",
  "chat.analytics.noChatData": "ยังไม่มีข้อมูลการแชท",
  "chat.analytics.usageByModel": "การใช้งานแยกตามโมเดล",
  "chat.analytics.noModelUsage": "ยังไม่มีการใช้งานโมเดล",
  "chat.analytics.recentConversations": "บทสนทนาล่าสุด",
  "chat.analytics.viewAll": "ดูทั้งหมด",
  "chat.analytics.noConversations": "ยังไม่มีบทสนทนา",

  // ── Conversation logs (list) ─────────────────────────────
  "chat.conversations.title": "บันทึกบทสนทนา",
  "chat.conversations.subtitle":
    "กรองและตรวจสอบบทสนทนาทั้งหมดในเวิร์กสเปซ",
  "chat.conversations.searchTitleLabel": "ค้นหาจากชื่อ",
  "chat.conversations.searchPlaceholder": "ค้นหาบทสนทนา...",
  "chat.conversations.modelIdLabel": "รหัสโมเดล",
  "chat.conversations.modelIdPlaceholder": "เช่น gpt-4o",
  "chat.conversations.from": "ตั้งแต่วันที่",
  "chat.conversations.to": "ถึงวันที่",

  // ── Conversation detail ──────────────────────────────────
  "chat.conversation.title": "บทสนทนา",
  "chat.conversation.backToLogs": "กลับไปหน้าบันทึก",
  "chat.conversation.maskedBanner":
    "กำลังดูเนื้อหาที่ถูกปิดบัง — ข้อมูลส่วนบุคคลถูกกลบโดยระบบ DLP แล้ว ข้อความต้นฉบับของผู้ใช้ไม่เคยถูกจัดเก็บในฐานข้อมูลนี้",
  "chat.conversation.userName": "ชื่อผู้ใช้",
  "chat.conversation.tokenSummary": "โทเคนขาเข้า {input} · ขาออก {output}",
  "chat.conversation.attachments": "ไฟล์แนบ",
  "chat.conversation.downloadsDisabled":
    "ระบบปิดการดาวน์โหลดไฟล์บนหน้าผู้ดูแลระบบโดยเจตนา — ผู้ดูแลระบบเห็นได้เฉพาะข้อมูลกำกับไฟล์เท่านั้น",
  "chat.role.user": "ผู้ใช้",
  "chat.role.assistant": "ผู้ช่วย AI",

  // ── Chat users ───────────────────────────────────────────
  "chat.users.title": "ผู้ใช้งานแชท",
  "chat.users.subtitle": "การใช้งานแชทและการควบคุมสิทธิ์รายบุคคล",
  "chat.users.searchPlaceholder": "ค้นหาด้วยอีเมลหรือชื่อ...",
  "chat.users.updateFailed": "อัปเดตผู้ใช้ไม่สำเร็จ",
  "chat.users.enableUser": "เปิดใช้งานผู้ใช้",
  "chat.users.disableUser": "ปิดใช้งานผู้ใช้",
  "chat.users.disableTitle": "ปิดใช้งานผู้ใช้รายนี้?",
  "chat.users.disableBody": "ปิดใช้งาน {name} หรือไม่? ผู้ใช้จะเข้าใช้แชทไม่ได้ทันที บทสนทนายังเก็บไว้ และเปิดใช้งานอีกครั้งได้จากหน้านี้",
  "chat.users.disableConfirm": "ปิดใช้งานผู้ใช้",

  // ── Knowledge bases (list) ───────────────────────────────
  "chat.kb.title": "ฐานความรู้",
  "chat.kb.subtitle": "ตรวจสอบฐานความรู้สำหรับการค้นคืนข้อมูลของทุกองค์กร",
  "chat.kb.filterName": "ค้นหาจากชื่อฐานความรู้",
  "chat.kb.searchPlaceholder": "ค้นหาฐานความรู้...",
  "chat.kb.filterTenantId": "รหัสองค์กร (uuid)",
  "chat.kb.emptyFilteredTitle": "ไม่พบฐานความรู้ตามเงื่อนไขที่กรอง",
  "chat.kb.emptyFilteredBody":
    "ลองใช้คำค้นที่กว้างขึ้น หรือระบุรหัสองค์กรอื่น",
  "chat.kb.clearFilters": "ล้างตัวกรอง",
  "chat.kb.emptyTitle": "ยังไม่มีฐานความรู้",
  "chat.kb.emptyBody":
    "ฐานความรู้ที่ผู้ใช้สร้างใน SRAN Chat จะแสดงที่นี่ พร้อมจำนวนเอกสารและชิ้นส่วนข้อมูลของแต่ละองค์กร",

  // ── Knowledge base detail ────────────────────────────────
  "chat.kb.detailTitle": "ฐานความรู้",
  "chat.kb.detailLoadError": "โหลดฐานความรู้นี้ไม่สำเร็จ",
  "chat.kb.notFound": "ไม่พบข้อมูล",
  "chat.kb.noDescription": "ไม่มีคำอธิบาย",
  "chat.kb.allKbs": "ฐานความรู้ทั้งหมด",
  "chat.kb.chunkSize": "ขนาดชิ้นส่วนข้อมูล",
  "chat.kb.overlap": "ส่วนซ้อนทับ",
  "chat.kb.chars": "{n} ตัวอักษร",
  "chat.kb.embeddingModel": "โมเดลสร้างเวกเตอร์",
  "chat.kb.documentsCount": "เอกสาร ({n})",
  "chat.kb.noDocuments": "ยังไม่มีเอกสารที่จัดทำดัชนี",
  "chat.kb.sampleChunks": "ตัวอย่างชิ้นส่วนข้อมูล",
  "chat.kb.noChunks": "ยังไม่มีชิ้นส่วนข้อมูล — อัปโหลดเอกสารเพื่อเริ่มสร้างรายการนี้",
  "chat.kb.section": "ส่วนที่ {n}",
  "chat.kb.backToList": "กลับไปหน้ารายการ",

  // Danger zone
  "chat.kb.adminActions": "การจัดการสำหรับผู้ดูแลระบบ",
  "chat.kb.adminActionsBody":
    "การจัดทำดัชนีใหม่จะสร้างเวกเตอร์ของทุกเอกสารอีกครั้ง — เหมาะกับกรณีที่เพิ่งเปลี่ยนค่าขนาดชิ้นส่วนข้อมูล ส่วนการลบจะลบต่อเนื่องไปยังเอกสาร ชิ้นส่วนข้อมูล และไฟล์ที่เก็บบนดิสก์ / S3",
  "chat.kb.reindex": "จัดทำดัชนีใหม่",
  "chat.kb.reindexAll": "จัดทำดัชนีใหม่ทุกเอกสาร",
  "chat.kb.reindexing": "กำลังจัดทำดัชนีใหม่…",
  "chat.kb.deleteKb": "ลบฐานความรู้",
  "chat.kb.confirmReindexTitle": "จัดทำดัชนีเอกสารใหม่หรือไม่?",
  "chat.kb.confirmReindexBody":
    "จัดทำดัชนีใหม่ให้ทุกเอกสารใน “{name}” หรือไม่? ระบบจะสร้างเวกเตอร์ของเอกสาร {count} รายการอีกครั้ง และอาจใช้เวลาสักครู่",
  "chat.kb.confirmReindexBodyOne":
    "จัดทำดัชนีใหม่ให้ทุกเอกสารใน “{name}” หรือไม่? ระบบจะสร้างเวกเตอร์ของเอกสาร 1 รายการอีกครั้ง และอาจใช้เวลาสักครู่",
  "chat.kb.confirmDeleteTitle": "ลบฐานความรู้นี้หรือไม่?",
  "chat.kb.confirmDeleteBody":
    "ลบ “{name}” หรือไม่? เอกสาร {documents} รายการ และชิ้นส่วนข้อมูล {chunks} รายการจะถูกลบอย่างถาวร",
  "chat.kb.reindexResult": "จัดทำดัชนีใหม่แล้ว {done} จาก {total} เอกสาร",
  "chat.kb.reindexFailedSuffix": "(ล้มเหลว {failed} รายการ)",
  "chat.kb.reindexError": "จัดทำดัชนีใหม่ไม่สำเร็จ: {message}",
  "chat.kb.reindexErrorPlain": "จัดทำดัชนีใหม่ไม่สำเร็จ",
  "chat.kb.deleteError": "ลบไม่สำเร็จ: {message}",
  "chat.kb.deleteErrorPlain": "ลบไม่สำเร็จ",

  // Document indexing status
  "chat.kb.docStatus.pending": "รอดำเนินการ",
  "chat.kb.docStatus.processing": "กำลังประมวลผล",
  "chat.kb.docStatus.indexed": "จัดทำดัชนีแล้ว",
  "chat.kb.docStatus.failed": "ล้มเหลว",

  // Retention & deletion
  "chat.retention.title": "การเก็บรักษาข้อมูล",
  "chat.retention.subtitle": "ตั้งช่วงเก็บบทสนทนาราย tenant ดู audit ของการลบ และดูตัวเลข dry-run · การเปลี่ยนค่ามีผลตามรอบเวลา (00:00 UTC) ไม่ใช่ทันที",
  "chat.retention.tenantLabel": "รหัส Tenant (Tenant ID)",
  "chat.retention.load": "โหลด",
  "chat.retention.enabled": "เปิดการเก็บรักษาบทสนทนาตามช่วงเวลา",
  "chat.retention.days": "ช่วงเก็บ (วัน)",
  "chat.retention.daysHint": "ขั้นต่ำ {n} วัน · เว้นว่าง = เก็บตลอดไป · บทสนทนาที่ไม่มีการใช้งานเกินช่วงนี้จะถูกลบถาวร",
  "chat.retention.purgeDays": "ช่วงลบจริงหลังผู้ใช้ลบ (วัน)",
  "chat.retention.purgeDaysHint": "แชทที่ผู้ใช้ลบจะหายจากหน้าจอทันที และถูกลบถาวรจากเซิร์ฟเวอร์หลังครบจำนวนวันนี้",
  "chat.retention.dryRun": "โหมดนับอย่างเดียว (dry run — ไม่ลบ)",
  "chat.retention.dryRunHint": "เปิดไว้สำหรับรอบแรก อ่านตัวเลขด้านล่างก่อน แล้วค่อยปิดเพื่อให้การลบทำงานจริง",
  "chat.retention.save": "บันทึกการตั้งค่า",
  "chat.retention.saved": "บันทึกแล้ว มีผล {at}",
  "chat.retention.effectiveAt": "รอบทำงานถัดไป: {at}",
  "chat.retention.preview": "ดูตัวเลขที่จะถูกลบ",
  "chat.retention.previewTitle": "ตัวเลข dry-run (ยังไม่มีอะไรถูกลบ)",
  "chat.retention.audit.title": "Audit ของการลบ",
  "chat.retention.audit.empty": "ยังไม่มีรายการ audit ของ tenant นี้",
  "chat.retention.audit.when": "เมื่อ",
  "chat.retention.audit.action": "การกระทำ",
  "chat.retention.audit.rule": "กฎ",
  "chat.retention.audit.actor": "ผู้กระทำ",
  "chat.retention.audit.counts": "จำนวน",
  "chat.retention.floorError": "ช่วงเก็บต้องไม่ต่ำกว่า {n} วัน",
};

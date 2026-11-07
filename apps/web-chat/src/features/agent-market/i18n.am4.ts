// Agent Market AM4 (make it simple) — the feature's new strings, merged into
// i18n.ts. Thai and English (AM11): the surface follows the app's language,
// and the two objects hold the same keys — the type makes a key that is in
// one of them only a compile error.

export const am4Th = {
  // the workspace header: one obvious action, everything else in one menu
  "ws.provenance": "ที่มาของตัวเลข",
  "ws.provenanceTitle": "ตัวเลขทุกตัวของรอบล่าสุดมาจากไฟล์ไหน แถวไหน และคิดอย่างไร",
  "ws.more": "ตัวเลือกเพิ่มเติม",
  "ws.menu.newChat": "แชทใหม่กับ agent นี้",
  "ws.menu.chats": "แชทกับ agent นี้",
  "ws.menu.pin": "ปักหมุดไว้บนสุดของแถบด้านซ้าย",
  "ws.menu.unpin": "เลิกปักหมุด",
  "ws.menu.overview": "ภาพรวมการทำงาน",
  "ws.menu.settings": "ตั้งค่า agent",
  "ws.menu.problem": "แจ้งปัญหา",
  "ws.chatRounds": "{n} รอบ",
  "ws.chatWaiting": "รอคำตอบ",
  "ws.chatCurrent": "แชทที่เปิดอยู่",
  "ws.newChatFailed": "เปิดแชทใหม่ไม่สำเร็จ",

  // an empty workspace: one sentence, the loudest thing on the page
  // ({label} is the agent's own name for its first required file)
  "empty.send": "ส่ง{label}มาได้เลย",
  "empty.pick": "เลือกไฟล์",
  "empty.drop": "หรือลากไฟล์มาวางตรงนี้",
  "empty.fallback": "ส่งไฟล์ที่ต้องการให้ agent ทำมาได้เลย",
  "empty.needs": "ที่ต้องใช้",

  // where people upload
  "upload.scanTip": "ถ้าเป็นเอกสารกระดาษ สแกนที่ 300 DPI อ่านได้แม่นกว่ารูปถ่ายจากมือถือเสมอ",
  "upload.notArchive": "ที่นี่ไม่ใช่ที่เก็บเอกสารของบริษัท — เก็บต้นฉบับไว้ตามเดิม ระบบเก็บสำเนาและหลักฐานว่าตัวเลขคำนวณจากไฟล์ใด",
  "upload.keepYears": "ไฟล์ของรอบที่สำเร็จเก็บ {years} ปีตามที่องค์กรกำหนด",
  "upload.infoLabel": "ไฟล์ที่ส่งเก็บไว้นานเท่าไร",

  // a finished round: a divider, its files, and the next round one click away
  "round.done": "รอบ {period} · เสร็จ",
  "round.doneNoPeriod": "รอบนี้เสร็จแล้ว",
  "round.failed": "รอบ {period} · ไม่สำเร็จ",
  "round.failedNoPeriod": "รอบนี้ไม่สำเร็จ",
  "round.cancelled": "รอบนี้ถูกยกเลิก",
  "round.start": "เริ่มรอบใหม่",
  "round.starting": "กำลังเริ่ม…",
  "round.carry": "รอบใหม่ใช้ค่าที่ตั้งไว้และคำตอบที่จำไว้ต่อ ไม่ใช้ไฟล์ ตัวเลข หรือคำตอบของรอบก่อน",
  "round.show": "ดูรอบนี้",
  "round.hide": "ย่อรอบนี้",
  "round.files": "ไฟล์ของรอบนี้",
  "round.selfTest": "ทดสอบกับข้อมูลตัวอย่าง · เสร็จ",
  "round.startFailed": "เริ่มรอบใหม่ไม่สำเร็จ",

  // the round-boundary chip: never guess what the person meant. Each sentence
  // has its own form for a round that names its period and for one that does
  // not (AM11: no fragment is glued in, so each language keeps its word order).
  "intent.title": "เริ่มรอบใหม่?",
  "intent.bodyFiles": "ได้รับไฟล์หลังจากรอบ {period} เสร็จแล้ว — จะเริ่มรอบใหม่ด้วยไฟล์นี้ หรือเป็นคำถามเกี่ยวกับรอบที่แล้ว",
  "intent.bodyFilesNoPeriod": "ได้รับไฟล์หลังจากรอบนี้เสร็จแล้ว — จะเริ่มรอบใหม่ด้วยไฟล์นี้ หรือเป็นคำถามเกี่ยวกับรอบที่แล้ว",
  "intent.bodyText": "รอบ {period} เสร็จไปแล้ว — ข้อความนี้เป็นการเริ่มรอบใหม่ หรือเป็นคำถามเกี่ยวกับรอบที่แล้ว",
  "intent.bodyTextNoPeriod": "รอบนี้เสร็จไปแล้ว — ข้อความนี้เป็นการเริ่มรอบใหม่ หรือเป็นคำถามเกี่ยวกับรอบที่แล้ว",
  "intent.new": "เริ่มรอบใหม่",
  "intent.about": "ถามเกี่ยวกับรอบที่แล้ว",
  "intent.fresh": "ไม่ต้องลบแชทเพื่อเริ่มใหม่ — รอบเก่ายังเก็บไว้ครบพร้อมที่มาของตัวเลข และรอบใหม่ไม่ใช้ไฟล์หรือตัวเลขของรอบเก่า",
  "intent.chosenNew": "เริ่มรอบใหม่แล้ว",
  "intent.chosenAbout": "ถือเป็นคำถามเกี่ยวกับรอบที่แล้ว",
  "intent.failed": "ส่งคำตอบไม่สำเร็จ",

  // the run panel: the one line that always shows, the rest folded for members
  "outputs.details": "รายละเอียดการตรวจ และโมเดล",
  "outputs.seenBy": "เอกสารที่เป็นสแกนหรือรูปถ่ายอ่านโดยผู้ช่วย AI ขององค์กร ({where})",
  "outputs.seenNone": "ไม่มีโมเดลใดเห็นเอกสาร — ทุกค่าอ่านจากไฟล์และคำนวณด้วยสคริปต์",
  "outputs.writtenBy": "ไม่มีโมเดลใดเห็นไฟล์ต้นฉบับ · คำบรรยายร่างโดยผู้ช่วย AI ขององค์กร ({where}) จากตารางที่คำนวณแล้ว",
  "outputs.writtenByRules": "ไม่มีโมเดลใดเห็นไฟล์ต้นฉบับ · คำบรรยายเขียนด้วยกฎของ agent (ไม่มีโมเดลที่องค์กรอนุญาตตอบได้)",
  "outputs.commentaryBy": "คำอธิบายรอบนี้ร่างโดยผู้ช่วย AI ขององค์กร ({where}) จากตารางที่คำนวณแล้ว ตัวเลขทุกตัวมาจากสคริปต์",
  "outputs.commentaryByRules": "คำอธิบายรอบนี้เรียบเรียงจากกฎของระบบ ไม่ได้ใช้คำตอบของโมเดล ไฟล์บอกไว้บนชีตคำอธิบาย",
  "outputs.notChosen": "รอบนี้ไม่ได้ใช้โมเดลที่คุณเลือกไว้ ผู้ช่วย AI ขององค์กรใช้โมเดลอื่นที่องค์กรอนุญาตแทน — {why}",
  "outputs.notChosenWhy": "โมเดลที่เลือกไม่ตอบ หรือองค์กรไม่อนุญาตให้ใช้ในตอนนี้",
  "outputs.noneInstead": "รอบนี้ไม่มีโมเดลที่องค์กรอนุญาตตอบได้แทนโมเดลที่คุณเลือกไว้ จึงอ่านด้วยสคริปต์บนเครื่องขององค์กรเท่านั้น",
  "where.inOrg": "ในองค์กร",
  "where.outside": "นอกองค์กร",
  // AM13b: masking follows where the data goes — which of the two this round used (what the gateway did)
  "outputs.masking.off": "สิ่งที่ส่งให้โมเดลในรอบนี้ไม่ถูกปิดบัง — ส่งให้โมเดลในองค์กรเท่านั้น",
  "outputs.masking.on": "ข้อมูลส่วนบุคคลในข้อความที่ส่งให้โมเดลในรอบนี้ถูกปิดบังก่อนส่ง",
  "outputs.masking.mixed": "รอบนี้บางขั้นตอนปิดบังข้อมูลส่วนบุคคลก่อนส่งให้โมเดล บางขั้นตอนไม่ปิดบัง",

  // what the agent's model is for (AM6: the menu itself is the chat's; its two lines are in i18n.am6.ts)
  "model.label": "โมเดลที่อ่านเอกสาร",
  "model.labelText": "โมเดลที่เขียนคำบรรยาย",

  // what is kept, for how long, and whose it is
  "storage.title": "ไฟล์ที่เก็บไว้",
  "storage.used": "ใช้พื้นที่ {size} (ไฟล์ที่ส่งเข้า {uploads} · ไฟล์ผลลัพธ์ {outputs})",
  "storage.warn": "ใช้พื้นที่ใกล้ {limit} ที่องค์กรตั้งไว้แล้ว — สแกนและรูปถ่ายใช้พื้นที่มากกว่าไฟล์รายงานหลายเท่า แจ้งผู้ดูแลถ้าต้องการเพิ่ม",
  "storage.deleteNote": "ถ้าลบบัญชี: แชทและการตั้งค่าของคุณถูกลบ ส่วนไฟล์ของรอบที่สำเร็จเป็นหลักฐานทางบัญชีขององค์กร เก็บต่อตามระยะที่องค์กรกำหนด",

  // the market grid
  "market.more": "ดูเพิ่ม",
  "market.less": "ย่อ",
  "market.soonNote": "ยังใช้ไม่ได้",

  // installing in one click
  "install.recommended": "ใช้ค่าแนะนำทั้งหมด",
  "install.recommendedBody": "ติดตั้งด้วยค่าที่ใช้กันทั่วไป แล้วทดสอบกับข้อมูลตัวอย่างทันที — ค่าทุกอย่างแก้ภายหลังได้ในหน้าตั้งค่าของ agent",
  "install.custom": "ตั้งค่าเองทีละขั้น",
  "install.recommending": "กำลังติดตั้ง…",
  "install.recommendedFailed": "ติดตั้งไม่สำเร็จ",
  "install.doneSelfTest": "ติดตั้งเสร็จแล้ว กำลังทดสอบกับข้อมูลตัวอย่าง — ผลจะขึ้นในห้องทำงานในไม่กี่วินาที",

  // the run page
  "run.provenanceTitle": "ที่มาของตัวเลข",
  "run.accessLog": "ผู้ดูแลที่เปิดดูรอบนี้",
  "run.accessLine": "{who} · {when} · เหตุผล: “{reason}”",
  "run.evidenceRemoved": "ไฟล์ของรอบนี้ถูกผู้ดูแลลบก่อนครบระยะเก็บ — บันทึกว่ารอบนี้เคยทำยังอยู่",
  "ds.keepYears": "{n} ปี (นับจากวันที่รอบเสร็จ)",
  "ds.keepOrgRecord": "แชทและการตั้งค่า: ลบ · หลักฐานของรอบที่สำเร็จ: เก็บต่อ",
  "file.status.active": "ใช้ในรอบนี้",
  "file.status.removed": "ลบแล้ว",
  "file.status.pending": "รอตัดสินใจ",
  "file.status.nextRound": "เก็บไว้รอบถัดไป",
  "file.status.staged": "ยังไม่ได้ใช้",

  "step.script.rules": "เขียนจากกฎ ไม่ใช้โมเดล",
} as const;

export type Am4Key = keyof typeof am4Th;

export const am4En = {
  // the workspace header: one obvious action, everything else in one menu
  "ws.provenance": "Source of figures",
  "ws.provenanceTitle": "Which file and row every figure of the latest run comes from, and how it was calculated",
  "ws.more": "More options",
  "ws.menu.newChat": "New chat with this agent",
  "ws.menu.chats": "Chats with this agent",
  "ws.menu.pin": "Pin to the top of the left sidebar",
  "ws.menu.unpin": "Unpin",
  "ws.menu.overview": "Activity overview",
  "ws.menu.settings": "Agent settings",
  "ws.menu.problem": "Report a problem",
  "ws.chatRounds": "Runs: {n}",
  "ws.chatWaiting": "Waiting for an answer",
  "ws.chatCurrent": "Current chat",
  "ws.newChatFailed": "Could not open a new chat",

  // an empty workspace: one sentence, the loudest thing on the page
  // ({label} is the agent's own name for its first required file)
  "empty.send": "To start, send: {label}",
  "empty.pick": "Choose files",
  "empty.drop": "or drag files here",
  "empty.fallback": "Send the files you want the agent to work on",
  "empty.needs": "Needed",

  // where people upload
  "upload.scanTip": "For paper documents, a 300 DPI scan always reads more accurately than a phone photo.",
  "upload.notArchive": "This is not your company's document archive — keep your originals where they are. The system keeps a copy, and the evidence of which file each figure was calculated from.",
  "upload.keepYears": "Files of successful runs are kept for {years} years, as the organisation requires",
  "upload.infoLabel": "How long the files you send are kept",

  // a finished run: a divider, its files, and the next run one click away
  "round.done": "{period} run · finished",
  "round.doneNoPeriod": "This run is finished",
  "round.failed": "{period} run · failed",
  "round.failedNoPeriod": "This run failed",
  "round.cancelled": "This run was cancelled",
  "round.start": "Start a new run",
  "round.starting": "Starting…",
  "round.carry": "A new run keeps your settings and remembered answers. It does not use the files, figures or answers of the previous run.",
  "round.show": "view this run",
  "round.hide": "collapse this run",
  "round.files": "This run's files",
  "round.selfTest": "Test with sample data · finished",
  "round.startFailed": "Could not start a new run",

  // the run-boundary chip: never guess what the person meant
  "intent.title": "Start a new run?",
  "intent.bodyFiles": "These files arrived after the {period} run finished — start a new run with them, or is this a question about the last run?",
  "intent.bodyFilesNoPeriod": "These files arrived after this run finished — start a new run with them, or is this a question about the last run?",
  "intent.bodyText": "The {period} run has already finished — does this message start a new run, or is it a question about the last run?",
  "intent.bodyTextNoPeriod": "This run has already finished — does this message start a new run, or is it a question about the last run?",
  "intent.new": "Start a new run",
  "intent.about": "Ask about the last run",
  "intent.fresh": "There is no need to delete the chat to start afresh — earlier runs are kept in full with their source of figures, and a new run does not use the files or figures of an earlier one.",
  "intent.chosenNew": "A new run was started",
  "intent.chosenAbout": "Treated as a question about the last run",
  "intent.failed": "Could not send your answer",

  // the run panel: the one line that always shows, the rest folded for members
  "outputs.details": "Details of checks and models",
  "outputs.seenBy": "Scanned or photographed documents were read by the organisation's AI assistant ({where})",
  "outputs.seenNone": "No model saw the documents — every value was read from the files and calculated by scripts",
  "outputs.writtenBy": "No model saw the original files · the commentary was drafted by the organisation's AI assistant ({where}) from the calculated tables",
  "outputs.writtenByRules": "No model saw the original files · the commentary was written by the agent's rules (no model the organisation allows could answer)",
  "outputs.commentaryBy": "This run's commentary was drafted by the organisation's AI assistant ({where}) from the calculated tables; every figure comes from the scripts",
  "outputs.commentaryByRules": "This run's commentary was composed from the system's rules — no model's answer was used; the file says so on its commentary sheet",
  "outputs.notChosen": "This run did not use the model you chose; the organisation's AI assistant used another model the organisation allows — {why}",
  "outputs.notChosenWhy": "the chosen model did not answer, or the organisation does not allow it at the moment",
  "outputs.noneInstead": "No model the organisation allows was available for this run in place of the one you chose, so the documents were read only by scripts on the organisation's own machines",
  "where.inOrg": "in the organisation",
  "where.outside": "outside the organisation",
  // AM13b: masking follows where the data goes — which of the two this round used (what the gateway did)
  "outputs.masking.off": "What was sent to a model in this run was not masked — it went only to a model in the organisation",
  "outputs.masking.on": "Personal data in the text sent to a model in this run was masked first",
  "outputs.masking.mixed": "In this run some steps masked personal data before sending it to a model; others did not",

  // what the agent's model is for (AM6: the menu itself is the chat's; its two lines are in i18n.am6.ts)
  "model.label": "Model that reads the documents",
  "model.labelText": "Model that writes the commentary",

  // what is kept, for how long, and whose it is
  "storage.title": "Stored files",
  "storage.used": "{size} used (files sent in {uploads} · output files {outputs})",
  "storage.warn": "Storage is close to the {limit} the organisation has set — scans and photos take many times more space than report files. Tell your admin if you need more.",
  "storage.deleteNote": "If you delete your account: your chats and settings are deleted. The files of successful runs are the organisation's accounting evidence and are kept for the period the organisation sets.",

  // the market grid
  "market.more": "Show more",
  "market.less": "Show less",
  "market.soonNote": "Not available yet",

  // installing in one click
  "install.recommended": "Use all recommended settings",
  "install.recommendedBody": "Installs with the commonly used settings, then tests with sample data straight away — every setting can be changed later in the agent's settings.",
  "install.custom": "Set it up step by step",
  "install.recommending": "Installing…",
  "install.recommendedFailed": "Could not install the agent",
  "install.doneSelfTest": "Installation complete. Testing with sample data — the result will appear in the workspace in a few seconds.",

  // the run page
  "run.provenanceTitle": "Source of figures",
  "run.accessLog": "Admins who opened this run",
  "run.accessLine": "{who} · {when} · reason: “{reason}”",
  "run.evidenceRemoved": "An admin removed this run's files before the end of the retention period — the record that this run took place is still kept.",
  "ds.keepYears": "{n} years (from the day the run finished)",
  "ds.keepOrgRecord": "Chats and settings: deleted · evidence of successful runs: kept",
  "file.status.active": "Used in this run",
  "file.status.removed": "Removed",
  "file.status.pending": "Awaiting decision",
  "file.status.nextRound": "Kept for the next run",
  "file.status.staged": "Not used yet",

  "step.script.rules": "Written from rules, no model used",
} as const satisfies Record<keyof typeof am4Th, string>;

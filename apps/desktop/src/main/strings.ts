// AM10 F — the words the shell draws itself, in the language the person chose.
//
// The window shows the web chat page, which has its own language (English /
// Thai, apps/web-chat/src/lib/i18n). Everything Electron draws on its own —
// the application menu, the right-click menu, the tray, dialogs, OS
// notifications, the shell's own pages (session.html, connector-remote.html,
// the OAuth loopback page) and the sentences the host writes for an error
// the page has no row for — used to be English whatever the page said.
//
// One table, two languages, the SAME keys: `en` is the source of the key
// set and `th` is typed against it, so a key missing from either side is a
// compile error, not a blank menu item. Values carry `{placeholders}` where
// a name, a path or a count goes in; a placeholder nobody filled stays
// visible rather than vanishing (the same rule as the web's useT).
//
// The current language is process state (one window set, one language),
// set by index.ts from shell-prefs.json at start and from the page's
// shell.prefs.set { locale } afterwards. Product names (SRAN Desktop, SRAN,
// MCP) are the same in both languages; the Thai wording follows the web
// app's dictionary for the terms both surfaces share (การตั้งค่า, ออกจากระบบ,
// แชทใหม่, คัดลอก, ยกเลิก, ถามด่วน).
//
// No 'electron' import: unit-tested in plain Node.

export type Locale = "en" | "th";

export const LOCALES: readonly Locale[] = Object.freeze(["en", "th"] as const);

/** Exactly "en" or "th" — not "TH", not "th-TH", nothing else. */
export function isLocale(v: unknown): v is Locale {
  return v === "en" || v === "th";
}

const en = {
  // ── windows ──────────────────────────────────────────────────────────────
  "window.quickTitle": "SRAN Desktop — Quick ask",
  "window.remoteForm": "Add remote connector",

  // ── application menu ─────────────────────────────────────────────────────
  "menu.file": "File",
  "menu.newChat": "New Chat",
  "menu.settings": "Settings",
  "menu.quit": "Quit",
  "menu.edit": "Edit",
  "menu.undo": "Undo",
  "menu.redo": "Redo",
  "menu.cut": "Cut",
  "menu.copy": "Copy",
  "menu.paste": "Paste",
  "menu.selectAll": "Select All",
  "menu.view": "View",
  "menu.reload": "Reload",
  "menu.resetZoom": "Actual Size",
  "menu.zoomIn": "Zoom In",
  "menu.zoomOut": "Zoom Out",
  "menu.alwaysOnTop": "Always on Top",
  "menu.fullscreen": "Toggle Full Screen",
  "menu.devTools": "Toggle Developer Tools",
  "menu.help": "Help",
  "menu.openLogs": "Open Log Folder",
  "menu.about": "About SRAN Desktop",

  // ── right-click menu (the edit set reuses menu.*) ─────────────────────────
  "context.openLink": "Open Link in Default Browser",
  "context.copyLink": "Copy Link Address",
  "context.copyImage": "Copy Image",
  "context.saveImage": "Save Image As…",

  // ── tray ─────────────────────────────────────────────────────────────────
  "tray.showHide": "Show / Hide",
  "tray.quickAsk": "Quick ask",
  "tray.alwaysOnTop": "Always on top",
  "tray.signIn": "Sign in",
  "tray.signOut": "Sign out",
  "tray.clearLocalData": "Clear local data…",
  "tray.quit": "Quit",

  // ── dialogs ──────────────────────────────────────────────────────────────
  "dialog.ok": "OK",
  "dialog.cancel": "Cancel",
  "dialog.about.title": "About SRAN Desktop",
  "dialog.about.message": "SRAN Desktop {version}",
  "dialog.about.detail": "Electron {electron} · config: {configPath}",
  "dialog.rendererGone.message": "The page process stopped unexpectedly.",
  "dialog.rendererGone.detail": "reason={reason} exitCode={exitCode}\nLog: {log}",
  "dialog.rendererGone.noLog": "(stdout)",
  "dialog.rendererGone.reload": "Reload",
  "dialog.rendererGone.quit": "Quit",
  "dialog.clearData.title": "Clear local data",
  "dialog.clearData.message": "Remove locally cached conversation transcripts, summaries, run records and logs from this computer?",
  "dialog.clearData.detail":
    "Your conversations stay on the server and re-sync on next use. This only clears this computer's local cache — the tool results kept with older conversations here are not re-synced.",
  "dialog.clearData.confirm": "Clear local data",
  "dialog.connector.localTitle": "Add MCP connector",
  "dialog.connector.remoteTitle": "Add remote MCP connector",
  "dialog.connector.add": "Add connector",
  "consent.spawn": "Add MCP connector \"{name}\"?\n\nIt will spawn this local process:\n{command}\n\nOnly do this for a program you trust.",
  "consent.remote": "Add remote MCP connector \"{host}\"?\n\nIt will connect to:\n{origin}\n\nKey: {key}\n\nOnly do this for a server you trust.",
  "consent.remote.keyYes": "yes — sent as {header}",
  "consent.remote.keyNone": "none",
  "dialog.attach.signInFirst": "Sign in first, then use “Ask SRAN about this file” again.",
  "dialog.attach.notOpened": "That file could not be opened.",
  "dialog.attach.tooLarge": "That file is larger than 200 MB.",
  "dialog.attach.title": "Ask SRAN about this file",
  "dialog.attach.message": "Attach this file to a new chat?\n{file}",
  "dialog.attach.detailInGrant": "The file is uploaded to the chat as an attachment. Nothing is sent to the model until you press Send.",
  "dialog.attach.detailNewFolder": "This folder is not one you have shared with SRAN yet:\n{folder}\n\nAttaching also lets SRAN read files in that folder when you ask it to.",
  "dialog.attach.attach": "Attach",
  "dialog.attach.allowAndAttach": "Allow folder and attach",
  "dialog.attach.folderRefused": "That folder cannot be shared with SRAN.",
  "dialog.attach.chatFailed": "Could not create a chat for the file.",
  "dialog.attach.failed": "The file could not be attached (unsupported type or upload failed).",

  // ── OS file / folder pickers (the buttons inside are the OS's own) ───────
  "picker.grantFolder": "Grant SRAN access to a folder",
  "picker.skillFile": "Choose a skill package",
  "picker.skillFilter": "Skill package",
  "picker.mcpProgram": "Choose the MCP server program (the user picks it — the page names no process)",

  // ── OS notifications ─────────────────────────────────────────────────────
  "notify.catchUp.title": "Scheduled task caught up",
  "notify.catchUp.body": "“{name}” was due while the app was closed — running it now (missed {when}{skipped}).",
  "notify.catchUp.skipped": "; {count} older run(s) skipped",
  "notify.dataCleared.body": "Local data cleared ({count} files).",
  "notify.hotkey.title": "SRAN Desktop — shortcut unavailable",
  "notify.hotkey.body": "{accelerator} for the {what} is held by another app. Change it in Settings.",
  "notify.hotkey.mainWindow": "main window",
  "notify.hotkey.quickWindow": "quick-ask window",

  // ── the sentences the host writes for an error (codes the web's table
  //    does not know — the page shows the host's sentence as it came) ──────
  "error.modelNotAccepted": "The selected model “{model}” is not available. Pick a model from the list and try again.",
  "error.hostException": "The turn ended unexpectedly. Nothing further was received.",
  "error.noResult": "The turn ended without producing a result.",
  "error.notPrepared": "The turn could not be prepared.",
  "error.editFailed": "The message could not be edited (chat-edge unreachable or refused).",
  "error.editStream": "The edited message could not be completed: {reason}",
  "error.interrupted": "This turn was interrupted when the app closed. What had been written is kept; send again to continue.",
  "error.noAgentKey": "The app could not obtain a gateway key for this account.",
  "error.unauthorized": "The gateway rejected the agent key (HTTP 401) — your session may have expired. Signing in again should fix it.",
  "error.forbidden": "The gateway refused this request (HTTP 403).",
  "error.forbiddenBecause": "The gateway refused this request (HTTP 403): {reason}",
  "error.httpStatus": "Gateway returned HTTP {status}.",
  "error.noStream": "Gateway returned no stream.",
  "error.outOfCredits":
    "Out of gateway credits{account} — the agent could not send this message. This is a credit balance of zero, not a rate limit. Ask your administrator to add credits to the account, then try again.",
  "error.rateLimited": "Rate limited — too many requests in a short time.{wait} This is a speed limit, not a credit problem, so no purchase is needed.",
  "error.rateLimited.retryIn": " Try again in about {seconds}s.",
  "error.rateLimited.wait": " Wait a few seconds and try again.",

  // ── the guardrail 403 explanation (prompt-guard.ts) ──────────────────────
  "guard.whyNotFound":
    "\n\nWhy: the gateway's content guardrail matched something in this request, but this app could not find the phrase (the gateway's rules may be newer than this app's). Check for jailbreak-like wording in your message, then in Memory (including notes saved on this computer), the project's instructions and Writing style — and remove it.",
  "guard.whyLikely": "\n\nWhy (likely): {hits}",
  "guard.hit": "{label} contains {span}, which matches guardrail rule {rule} — remove it {fixAt}.",
  "guard.part.userMessage.label": "Your message",
  "guard.part.userMessage.fixAt": "by rewording the message",
  "guard.part.earlierMessage.label": "An earlier message in this conversation",
  "guard.part.earlierMessage.fixAt": "by starting a new conversation or editing that message",
  "guard.part.projectInstructions.label": "The instructions of project “{name}”",
  "guard.part.projectInstructions.fixAt": "in Projects → that project's instructions",
  "guard.part.writingStyle.label": "Your writing-style profile",
  "guard.part.writingStyle.fixAt": "in Writing style",
  "guard.part.serverMemory.label": "A saved memory",
  "guard.part.serverMemory.fixAt": "in Memory (delete or edit that memory)",
  "guard.part.localGlobalNote.label": "A note saved on this computer (remember, global)",
  "guard.part.localGlobalNote.fixAt": "in Memory → Notes on this computer",
  "guard.part.localConversationNote.label": "A note saved for this conversation (remember)",
  "guard.part.localConversationNote.fixAt": "by starting a new conversation",
  "guard.part.skill.label": "The skill “{name}”",
  "guard.part.skill.fixAt": "in Skills (disable or delete that skill)",
  "guard.part.attachedFile.label": "The attached file {names}",
  "guard.part.attachedFile.fixAt": "by removing that attachment and sending the message again",

  // ── the OAuth loopback page shown in the system browser ──────────────────
  "oauth.connected.title": "Connected to SRAN Desktop",
  "oauth.connected.body": "You can close this tab and return to the app.",
  "oauth.notConnected.title": "Not connected",
  "oauth.notConnected.body": "You declined, or the provider refused. You can close this tab.",
  "oauth.incomplete.title": "Sign-in incomplete",
  "oauth.incomplete.body": "No authorization code came back. You can close this tab and try again.",
  "oauth.mismatch.title": "Sign-in did not match",
  "oauth.mismatch.body": "This sign-in does not belong to the current request. Go back to SRAN Desktop and click Connect again.",

  // ── assets/session.html (keys = data-t attributes there) ─────────────────
  "session.signedOut": "You are signed out.",
  "session.signIn": "Sign in",
  "session.signingIn": "Signing in…",
  "session.finishInBrowser": "Finish signing in with your browser. This window will update on its own.",
  "session.cancel": "Cancel",

  // ── why the signed-out view is shown (session-controller.ts → ?reason=) ──
  "signedOut.unreachable": "The gateway could not be reached. Check the connection and try again.",
  "signedOut.stillUnreachable": "Still cannot reach the gateway.",
  "signedOut.timedOut": "Sign-in timed out. Try again.",
  "signedOut.failed": "Sign-in could not be completed. Try again.",
  "signedOut.ended": "Your session ended. Sign in again.",
  "signedOut.reauthLoop": "The page kept asking to sign in although the session is valid. Your sign-in is kept — try again.",

  // ── assets/connector-remote.html ─────────────────────────────────────────
  "remote.title": "Add a remote MCP connector",
  "remote.intro":
    "The server must speak MCP over HTTPS. A key, if the server needs one, is stored encrypted on this computer and shown to nobody — not even this app's chat page.",
  "remote.urlLabel": "Server URL (https only)",
  "remote.hintHttpsOnly": "Only https:// addresses are accepted.",
  "remote.hintUnusable": "That address is not usable.",
  "remote.hintInvalid": "That is not a valid URL.",
  "remote.keyLabel": "Key (optional)",
  "remote.headerAria": "How the key is sent",
  "remote.headerXApiKey": "X-Api-Key header",
  "remote.headerOther": "Other header…",
  "remote.headerNamePlaceholder": "Header name",
  "remote.keyPlaceholder": "Paste the key",
  "remote.cancel": "Cancel",
  "remote.next": "Next",
  "remote.keyOnlyTitle": "Enter the key for {name}",
  "remote.keyOnlyIntro": "The key is stored encrypted on this computer and shown to nobody — not even this app's chat page.",
  "remote.thisConnector": "this connector",
} as const;

export type StringKey = keyof typeof en;

const th: Record<StringKey, string> = {
  "window.quickTitle": "SRAN Desktop — ถามด่วน",
  "window.remoteForm": "เพิ่มตัวเชื่อมต่อระยะไกล",

  "menu.file": "ไฟล์",
  "menu.newChat": "แชทใหม่",
  "menu.settings": "การตั้งค่า",
  "menu.quit": "ออกจากโปรแกรม",
  "menu.edit": "แก้ไข",
  "menu.undo": "เลิกทำ",
  "menu.redo": "ทำซ้ำ",
  "menu.cut": "ตัด",
  "menu.copy": "คัดลอก",
  "menu.paste": "วาง",
  "menu.selectAll": "เลือกทั้งหมด",
  "menu.view": "มุมมอง",
  "menu.reload": "โหลดใหม่",
  "menu.resetZoom": "ขนาดจริง",
  "menu.zoomIn": "ซูมเข้า",
  "menu.zoomOut": "ซูมออก",
  "menu.alwaysOnTop": "อยู่บนสุดเสมอ",
  "menu.fullscreen": "สลับเต็มหน้าจอ",
  "menu.devTools": "สลับเครื่องมือนักพัฒนา",
  "menu.help": "ช่วยเหลือ",
  "menu.openLogs": "เปิดโฟลเดอร์ไฟล์บันทึก",
  "menu.about": "เกี่ยวกับ SRAN Desktop",

  "context.openLink": "เปิดลิงก์ในเบราว์เซอร์",
  "context.copyLink": "คัดลอกที่อยู่ลิงก์",
  "context.copyImage": "คัดลอกรูปภาพ",
  "context.saveImage": "บันทึกรูปภาพเป็น…",

  "tray.showHide": "แสดง / ซ่อน",
  "tray.quickAsk": "ถามด่วน",
  "tray.alwaysOnTop": "อยู่บนสุดเสมอ",
  "tray.signIn": "เข้าสู่ระบบ",
  "tray.signOut": "ออกจากระบบ",
  "tray.clearLocalData": "ล้างข้อมูลในเครื่อง…",
  "tray.quit": "ออกจากโปรแกรม",

  "dialog.ok": "ตกลง",
  "dialog.cancel": "ยกเลิก",
  "dialog.about.title": "เกี่ยวกับ SRAN Desktop",
  "dialog.about.message": "SRAN Desktop เวอร์ชัน {version}",
  "dialog.about.detail": "Electron {electron} · ไฟล์ตั้งค่า: {configPath}",
  "dialog.rendererGone.message": "หน้าเว็บของแอปหยุดทำงานโดยไม่คาดคิด",
  "dialog.rendererGone.detail": "สาเหตุ: {reason} · รหัสออก: {exitCode}\nไฟล์บันทึก: {log}",
  "dialog.rendererGone.noLog": "(ไม่มีไฟล์ — stdout)",
  "dialog.rendererGone.reload": "โหลดใหม่",
  "dialog.rendererGone.quit": "ออกจากโปรแกรม",
  "dialog.clearData.title": "ล้างข้อมูลในเครื่อง",
  "dialog.clearData.message": "ลบสำเนาบทสนทนา บทสรุป บันทึกการทำงาน และไฟล์บันทึกที่เก็บไว้ในคอมพิวเตอร์เครื่องนี้หรือไม่?",
  "dialog.clearData.detail":
    "บทสนทนาของคุณยังอยู่บนเซิร์ฟเวอร์และจะซิงก์กลับมาเมื่อใช้งานครั้งต่อไป การล้างนี้ลบเฉพาะแคชในเครื่องนี้ — ผลลัพธ์ของเครื่องมือที่เก็บไว้กับบทสนทนาเก่าในเครื่องนี้จะไม่ถูกซิงก์กลับ",
  "dialog.clearData.confirm": "ล้างข้อมูลในเครื่อง",
  "dialog.connector.localTitle": "เพิ่มตัวเชื่อมต่อ MCP",
  "dialog.connector.remoteTitle": "เพิ่มตัวเชื่อมต่อ MCP ระยะไกล",
  "dialog.connector.add": "เพิ่มตัวเชื่อมต่อ",
  "consent.spawn": "เพิ่มตัวเชื่อมต่อ MCP \"{name}\" หรือไม่?\n\nจะเริ่มโปรเซสนี้ในเครื่องของคุณ:\n{command}\n\nทำเช่นนี้เฉพาะกับโปรแกรมที่คุณเชื่อถือเท่านั้น",
  "consent.remote": "เพิ่มตัวเชื่อมต่อ MCP ระยะไกล \"{host}\" หรือไม่?\n\nจะเชื่อมต่อไปที่:\n{origin}\n\nคีย์: {key}\n\nทำเช่นนี้เฉพาะกับเซิร์ฟเวอร์ที่คุณเชื่อถือเท่านั้น",
  "consent.remote.keyYes": "มี — ส่งเป็น {header}",
  "consent.remote.keyNone": "ไม่มี",
  "dialog.attach.signInFirst": "กรุณาเข้าสู่ระบบก่อน แล้วใช้ “ถาม SRAN เกี่ยวกับไฟล์นี้” อีกครั้ง",
  "dialog.attach.notOpened": "เปิดไฟล์นี้ไม่ได้",
  "dialog.attach.tooLarge": "ไฟล์นี้มีขนาดเกิน 200 MB",
  "dialog.attach.title": "ถาม SRAN เกี่ยวกับไฟล์นี้",
  "dialog.attach.message": "แนบไฟล์นี้ในแชทใหม่หรือไม่?\n{file}",
  "dialog.attach.detailInGrant": "ไฟล์จะถูกอัปโหลดเข้าแชทเป็นไฟล์แนบ จะยังไม่มีอะไรส่งถึงโมเดลจนกว่าคุณจะกดส่ง",
  "dialog.attach.detailNewFolder": "โฟลเดอร์นี้ยังไม่ได้แชร์ให้ SRAN:\n{folder}\n\nการแนบไฟล์จะอนุญาตให้ SRAN อ่านไฟล์ในโฟลเดอร์นั้นได้เมื่อคุณขอด้วย",
  "dialog.attach.attach": "แนบไฟล์",
  "dialog.attach.allowAndAttach": "อนุญาตโฟลเดอร์และแนบไฟล์",
  "dialog.attach.folderRefused": "แชร์โฟลเดอร์นี้ให้ SRAN ไม่ได้",
  "dialog.attach.chatFailed": "สร้างแชทสำหรับไฟล์นี้ไม่สำเร็จ",
  "dialog.attach.failed": "แนบไฟล์ไม่สำเร็จ (ชนิดไฟล์ไม่รองรับ หรืออัปโหลดไม่สำเร็จ)",

  "picker.grantFolder": "ให้ SRAN เข้าถึงโฟลเดอร์",
  "picker.skillFile": "เลือกแพ็กเกจสกิล",
  "picker.skillFilter": "แพ็กเกจสกิล",
  "picker.mcpProgram": "เลือกโปรแกรมเซิร์ฟเวอร์ MCP (คุณเป็นผู้เลือกเอง — หน้าเว็บไม่ได้ระบุโปรแกรมใด)",

  "notify.catchUp.title": "เรียกใช้งานตามกำหนดเวลาย้อนหลังแล้ว",
  "notify.catchUp.body": "“{name}” ถึงกำหนดขณะที่แอปปิดอยู่ — กำลังเรียกใช้ตอนนี้ (พลาดเมื่อ {when}{skipped})",
  "notify.catchUp.skipped": "; ข้ามรอบเก่ากว่านั้น {count} รอบ",
  "notify.dataCleared.body": "ล้างข้อมูลในเครื่องแล้ว ({count} ไฟล์)",
  "notify.hotkey.title": "SRAN Desktop — ใช้คีย์ลัดไม่ได้",
  "notify.hotkey.body": "คีย์ลัด {accelerator} สำหรับ{what}ถูกแอปอื่นใช้อยู่ เปลี่ยนได้ในการตั้งค่า",
  "notify.hotkey.mainWindow": "หน้าต่างหลัก",
  "notify.hotkey.quickWindow": "หน้าต่างถามด่วน",

  "error.modelNotAccepted": "โมเดล “{model}” ที่เลือกไว้ใช้ไม่ได้ กรุณาเลือกโมเดลจากรายการแล้วลองใหม่",
  "error.hostException": "รอบนี้จบลงโดยไม่คาดคิด ไม่ได้รับข้อมูลเพิ่มเติม",
  "error.noResult": "รอบนี้จบลงโดยไม่มีผลลัพธ์",
  "error.notPrepared": "เตรียมคำขอรอบนี้ไม่สำเร็จ",
  "error.editFailed": "แก้ไขข้อความไม่สำเร็จ (ติดต่อเซิร์ฟเวอร์แชทไม่ได้ หรือถูกปฏิเสธ)",
  "error.editStream": "รับคำตอบของข้อความที่แก้ไขไม่สำเร็จ: {reason}",
  "error.interrupted": "รอบนี้ถูกขัดจังหวะเมื่อแอปปิดลง สิ่งที่เขียนไว้แล้วยังอยู่ ส่งอีกครั้งเพื่อทำต่อ",
  "error.noAgentKey": "แอปขอคีย์เกตเวย์สำหรับบัญชีนี้ไม่สำเร็จ",
  "error.unauthorized": "เกตเวย์ปฏิเสธคีย์ของแอป (HTTP 401) — เซสชันของคุณอาจหมดอายุ ลองเข้าสู่ระบบอีกครั้ง",
  "error.forbidden": "เกตเวย์ปฏิเสธคำขอนี้ (HTTP 403)",
  "error.forbiddenBecause": "เกตเวย์ปฏิเสธคำขอนี้ (HTTP 403): {reason}",
  "error.httpStatus": "เกตเวย์ตอบกลับด้วย HTTP {status}",
  "error.noStream": "เกตเวย์ไม่ได้ส่งสตรีมกลับมา",
  "error.outOfCredits":
    "เครดิตเกตเวย์หมด{account} — ส่งข้อความนี้ไม่ได้ นี่คือยอดเครดิตเป็นศูนย์ ไม่ใช่การจำกัดความถี่ กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต แล้วลองใหม่",
  "error.rateLimited": "ถูกจำกัดความถี่ — มีคำขอมากเกินไปในเวลาสั้น ๆ{wait} นี่คือการจำกัดความเร็ว ไม่ใช่ปัญหาเครดิต จึงไม่ต้องซื้อเพิ่ม",
  "error.rateLimited.retryIn": " ลองใหม่ในอีกประมาณ {seconds} วินาที",
  "error.rateLimited.wait": " รอสักครู่แล้วลองใหม่",

  "guard.whyNotFound":
    "\n\nสาเหตุ: ระบบป้องกันเนื้อหาของเกตเวย์พบบางอย่างในคำขอนี้ แต่แอปหาข้อความนั้นไม่พบ (กฎของเกตเวย์อาจใหม่กว่าแอปนี้) ลองตรวจดูถ้อยคำที่คล้ายการหลบเลี่ยงข้อจำกัดในข้อความของคุณ จากนั้นในความจำ (รวมถึงโน้ตที่บันทึกไว้ในเครื่องนี้) คำสั่งของโปรเจกต์ และสไตล์การเขียน — แล้วลบออก",
  "guard.whyLikely": "\n\nสาเหตุ (น่าจะเป็น): {hits}",
  "guard.hit": "{label}มีข้อความ {span} ซึ่งตรงกับกฎป้องกันเนื้อหา {rule} — ลบออก{fixAt}",
  "guard.part.userMessage.label": "ข้อความของคุณ",
  "guard.part.userMessage.fixAt": "โดยเขียนข้อความใหม่",
  "guard.part.earlierMessage.label": "ข้อความก่อนหน้าในบทสนทนานี้",
  "guard.part.earlierMessage.fixAt": "โดยเริ่มบทสนทนาใหม่หรือแก้ไขข้อความนั้น",
  "guard.part.projectInstructions.label": "คำสั่งของโปรเจกต์ “{name}”",
  "guard.part.projectInstructions.fixAt": "ในหน้าโปรเจกต์ → คำสั่งของโปรเจกต์นั้น",
  "guard.part.writingStyle.label": "โปรไฟล์สไตล์การเขียนของคุณ",
  "guard.part.writingStyle.fixAt": "ในหน้าสไตล์การเขียน",
  "guard.part.serverMemory.label": "ความจำที่บันทึกไว้",
  "guard.part.serverMemory.fixAt": "ในหน้าความจำ (ลบหรือแก้ไขความจำนั้น)",
  "guard.part.localGlobalNote.label": "โน้ตที่บันทึกไว้ในเครื่องนี้ (remember แบบทั่วไป)",
  "guard.part.localGlobalNote.fixAt": "ในหน้าความจำ → โน้ตในเครื่องนี้",
  "guard.part.localConversationNote.label": "โน้ตที่บันทึกไว้สำหรับบทสนทนานี้ (remember)",
  "guard.part.localConversationNote.fixAt": "โดยเริ่มบทสนทนาใหม่",
  "guard.part.skill.label": "สกิล “{name}”",
  "guard.part.skill.fixAt": "ในหน้า Skills (ปิดหรือลบสกิลนั้น)",
  "guard.part.attachedFile.label": "ไฟล์แนบ {names}",
  "guard.part.attachedFile.fixAt": "โดยลบไฟล์แนบนั้นแล้วส่งข้อความอีกครั้ง",

  "oauth.connected.title": "เชื่อมต่อกับ SRAN Desktop แล้ว",
  "oauth.connected.body": "ปิดแท็บนี้แล้วกลับไปที่แอปได้",
  "oauth.notConnected.title": "ยังไม่ได้เชื่อมต่อ",
  "oauth.notConnected.body": "คุณปฏิเสธ หรือผู้ให้บริการไม่อนุญาต ปิดแท็บนี้ได้",
  "oauth.incomplete.title": "การเข้าสู่ระบบไม่สมบูรณ์",
  "oauth.incomplete.body": "ไม่ได้รับรหัสอนุญาตกลับมา ปิดแท็บนี้แล้วลองใหม่ได้",
  "oauth.mismatch.title": "การเข้าสู่ระบบไม่ตรงกับคำขอ",
  "oauth.mismatch.body": "การเข้าสู่ระบบนี้ไม่ใช่ของคำขอปัจจุบัน กลับไปที่ SRAN Desktop แล้วกดเชื่อมต่ออีกครั้ง",

  "session.signedOut": "คุณออกจากระบบแล้ว",
  "session.signIn": "เข้าสู่ระบบ",
  "session.signingIn": "กำลังเข้าสู่ระบบ…",
  "session.finishInBrowser": "ดำเนินการเข้าสู่ระบบต่อในเบราว์เซอร์ หน้าต่างนี้จะอัปเดตเอง",
  "session.cancel": "ยกเลิก",

  "signedOut.unreachable": "ติดต่อเกตเวย์ไม่ได้ กรุณาตรวจสอบการเชื่อมต่อแล้วลองใหม่",
  "signedOut.stillUnreachable": "ยังติดต่อเกตเวย์ไม่ได้",
  "signedOut.timedOut": "การเข้าสู่ระบบหมดเวลา กรุณาลองใหม่",
  "signedOut.failed": "เข้าสู่ระบบไม่สำเร็จ กรุณาลองใหม่",
  "signedOut.ended": "เซสชันของคุณสิ้นสุดแล้ว กรุณาเข้าสู่ระบบอีกครั้ง",
  "signedOut.reauthLoop": "หน้าเว็บขอให้เข้าสู่ระบบซ้ำแม้เซสชันยังใช้ได้ การเข้าสู่ระบบของคุณยังอยู่ — ลองใหม่อีกครั้ง",

  "remote.title": "เพิ่มตัวเชื่อมต่อ MCP ระยะไกล",
  "remote.intro":
    "เซิร์ฟเวอร์ต้องรองรับ MCP ผ่าน HTTPS หากเซิร์ฟเวอร์ต้องใช้คีย์ คีย์จะถูกเก็บแบบเข้ารหัสในคอมพิวเตอร์เครื่องนี้และไม่แสดงให้ใครเห็น — แม้แต่หน้าแชทของแอปนี้",
  "remote.urlLabel": "URL ของเซิร์ฟเวอร์ (https เท่านั้น)",
  "remote.hintHttpsOnly": "รับเฉพาะที่อยู่ที่เริ่มด้วย https:// เท่านั้น",
  "remote.hintUnusable": "ที่อยู่นี้ใช้ไม่ได้",
  "remote.hintInvalid": "ไม่ใช่ URL ที่ถูกต้อง",
  "remote.keyLabel": "คีย์ (ไม่บังคับ)",
  "remote.headerAria": "วิธีส่งคีย์",
  "remote.headerXApiKey": "ส่วนหัว X-Api-Key",
  "remote.headerOther": "ส่วนหัวอื่น…",
  "remote.headerNamePlaceholder": "ชื่อส่วนหัว",
  "remote.keyPlaceholder": "วางคีย์ที่นี่",
  "remote.cancel": "ยกเลิก",
  "remote.next": "ถัดไป",
  "remote.keyOnlyTitle": "ใส่คีย์สำหรับ {name}",
  "remote.keyOnlyIntro": "คีย์จะถูกเก็บแบบเข้ารหัสในคอมพิวเตอร์เครื่องนี้และไม่แสดงให้ใครเห็น — แม้แต่หน้าแชทของแอปนี้",
  "remote.thisConnector": "ตัวเชื่อมต่อนี้",
};

export const STRINGS: Readonly<Record<Locale, Readonly<Record<StringKey, string>>>> = Object.freeze({ en: Object.freeze(en), th: Object.freeze(th) });

let current: Locale = "en";

export function currentLocale(): Locale {
  return current;
}

/** Sets the language the shell draws in. Refuses anything that is not
 *  exactly "en" or "th" and leaves the current language as it was. */
export function setLocale(v: unknown): boolean {
  if (!isLocale(v)) return false;
  current = v;
  return true;
}

/** The language to start in: what the person chose (shell-prefs.json), else
 *  the operating system's — the page's own rule: a language tag starting
 *  with "th" is Thai, anything else (or nothing) is English. A stored value
 *  that is not a language is no choice. */
export function resolveLocale(stored: unknown, systemLocale: string | null | undefined): Locale {
  if (isLocale(stored)) return stored;
  return typeof systemLocale === "string" && systemLocale.toLowerCase().startsWith("th") ? "th" : "en";
}

/** One string in the current language (or the one named), with its
 *  {placeholders} filled from `vars`; a placeholder not in `vars` is left
 *  visible rather than dropped. */
export function t(key: StringKey, vars?: Record<string, string | number>, locale: Locale = current): string {
  const raw = STRINGS[locale][key];
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (whole, name: string) => (vars[name] !== undefined ? String(vars[name]) : whole));
}

export type ShellPage = "session" | "remote";

/** The strings one of the shell's own pages (assets/*.html) asks for, keyed
 *  as its data-t attributes name them — handed to the page as a query
 *  parameter before load, so it never shows one language then another. */
export function pageStrings(page: ShellPage, locale: Locale = current): Record<string, string> {
  const prefix = `${page}.`;
  const out: Record<string, string> = {};
  for (const key of Object.keys(en) as StringKey[]) {
    if (key.startsWith(prefix)) out[key] = STRINGS[locale][key];
  }
  return out;
}

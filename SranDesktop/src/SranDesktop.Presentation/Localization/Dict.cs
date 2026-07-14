namespace SranDesktop.Presentation.Localization;

/// <summary>
/// EN + TH translation dictionaries (desktop mirror of apps/web-chat's dict.ts).
/// Every key present in <see cref="En"/> MUST exist in <see cref="Th"/> and vice
/// versa — DictParityTests asserts key-set equality.
/// </summary>
public static class Dict
{
    public static readonly IReadOnlyDictionary<string, string> En = new Dictionary<string, string>
    {
        // Sidebar
        ["sidebar.newChat"] = "New chat",
        ["sidebar.newFolder"] = "New folder",
        ["sidebar.searchChats"] = "Search chats",
        ["sidebar.searchPlaceholder"] = "Search chats...",
        ["sidebar.empty"] = "No conversations yet",
        ["sidebar.group.pinned"] = "PINNED",
        ["sidebar.group.today"] = "TODAY",
        ["sidebar.group.yesterday"] = "YESTERDAY",
        ["sidebar.group.last7"] = "PREVIOUS 7 DAYS",
        ["sidebar.group.last30"] = "PREVIOUS 30 DAYS",
        ["sidebar.group.older"] = "OLDER",

        // Account / settings menu
        ["menu.section.appearance"] = "APPEARANCE",
        ["menu.section.data"] = "DATA",
        ["menu.section.other"] = "OTHER",
        ["menu.keyboardShortcuts"] = "Keyboard Shortcuts",
        ["menu.darkMode"] = "Dark mode",
        ["menu.language"] = "Language",
        ["menu.knowledgeBase"] = "Knowledge Base",
        ["menu.voiceSettings"] = "Voice Settings",
        ["menu.voiceComingSoon"] = "Coming soon",
        ["menu.memory"] = "Memory",
        ["menu.desktopSettings"] = "Desktop settings",
        ["menu.logout"] = "Log out",
        ["menu.account"] = "Account",

        // Empty state
        ["empty.greet.morning"] = "Good morning",
        ["empty.greet.afternoon"] = "Good afternoon",
        ["empty.greet.evening"] = "Good evening",
        ["empty.subtitle"] = "How can I help you today?",
        ["empty.chip.write"] = "Write",
        ["empty.chip.learn"] = "Learn",
        ["empty.chip.code"] = "Code",
        ["empty.chip.analyze"] = "Analyze",

        // Composer / input
        ["input.placeholder"] = "Message SRAN Chat...",
        ["input.attach"] = "Attach files",
        ["input.imageMode"] = "Generate image",
        ["input.videoMode"] = "Generate video",
        ["input.send"] = "Send",
        ["input.stop"] = "Stop",
        ["input.footer"] = "SRAN Chat can make mistakes. Check important info.",
        ["input.selectModel"] = "Select model",
        ["input.selectKnowledgeBase"] = "Select knowledge base",
        ["input.knowledgePlaceholder"] = "Knowledge",
        ["input.dropFiles"] = "Drop files to attach",

        // Conversation / folder context menus
        ["conv.rename"] = "Rename",
        ["conv.delete"] = "Delete",
        ["conv.pin"] = "Pin",
        ["conv.unpin"] = "Unpin",
        ["conv.moveToFolder"] = "Move to folder",
        ["conv.removeFromFolder"] = "Remove from folder",
        ["folder.new"] = "New folder",
        ["folder.rename"] = "Rename folder",
        ["folder.delete"] = "Delete folder",

        // Header actions
        ["header.memory"] = "Memory",
        ["header.memoryOn"] = "Memory is on for this chat",
        ["header.memoryOff"] = "Memory is off for this chat",
        ["header.memoryTurnOff"] = "Turn off for this chat",
        ["header.memoryTurnOn"] = "Turn on for this chat",
        ["header.openMemorySettings"] = "Open Memory Settings",
        ["header.exportMarkdown"] = "Export as Markdown",
        ["header.exportJson"] = "Export as JSON",
        ["header.copyMarkdown"] = "Copy as Markdown",
        ["header.moreOptions"] = "More options",
        ["header.toggleSidebar"] = "Toggle sidebar (Ctrl+Shift+S)",

        // Message actions
        ["msg.copy"] = "Copy",
        ["msg.editResend"] = "Edit & resend",
        ["msg.goodResponse"] = "Good response",
        ["msg.badResponse"] = "Bad response",
        ["msg.regenerate"] = "Regenerate",

        // Model selector
        ["model.auto.name"] = "Auto",
        ["model.auto.description"] = "Selects the best model automatically",
        ["model.searchPlaceholder"] = "Search models...",
        ["model.popular"] = "POPULAR",
        ["model.recent"] = "RECENTLY USED",
        ["model.none"] = "No models available",
        ["kbPopup.none"] = "No knowledge base",
        ["kbPopup.empty"] = "No knowledge bases yet",

        // Common
        ["common.save"] = "Save",
        ["common.cancel"] = "Cancel",
        ["common.delete"] = "Delete",
        ["common.confirm"] = "Confirm",
        ["common.reject"] = "Reject",
        ["common.edit"] = "Edit",
        ["common.close"] = "Close",
        ["common.create"] = "Create",
        ["common.download"] = "Download",
        ["common.retry"] = "Retry",
        ["common.loading"] = "Loading...",

        // Image / video generation
        ["gen.image.working"] = "Generating image...",
        ["gen.video.working"] = "Generating video...",
        ["gen.mayTakeAWhile"] = "This may take a moment",
        ["gen.saveImage"] = "Save image",
        ["gen.saveVideo"] = "Save video",

        // Plan cards
        ["plan.excel.title"] = "Excel Edit Plan",
        ["plan.doc.title"] = "Document Plan",
        ["plan.pptx.title"] = "Presentation Plan",
        ["plan.changes"] = "Changes ({0} actions):",
        ["plan.execute"] = "Execute Plan",
        ["plan.executing"] = "Executing...",
        ["plan.failed"] = "Plan execution failed",
        ["plan.result.changes"] = "Changes:",
        ["plan.result.download"] = "Download File",
        ["plan.result.sheets"] = "sheets",
        ["plan.result.charts"] = "charts",

        // Knowledge Base management
        ["kb.title"] = "Knowledge Base",
        ["kb.subtitle"] = "Manage knowledge bases for RAG",
        ["kb.create"] = "Create Knowledge Base",
        ["kb.card.manage"] = "Manage",
        ["kb.card.active"] = "Active",
        ["kb.card.inactive"] = "Inactive",
        ["kb.unit.documents"] = "documents",
        ["kb.unit.chunks"] = "chunks",
        ["kb.empty"] = "No knowledge bases yet. Create one to get started.",
        ["kb.name"] = "Name",
        ["kb.description"] = "Description (optional)",
        ["kb.uploadDocument"] = "Upload Document",
        ["kb.documents"] = "Documents",
        ["kb.noDocuments"] = "No documents yet",
        ["kb.reindex"] = "Reindex",
        ["kb.deleteConfirm"] = "Delete knowledge base \"{0}\"? Its documents will be removed.",
        ["kb.deleteDocConfirm"] = "Delete document \"{0}\"?",
        ["kb.status.indexed"] = "indexed",
        ["kb.status.processing"] = "processing",
        ["kb.status.failed"] = "failed",
        ["kb.uploading"] = "Uploading...",
        ["kb.loadFailed"] = "Could not load knowledge bases - server unreachable.",

        // Memory management
        ["memory.title"] = "Memory",
        ["memory.description"] = "Memories are automatically saved from your conversations",
        ["memory.addMemory"] = "Add Memory",
        ["memory.searchPlaceholder"] = "Search memories...",
        ["memory.suggested.title"] = "Suggested Preferences",
        ["memory.saved"] = "Saved Memories",
        ["memory.empty"] = "No memories yet",
        ["memory.pin"] = "Pin",
        ["memory.unpin"] = "Unpin",
        ["memory.usedTimes"] = "used {0} times",
        ["memory.type.fact"] = "Fact",
        ["memory.type.preference"] = "Preference",
        ["memory.type.interest"] = "Interest",
        ["memory.type.instruction"] = "Instruction",
        ["memory.form.titleAdd"] = "Add Memory",
        ["memory.form.titleEdit"] = "Edit Memory",
        ["memory.form.content"] = "Content",
        ["memory.form.type"] = "Type",
        ["memory.form.category"] = "Category (optional)",
        ["memory.dataManagement"] = "Data Management",
        ["memory.export.button"] = "Export Memories (JSON)",
        ["memory.danger.title"] = "Danger Zone",
        ["memory.danger.clearButton"] = "Clear All Memories",
        ["memory.danger.clearConfirm"] = "Clear all memories? They will no longer be used in conversations.",
        ["memory.danger.purgeButton"] = "Permanently Purge (GDPR)",
        ["memory.danger.purgeConfirm"] = "Permanently delete ALL memories? This cannot be undone. Type DELETE to confirm.",
        ["memory.danger.purgePhrase"] = "DELETE",
        ["memory.deleteConfirm"] = "Delete this memory?",
        ["memory.loadFailed"] = "Could not load memories - server unreachable.",

        // Errors / status
        ["error.serverUnreachable"] = "Can't reach the chat server. Check your connection and the server URL in Settings, then try again.",
        ["error.connectionLost"] = "Connection lost while streaming. Please try again.",
        ["status.stopped"] = "Stopped",
        ["agent.contextTrimmed"] = "Older messages were trimmed to fit the model's context window.",
        ["agent.contextTooLong"] = "This conversation is too long for the model. Start a new chat for best results.",

        // Phase 4 — streaming fence indicator chips
        ["stream.preparingDoc"] = "Preparing a document…",
        ["stream.preparingArtifact"] = "Building an artifact…",

        // Phase 5 — backend wiring
        ["chat.modelFallback"] = "Answered by {0} — {1} was unavailable",
        ["chat.gettingLong"] = "This chat is getting long — older messages are now summarized before each reply. A new chat will be faster and more precise.",
        ["search.inMessages"] = "In messages",
        ["search.untitled"] = "Untitled",

        // R9 — Artifacts
        ["artifact.copyCode"] = "Copy code",
        ["artifact.fullscreen"] = "Fullscreen",
        ["artifact.openExternal"] = "Open in browser",

        // R9 — Projects
        ["sidebar.projects"] = "PROJECTS",
        ["project.create"] = "New project",
        ["project.name"] = "Project name",
        ["project.instructions"] = "Custom instructions",
        ["project.instructionsHint"] = "Instructions the AI follows in every chat of this project",
        ["project.files"] = "Project files",
        ["project.addFile"] = "Add file",
        ["project.noFiles"] = "No files yet",
        ["project.conversations"] = "Conversations",
        ["project.noConversations"] = "No conversations yet",
        ["project.startChat"] = "Start chat",
        ["project.deleteConfirm"] = "Delete project \"{0}\"? Conversations stay but lose the project context.",
        ["project.delete"] = "Delete project",

        // R9 — Web search
        ["input.webSearch"] = "Web search",
        ["tool.searchingWeb"] = "Searching the web...",

        // R9 — Tool cards
        ["tool.input"] = "Input",
        ["tool.output"] = "Output",
        ["tool.retry"] = "Retry",

        // R9 — Scheduled tasks
        ["menu.scheduledTasks"] = "Scheduled tasks",
        ["task.title"] = "Scheduled Tasks",
        ["task.subtitle"] = "Prompts that run automatically on a schedule",
        ["task.create"] = "New task",
        ["task.name"] = "Name",
        ["task.prompt"] = "Prompt",
        ["task.schedule"] = "Schedule",
        ["task.runNow"] = "Run now",
        ["task.nextRun"] = "Next run",
        ["task.lastRun"] = "Last run",
        ["task.never"] = "never",
        ["task.empty"] = "No scheduled tasks yet",
        ["task.deleteConfirm"] = "Delete task \"{0}\"?",
        ["task.preset.hourly"] = "Every hour",
        ["task.preset.daily"] = "Daily at 06:00",
        ["task.preset.weekly"] = "Weekly (Mon 06:00)",
        ["task.preset.custom"] = "Custom cron",

        // R9 — Skills
        ["menu.skills"] = "Skills",
        ["skill.title"] = "Skills",
        ["skill.subtitle"] = "Capability modules that specialize the AI for a task",
        ["skill.install"] = "Install skill",
        ["skill.installed"] = "Installed",
        ["skill.builtin"] = "Built-in",
        ["skill.empty"] = "No skills installed",
        ["skill.deleteConfirm"] = "Remove skill \"{0}\"?",
        ["skill.triggers"] = "Triggers",

        // Phase 8 — Toasts
        ["toast.copied"] = "Copied to clipboard",
        ["toast.exportSaved"] = "Export saved",
        ["toast.connectorConnected"] = "Connector connected",
        ["toast.connectorFailed"] = "Connector connection failed",
        ["toast.syncFailed"] = "Some chats could not be backed up — will retry",
        ["toast.updateReady"] = "An update is available",
        ["toast.pullFailed"] = "Could not open that chat — server unreachable",

        // Phase 8 — Empty states
        ["skill.empty"] = "No skills installed yet — add one to teach the AI a new trick",
        ["connector.empty"] = "No connectors match your search",

        // Phase 8 — Accessibility names
        ["msg.branchPrev"] = "Previous version",
        ["msg.branchNext"] = "Next version",
        ["tool.toggleDetails"] = "Show tool details",
        ["media.playPause"] = "Play or pause",

        // R9 — Connectors
        ["menu.connectors"] = "Connectors",
        ["connector.title"] = "Connector Marketplace",
        ["connector.subtitle"] = "Connect external services the AI can use",
        ["connector.install"] = "Connect",
        ["connector.connected"] = "Connected",
        ["connector.connecting"] = "Connecting…",
        ["connector.connectFailed"] = "Connection failed — try again",
        ["connector.disconnect"] = "Disconnect",
        ["connector.searchPlaceholder"] = "Search connectors...",
        ["connector.comingSoon"] = "Remote execution requires a reachable MCP server",

        // R9 — Voice
        ["input.voice"] = "Voice input",
        ["voice.readAloud"] = "Read aloud",
        ["voice.stopReading"] = "Stop reading",
        ["voice.title"] = "Voice Settings",
        ["voice.voice"] = "Voice",
        ["voice.speed"] = "Speed",
        ["voice.autoRead"] = "Read responses aloud automatically",
        ["voice.listening"] = "Listening...",
        ["voice.backend"] = "Speech backend",
        ["voice.language"] = "Recognition language",
        ["voice.azureKey"] = "Azure Speech key",
        ["voice.azureRegion"] = "Region",
        ["voice.test"] = "Test",
        ["voice.testOk"] = "Key is valid ✓",
        ["voice.testFail"] = "Key or region is invalid",
        ["voice.azureHint"] = "Add an Azure AI Speech key to enable Thai speech recognition and neural Thai voices.",

        // R9 — Share
        ["header.share"] = "Share",
        ["share.title"] = "Share conversation",
        ["share.copyLink"] = "Copy link",
        ["share.copied"] = "Copied!",
        ["share.unavailable"] = "Sharing is not available on this server yet.",
        ["share.exportInstead"] = "Export the conversation as Markdown/JSON instead from the ... menu.",

        // R9 — Branching
        ["message.branch"] = "Branch",
        ["message.edit"] = "Edit",

        // R11 — Auto-update
        ["update.available"] = "A new version is available:",
        ["update.install"] = "Install now",
        ["update.later"] = "Later",
        ["update.downloading"] = "Downloading update...",
        ["update.mandatory"] = "This update is required",
        ["update.failed"] = "Update failed",

        // R11 — Setup / first run
        ["setup.serverTitle"] = "Server connection",
        ["setup.serverDesc"] = "Choose how to connect to SRAN AI",
        ["setup.cloud"] = "SRAN Cloud (managed)",
        ["setup.selfHosted"] = "Self-hosted server",
        ["setup.serverUrl"] = "Server URL",
        ["setup.serverUrlHint"] = "Enter the URL provided by your admin, e.g. https://sran.yourcompany.com",
        ["setup.testConnection"] = "Test connection",
        ["setup.testing"] = "Testing...",
        ["setup.connected"] = "Connected ✓",

        // R11 — Crash reporting
        ["crash.title"] = "Something went wrong",
        ["crash.description"] = "SRAN Desktop hit an unexpected error. You can keep working, restart the app, or send a report to help us fix it.",
        ["crash.details"] = "Error details",
        ["crash.sendReport"] = "Send crash report",
        ["crash.sent"] = "Report sent. Thank you!",
        ["crash.restart"] = "Restart",
        ["crash.optIn"] = "Send crash reports to help improve SRAN Desktop",

        // Phase 3 — agent-chat backup
        ["settings.syncAgentChats"] = "Back up agent chats to your account",
        ["settings.syncAgentChatsHint"] = "Conversation text only — tool activity and screenshots stay on this device.",

        // Phase 6 — folder access
        ["settings.folderAccess"] = "Folder access",
        ["settings.folderAccessHint"] = "The agent can read and write inside folders you grant here (in addition to your user profile and temp folder). It still asks before each write or command.",
        ["settings.addFolder"] = "Add folder…",
        ["settings.removeFolder"] = "Remove folder",
        ["settings.folderRejected"] = "That folder can't be granted. Pick a specific folder — not a whole drive, the Windows or Program Files folders, or one already added.",

        // R11 — Version
        ["settings.version"] = "Version",

        // R12 — Login screen (simplified R12-fix: centered logo + form only)
        ["login.email"] = "Email",
        ["login.emailPlaceholder"] = "you@company.com",
        ["login.password"] = "Password",
        ["login.signIn"] = "Sign in",
        ["login.signingIn"] = "Signing in...",
        ["login.forgotPassword"] = "Forgot password?",
        ["login.showPassword"] = "Show or hide password",
        ["login.toggleTheme"] = "Switch theme",
        ["login.errorInvalid"] = "Invalid email or password.",
        ["login.errorUnreachable"] = "Cannot reach the server. Check that the gateway is running and the server URL is correct.",
        ["login.errorTimeout"] = "The server took too long to respond. Please try again.",

        // R9 — Writing style
        ["style.learn"] = "Learn my style",
        ["style.profile"] = "Writing style",
        ["style.title"] = "Writing Style",
        ["style.subtitle"] = "Paste a few samples of your writing; the AI learns your tone and applies it",
        ["style.samplesHint"] = "Paste 3-5 samples of your writing here",
        ["style.analyze"] = "Analyze",
        ["style.analyzing"] = "Analyzing...",
        ["style.active"] = "Style profile active",
        ["style.clear"] = "Clear profile",
    };

    public static readonly IReadOnlyDictionary<string, string> Th = new Dictionary<string, string>
    {
        // Sidebar
        ["sidebar.newChat"] = "แชทใหม่",
        ["sidebar.newFolder"] = "โฟลเดอร์ใหม่",
        ["sidebar.searchChats"] = "ค้นหาแชท",
        ["sidebar.searchPlaceholder"] = "ค้นหาแชท...",
        ["sidebar.empty"] = "ยังไม่มีบทสนทนา",
        ["sidebar.group.pinned"] = "ปักหมุด",
        ["sidebar.group.today"] = "วันนี้",
        ["sidebar.group.yesterday"] = "เมื่อวาน",
        ["sidebar.group.last7"] = "7 วันที่แล้ว",
        ["sidebar.group.last30"] = "30 วันที่แล้ว",
        ["sidebar.group.older"] = "เก่ากว่า",

        // Account / settings menu
        ["menu.section.appearance"] = "การแสดงผล",
        ["menu.section.data"] = "ข้อมูล",
        ["menu.section.other"] = "อื่น ๆ",
        ["menu.keyboardShortcuts"] = "คีย์ลัด",
        ["menu.darkMode"] = "โหมดมืด",
        ["menu.language"] = "ภาษา",
        ["menu.knowledgeBase"] = "ฐานความรู้",
        ["menu.voiceSettings"] = "ตั้งค่าเสียง",
        ["menu.voiceComingSoon"] = "เร็ว ๆ นี้",
        ["menu.memory"] = "ความจำ",
        ["menu.desktopSettings"] = "ตั้งค่าเดสก์ท็อป",
        ["menu.logout"] = "ออกจากระบบ",
        ["menu.account"] = "บัญชี",

        // Empty state
        ["empty.greet.morning"] = "สวัสดีตอนเช้า",
        ["empty.greet.afternoon"] = "สวัสดีตอนบ่าย",
        ["empty.greet.evening"] = "สวัสดีตอนเย็น",
        ["empty.subtitle"] = "วันนี้ให้ช่วยอะไรดี?",
        ["empty.chip.write"] = "เขียน",
        ["empty.chip.learn"] = "เรียนรู้",
        ["empty.chip.code"] = "โค้ด",
        ["empty.chip.analyze"] = "วิเคราะห์",

        // Composer / input
        ["input.placeholder"] = "พิมพ์ข้อความ...",
        ["input.attach"] = "แนบไฟล์",
        ["input.imageMode"] = "สร้างรูปภาพ",
        ["input.videoMode"] = "สร้างวิดีโอ",
        ["input.send"] = "ส่ง",
        ["input.stop"] = "หยุด",
        ["input.footer"] = "SRAN Chat อาจผิดพลาดได้ กรุณาตรวจสอบข้อมูลสำคัญ",
        ["input.selectModel"] = "เลือกโมเดล",
        ["input.selectKnowledgeBase"] = "เลือกฐานความรู้",
        ["input.knowledgePlaceholder"] = "ฐานความรู้",
        ["input.dropFiles"] = "วางไฟล์เพื่อแนบ",

        // Conversation / folder context menus
        ["conv.rename"] = "เปลี่ยนชื่อ",
        ["conv.delete"] = "ลบ",
        ["conv.pin"] = "ปักหมุด",
        ["conv.unpin"] = "เลิกปักหมุด",
        ["conv.moveToFolder"] = "ย้ายไปโฟลเดอร์",
        ["conv.removeFromFolder"] = "นำออกจากโฟลเดอร์",
        ["folder.new"] = "โฟลเดอร์ใหม่",
        ["folder.rename"] = "เปลี่ยนชื่อโฟลเดอร์",
        ["folder.delete"] = "ลบโฟลเดอร์",

        // Header actions
        ["header.memory"] = "ความจำ",
        ["header.memoryOn"] = "เปิดความจำสำหรับแชทนี้",
        ["header.memoryOff"] = "ปิดความจำสำหรับแชทนี้",
        ["header.memoryTurnOff"] = "ปิดสำหรับแชทนี้",
        ["header.memoryTurnOn"] = "เปิดสำหรับแชทนี้",
        ["header.openMemorySettings"] = "เปิดการตั้งค่าความจำ",
        ["header.exportMarkdown"] = "ส่งออกเป็น Markdown",
        ["header.exportJson"] = "ส่งออกเป็น JSON",
        ["header.copyMarkdown"] = "คัดลอกเป็น Markdown",
        ["header.moreOptions"] = "ตัวเลือกเพิ่มเติม",
        ["header.toggleSidebar"] = "ซ่อน/แสดงแถบข้าง (Ctrl+Shift+S)",

        // Message actions
        ["msg.copy"] = "คัดลอก",
        ["msg.editResend"] = "แก้ไขและส่งใหม่",
        ["msg.goodResponse"] = "คำตอบดี",
        ["msg.badResponse"] = "คำตอบไม่ดี",
        ["msg.regenerate"] = "สร้างคำตอบใหม่",

        // Model selector
        ["model.auto.name"] = "อัตโนมัติ",
        ["model.auto.description"] = "เลือกโมเดลที่เหมาะสมอัตโนมัติ",
        ["model.searchPlaceholder"] = "ค้นหาโมเดล...",
        ["model.popular"] = "ยอดนิยม",
        ["model.recent"] = "ใช้ล่าสุด",
        ["model.none"] = "ไม่มีโมเดลที่ใช้ได้",
        ["kbPopup.none"] = "ไม่ใช้ฐานความรู้",
        ["kbPopup.empty"] = "ยังไม่มีฐานความรู้",

        // Common
        ["common.save"] = "บันทึก",
        ["common.cancel"] = "ยกเลิก",
        ["common.delete"] = "ลบ",
        ["common.confirm"] = "ยืนยัน",
        ["common.reject"] = "ปฏิเสธ",
        ["common.edit"] = "แก้ไข",
        ["common.close"] = "ปิด",
        ["common.create"] = "สร้าง",
        ["common.download"] = "ดาวน์โหลด",
        ["common.retry"] = "ลองใหม่",
        ["common.loading"] = "กำลังโหลด...",

        // Image / video generation
        ["gen.image.working"] = "กำลังสร้างรูปภาพ...",
        ["gen.video.working"] = "กำลังสร้างวิดีโอ...",
        ["gen.mayTakeAWhile"] = "อาจใช้เวลาสักครู่",
        ["gen.saveImage"] = "บันทึกรูปภาพ",
        ["gen.saveVideo"] = "บันทึกวิดีโอ",

        // Plan cards
        ["plan.excel.title"] = "แผนแก้ไข Excel",
        ["plan.doc.title"] = "แผนสร้างเอกสาร",
        ["plan.pptx.title"] = "แผนสร้างพรีเซนเทชัน",
        ["plan.changes"] = "การเปลี่ยนแปลง ({0} รายการ):",
        ["plan.execute"] = "ดำเนินการตามแผน",
        ["plan.executing"] = "กำลังดำเนินการ...",
        ["plan.failed"] = "ดำเนินการตามแผนไม่สำเร็จ",
        ["plan.result.changes"] = "การเปลี่ยนแปลง:",
        ["plan.result.download"] = "ดาวน์โหลดไฟล์",
        ["plan.result.sheets"] = "ชีต",
        ["plan.result.charts"] = "แผนภูมิ",

        // Knowledge Base management
        ["kb.title"] = "ฐานความรู้",
        ["kb.subtitle"] = "จัดการฐานความรู้สำหรับ RAG",
        ["kb.create"] = "สร้างฐานความรู้",
        ["kb.card.manage"] = "จัดการ",
        ["kb.card.active"] = "ใช้งาน",
        ["kb.card.inactive"] = "ปิดใช้งาน",
        ["kb.unit.documents"] = "เอกสาร",
        ["kb.unit.chunks"] = "ชิ้น",
        ["kb.empty"] = "ยังไม่มีฐานความรู้ สร้างใหม่เพื่อเริ่มต้น",
        ["kb.name"] = "ชื่อ",
        ["kb.description"] = "คำอธิบาย (ไม่บังคับ)",
        ["kb.uploadDocument"] = "อัปโหลดเอกสาร",
        ["kb.documents"] = "เอกสาร",
        ["kb.noDocuments"] = "ยังไม่มีเอกสาร",
        ["kb.reindex"] = "จัดทำดัชนีใหม่",
        ["kb.deleteConfirm"] = "ลบฐานความรู้ \"{0}\"? เอกสารทั้งหมดจะถูกลบด้วย",
        ["kb.deleteDocConfirm"] = "ลบเอกสาร \"{0}\"?",
        ["kb.status.indexed"] = "พร้อมใช้",
        ["kb.status.processing"] = "กำลังประมวลผล",
        ["kb.status.failed"] = "ล้มเหลว",
        ["kb.uploading"] = "กำลังอัปโหลด...",
        ["kb.loadFailed"] = "โหลดฐานความรู้ไม่ได้ - เชื่อมต่อเซิร์ฟเวอร์ไม่สำเร็จ",

        // Memory management
        ["memory.title"] = "ความจำ",
        ["memory.description"] = "ระบบจะจำข้อมูลสำคัญจากบทสนทนาโดยอัตโนมัติ",
        ["memory.addMemory"] = "เพิ่มความจำ",
        ["memory.searchPlaceholder"] = "ค้นหาความจำ...",
        ["memory.suggested.title"] = "การตั้งค่าที่แนะนำ",
        ["memory.saved"] = "ความจำที่บันทึกไว้",
        ["memory.empty"] = "ยังไม่มีความจำ",
        ["memory.pin"] = "ปักหมุด",
        ["memory.unpin"] = "เลิกปักหมุด",
        ["memory.usedTimes"] = "ใช้ไป {0} ครั้ง",
        ["memory.type.fact"] = "ข้อเท็จจริง",
        ["memory.type.preference"] = "ความชอบ",
        ["memory.type.interest"] = "ความสนใจ",
        ["memory.type.instruction"] = "คำสั่ง",
        ["memory.form.titleAdd"] = "เพิ่มความจำ",
        ["memory.form.titleEdit"] = "แก้ไขความจำ",
        ["memory.form.content"] = "เนื้อหา",
        ["memory.form.type"] = "ประเภท",
        ["memory.form.category"] = "หมวดหมู่ (ไม่บังคับ)",
        ["memory.dataManagement"] = "การจัดการข้อมูล",
        ["memory.export.button"] = "ส่งออกความจำ (JSON)",
        ["memory.danger.title"] = "โซนอันตราย",
        ["memory.danger.clearButton"] = "ล้างความจำทั้งหมด",
        ["memory.danger.clearConfirm"] = "ล้างความจำทั้งหมด? ระบบจะไม่ใช้ความจำเหล่านี้ในบทสนทนาอีก",
        ["memory.danger.purgeButton"] = "ลบถาวร (GDPR)",
        ["memory.danger.purgeConfirm"] = "ลบความจำทั้งหมดอย่างถาวร? ย้อนกลับไม่ได้ พิมพ์ DELETE เพื่อยืนยัน",
        ["memory.danger.purgePhrase"] = "DELETE",
        ["memory.deleteConfirm"] = "ลบความจำนี้?",
        ["memory.loadFailed"] = "โหลดความจำไม่ได้ - เชื่อมต่อเซิร์ฟเวอร์ไม่สำเร็จ",

        // Errors / status
        ["error.serverUnreachable"] = "เชื่อมต่อเซิร์ฟเวอร์แชทไม่ได้ กรุณาตรวจสอบการเชื่อมต่อและ URL เซิร์ฟเวอร์ในการตั้งค่า แล้วลองใหม่",
        ["error.connectionLost"] = "การเชื่อมต่อหลุดระหว่างสตรีม กรุณาลองใหม่",
        ["status.stopped"] = "หยุดแล้ว",
        ["agent.contextTrimmed"] = "ตัดข้อความเก่าบางส่วนออกเพื่อให้พอดีกับหน้าต่างบริบทของโมเดล",
        ["agent.contextTooLong"] = "บทสนทนานี้ยาวเกินขีดจำกัดของโมเดลแล้ว แนะนำให้เริ่มแชทใหม่",

        // Phase 4 — streaming fence indicator chips
        ["stream.preparingDoc"] = "กำลังเตรียมเอกสาร…",
        ["stream.preparingArtifact"] = "กำลังสร้างชิ้นงาน…",

        // Phase 5 — backend wiring
        ["chat.modelFallback"] = "ตอบโดย {0} — เนื่องจาก {1} ไม่พร้อมใช้งาน",
        ["chat.gettingLong"] = "แชทนี้เริ่มยาวแล้ว ระบบจะสรุปย่อข้อความเก่าก่อนตอบทุกครั้ง เปิดแชทใหม่จะเร็วและแม่นยำกว่า",
        ["search.inMessages"] = "ในข้อความ",
        ["search.untitled"] = "ไม่มีชื่อ",

        // R9 — Artifacts
        ["artifact.copyCode"] = "คัดลอกโค้ด",
        ["artifact.fullscreen"] = "เต็มจอ",
        ["artifact.openExternal"] = "เปิดในเบราว์เซอร์",

        // R9 — Projects
        ["sidebar.projects"] = "โปรเจกต์",
        ["project.create"] = "โปรเจกต์ใหม่",
        ["project.name"] = "ชื่อโปรเจกต์",
        ["project.instructions"] = "คำสั่งพิเศษ",
        ["project.instructionsHint"] = "คำสั่งที่ AI จะทำตามในทุกแชทของโปรเจกต์นี้",
        ["project.files"] = "ไฟล์โปรเจกต์",
        ["project.addFile"] = "เพิ่มไฟล์",
        ["project.noFiles"] = "ยังไม่มีไฟล์",
        ["project.conversations"] = "บทสนทนา",
        ["project.noConversations"] = "ยังไม่มีบทสนทนา",
        ["project.startChat"] = "เริ่มแชท",
        ["project.deleteConfirm"] = "ลบโปรเจกต์ \"{0}\"? บทสนทนาจะยังอยู่แต่ไม่มี context ของโปรเจกต์",
        ["project.delete"] = "ลบโปรเจกต์",

        // R9 — Web search
        ["input.webSearch"] = "ค้นหาเว็บ",
        ["tool.searchingWeb"] = "กำลังค้นหาเว็บ...",

        // R9 — Tool cards
        ["tool.input"] = "อินพุต",
        ["tool.output"] = "ผลลัพธ์",
        ["tool.retry"] = "ลองใหม่",

        // R9 — Scheduled tasks
        ["menu.scheduledTasks"] = "งานที่ตั้งเวลา",
        ["task.title"] = "งานที่ตั้งเวลา",
        ["task.subtitle"] = "ข้อความที่รันอัตโนมัติตามตารางเวลา",
        ["task.create"] = "สร้างงานใหม่",
        ["task.name"] = "ชื่อ",
        ["task.prompt"] = "ข้อความ",
        ["task.schedule"] = "ตารางเวลา",
        ["task.runNow"] = "รันเดี๋ยวนี้",
        ["task.nextRun"] = "รันครั้งถัดไป",
        ["task.lastRun"] = "รันล่าสุด",
        ["task.never"] = "ยังไม่เคย",
        ["task.empty"] = "ยังไม่มีงานที่ตั้งเวลา",
        ["task.deleteConfirm"] = "ลบงาน \"{0}\"?",
        ["task.preset.hourly"] = "ทุกชั่วโมง",
        ["task.preset.daily"] = "ทุกวัน 06:00",
        ["task.preset.weekly"] = "ทุกสัปดาห์ (จันทร์ 06:00)",
        ["task.preset.custom"] = "cron กำหนดเอง",

        // R9 — Skills
        ["menu.skills"] = "ทักษะ",
        ["skill.title"] = "ทักษะ",
        ["skill.subtitle"] = "โมดูลความสามารถเฉพาะทางของ AI",
        ["skill.install"] = "ติดตั้งทักษะ",
        ["skill.installed"] = "ติดตั้งแล้ว",
        ["skill.builtin"] = "มากับแอป",
        ["skill.empty"] = "ยังไม่มีทักษะติดตั้ง",
        ["skill.deleteConfirm"] = "ลบทักษะ \"{0}\"?",
        ["skill.triggers"] = "คำที่กระตุ้น",

        // Phase 8 — Toasts
        ["toast.copied"] = "คัดลอกไปยังคลิปบอร์ดแล้ว",
        ["toast.exportSaved"] = "บันทึกไฟล์ส่งออกแล้ว",
        ["toast.connectorConnected"] = "เชื่อมต่อส่วนเสริมสำเร็จ",
        ["toast.connectorFailed"] = "เชื่อมต่อส่วนเสริมไม่สำเร็จ",
        ["toast.syncFailed"] = "สำรองบางแชทไม่สำเร็จ — จะลองใหม่อีกครั้ง",
        ["toast.updateReady"] = "มีอัปเดตใหม่พร้อมติดตั้ง",
        ["toast.pullFailed"] = "เปิดแชทไม่ได้ — เชื่อมต่อเซิร์ฟเวอร์ไม่สำเร็จ",

        // Phase 8 — Empty states
        ["skill.empty"] = "ยังไม่มีสกิลติดตั้ง — เพิ่มสกิลเพื่อสอนความสามารถใหม่ให้ AI",
        ["connector.empty"] = "ไม่พบส่วนเสริมที่ตรงกับคำค้นหา",

        // Phase 8 — Accessibility names
        ["msg.branchPrev"] = "เวอร์ชันก่อนหน้า",
        ["msg.branchNext"] = "เวอร์ชันถัดไป",
        ["tool.toggleDetails"] = "แสดงรายละเอียดเครื่องมือ",
        ["media.playPause"] = "เล่นหรือหยุดชั่วคราว",

        // R9 — Connectors
        ["menu.connectors"] = "เชื่อมต่อ",
        ["connector.title"] = "ตลาดส่วนเสริม",
        ["connector.subtitle"] = "เชื่อมต่อบริการภายนอกให้ AI ใช้งาน",
        ["connector.install"] = "เชื่อมต่อ",
        ["connector.connected"] = "เชื่อมต่อแล้ว",
        ["connector.connecting"] = "กำลังเชื่อมต่อ…",
        ["connector.connectFailed"] = "เชื่อมต่อไม่สำเร็จ — ลองอีกครั้ง",
        ["connector.disconnect"] = "ยกเลิกการเชื่อมต่อ",
        ["connector.searchPlaceholder"] = "ค้นหาส่วนเสริม...",
        ["connector.comingSoon"] = "การรันระยะไกลต้องมี MCP server ที่เข้าถึงได้",

        // R9 — Voice
        ["input.voice"] = "พูดข้อความ",
        ["voice.readAloud"] = "อ่านออกเสียง",
        ["voice.stopReading"] = "หยุดอ่าน",
        ["voice.title"] = "ตั้งค่าเสียง",
        ["voice.voice"] = "เสียง",
        ["voice.speed"] = "ความเร็ว",
        ["voice.autoRead"] = "อ่านคำตอบออกเสียงอัตโนมัติ",
        ["voice.listening"] = "กำลังฟัง...",
        ["voice.backend"] = "ระบบเสียง",
        ["voice.language"] = "ภาษาที่ใช้พูด",
        ["voice.azureKey"] = "Azure Speech key",
        ["voice.azureRegion"] = "Region",
        ["voice.test"] = "ทดสอบ",
        ["voice.testOk"] = "คีย์ใช้งานได้ ✓",
        ["voice.testFail"] = "คีย์หรือ region ไม่ถูกต้อง",
        ["voice.azureHint"] = "ใส่ Azure AI Speech key เพื่อเปิดการพูดภาษาไทยและเสียงอ่านภาษาไทยแบบ neural",

        // R9 — Share
        ["header.share"] = "แชร์",
        ["share.title"] = "แชร์บทสนทนา",
        ["share.copyLink"] = "คัดลอกลิงก์",
        ["share.copied"] = "คัดลอกแล้ว!",
        ["share.unavailable"] = "เซิร์ฟเวอร์นี้ยังไม่รองรับการแชร์",
        ["share.exportInstead"] = "ใช้ส่งออกเป็น Markdown/JSON จากเมนู ... แทนได้",

        // R9 — Branching
        ["message.branch"] = "สาขา",
        ["message.edit"] = "แก้ไข",

        // R11 — Auto-update
        ["update.available"] = "มีเวอร์ชันใหม่:",
        ["update.install"] = "ติดตั้งเลย",
        ["update.later"] = "ภายหลัง",
        ["update.downloading"] = "กำลังดาวน์โหลดอัปเดต...",
        ["update.mandatory"] = "อัปเดตนี้จำเป็นต้องติดตั้ง",
        ["update.failed"] = "อัปเดตไม่สำเร็จ",

        // R11 — Setup / first run
        ["setup.serverTitle"] = "เชื่อมต่อเซิร์ฟเวอร์",
        ["setup.serverDesc"] = "เลือกวิธีเชื่อมต่อกับ SRAN AI",
        ["setup.cloud"] = "SRAN Cloud (บริการสำเร็จรูป)",
        ["setup.selfHosted"] = "เซิร์ฟเวอร์ขององค์กร",
        ["setup.serverUrl"] = "URL เซิร์ฟเวอร์",
        ["setup.serverUrlHint"] = "ใส่ URL ที่ผู้ดูแลระบบให้มา เช่น https://sran.yourcompany.com",
        ["setup.testConnection"] = "ทดสอบการเชื่อมต่อ",
        ["setup.testing"] = "กำลังทดสอบ...",
        ["setup.connected"] = "เชื่อมต่อสำเร็จ ✓",

        // R11 — Crash reporting
        ["crash.title"] = "เกิดข้อผิดพลาด",
        ["crash.description"] = "SRAN Desktop พบข้อผิดพลาดที่ไม่คาดคิด คุณสามารถใช้งานต่อ รีสตาร์ทแอป หรือส่งรายงานเพื่อช่วยเราแก้ไข",
        ["crash.details"] = "รายละเอียดข้อผิดพลาด",
        ["crash.sendReport"] = "ส่งรายงานข้อผิดพลาด",
        ["crash.sent"] = "ส่งรายงานแล้ว ขอบคุณครับ!",
        ["crash.restart"] = "รีสตาร์ท",
        ["crash.optIn"] = "ส่งรายงานข้อผิดพลาดเพื่อช่วยพัฒนา SRAN Desktop",

        // Phase 3 — agent-chat backup
        ["settings.syncAgentChats"] = "สำรองแชทโหมดเอเจนต์ไปยังบัญชีของคุณ",
        ["settings.syncAgentChatsHint"] = "สำรองเฉพาะข้อความสนทนาเท่านั้น — ประวัติการใช้เครื่องมือและภาพหน้าจอยังอยู่ในเครื่องนี้",

        // Phase 6 — folder access
        ["settings.folderAccess"] = "การเข้าถึงโฟลเดอร์",
        ["settings.folderAccessHint"] = "เอเจนต์สามารถอ่านและเขียนไฟล์ในโฟลเดอร์ที่คุณอนุญาตที่นี่ได้ (เพิ่มเติมจากโฟลเดอร์โปรไฟล์ผู้ใช้และโฟลเดอร์ชั่วคราว) โดยยังคงขออนุญาตก่อนเขียนหรือรันคำสั่งทุกครั้ง",
        ["settings.addFolder"] = "เพิ่มโฟลเดอร์…",
        ["settings.removeFolder"] = "ลบโฟลเดอร์",
        ["settings.folderRejected"] = "ไม่สามารถอนุญาตโฟลเดอร์นี้ได้ กรุณาเลือกโฟลเดอร์ที่เจาะจง — ไม่ใช่ทั้งไดรฟ์ ไม่ใช่โฟลเดอร์ Windows หรือ Program Files และต้องไม่ใช่โฟลเดอร์ที่เพิ่มไว้แล้ว",

        // R11 — Version
        ["settings.version"] = "เวอร์ชัน",

        // R12 — Login screen (simplified R12-fix: centered logo + form only)
        ["login.email"] = "อีเมล",
        ["login.emailPlaceholder"] = "you@company.com",
        ["login.password"] = "รหัสผ่าน",
        ["login.signIn"] = "เข้าสู่ระบบ",
        ["login.signingIn"] = "กำลังเข้าสู่ระบบ...",
        ["login.forgotPassword"] = "ลืมรหัสผ่าน?",
        ["login.showPassword"] = "แสดง/ซ่อนรหัสผ่าน",
        ["login.toggleTheme"] = "สลับธีม",
        ["login.errorInvalid"] = "อีเมลหรือรหัสผ่านไม่ถูกต้อง",
        ["login.errorUnreachable"] = "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ตรวจสอบว่า gateway ทำงานอยู่และ URL เซิร์ฟเวอร์ถูกต้อง",
        ["login.errorTimeout"] = "เซิร์ฟเวอร์ตอบสนองช้าเกินไป กรุณาลองใหม่อีกครั้ง",

        // R9 — Writing style
        ["style.learn"] = "เรียนรู้สไตล์เขียน",
        ["style.profile"] = "สไตล์การเขียน",
        ["style.title"] = "สไตล์การเขียน",
        ["style.subtitle"] = "วางตัวอย่างงานเขียนของคุณ AI จะเรียนรู้โทนและนำไปใช้",
        ["style.samplesHint"] = "วางตัวอย่างงานเขียน 3-5 ชิ้นที่นี่",
        ["style.analyze"] = "วิเคราะห์",
        ["style.analyzing"] = "กำลังวิเคราะห์...",
        ["style.active"] = "โปรไฟล์สไตล์ทำงานอยู่",
        ["style.clear"] = "ล้างโปรไฟล์",
    };
}

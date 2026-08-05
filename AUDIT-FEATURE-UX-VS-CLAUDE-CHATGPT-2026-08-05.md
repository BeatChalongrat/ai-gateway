# AUDIT — webchat + desktop เทียบ Claude / ChatGPT (feature + UX)

- **State:** commit `7b73d4d` · audit-only (อ่าน source อย่างเดียว, ไม่รัน/ไม่แก้)
- **วันที่:** 2026-08-05
- **แกนคะแนน:** A = Office main-use usability /100 · B = Closeness to Claude/ChatGPT /100
- **วิธีทำ:** Explore agents 8 ตัวอ่านโค้ดจริงทุก dimension พร้อมอ้างไฟล์:บรรทัด; benchmark ฝั่งเจ้าใหญ่มาจากความรู้ ณ ม.ค. 2026 (จุดที่ไม่ชัวร์ระบุใน Honest note)

---

## 1. Executive summary

| Component | A: Office usability | B: Closeness to big players | Verdict |
|---|---|---|---|
| **Webchat** | **81/100** | **77/100** | ใช้งานหลักในออฟฟิศได้จริงวันนี้ — chat/ไฟล์/OCR ไทย/ค้นเว็บ/สร้างเอกสารครบ; สะดุดเรื่อง i18n ปนภาษา, search แชทอ่อน, ไม่มี custom-instructions UI |
| **Desktop** | **75/100** | **78/100** | ความสามารถ agentic **กว้างกว่า** Claude Desktop / ChatGPT desktop ในหลายมิติ (39 tools, computer use, browser bridge) แต่ **onboarding/ops ยังไม่พร้อมสำหรับ non-IT** (unsigned installer, offline lockout, first-run ครึ่งอังกฤษ) |

ความใกล้เจ้าใหญ่โดยรวม: **~75-80%** — feature หลักครบเกือบหมด และมีบางอย่างที่เจ้าใหญ่ไม่มี (OCR ไทย, Excel/Doc/Pptx versioned workspace, video gen ในแชท, folder-scoped consent) แต่แพ้ที่ "ความเนียนรอบตัว": ภาษา UI สม่ำเสมอ, artifacts/canvas แบบแก้ไขได้, การเก็บรายละเอียดเล็ก (timestamps, scroll memory, retry affordance, notifications)

---

## 2. Scorecard — WEBCHAT (เทียบ Claude.ai / ChatGPT web)

### 2.1 Chat core & streaming — A 85 / B 82 (สถานะ: ใกล้เคียง)
มีจริง (หลักฐาน):
- Streaming SSE + rAF batching เนียน: `apps/web-chat/src/lib/stream.ts:32-78`, `stores/chatStore.ts:1286-1344`
- Stop ครบสองฝั่ง (client abort + server cancel): `ChatInput.tsx:812-822`, `chatStore.ts:1078-1086`, `services/chat-edge-api/internal/handler/message.go:692-715`
- Regenerate: `MessageBubble.tsx:384-388`, backend `message.go:728-793`
- **Edit & resend พร้อม branching จริง + branch navigator** (`parent_id`/`branch_index`, is_active): `chatStore.ts:1151-1229`, `message_service.go:1322-1368`, `BranchNavigator.tsx` — เทียบเท่า ChatGPT
- Typing dots / thinking state / skeleton: `MessageBubble.tsx:651-657`, `[conversationId]/page.tsx:105-113`
- คำตอบค้างครึ่งรอดฝั่ง server (เขียนต่อหลัง disconnect): `message_service.go:986-1019`
- Error mid-stream มี card แดง + log ความพยายามต่อ model (ภาษาไทย): `MessageBubble.tsx:435-471`

ความต่างจากเจ้าใหญ่:
- ✖ **ไม่มี stream-resume หลัง refresh** (ChatGPT resume ได้; ของเราเห็นเป็นข้อความจบเฉย ๆ ไม่บอกว่าถูกตัด) — client half ABSENT
- ✖ **ไม่มี IME composition guard** (`ChatInput.tsx:372-377` ไม่มี isComposing) — Enter ระหว่าง compose ส่งข้อความครึ่ง ๆ; ไทย Kedmanee ไม่โดน แต่ CJK/predictive IME โดน
- ⚠️ ไม่มี auto-retry / ปุ่ม "Try again" เฉพาะ — ใช้ Regenerate แทน (`ChatThread.tsx:337-342` ยอมให้กดบน error ได้)

### 2.2 Markdown/output — A 85 / B 75 (สถานะ: ด้อยกว่าเรื่อง canvas, ดีกว่าเรื่อง versioned files)
มีจริง:
- ReactMarkdown + gfm + rehype-highlight + rehype-katex, no rehype-raw (ปลอดภัย): `Markdown.tsx:33-85`
- Copy-code ต่อบล็อก + language label + filename syntax + collapse >30 บรรทัด: `CodeBlock.tsx:53-149`
- Copy ทั้งคำตอบ: `MessageBubble.tsx:134-153,370-376` · LaTeX: `Markdown.tsx:75-85` (trust:false)
- ตาราง scroll container + zebra: `Markdown.tsx:112-121`, `globals.css:360-441`
- รูป generated ผ่าน authed blob + lightbox: `MessageAttachments.tsx:65-88`, `AuthedImage.tsx`
- **Plan cards Excel/Doc/Pptx + Workspace drawer (version timeline / compare / rollback)**: `MessageBubble.tsx:714-743`, `ExcelPlanCard.tsx`, `WorkspacePanel.tsx`, `VersionTimeline.tsx` — เจ้าใหญ่ไม่มี version lineage ของไฟล์ที่ AI สร้าง
- Streaming markdown ไม่ flicker (memo + comparator 20 field): `MessageBubble.tsx:610-635,753-804`

ความต่างจากเจ้าใหญ่:
- ✖ **ไม่มี artifacts/canvas แบบ live-preview/แก้ไขได้** (Claude artifacts render HTML/React สด, ChatGPT canvas แก้ text/code ในตัว) — Workspace ของเราเป็น file-lineage ไม่ใช่ editor
- ⚠️ Compare เป็น summarized change list ไม่ใช่ diff จริง (`ComparisonCard.tsx`) · ไม่มี wrap toggle โค้ด · rollback ใช้ `window.confirm()` (`VersionTimeline.tsx:69`)

### 2.3 File handling — A 82 / B 78 (สถานะ: ใกล้เคียง; OCR ไทยดีกว่า, limit/preview ด้อยกว่า)
มีจริง:
- Drag-drop เขต chat + landing (stage เข้า draft): `AttachmentDropZone.tsx:17-107`, `chat/page.tsx:118-141` · paste รูปจาก clipboard: `ChatInput.tsx:548-569`
- 5 ไฟล์/ข้อความ, FE 25MiB: `lib/fileUtil.ts:36-37` · ชนิด: pdf/docx/xlsx/pptx/img/text-code ~30 ext (`file_service.go:176-215`)
- **OCR สแกน PDF tha+eng** (pypdfium2+pytesseract, 20 หน้า, confidence meta): `scripts/extract_text.py:52-158` — จุดแข็งเฉพาะเรา
- Vision blocks บน last user message + generated-image replay แบบ detail:low: `message_service.go:2668-2792`
- Extraction non-blocking + chip "Reading…" + retry upload: `fileExtraction.ts:74-151`, `FilePreviewCard.tsx:141-155`

ความต่างจากเจ้าใหญ่:
- ✖ ไม่มี in-app PDF/doc preview (เจ้าใหญ่คลิกดูได้) — มีแค่ download card
- ⚠️ Progress วัดจริงแต่ **ไม่เคยแสดง** (spinner อย่างเดียว): `api.ts:899-903` vs `FilePreviewCard.tsx:110-117`
- ⚠️ FE 25MiB / BE 200MiB ไม่ตรงกัน + ข้อความ 413 ยังพูด "25 MB": `config.go:126`, `handler/file.go:184` · ChatGPT รับไฟล์ใหญ่กว่ามาก (assumption: ~512MB)
- ⚠️ docx MIME ว่าง → FE รับแต่ BE 415 (`file_service.go:206-215` ไม่มี office ext ใน text-like)
- ✖ paste ไฟล์ non-image จาก clipboard ไม่ได้

### 2.4 Conversation management — A 80 / B 78 (สถานะ: folders/share ดีกว่า Claude.ai, search ด้อยกว่า ChatGPT)
มีจริง: list จัดกลุ่มวัน + virtualization ≥80 แถว (`Sidebar.tsx:36,1196-1226`), rename inline + **AI auto-title** (`title_service.go`, gpt-4o-mini + บังคับ mirror ภาษา), delete confirm (soft-delete), pin + PINNED section (migration 021), **folders เต็มรูปแบบ** (migration 022, ย้าย/สร้าง/ลบ), **share link พร้อมวันหมดอายุ + หน้า public** (`ShareModal.tsx`, `shared/[token]/page.tsx`), export md/json + copy-as-markdown (`ExportMenu.tsx`), pagination ครบสองแกน

ความต่างจากเจ้าใหญ่:
- ⚠️ **Search แชท = title ILIKE เท่านั้น** (`conversation_repo.go:264-281`) ทั้งที่ full-text engine (`GET /api/v1/search`, ts_rank, migration 016) **มีอยู่แล้วแต่ frontend ไม่ได้ต่อ** — ChatGPT ค้นเนื้อหาแชทได้
- ✖ ไม่มี archive · ✖ ไม่มี Projects (folders จัดกลุ่มอย่างเดียว ไม่มี shared files/instructions)

### 2.5 Input UX — A 85 / B 83 (สถานะ: voice ดีกว่า Claude web, IME/shortcut ด้อยกว่า)
มีจริง: multiline + Shift+Enter + auto-grow มี cap มือถือ/desktop (`ChatInput.tsx:69-70,219-224`), ปุ่ม stop แทน send ตอน stream, attach + validation + concurrent 3, **voice input 2 ทาง (WebSpeech + Whisper) + TTS อ่านคำตอบ** (`useVoiceInput.ts`, `useWhisperInput.ts`, `useVoiceOutput.ts`) — Claude.ai web ไม่มี dictation (assumption), placeholder ตามสถานะ + suggestion chips + footer disclaimer, visualViewport ดันคีย์บอร์ดมือถือ

ความต่างจากเจ้าใหญ่:
- ✖ IME guard (ดู 2.1) · ✖ slash commands / @-mentions · ⚠️ shortcuts มีแค่ 3 (⌘K/⌘⇧S/⌘⇧N, `useKeyboardShortcuts.ts:38-74`) ไม่มี cheatsheet ไม่มี focus-composer key · ✖ token/char counter (เจ้าใหญ่ก็ไม่โชว์ — ไม่หักมาก)

### 2.6 Context/knowledge — A 78 / B 72 (สถานะ: KB/memory เท่า, custom instructions/Projects ด้อยกว่า)
มีจริง:
- **KB/RAG ครบ stack มี UI**: จัดการ KB + upload + reindex (`chat/knowledge/page.tsx` 711 บรรทัด), ผูกต่อบทสนทนา (`KBSelector.tsx`), retrieval topK=5 cos≥0.70 → Layer 13 (`rag_service.go:28-153`)
- **Cross-chat memory + UI เต็ม**: save_memory/list_memories/search_past_conversations tools, pending-preference confirm, memory page 718 บรรทัด (`tools.go:56-223`, `memory/page.tsx`) — ระดับเดียวกับ ChatGPT memory
- Token budgeting ต่อ model + trim หัว + conversation-state compaction: `context_budget.go:59-157`, `context_compiler.go:149-348`
- วันที่/เวลา inject (Layer 2) + กติกาภาษาไทย ครับ/ค่ะ (Layer 3): `prompt_compiler.go:198-224`

ความต่างจากเจ้าใหญ่:
- ⚠️ **Custom instructions มี backend + API แต่ไม่มี UI เลย** (`preferences.go`, `api.ts:624-628` — zero call sites) — ChatGPT/Claude มีหน้า settings
- ✖ Projects แบบ shared files+instructions ไม่มี
- ⚠️ timezone hardcode Asia/Bangkok (`prompt_compiler.go:45`) — โอเคสำหรับออฟฟิศไทย แต่ไม่ตาม user
- ⚠️ budget ของ Auto ตกไป default 120K เสมอ (`context_compiler.go:225` ใช้ `Conv.ModelID=="auto"`)
- ⚠️ "Conversation compacted (…summarized)" เกินจริง — เป็น state extraction ไม่ใช่ summarization และถูกปิดเมื่อ MemoryEnabled=false (`context_compiler.go:149-189`)

### 2.7 Models & tools — A 75 / B 70 (สถานะ: media gen ดีกว่า, code interpreter UI/URL reading ด้อยกว่า)
มีจริง: model picker มี search/Popular/Recents/provider groups/badges (`ModelSelector.tsx` 626 บรรทัด), Auto + RoutingHints (`message_service.go:2548-2573`), **web search + citation chips + provider chain 5 เจ้า** (`tools.go:1143-1233`, `SearchCitations.tsx`, `backend/app/services/search/providers.py`), **image gen + video gen + audio gen ในแชท** (`tools.go:841-1133`, media forms), run_python sandbox แข็ง (network-none, seccomp, `code_interpreter.go:47-75`), Excel/Doc/Pptx plan→execute + validation + versioning, reasoning display (`ThinkingBlock.tsx`)

ความต่างจากเจ้าใหญ่:
- ✖ **web page fetch/URL reading tool ไม่มี** — วาง URL แล้วให้อ่านไม่ได้ (เจ้าใหญ่ทำได้ทั้งคู่); PROMPT-1 เป็นแผน ยังไม่ implement ฝั่ง webchat
- ✖ **run_python ไม่มี UI โชว์โค้ด/stdout/สถานะ "กำลังรัน"** — เห็นแค่ artifact ไฟล์; ChatGPT โชว์ code+output สวยงาม · แถม default `SANDBOX_ENABLED=false` (`config.go:138`)
- ⚠️ **Doc/Pptx from-scratch โดนปิดเงียบในแชทเปล่า** — `docContext`/`pptxContext` return "" เมื่อไม่มีไฟล์ (`message_service.go:1864-1867,1979-1982`) → "สร้าง PDF ให้หน่อย" ในแชทใหม่ได้ prose ไม่ได้ card
- ⚠️ **Capability prompt โกหก model**: `prompt_compiler.go:275-278` บอก "CANNOT run code / browse / create images" ทั้งที่ tools แนบอยู่ในรีเควสต์เดียวกัน + tool_policy ครอบแค่ 4/8 tools
- ⚠️ description/context/pricing ของ model ถูกส่งมาแต่ **ไม่ render** (`handler/model.go:140-247` vs `ModelSelector.tsx:547-563`) · reasoning toggle ไม่มี UI (store action zero call sites) · locked_model dead (SetLockedModel zero callers)

### 2.8 Polish/micro-UX — A 78 / B 75
มีจริง: skeletons ครบ, auto-scroll ฉลาด + 2 pills (New messages / Scroll to latest) + prepend preservation (`ChatThread.tsx:66-145,206-240`), empty state สวย (โลโก้+greeting+chips), responsive/มือถือละเอียด (bottom-sheet, safe-area, tap-44), dark mode ผ่าน CSS vars, animations ประณีต + comment เหตุผล, credit badge, error boundary

ความต่างจากเจ้าใหญ่ (micro-UX ที่คนชินแล้วสะดุด):
- ✖ ไม่มี timestamp ต่อข้อความ (`MessageMeta` ไม่มีเวลา) · ✖ ไม่จำ scroll position ข้ามบทสนทนา
- ⚠️ **ภาษา UI ปน 3 ชั้น**: dict EN/TH ครอบ 14/75 components, toast อังกฤษล้วน 58 จุด, error/media-progress ไทยล้วนแม้ locale EN (`lib/errorMessages.ts`, `SearchingIndicator.tsx:18`) — ผู้ใช้เห็นภาษาสลับไปมาในหน้าจอเดียว
- ⚠️ toast เดี่ยว ไม่ stack ไม่มี action/retry (`Toast.tsx:5-6`) · ไม่มี retry บน error banner/failed turn · prefers-reduced-motion เคารพแค่ shimmer-text (`globals.css:732-740`) · `MessageMeta` ซ่อนชื่อ model เมื่อไม่มี token_usage (`MessageBubble.tsx:411`)

---

## 3. Scorecard — DESKTOP (เทียบ Claude Desktop / ChatGPT desktop)

### 3.1 Local file / folder work — A 85 / B 88 (สถานะ: integration ดีกว่า, ecosystem ด้อยกว่า)
มีจริง: read_file/read_document (pdf/docx/xlsx/xlsm/pptx + text ~14 ext, PdfPig/OpenXml/ClosedXML: `DocumentTextExtractor.cs:37-56`), **OCR สแกนผ่าน chat-edge tha+eng** (`ScannedDocumentOcrService.cs`), edit_file/write_file/create_directory/move_file + **.sran.bak backup** (`ToolHelpers.cs:46-59`), **folder grant + validator + PathGuard + hard-deny app-state/system dirs** (`FolderGrantValidator.cs:31-88`, `ToolHelpers.cs:80-197`), working folder ใน composer + system prompt (`ChatViewModel.cs:175-227,2580-2586`), attachments picker/drag/paste + vision blocks, filecache offline

เทียบเจ้าใหญ่: Claude Desktop ทำผ่าน MCP filesystem (ต้อง config เอง — ของเรา turnkey กว่าและ scope ปลอดภัยกว่า); ChatGPT desktop **แก้ไฟล์ในเครื่องไม่ได้เลย** (แค่อ่านจาก paired apps — assumption) → ข้อนี้เรา **นำ**
Gaps: ✖ delete-file tool (ต้องผ่าน run_command) · ⚠️ .sran.bak เก็บเวอร์ชันเดียว ทับแล้วหาย · ✖ ปุ่ม capture หน้าจอฝั่งผู้ใช้ · OCR ต้องมี server-synced conversation

### 3.2 Command / automation / computer use — A 80 / B 90 (สถานะ: กว้างกว่า consumer desktop ของเจ้าใหญ่)
มีจริง: **39 local tools** (`AppHostBuilder.cs:163-276`): run_command streaming mini-terminal + cwd ต่อบทสนทนา + timeout ปรับได้ + **DestructiveCommandDetector tokenized** (unwrap cmd/powershell/base64 ลึก 4 ชั้น: `DestructiveCommandDetector.cs:44-244`), run_python (remote sandbox), **run_subagent fan-out ≤6 tasks/3 concurrent read-only + nested progress UI** (`SpawnAgentTool.cs`), computer use ครบ (screenshot virtual-desktop multi-monitor + click/type/key/scroll/drag/zoom + coord mapping: `ScreenContext.cs`), **UIA tools** (read_screen_elements/uia_click/uia_set_value + snapshot staleness), **browser bridge 9 tools ผ่าน Chrome extension + named pipe + DPAPI token**, update_plan checklist + soft budget 100 steps + Continue, web_fetch SSRF-guarded + web_search, **MCP stdio+HTTP + add-connector UI + OAuth broker**

เทียบเจ้าใหญ่: Claude Desktop consumer ไม่มี computer use/shell ในตัว (ต้อง MCP; Claude Code เป็นคนละ product), ChatGPT desktop ไม่มี local automation (Operator/agent อยู่ฝั่ง cloud — assumption) → breadth เรา **นำชัด**
Gaps: ⚠️ extension เป็น load-unpacked เท่านั้น (store id placeholder `BrowserExtensionInstaller.cs:22`) · ความน่าเชื่อถือ computer-use จริงยังไม่ผ่าน batch live-validate (per UIA-first initiative)

### 3.3 Approval / permission UX — A 82 / B 85 (สถานะ: ดีกว่าในความละเอียด, มีรูรั่ว audit)
มีจริง: Manual/Auto/Skip เลือกใน composer popup (`ComposerControl.xaml:786-888`), danger classification ตาม category + per-tool default 3 ระดับ, consent dialog มี args + **danger banner แดง + ไทยครบ parity-tested** (`ToolConsentDialog.xaml:84-108`, `Dict.cs:817-830`), **folder-scoped Always-Allow** (`FolderScopeResolver.cs`), SensitiveReadGuard นอก scope, Skip warn-once confirm, Enter/Esc ใน dialog, foreground restore ก่อน screen tools, ปิด dialog = Deny (fail-safe)

เทียบเจ้าใหญ่: ระดับเดียวกับ approval ของ Claude Desktop/Code และ **เหนือกว่าเรื่อง scope ต่อโฟลเดอร์ + ไทย**
Gaps: ⚠️ dialog **ไม่โชว์ scope/โฟลเดอร์** ที่ Always-Allow จะผูก (`ToolConsentViewModel` ไม่อ่าน scope) · ⚠️ Skip bypass audit ครอบแค่ run_command (`ChatOrchestrator.cs:111` log เป็น auto ธรรมดา) · run_python ไม่ผ่าน detector · "Waiting for permission…" hardcode อังกฤษ (`ChatViewModel.cs:1592`)

### 3.4 Chat core (desktop) — A 80 / B 78
มีจริง: Markdig→native WPF + block-diff streaming 150ms (`MarkdownViewer.cs`), code block header + copy + syntax highlight + ตาราง + **KaTeX ผ่าน WebView2 ล็อค CSP** + inline images ปลอดภัย (`MarkdownRenderer.cs`), stop (local CTS + server cancel), **edit-in-place → branch fork + ◀ n/m ▶ persisted** (`ChatViewModel.cs:2672-2846`), copy message + thumbs + TTS, tool cards + mini-terminal + nested sub-agent + update_plan checklist + search source cards, jump-to-latest + intent detection, model picker keyboard nav + descriptions, system prompt มี Language rule + proactivity, server-authoritative transcript sync + deletes propagate

Gaps: ⚠️ **Regenerate = append user message ซ้ำ** ไม่แทนที่ (`ChatViewModel.cs:2632-2645`) · ⚠️ **error turn ซ่อน action row ทั้งแถบ → ไม่มีปุ่ม retry** (`MessageBubble.xaml:766-768`) · ⚠️ reasoning display เป็น dead UI บน agent path หลัก (event ไม่มี) · ⚠️ resume หลังปิดแอปกลางคันไม่ได้ (`_agentResume` in-memory เท่านั้น) · สตริงสถานะ agent hardcode อังกฤษ

### 3.5 App shell — A 65 / B 70 (สถานะ: ด้อยกว่า)
มีจริง: tray + context menu + minimize-to-tray (`App.xaml.cs:238-300`), **global hotkey Ctrl+Shift+Space** (`GlobalHotkey.cs`), autostart --minimized, single-instance + `sran://` deep link, **auto-update: manifest + SHA-256 + silent install + banner** (`UpdateService.cs`), version ใน menu, crash dialog + opt-in telemetry

Gaps เทียบเจ้าใหญ่:
- ✖ จำขนาด/ตำแหน่งหน้าต่างไม่ได้ (hardcode 1200x760) · ✖ Windows toast notifications (มีแค่ in-window) · ✖ companion/mini window (ChatGPT desktop มี — assumption)
- ⚠️ **Installer/exe ไม่ได้ sign (NotSigned) → SmartScreen บล็อค non-IT** · manifest sha256 ไม่ตรง installer จริง + version เท่ากันจึงไม่มีวัน update trigger (`releases/latest.json` vs actual hash) · manifest ไม่มี sha256 = ข้าม verify เงียบ (`UpdateService.cs:159-160`)
- ⚠️ **Offline cold start ดีดไป login ทั้งที่มีประวัติ local** (`AuthService.cs:31-53` จับแค่ 401/403) · token expiry กลางคันไม่พากลับ login (SessionChanged ไม่มี subscriber)

### 3.6 Conversation management + persistence — A 82 / B 80
มีจริง: rename inline/delete confirm/pin/folders (server migration 022)/auto-title client-side, **search = titles + server FTS ผ่าน `/search` (ดีกว่า webchat!)** (`ConversationListViewModel.cs:264-376`), SQLite local-first (`data.db` + drafts + tool_executions + branch_sets), pull sync 500 cap + server-authoritative sweep, push backup ทางเดียว (tool chains อยู่ local), export md/json + share dialog, empty-chat cleanup, **draft memory ต่อบทสนทนา** (debounced + race guards)

Gaps: export/share ต้อง online (ไม่มี local export) · date bucket มีแค่ PINNED/TODAY/30/OLDER (คีย์ yesterday/last7 dead) · reinstall ไม่ re-show onboarding (settings.json รอด)

### 3.7 Onboarding (non-IT) — A 55 / B 60 (สถานะ: จุดอ่อนที่สุดของทั้งระบบ)
มีจริง: first-run dialog (Cloud vs Self-hosted + Test connection + tips + autostart), login email/password + ข้อความ error ไทย + Forgot password + Server-settings escape, DPAPI token store, **extension pairing UI ซื่อสัตย์ + บอกขั้นตอนละเอียด + Save & reconnect รีโหลด ext อัตโนมัติ** (`SettingsView.xaml:182-245`, `options.js`), folder access UI localized

Gaps เทียบเจ้าใหญ่ (installer signed, sign-in ผ่าน browser, zero-config):
- ⚠️ **First-run gate ข้ามได้ด้วย Esc** (`FirstRunDialog.xaml.cs:17-25` commit โดยไม่เช็ค CanContinue) — recreate dead-end ที่ test ตั้งใจกัน
- ⚠️ **FirstRunDialog ครึ่งอังกฤษ** (Title/heading/Quick tips/Get Started hardcode EN) ทั้งที่กลุ่มเป้าหมายไทย
- ⚠️ Unsigned + SmartScreen (ดู 3.5) · pairing จริงต้อง load-unpacked + วาง token เอง (ยากสำหรับ non-IT) · **portal usage link hardcode `http://localhost:3001`** (`MainWindow.xaml.cs:294`) · ✖ ไม่มี in-app help/user guide เลย (ไทยหรืออังกฤษ)

### 3.8 Polish (desktop) — A 72 / B 72
มีจริง: **theme Dark/Light/System follow OS สด ๆ + dark title bar** (`ThemeManager.cs`), virtualization + dual throttle (33ms scroll/150ms markdown block-diff), toast system fixed-frozen-template + tests, draft per convo, **i18n 475 คีย์ EN/TH parity test-enforced**, shortcuts 8 ตัว + ShortcutsDialog, reduced-motion gate (บางส่วน), เข้า transcript ด้วยคีย์บอร์ดได้

Gaps: ✖ timestamp ต่อข้อความ (CreatedAt มีแต่ไม่ render) · ✖ **notification เมื่อ task เสร็จตอน minimize/unfocused** (toast อยู่ใน window เท่านั้น) · ✖ scroll memory · ✖ skeleton (มีแต่ progress bar) · ⚠️ ShortcutsDialog/SettingsView หลาย label/FirstRun/tray menu/AutomationProperties **อังกฤษล้วน** · ⚠️ theme quick-toggle ทำลายค่า System (`MainViewModel.cs:97,133`) · ⚠️ clipboard มี image+text → paste เป็น text (`ComposerControl.xaml.cs:190-198`) · drag-drop ตายบน home screen · "N tokens"/"Thinking..." hardcode EN

---

## 4. Micro-UX checklist (สิ่งที่คนใช้ Claude/ChatGPT ชิน)

| Micro-UX | Webchat | Desktop | ผลกระทบ office |
|---|---|---|---|
| Copy code ต่อบล็อก | ✅ | ✅ | — |
| Stop generation | ✅ | ✅ | — |
| Edit & resend (+branch) | ✅ | ✅ | — |
| Regenerate | ✅ | ⚠️ append ซ้ำ | กลาง — transcript รก |
| Retry บน turn ที่ error | ⚠️ ผ่าน Regenerate | ✖ ซ่อนปุ่ม | สูง — เจอ error แล้วตัน ต้องพิมพ์ใหม่ |
| Auto-scroll + jump-to-bottom | ✅ (2 pills) | ✅ | — |
| Copy คำตอบทั้งก้อน | ✅ | ✅ | — |
| Drag-drop ไฟล์ | ✅ เขตแชท | ⚠️ home screen ตาย | ต่ำ |
| Paste รูปจาก clipboard | ✅ | ⚠️ image+text หลุด | กลาง — paste จาก browser/Office พลาดบ่อย |
| ค้นหาแชท (เนื้อหา) | ⚠️ title เท่านั้น | ✅ titles+FTS | สูง (web) — หาไฟล์/มติเก่าไม่เจอ |
| Auto-title | ✅ AI | ✅ | — |
| Timestamp ต่อข้อความ | ✖ | ✖ | กลาง — อ้างอิง "คุยเมื่อไหร่" ไม่ได้ |
| Scroll memory ข้ามแชท | ✖ | ✖ | ต่ำ-กลาง |
| Draft memory ต่อแชท | ✖ | ✅ | กลาง (web) |
| Stream resume หลัง refresh | ✖ | n/a (มี history) | กลาง |
| IME composition guard | ✖ | ✖ (ไทยตรงไม่โดน) | ต่ำสำหรับไทย Kedmanee, สูงถ้ามี CJK/predictive |
| Keyboard shortcuts + cheatsheet | ⚠️ 3 คีย์ ไม่มี dialog | ✅ 8 + dialog (EN) | ต่ำ |
| Notification งานเสร็จตอน minimize | n/a | ✖ | สูง — งาน agent ยาว ๆ ไม่รู้ว่าเสร็จ |
| Loading/empty/error states | ✅ ส่วนใหญ่ | ⚠️ ไม่มี skeleton/sidebar empty | ต่ำ |
| ภาษา UI สม่ำเสมอ | ⚠️ ปน 3 ชั้น | ⚠️ ไทย+EN ปน | สูง — "รู้สึกไม่เนียน" ชัดสุด |

---

## 5. Top gaps เรียงตามผลกระทบ office

1. **[Desktop] Onboarding + trust chain ยังไม่ผ่านเกณฑ์ non-IT** — unsigned installer (SmartScreen), first-run Esc-bypass + ครึ่งอังกฤษ, extension load-unpacked, portal link localhost, offline cold-start lockout → พนักงานทั่วไปติดตั้ง/ใช้เองไม่รอด (หลักฐาน §3.5, §3.7)
2. **[ทั้งคู่] ภาษา UI ไม่สม่ำเสมอ** — web: toast/plan-card/workspace อังกฤษ + error ไทยล้วนข้าม locale; desktop: Settings/Shortcuts/FirstRun/tray/สถานะ agent อังกฤษ → จุดที่ทำให้ "ไม่เท่าเจ้าใหญ่" ชัดที่สุดในสายตาผู้ใช้ไทย
3. **[Web] ค้นหาแชทไม่เจอเนื้อหา** — ILIKE title เท่านั้น ทั้งที่ FTS backend เสร็จแล้ว (ต่อ 1 endpoint จบ) — งานออฟฟิศพึ่งการค้นประวัติมาก
4. **[Web] Doc/Pptx from-scratch ตายในแชทเปล่า** (`len(files)==0` → prompt ไม่ inject) — use case สร้างเอกสารจากศูนย์คือ office หลัก
5. **[Desktop] error turn ไม่มีปุ่ม retry + Regenerate append ซ้ำ** — จุดสะดุดรายวัน
6. **[Web] ไม่มี URL fetch tool** — "อ่านหน้านี้ให้หน่อย" คืองาน office ประจำ (desktop มี web_fetch แล้ว)
7. **[Desktop] ไม่มี notification ตอน task เสร็จขณะ minimize** — ลด value ของ agentic run ยาว
8. **[Web] custom instructions ไม่มี UI** — backend เสร็จแล้ว
9. **[Web] run_python ไม่มี UI code/output + SANDBOX_ENABLED default off** — มี code interpreter แต่ผู้ใช้มองไม่เห็น
10. **[Web] capability prompt ขัดแย้งกับ tools จริง** (`prompt_compiler.go:275-278`) — เสี่ยง model ปฏิเสธงานที่ทำได้

## 6. Quick wins vs Big gaps

**Quick wins (งานน้อย ใกล้เจ้าใหญ่ทันตา):**
- ต่อ sidebar search → `GET /api/v1/search` ที่มีอยู่ (web)
- ลบ/แก้ capability-contradiction block + เพิ่ม tool_policy 4 tools ที่ขาด (web, ไฟล์เดียว)
- ปลด gate `len(files)==0` ของ doc/pptx from-scratch (web, 2 จุด)
- โชว์ Regenerate/Retry บน error turn (desktop, XAML trigger เดียว) + เปลี่ยน Regenerate เป็น replace
- แสดง scope โฟลเดอร์ใน consent dialog (desktop, VM อ่าน field ที่มีอยู่แล้ว)
- แก้ paste guard `ContainsImage && !ContainsText` (desktop)
- เก็บกวาด hardcoded strings เข้าสองฝั่ง dict (ทยอยได้ มี parity test รออยู่แล้วฝั่ง desktop)
- แสดง upload progress % ที่วัดอยู่แล้ว (web) · แก้ข้อความ 413 "25 MB" ให้ตรง config
- เพิ่ม isComposing guard 1 บรรทัด (web)
- แก้ portal usage link localhost → derive จาก gateway (desktop)
- First-run: บังคับ CanContinue บน Esc + แปลไทย (desktop)

**Big gaps (ต้องลงแรง):**
- Code signing cert + release pipeline จริง (EV cert + manifest ถูก hash) — บล็อคการ rollout non-IT
- Artifacts/canvas แบบ live-preview/แก้ไขได้ (web) — feature เรือธงของเจ้าใหญ่
- Chrome Web Store listing ของ extension (เลิก load-unpacked)
- Stream resume (web) + run resume ข้าม restart (desktop)
- Projects (shared files+instructions) ทั้งสองฝั่ง
- run_python UI (code + stdout + running state) + เปิด sandbox โดย default
- Windows toast notifications + window state persistence (desktop)

## 7. จุดที่เราเท่า/ดีกว่าเจ้าใหญ่ (ให้เครดิตตามจริง)

- **OCR ภาษาไทยบนสแกน PDF (tha+eng) ทั้ง web และ desktop** — เจ้าใหญ่อ่านสแกนผ่าน vision ได้แต่ไม่มี pipeline OCR + confidence + marker แบบนี้
- **Excel/Doc/Pptx plan→execute + validation + version timeline/compare/rollback** — versioned file lineage ที่ Claude/ChatGPT ไม่มี (ผลิตไฟล์แล้วจบ)
- **Video + audio generation ในแชท** (submit/poll/attach) — เจ้าใหญ่แยกไป product อื่น
- **Desktop tool breadth**: 39 tools + UIA + browser bridge + sub-agent fan-out + destructive detector tokenized + folder-scoped consent — เกิน consumer desktop ของทั้งสองเจ้า; ChatGPT desktop ไม่แก้ไฟล์ local เลย
- **Folder-scoped Always-Allow + SensitiveReadGuard + PathGuard hard-deny** — ละเอียดกว่า approval ของเจ้าใหญ่
- **Consent dialog + turn errors ภาษาไทย parity-tested 475 คีย์** — ไทยจริงจังกว่าเจ้าใหญ่ (Claude/ChatGPT UI ไทยยังจำกัด — assumption)
- **Share link มีวันหมดอายุ + revoke** (web) — Claude.ai ไม่มี expiry (assumption)
- **Edit & resend เป็น branching จริงทั้งสองฝั่ง** พร้อม navigator — เท่าชั้น ChatGPT
- **Voice input dual-path + TTS** บน web — เหนือ Claude.ai web
- **DLP ทั้ง ingress/egress + tool-arg policy** — เจ้าใหญ่ไม่มีเทียบ (คนละ category แต่เป็นแต้มต่อ enterprise ไทย)

## 8. Honest note — assumption เรื่องเจ้าใหญ่

ข้อมูลฝั่ง Claude/ChatGPT อิงความรู้ถึง ม.ค. 2026 และ product เหล่านี้เปลี่ยนเร็ว จุดที่เป็น assumption ไม่ใช่ fact:
- ChatGPT web ค้นเนื้อหาแชทได้ / limit ไฟล์ ~512MB / canvas แก้ไขได้ — เชื่อมั่นสูงแต่ตัวเลขอาจคลาด
- Claude.ai ไม่มี dictation บน web, ไม่มี share expiry, artifacts render สด — อาจเปลี่ยนแล้ว
- ChatGPT desktop: companion window Alt+Space, ไม่แก้ไฟล์ local — ฟีเจอร์ "work with apps" ขยายเร็ว อาจล้าสมัย
- Claude Desktop: ทุกอย่างผ่าน MCP config manual, ไม่มี computer use ในตัว consumer — Cowork/ฟีเจอร์ใหม่หลังม.ค. 2026 ไม่ทราบ
- คะแนน B ทุกช่องอิง benchmark ตาม assumption ชุดนี้ · ส่วน "เรามีอะไร" ทุกข้อยืนยันจาก source ที่ 7b73d4d พร้อมไฟล์:บรรทัดแล้วทั้งหมด

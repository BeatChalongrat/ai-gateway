# SRAN Desktop — Audit เทียบ Claude Desktop (8 หมวด) + Reliability + UI Runtime

> **โหมด:** อ่านและรายงาน (read-only) — ยังไม่แก้โค้ดจนกว่าจะได้อนุมัติเฟสถัดไป
> **ขอบเขต:** `SranDesktop/` — WPF .NET 8, Clean Architecture, WebView2, MCP tools
> **กติกา:** ทุกข้อสรุปอ้าง `path:line` จริง • แยก **[ข้อเท็จจริงจากโค้ด]** ออกจาก **[ข้อเสนอ]**
> เอกสารนี้เป็นภาคต่อของ `SRAN_AGENT_CAPABILITY_AUDIT.md` (มุมมอง 12-ข้อ) แต่มองผ่านเลนส์ **8 หมวดของ Claude Desktop** + เจาะ **X แดง** และ **UI ตอนรัน**

---

## 1. Executive Summary

SRAN Desktop **ไม่ใช่ chat wrapper** — มันคือ desktop-automation agent ที่ครบเครื่องอย่างน่าประหลาดใจ มี **41 MCP tools** (`src/SranDesktop.Infrastructure/Mcp/Tools/`), durable runtime ที่ verify ผลจริง (`DurableTaskRuntime.cs`), ควบคุม Windows ด้วย Win32 SendInput + UIA จริง, และมีครบทั้ง subagent / scheduled task / MCP connector marketplace / office generator ที่หลายตัวใน 8 หมวดของ Claude Desktop **มีอยู่แล้ว**

**สถานะตอนนี้ (คะแนนรวมโดยประมาณ ~76%):** โครงกระดูกครบ 8 หมวด จุดอ่อนคือ **ความลึกของ browser automation, live artifacts, และที่สำคัญที่สุดคือ "reliability ของ computer-use"** ที่ทำให้เกิด X แดงเป็นแถบในภาพ

**ช่องว่างใหญ่สุด 3 อย่าง:**
1. **Reliability ของ computer-use (X แดง)** — `computer_type`/`computer_click` เป็น tool ที่ verify ตัวเองเข้มมาก (bind capture+HWND+PID+UIA target + read-back) แต่ **ไม่มี auto-retry/backoff เร็ว ๆ ในระดับ tool** สำหรับสภาวะชั่วคราว (readiness timeout 2 วินาที, focus race, target ยังไม่ทันโผล่) → พลาดครั้งเดียว = X แดงทันที และ retry ต้องพึ่งโมเดลตัดสินใจใหม่ (ช้า, เห็นเป็นการ์ดแดงซ้อน ๆ)
2. **UI ตอนรันไม่มี hierarchy/grouping** — ทุก tool call เป็นการ์ดแบนใบเดียวเรียงยาว, error เป็น `FailureCode` ดิบ (`FocusLost:`, `EffectMismatch:`) ที่ผู้ใช้ทั่วไปอ่านไม่รู้เรื่อง, ไม่มีการรวมกลุ่มตาม goal/step
3. **Browser automation ตื้นกว่า Claude** — มี navigate/read/click/type/tabs แต่ **ขาด**: form file-upload, รัน JS ตามใจ, อ่าน console/network, สร้าง GIF, find-in-page

**แนะนำเริ่มจาก:** **Reliability quick wins (เฟส 2)** ก่อน เพราะเห็นผลกับ X แดงในภาพทันที แล้วตามด้วย **UI redesign (เฟส 3)** ที่ทำให้ error อ่านรู้เรื่อง — สองอย่างนี้ยกระดับ "ความรู้สึกว่าใช้ได้จริง" มากที่สุดต่อ effort

---

## 2. แผนที่สถาปัตยกรรม (เฟส 0) — [ข้อเท็จจริงจากโค้ด]

**Stack:** .NET 8 / WPF / MVVM (CommunityToolkit.Mvvm) / WebView2 / SQLite / Clean Architecture 6 โปรเจกต์ (`SranDesktop.sln`)

```
┌─────────────────────── SranDesktop.App (composition root) ───────────────────────┐
│ App.xaml.cs · Hosting/AppHostBuilder.cs — DI, host, single-instance, startup       │
└───────────────────────────────────┬────────────────────────────────────────────────┘
                                     │
        ┌────────────────────────────┼─────────────────────────────┐
        ▼                            ▼                             ▼
┌─────────────────┐   ┌──────────────────────────────┐   ┌────────────────────────┐
│ Presentation    │   │ Application (agent brain)     │   │ Infrastructure         │
│ (WPF MVVM)      │   │                              │   │ (executor + IO)        │
│                 │   │ ChatOrchestrator.cs ── loop  │   │ Mcp/Tools/*  (41 tools)│
│ ChatViewModel   │◄──┤ DurableTaskRuntime.cs        │──►│ Native/ Win32Input,Uia │
│ ToolExecution   │evt│ GoalInterpreter /            │   │ Office/ Doc,Excel,Pptx │
│  ItemViewModel  │   │ OutcomeCompiler /            │   │ Mcp/Remote/ (MCP client)│
│ MessageBubble   │   │ SemanticOutcomeRuntime       │   │ Api/ GatewayApiClient  │
│  .xaml (cards)  │   │ CapabilityRouter             │   │ Speech/ Storage/       │
└─────────────────┘   └──────────────────────────────┘   └────────────────────────┘
        ▲                                                            │
        │        BrowserHost (stdio relay) ◄── Chrome MV3 extension ─┘
        └── AgentStreamEvent (Events/AgentStreamEvents.cs) — streaming bus UI←runtime
```

**(a) Engine ที่สั่ง action บนเครื่อง** = `src/SranDesktop.Infrastructure/Mcp/Tools/*` (41 ไฟล์) + `Native/Win32Input.cs`, `Native/Uia/`, `Native/ScreenContext.cs` (พิกัด/มัลติจอ), `Native/ApplicationReadinessService.cs` (รอแอปพร้อม)

**(b) Planner / agent loop** = `Application/Services/ChatOrchestrator.cs` (วน tool-call) + `DurableTaskRuntime.cs` (2,200+ บรรทัด: goal→outcome→plan→action→verify→DoD, checkpoint SQLite, retry budget) + `SemanticOutcomeRuntime.cs` (จับคู่หลักฐานกับ outcome) + `CapabilityRouter` (`RuntimeActionPolicy.cs`)

**(c) UI/frontend ที่แสดง step** = `Presentation/ViewModels/AgentExecutionViewModel.cs` → คลาส `ToolExecutionItemViewModel` (การ์ดต่อ tool) + `Presentation/Controls/MessageBubble.xaml:291-531` (template การ์ด) ; ป้ายชื่อ action มาจาก `Presentation/Localization/Dict.cs:293-302`

**(d) ชั้นเชื่อม LLM/model** = `Infrastructure/Api/` (`IGatewayApiClient` → ai-gateway) — โมเดลถูก resolve ที่ gateway ไม่ใช่ในแอป

**Step runner ที่ผลิต X แดงในภาพ** อยู่ตรงนี้แน่นอน:
- ป้าย **"Open app"** = `Dict.cs:298` (`tool.name.open_application`)
- ป้าย **"Read screen controls"** = `Dict.cs:300` (`tool.name.read_screen_elements`)
- ป้าย **"Type text"** = `Dict.cs:293` (`tool.name.computer_type`)
- ป้าย **"Fill in field"** = `Dict.cs:302` (`tool.name.uia_set_value`)
- สถานะการ์ด (pending / verification_pending / verified / **failed**) = `AgentExecutionViewModel.cs:41` และ mapping ที่ `AgentExecutionViewModel.cs:145-151` (`Complete()`)
- X แดง render ที่ `MessageBubble.xaml:382-385` (`IsFailed` → `Geo_Close` สีแดง)

---

## 3. ตาราง Capability Gap — 8 หมวด (เฟส 1)

> S = เติมง่าย (1-2 วัน) · M = กลาง (สัปดาห์) · L = ใหญ่ (หลายสัปดาห์)

### หมวด 1 — Computer Use (ควบคุมเดสก์ท็อปจริง)

| Sub-capability | สถานะ | ไฟล์อ้างอิง | ช่องว่าง | ยาก |
|---|---|---|---|---|
| Screenshot | ✅ มี | `Tools/ComputerScreenshotTool.cs`, `Native/ScreenContext.cs` | — | — |
| คลิกซ้าย/ขวา/กลาง | ✅ มี | `Tools/ComputerClickTool.cs` | รองรับ button variants | — |
| ดับเบิล/ทริปเปิลคลิก | ⚠️ บางส่วน | `Tools/ComputerClickTool.cs` | ยืนยัน triple-click มีจริงไหม (schema รองรับ double; triple ต้องเช็ค) | S |
| Drag | ✅ มี | `Tools/ComputerDragTool.cs` | — | — |
| Scroll | ✅ มี | `Tools/ComputerScrollTool.cs` | — | — |
| พิมพ์ข้อความ | ✅ มี | `Tools/ComputerTypeTool.cs`, `Native/Win32Input.cs` | reliability (ดูเฟส 2) | — |
| ปุ่มลัด/กดค้าง | ✅ มี | `Tools/ComputerKeyTool.cs` | key-hold (down/up แยก) ยืนยัน | S |
| อ่าน/เขียน clipboard | ✅ มี | `Tools/ReadClipboardTool.cs`, `WriteClipboardTool.cs` | — | — |
| เปิดแอป | ✅ มี | `Tools/OpenApplicationTool.cs` | — | — |
| Multi-display | ✅ มี | `Native/ScreenContext.cs`, `Tools/ComputerZoomTool.cs` | — | — |
| อ่าน UI element (semantic) | ✅ มี | `Tools/ReadScreenElementsTool.cs`, `Tools/UiaClickTool.cs`, `UiaSetValueTool.cs` | — | — |
| Teach mode | ❌ ไม่มี | — | ไม่มี record-and-replay macro | L |
| ระบบสิทธิ์ **แบบ tier ตามคลาสแอป** (browser=อ่าน, terminal/IDE=คลิกได้พิมพ์ไม่ได้) | ❌ ไม่มี (มีคนละแบบ) | `Mcp/PermissionService.cs`, `Application/Services/ToolDangerClassifier.cs:42-48` | SRAN ใช้ permission **ต่อ-tool** (Ask/Allow/Deny) ไม่ใช่ tier ตาม **คลาสของแอปเป้าหมาย** — ไม่มีตรรกะ "แอปนี้เป็น terminal ห้ามพิมพ์" | M |
| Flow ขออนุญาตก่อนทำงาน | ✅ มี | `ViewModels/ToolConsentViewModel.cs`, `Mcp/PermissionService.cs`, `Mcp/FolderScopeResolver.cs` | — | — |

**คะแนนหมวด 1: ~82%** — action ครบเกือบหมดและเป็นของจริง (ไม่ mock) จุดขาดคือ teach-mode และ **tier ตามคลาสแอป** (ของ Claude Desktop ป้องกันพิมพ์ลง terminal/IDE โดยเจตนา — SRAN ยังไม่มี concept นี้)

### หมวด 2 — Browser Automation (เข้าใจ DOM)

| Sub-capability | สถานะ | ไฟล์อ้างอิง | ช่องว่าง | ยาก |
|---|---|---|---|---|
| เปิด/ปิด/สลับแท็บ | ✅ มี | `Tools/BrowserTabsTool.cs`, `BrowserListTabsTool.cs` | — | — |
| นำทาง URL | ✅ มี | `Tools/BrowserNavigateTool.cs` | — | — |
| อ่านหน้าเว็บ (DOM/JS-rendered) | ✅ มี | `Tools/BrowserReadPageTool.cs`, `BrowserFindTool.cs` | — | — |
| คลิก/พิมพ์ใน DOM | ✅ มี | `Tools/BrowserClickTool.cs`, `BrowserTypeTool.cs` | — | — |
| Screenshot หน้าเว็บ | ✅ มี | `Tools/BrowserScreenshotTool.cs` | — | — |
| กรอกฟอร์ม | ⚠️ บางส่วน | `Tools/BrowserTypeTool.cs` | ทำได้ผ่าน type ทีละช่อง ไม่มี form-fill รวม | S |
| อัปโหลดไฟล์/รูป | ❌ ไม่มี | — | ไม่มี `browser_file_upload` | M |
| รัน JavaScript ตามใจ | ❌ ไม่มี | — | ไม่มี `browser_execute_js` | M |
| อ่าน console/network | ❌ ไม่มี | — | ไม่มี console/network reader | M |
| สร้าง GIF | ❌ ไม่มี | — | — | M |
| ค้นหาข้อความในหน้า | ✅ มี | `Tools/BrowserFindTool.cs` | — | — |

**คะแนนหมวด 2: ~58%** — แกน DOM automation ครบ (ผ่าน Chrome MV3 extension + `BrowserHost` relay) แต่ตื้นกว่า Claude ในเรื่อง upload / JS / console / network / GIF

### หมวด 3 — ระบบไฟล์ & Shell

| Sub-capability | สถานะ | ไฟล์อ้างอิง | ช่องว่าง | ยาก |
|---|---|---|---|---|
| อ่าน/เขียน/แก้ไขไฟล์ | ✅ มี | `Tools/ReadFileTool.cs`, `WriteFileTool.cs`, `EditFileTool.cs` | — | — |
| สร้างโฟลเดอร์/ย้ายไฟล์ | ✅ มี | `Tools/CreateDirectoryTool.cs`, `MoveFileTool.cs` | — | — |
| รัน shell (CLI) | ✅ มี | `Tools/RunCommandTool.cs`, `Application/Services/ShellSession.cs` | streaming stdout ผ่าน mini-terminal จริง | — |
| รัน Python ใน sandbox | ✅ มี | `Tools/RunPythonTool.cs` (docker sandbox) | — | — |
| ค้นไฟล์/เนื้อหา (glob/grep) | ✅ มี | `Tools/SearchFilesTool.cs`, `ListDirectoryTool.cs` | — | — |
| ขอสิทธิ์เข้าถึงโฟลเดอร์ | ✅ มี | `Mcp/FolderScopeResolver.cs`, `Application/Services/FolderGrantValidator.cs` | — | — |

**คะแนนหมวด 3: ~92%** — แข็งที่สุด ครบและปลอดภัย (folder-scoped consent + docker sandbox สำหรับ python)

### หมวด 4 — สร้างเอกสาร

| Sub-capability | สถานะ | ไฟล์อ้างอิง | ช่องว่าง | ยาก |
|---|---|---|---|---|
| docx | ✅ มี | `Office/LocalDocPlanExecutor.cs` | — | — |
| xlsx | ✅ มี | `Office/LocalExcelPlanExecutor.cs` | — | — |
| pptx | ✅ มี | `Office/LocalPptxPlanExecutor.cs`, `PptxShapeTreeBuilder.cs` | — | — |
| pdf | ⚠️ บางส่วน | (สาย doc-plan / reportlab ฝั่ง backend) | ยืนยัน PDF local executor มีจริงไหมใน desktop path | S |
| HTML/React artifacts | ✅ มี | `Controls/ArtifactControl.xaml.cs`, `Rendering/ArtifactSandbox.cs` | — | — |

**คะแนนหมวด 4: ~85%** — Office 3 ตัวครบ + artifact sandbox; PDF ต้องยืนยัน path ใน desktop

### หมวด 5 — Subagents

| Sub-capability | สถานะ | ไฟล์อ้างอิง | ช่องว่าง | ยาก |
|---|---|---|---|---|
| มอบหมายงานย่อยขนาน | ✅ มี | `Tools/SpawnAgentTool.cs` (`run_subagent`, `tasks[]`, 2-3 concurrent) | depth cap = 1 | — |
| Read-only sandbox ของ subagent | ✅ มี | `Tools/SpawnAgentTool.cs` (scope resolver, read-only) | — | — |
| UI ของ subagent (nested cards) | ✅ มี | `AgentExecutionViewModel.cs:100-123` (`RouteNestedEvent`, `Children`) | — | — |
| ประเภท agent เฉพาะทาง (search/plan/general) | ⚠️ บางส่วน | `Tools/SpawnAgentTool.cs` | เป็น general-purpose ตัวเดียว ไม่มี "typed roles" ให้เลือก | M |

**คะแนนหมวด 5: ~72%** — fan-out ขนานทำได้จริง; ขาดแค่ "บทบาท agent เฉพาะทาง" แบบ Claude

### หมวด 6 — งานอัตโนมัติ & ตั้งเวลา

| Sub-capability | สถานะ | ไฟล์อ้างอิง | ช่องว่าง | ยาก |
|---|---|---|---|---|
| Scheduled tasks (cron) | ✅ มี | `Infrastructure/Services/TaskSchedulerService.cs` (timer 60s), `Application/Services/CronHelper.cs`, `Storage/SqliteScheduledTaskStore.cs` | — | — |
| UI จัดการงานตั้งเวลา | ✅ มี | `Views/ScheduledTasksView.xaml`, `ViewModels/ScheduledTasksViewModel.cs` | — | — |
| รายวัน/สัปดาห์/ครั้งเดียวอนาคต | ✅ มี | `CronHelper.NextOccurrence` | UX เลือก preset (ไม่ต้องเขียน cron) ยืนยัน | S |
| Live artifacts (ดึงข้อมูลสด) | ❌ ไม่มี | `Rendering/ArtifactSandbox.cs` (static) | artifact เป็น static ไม่มี auto-refresh/live-data | L |

**คะแนนหมวด 6: ~58%** — scheduled tasks ครบและเป็นของจริง; **live artifacts ยังไม่มี** (ช่องว่างเด่น)

### หมวด 7 — MCP Connectors / Plugins

| Sub-capability | สถานะ | ไฟล์อ้างอิง | ช่องว่าง | ยาก |
|---|---|---|---|---|
| MCP client (HTTP + stdio) | ✅ มี | `Mcp/Remote/McpRemoteClient.cs`, `HttpMcpTransport.cs`, `StdioMcpTransport.cs`, `RemoteMcpTool.cs` | — | — |
| Marketplace ค้น/แนะนำ/ติดตั้ง | ✅ มี | `Views/ConnectorMarketplaceView.xaml`, `ViewModels/ConnectorMarketplaceViewModel.cs`, `Services/McpConnectorService.cs` | — | — |
| เพิ่ม custom connector | ✅ มี | `ViewModels/AddCustomConnectorViewModel.cs` | — | — |
| OAuth ต่อ connector | ✅ มี | `Application/Services/ConnectorOAuthCoordinator.cs`, `OAuthDeepLink.cs` | — | — |
| Untrusted boundary ต่อ MCP output | ✅ มี | `RemoteMcpTool.cs` (เรียก `UntrustedObservationBoundary`) | — | — |

**คะแนนหมวด 7: ~78%** — สถาปัตยกรรม connector ครบและปลอดภัย (credential-guard + untrusted boundary) จุดที่เหลือคือ curation/ความกว้างของ registry

### หมวด 8 — เว็บ

| Sub-capability | สถานะ | ไฟล์อ้างอิง | ช่องว่าง | ยาก |
|---|---|---|---|---|
| Web search | ✅ มี (จริง, ผ่าน gateway) | `Tools/WebSearchTool.cs:57` (`_gateway.SearchAsync`) | gated ด้วย toggle | — |
| Web fetch (SSRF-guarded) | ✅ มี | `Tools/WebFetchTool.cs` | — | — |
| Untrusted boundary ต่อผลเว็บ | ✅ มี | `WebSearchTool.cs:75` (`UntrustedObservationBoundary.ProtectIfUntrusted`) | ⚠️ ตรวจว่า `WebFetchTool` ห่อด้วยหรือยัง (audit เก่าชี้ว่าเคยขาด) | S |

**คะแนนหมวด 8: ~85%** — search จริง (ไม่ stub) + fetch มี SSRF guard; boundary ผูกกับ search แล้ว (แก้จาก audit เก่า)

### สรุปคะแนนภาพรวม

| หมวด | คะแนน |
|---|---|
| 1. Computer Use | 82% |
| 2. Browser Automation | 58% |
| 3. Filesystem & Shell | 92% |
| 4. Documents | 85% |
| 5. Subagents | 72% |
| 6. Scheduled / Live artifacts | 58% |
| 7. MCP Connectors | 78% |
| 8. Web | 85% |
| **ภาพรวมถ่วงน้ำหนัก** | **~76%** |

**ข้อสังเกต:** ช่องว่างส่วนใหญ่คือ **"ความลึก/ความครบของ feature"** ไม่ใช่ "ไม่มีสถาปัตยกรรมรองรับ" — ทุกหมวดมี abstraction (tool registry, permission layer, MCP client, event bus) พร้อมต่อยอดอยู่แล้ว

---

## 4. Reliability / ทำไม step เป็น X แดง (เฟส 2)

### 4.1 Error handling ปัจจุบัน — [ข้อเท็จจริงจากโค้ด]

**ระดับ tool (computer_type):** `Tools/ComputerTypeTool.cs` เข้มมาก ลำดับ gate:
1. `text` ว่าง / ดูเหมือน credential → `Fail` (`:47-52`)
2. `application` / `target_description` ว่าง → `UnboundInput` (`:57-60`)
3. resolve capture ไม่ได้ / ไม่มี HWND+PID → `UnboundInput` (`:63-67`)
4. `ValidateLive` (พิกัด/หน้าต่าง) ไม่ผ่าน → คืน `FailureCode` (`:71-74`)
5. **`ApplicationReadinessService.WaitForAsync(... InputReady, timeout: 2s)`** ไม่พร้อม → `Fail` (`:76-86`)
6. หา UIA target ไม่เจอ → `TargetNotFound` (`:91-94`)
7. focus หลุดก่อน dispatch → `FocusLost` (`:108-110`)
8. หลังพิมพ์ อ่าน read-back ไม่ตรง → `EffectMismatch` / `FailedComputerAction` (`:136-151`)
9. target หลุด stale → `StaleObservation` (`:153-156`)

**ระดับ runtime (retry):** `DurableTaskRuntime.cs`
- retry budget = 5 (`:279`, `:1477` `RetryBudget = 5`); เกิน → `RetryBudgetExceeded` (`:380-381`)
- **fresh observation required** ก่อน retry action ที่ state ล้ม (`:572-575` `FreshObservationRequired`)
- **blind-retry blocked** ถ้าไม่เปลี่ยน args/tool/strategy/plan และไม่มี observation ใหม่ (`:596-609` `BlindRetryBlocked`)

**ระดับ tool ที่ resilient กว่า:** `Tools/UiaSetValueTool.cs` มี **"injection ladder"** (ลองหลาย rung + verify read-back + หยุดเมื่อสำเร็จ) และ focus retry 3 ครั้ง (`:264-271`) — **ต่างจาก `ComputerTypeTool` ที่ไม่มี ladder**

### 4.2 รากของ X แดง — map กับโค้ดจริง — [ข้อเท็จจริง + วิเคราะห์]

จากภาพ: `Open app` ✅ + `Read screen controls` ✅ ผ่าน (สอง action นี้ไม่ต้อง bind target/พิมพ์) แต่ `Type text` / `Click` / `Fill in field` ❌ ล้ม — ทั้งสามเป็น action ที่ต้อง **resolve control + รอ readiness + retain focus + read-back**

| สาเหตุคลาสสิก | map กับโค้ด | ทำไมล้ม |
|---|---|---|
| **readiness timeout สั้นไป** | `ComputerTypeTool.cs:83` `timeout: 2s` | Notepad/แอปหนักบางตัวยังไม่ InputReady ใน 2 วิ → `Fail` ทันที ไม่มีรอบสอง |
| **focus race** | `:108-110` `FocusLost` | หน้าต่างอื่น (แม้แต่ SRAN เอง) แย่ง foreground ระหว่าง `SetFocus`→`TypeText` |
| **read-back ไม่ได้ (RichEdit)** | `:117-118`, `:132` | Win11 Notepad = RichEdit ที่ไม่คืน `ValuePattern` → `after` ว่าง → `exact=false` → `EffectMismatch` **แม้ตัวอักษรลงจริง** (นี่คือรากของ FIX_PROMPT_T3/T4) |
| **target หายก่อน dispatch** | `:91-94` `TargetNotFound` | หน้าจอเปลี่ยนหลัง capture (dialog เด้ง, layout ขยับ) |
| **ไม่มี auto-retry เร็วในระดับ tool** | ทั้งไฟล์ — ไม่มี loop | พลาด transient ครั้งเดียว = X แดง; retry ต้องวนกลับไปให้โมเดล re-observe (ช้า + การ์ดแดงซ้อน) |
| **retry ถูกบล็อกถ้าโมเดลไม่เปลี่ยนอะไร** | `DurableTaskRuntime.cs:596-609` | ป้องกัน blind-retry (ดี) แต่ถ้าโมเดล retry ด้วย args เดิม → `BlindRetryBlocked` = X แดงอีกใบ |

**ข้อสรุปเชิงวิศวกรรม:** X แดงเกิดจาก **ปรัชญา "verify เข้มเพื่อกัน false-success"** (จุดแข็งข้อ 12) ปะทะกับ **การไม่มีชั้น retry/รอ ที่นุ่มนวลสำหรับสภาวะชั่วคราว** — ผลคือ agent ที่ "ซื่อสัตย์แต่เปราะ" ทุกความไม่แน่นอนกลายเป็น failure ถาวรที่ผู้ใช้เห็น

### 4.3 กลไก reliability ที่เสนอ — [ข้อเสนอ]

**R1 — Adaptive readiness wait (แทน hard 2s):** เปลี่ยน `ComputerTypeTool.cs:83`/`ComputerClickTool` ให้ poll readiness แบบ backoff จนถึง deadline ที่ยาวขึ้น (เช่น 6-8s, poll ทุก 100ms — โครง poll มีอยู่แล้วใน `ApplicationReadinessService.cs:28`) และ **แยก "ยังไม่พร้อม (retryable)" ออกจาก "ล้มเหลวถาวร"**

**R2 — In-tool typing ladder ให้ `computer_type` เท่า `uia_set_value`:** ยก injection ladder + focus-retry 3 ครั้งจาก `UiaSetValueTool.cs:264-271` มาใช้ใน `ComputerTypeTool` (ValuePattern → SetFocus+SendInput → WM_SETTEXT) ก่อนยอมแพ้

**R3 — จำแนก read-back-not-available ≠ failure:** ที่ `ComputerTypeTool.cs:132-151` ถ้า `os_input_accepted=true` แต่อ่าน read-back ไม่ได้ (RichEdit) → สถานะ **"verification_pending"** (amber) ไม่ใช่ **"failed"** (แดง) แล้วให้ **observation รอบถัดไป** (`read_screen_elements` บน same HWND) เป็นตัว verify — ตรงกับเจตนา FIX_PROMPT_T4 (verify จาก observed_value จริง) โดยไม่ต้องปั๊ม X แดง

**R4 — Bounded auto-retry ที่ runtime สำหรับ FailureCode ที่ retryable:** ใน `DurableTaskRuntime` เพิ่ม fast-path: `FocusLost` / `TargetNotFound` / readiness-timeout → auto re-observe (screenshot/read_screen_elements) + retry **โดยไม่นับเป็น turn ใหม่ในสายตาผู้ใช้** (ยุบเป็นการ์ดเดียว "กำลังลองใหม่ 2/3") — เคารพ `BlindRetryBlocked` โดยถือว่า "fresh observation" ที่ระบบแทรกให้ = การเปลี่ยนที่ถูกต้อง

**R5 — Error message ที่บอกสาเหตุ + ทางแก้:** map `FailureCode` → ข้อความมนุษย์ + คำแนะนำ (เช่น `FocusLost` → "มีหน้าต่างอื่นแย่งโฟกัส — จะลองคลิกหน้าต่างเป้าหมายก่อนพิมพ์ใหม่") — ผูกกับ UI เฟส 3

### 4.4 จัดอันดับ fix (impact / effort)

| # | Fix | Impact | Effort | หมวด |
|---|---|---|---|---|
| 1 | **R3** read-back-not-available → amber ไม่ใช่แดง | 🔥 สูงมาก (ตัด X แดงปลอมทันที) | S | 2 |
| 2 | **R1** adaptive readiness wait | 🔥 สูง | S | 2 |
| 3 | **R2** typing ladder ใน computer_type | สูง | M | 2 |
| 4 | **R5** human error message | สูง (ต่อ UX) | S | 2/3 |
| 5 | **R4** bounded auto-retry ที่ runtime | สูง | M | 2 |

> **Quick-win 3 ตัวแรก (R3+R1+R5) ประเมินได้ผลกับ X แดงในภาพทันที ทำได้ ~1-2 วัน**

---

## 5. UI/UX Runtime Redesign (เฟส 3) — [ข้อเสนอ]

### 5.1 ปัญหา UI ปัจจุบัน — [ข้อเท็จจริงจากโค้ด]

- การ์ด tool เป็น **flat list** ใบต่อใบ (`MessageBubble.xaml:292` `ItemsControl ItemsSource="{Binding ToolExecutions}"`) — ไม่มีการจัดกลุ่มตาม goal/step
- error แสดงเป็น `FailureCode` ดิบ (มาจาก `FullResult`) ที่ผู้ใช้อ่านไม่รู้เรื่อง; ปุ่ม retry จริง ๆ = **regenerate ทั้ง turn** (`MessageBubble.xaml:449-452`) ไม่ใช่ retry step เดียว
- มี 4 สถานะครบแล้ว (pending pulse / verification amber / verified check / failed X — `MessageBubble.xaml:358-386`) และ **live streaming มีอยู่แล้ว** (`StreamingOutput` mini-terminal `:407-417`) — โครงดี แต่ขาด **hierarchy + human error + ต่อ X แดงซ้ำ**

### 5.2 หลักการออกแบบใหม่ (อิง Claude Desktop)

1. **จัดกลุ่มเป็น "goal → steps"** ไม่ใช่ flat: หัวเป็นบรรทัดสรุปเป้าหมาย (checklist ขีดฆ่าเมื่อเสร็จ — ใช้ `PlanStepViewModel` ที่มีอยู่แล้ว `ViewModels/PlanStepViewModel.cs`) แล้ว nest การ์ด tool ใต้แต่ละ step
2. **สถานะ visual ที่มีอยู่แล้ว** คงไว้ แต่เพิ่ม **"retrying" state** (สปินเนอร์ + "ลองใหม่ 2/3") เพื่อยุบ X แดงซ้ำ ๆ ให้เป็นการ์ดเดียว
3. **Human error ที่ขยายได้:** แทน `FocusLost:` ด้วยประโยคไทย + ปุ่ม "ดูรายละเอียดทางเทคนิค" (progressive disclosure) — เก็บ `FailureCode` ดิบไว้ใน expander
4. **Progressive disclosure ของ args:** มีบางส่วนแล้ว (`ArgsSummary` chip + `InputDisplay` ใน expander) — คงไว้ แต่ย่อ `capture_id` JSON ยาว ๆ ให้เป็น chip เดียว
5. **Design tokens ไม่ hardcode:** ใช้ `DynamicResource` ที่มีอยู่ (`AccentRedBrush`, `AccentGreenBrush`, `AccentBlueBrush`, `TextTertiaryBrush`, `BgSecondaryBrush`, `BorderBrush`) — เพิ่ม `AccentAmberBrush` สำหรับ verification-pending/retrying

### 5.3 Component spec

```
StepGroup (ใหม่ — wrap ToolExecutions ตาม step)
├─ Header:  [icon เป้าหมาย] "พิมพ์ข้อความลง Notepad"   [progress 2/3]  [รวมเวลา]
├─ ToolExecutionCard (ที่มีอยู่ — ปรับ)
│   ├─ Row: [type-icon] FriendlyName · ArgsSummary(chip) ······ [status] [duration] [⌄]
│   ├─ (retrying) สปินเนอร์ + "กำลังลองใหม่ 2/3 — โฟกัสหลุด"
│   ├─ (failed)  ข้อความมนุษย์: "พิมพ์ไม่สำเร็จ: มีหน้าต่างอื่นแย่งโฟกัส"
│   │            [ปุ่ม: ลอง step นี้ใหม่]  [ดูรายละเอียดทางเทคนิค ⌄]
│   └─ (expanded) Input(label:value) · Output · screenshot · FailureCode ดิบ
└─ …
```

### 5.4 ตัวอย่างโค้ด (WPF — stack จริง)

**(ก) เพิ่ม state + human message ใน `ToolExecutionItemViewModel` (`AgentExecutionViewModel.cs`):**

```csharp
// state ใหม่: "retrying" + สาเหตุอ่านรู้เรื่อง
private string _state = "pending"; // pending | retrying | verification_pending | verified | failed
public bool IsRetrying => State == "retrying";
[ObservableProperty] private string? _retryHint;      // "กำลังลองใหม่ 2/3 — โฟกัสหลุด"
[ObservableProperty] private string? _humanError;     // map จาก FailureCode

// map FailureCode → ประโยคมนุษย์ (แยกจากข้อความดิบ)
private static string HumanizeFailure(string? code) => code switch
{
    var c when c?.StartsWith("FocusLost") == true      => "พิมพ์ไม่สำเร็จ: มีหน้าต่างอื่นแย่งโฟกัส",
    var c when c?.StartsWith("TargetNotFound") == true => "หาช่องกรอกไม่พบ — หน้าจออาจเปลี่ยนไป",
    var c when c?.StartsWith("EffectMismatch") == true => "พิมพ์ลงแล้วแต่ยืนยันค่าไม่ได้ (บางแอปอ่านค่ากลับไม่ได้)",
    var c when c?.Contains("Readiness") == true        => "แอปยังเปิดไม่พร้อมรับการพิมพ์",
    _ => "ทำขั้นตอนนี้ไม่สำเร็จ",
};
```

**(ข) เพิ่ม token สีใน theme (`Presentation/Resources/*` ที่นิยาม brush):**

```xml
<SolidColorBrush x:Key="AccentAmberBrush" Color="#E8A23D" />
```

**(ค) แก้ status block + error ใน `MessageBubble.xaml` (แถว ~357-453):**

```xml
<!-- status: เพิ่ม retrying (สปินเนอร์ amber) ก่อน failed -->
<controls:CircularProgress Width="14" Height="14"
    Visibility="{Binding IsRetrying, Converter={StaticResource BoolToVisibility}}"/>

<!-- retry hint (แทนที่จะโชว์ X แดงระหว่างลองใหม่) -->
<TextBlock Text="{Binding RetryHint}" FontSize="11.5"
           Foreground="{DynamicResource AccentAmberBrush}"
           Visibility="{Binding IsRetrying, Converter={StaticResource BoolToVisibility}}"/>

<!-- failed: ประโยคมนุษย์ + ปุ่ม + expander ของ FailureCode ดิบ -->
<StackPanel Visibility="{Binding IsFailed, Converter={StaticResource BoolToVisibility}}"
            Margin="0,6,0,0">
    <TextBlock Text="{Binding HumanError}" TextWrapping="Wrap"
               Foreground="{DynamicResource AccentRedBrush}" FontSize="12.5"/>
    <StackPanel Orientation="Horizontal" Margin="0,6,0,0">
        <Button Style="{DynamicResource PillButton}" Content="{loc:T tool.retryStep}"
                Command="{Binding DataContext.RetryStepCommand,
                          RelativeSource={RelativeSource AncestorType=ItemsControl}}"
                CommandParameter="{Binding}"/>
    </StackPanel>
    <Expander Header="{loc:T tool.technicalDetails}" Margin="0,4,0,0">
        <TextBlock Text="{Binding FullResult}" FontFamily="Consolas" FontSize="11"
                   Foreground="{DynamicResource TextTertiaryBrush}" TextWrapping="Wrap"/>
    </Expander>
</StackPanel>
```

**ไฟล์ที่ต้องแก้:** `AgentExecutionViewModel.cs` (state/human-error), `MessageBubble.xaml:357-453` (status+error UI), theme resource (`AccentAmberBrush`), `Dict.cs` (คีย์ `tool.retryStep`, `tool.technicalDetails`, human-error strings) — และเพิ่มคำสั่ง `RetryStepCommand` ใน `ChatViewModel`

> **หมายเหตุ:** โครง live-streaming, expander, status dot, screenshot preview **มีครบแล้ว** — งาน UI นี้เป็นการ **เพิ่ม 1 state (retrying) + human-error layer + grouping** ไม่ใช่รื้อใหม่ ความเสี่ยงต่ำ

---

## 6. Architecture / Roadmap (เฟส 4)

### 6.1 ปัญหาเชิงสถาปัตยกรรมที่ขวางการทำครบ 8 หมวด — [ข้อเท็จจริง + วิเคราะห์]

1. **ไม่มีชั้น "resilience policy" กลาง** — retry/wait กระจายอยู่ในแต่ละ tool (บางตัวมี ladder, บางตัวไม่มี) → พฤติกรรมไม่สม่ำเสมอ (`ComputerTypeTool` vs `UiaSetValueTool`)
2. **Permission เป็นต่อ-tool ไม่ใช่ต่อ-app-class** — ขาด concept "แอปเป้าหมายคลาสอะไร" ที่ Claude ใช้กำหนด tier (`PermissionService.cs`, `ToolDangerClassifier.cs`)
3. **Artifact เป็น static** — ไม่มี data-source binding / refresh loop → ทำ live artifacts ไม่ได้ (`ArtifactSandbox.cs`)
4. **Browser tools ตื้น** — bridge ผ่าน extension ครบ แต่ยังไม่ expose JS-eval / console / network / upload
5. **Subagent เป็น role เดียว** — ไม่มี typed-agent registry

**จุดแข็งที่ควรรักษา:** tool registry (`McpToolRegistry.cs`), event bus streaming (`Events/AgentStreamEvents.cs`), durable runtime + verify (`DurableTaskRuntime.cs`, `SemanticOutcomeRuntime.cs`), MCP client (`Mcp/Remote/`) — abstraction เหล่านี้พร้อมต่อยอดทั้ง 8 หมวด

### 6.2 สถาปัตยกรรมเป้าหมาย — [ข้อเสนอ]

- **Resilience layer กลาง** (`IActionResiliencePolicy`): wrap ทุก computer-use tool ด้วย adaptive-wait + bounded-retry + read-back-classification เดียวกัน → ลบความไม่สม่ำเสมอ (แก้ปัญหา #1)
- **App-class tier resolver**: จำแนกหน้าต่างเป้าหมาย (browser / terminal / IDE / other) → ผูกกับ permission เพื่อทำ tier แบบ Claude (แก้ #2, หมวด 1)
- **Live artifact host**: artifact + data-source + refresh scheduler (ใช้ `TaskSchedulerService` ที่มี) (แก้ #3, หมวด 6)
- **Browser tool expansion**: `browser_execute_js`, `browser_file_upload`, `browser_console`, `browser_network`, `browser_gif` (แก้ #4, หมวด 2)
- **Typed subagent registry**: search / plan / general roles (แก้ #5, หมวด 5)

### 6.3 Roadmap: Now / Next / Later

**NOW (สัปดาห์นี้ — แก้ X แดง + อ่านรู้เรื่อง):**
| Item | หมวด | Effort | DoD |
|---|---|---|---|
| R3 read-back-not-available → amber | 2 (reliability) | S | Notepad RichEdit ที่พิมพ์ลงจริง ไม่ขึ้น X แดงอีก |
| R1 adaptive readiness wait | 2 | S | แอปหนักไม่ fail เพราะ 2s |
| R5 + UI human-error layer | 3 | S | ทุก FailureCode มีประโยคไทย + expander ดิบ |
| UI: retrying state + grouping | 3 | M | X แดงซ้ำยุบเป็นการ์ดเดียว "ลองใหม่ n/m" |

**NEXT (2-4 สัปดาห์ — ยกความครบหมวดอ่อน):**
| Item | หมวด | Effort | Dependency |
|---|---|---|---|
| R2 typing ladder ใน computer_type + R4 runtime auto-retry | 1/2 | M | resilience layer |
| Browser: execute_js + file_upload + console/network | 2 | M | extension bridge (มี) |
| App-class tier resolver + permission | 1 | M | ScreenContext (มี) |
| Typed subagent roles | 5 | M | SpawnAgentTool (มี) |

**LATER (>1 เดือน — feature ใหญ่):**
| Item | หมวด | Effort |
|---|---|---|
| Live artifacts (data-source + refresh) | 6 | L |
| Teach mode (record-and-replay) | 1 | L |
| Browser GIF creator | 2 | M |

### 6.4 Quick wins (1-2 วัน, ผลมาก)

1. **R3** — read-back-not-available เป็น amber ไม่ใช่แดง (`ComputerTypeTool.cs:132-151`) → **ตัด X แดงปลอมทันที**
2. **R1** — ขยาย readiness timeout เป็น adaptive (`ComputerTypeTool.cs:83`)
3. **R5+UI** — human-error map + expander (`AgentExecutionViewModel.cs` + `MessageBubble.xaml`)
4. ยืนยัน `WebFetchTool` ห่อ `UntrustedObservationBoundary` (ปิดช่องที่ audit เก่าชี้)

---

## 7. คำถามถึงผู้ใช้ (ก่อนลงมือทำจริง)

Audit เสร็จแล้ว — **ยังไม่แตะโค้ด** ตามกติกา ผมแนะนำเริ่มจาก **Quick wins R3 + R1 + R5/UI** เพราะเห็นผลกับ X แดงในภาพภายใน 1-2 วัน

**จะให้ลงมือเฟสไหนก่อน?**
- **(ก) Reliability quick wins** — R3 (amber แทนแดง) + R1 (adaptive wait) + R5 (human error) ← *แนะนำ*
- **(ข) UI redesign** — retrying state + grouping + human-error layer
- **(ค) เติมความครบหมวดอ่อน** — browser JS/upload หรือ live artifacts
- **(ง) อื่น ๆ** ตามที่ระบุ

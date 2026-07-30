# SRAN Desktop — Phase 4: Documents + MCP Isolated Workers

> วันที่: 2026-07-28 · สถานะ: **PARTIAL PASS**
> Documents = ผ่านจริง · MCP isolated stdio worker = **ยังไม่ทำ (รายงานตรง ๆ)**
> ยังไม่ commit / ไม่ push ตามคำสั่ง
>
> หมายเหตุชื่อไฟล์: `SRAN_PHASE4_RESULT.md` เป็นของ Phase 4 คนละสาย
> (UIA-first retry-engine, ปิดไปแล้ว 2026-07-25) จึงไม่ทับ

---

## 1. Executive Result

| หัวข้อ | ผล |
|---|---|
| Shared worker framework | **ผ่าน** — dispatch/classification/quota/cleanup ทำงานจริงบน AppContainer + Job Object |
| Document Worker | **ผ่าน** — process แยกจริง, DOCX/XLSX/PPTX สร้าง+แก้ได้, reopen+validate ก่อน publish |
| MCP Worker | **ไม่ผ่าน — ยังไม่ได้สร้าง** (ดูข้อ 9); local stdio ยัง fail-closed เหมือนเดิม |
| Phase 4 โดยรวม | **PARTIAL PASS** |
| Security Sign-off | **ไม่เปลี่ยน — ยังไม่ผ่าน** (privileged symlink 4 ตัวยัง skip จาก Phase 3.1) |
| Regression | **ผ่าน** — .NET 1498 passed / 0 failed / 4 skipped, UiTests 5/5, solution build ผ่าน |
| พร้อม Phase 5 | **ยังไม่ควร** — MCP ครึ่งหลังของ Phase 4 ยังค้าง |

**ข้อค้นพบที่เปิดทางให้ทั้ง phase:** probe พิสูจน์ว่า `dotnet.exe` รันได้ใน capability-free
AppContainer (exit=0, คืน `8.0.422`) → worker เป็น .NET ได้จริง ไม่ต้องเขียน native

---

## 2. Runtime Path Matrix

| Entry point | Old path | New path | E2E verified | Bypass เหลือ |
|---|---|---|---|---|
| Create DOCX/XLSX/PPTX (agent) | ไม่มี tool เลย | `document` tool → runtime → isolated worker → reopen → atomic publish | **ผ่าน** | 0 |
| Edit DOCX/XLSX/PPTX (agent) | ไม่มี | เหมือนบน + backup + rollback | **ผ่าน** | 0 |
| Inspect/validate document | ไม่มี | worker reopen → untrusted observation | ผ่าน | 0 |
| Inspect PDF | ไม่มี | worker structural parse (ไม่ execute JS/action) | ผ่าน (validator unit) | 0 |
| **Plan fence (:::excel-plan ฯลฯ) จาก UI** | **`PlanBlockViewModel` → `ILocalPlanExecutor` รัน OpenXML ใน UI process, เขียนไฟล์ตรง, ประกาศ `PlanValidationDto(true,…)` แบบ hardcoded** | `IIsolatedPlanExecutor` → worker → reopen+validate → ผลจริง | **ผ่าน** (E2E ผ่าน worker จริง) | 0 |
| Local MCP stdio | fail-closed (throw Blocked) | **ไม่เปลี่ยน** | — | 0 (ยัง fail-closed) |
| Remote MCP HTTP | untrusted observation + ไม่เคลม verified | + schema validation + namespace collision guard | ผ่าน (unit) | 0 |
| MCP tool schema | **schema พัง → downgrade เป็น `{"type":"object"}`** | schema พัง → **ตัด tool ทิ้ง** | ผ่าน (unit) | 0 |

---

## 3. สิ่งที่แก้จริง (defect ที่พิสูจน์ได้)

### D1 — document generation รันใน privileged UI process (P0, invariant #5)
`PlanBlockViewModel.TryExecuteLocallyAsync()` เรียก `ILocalPlanExecutor.ExecuteAsync` ตรง ๆ:
รัน OpenXML/ClosedXML ใน UI process, เขียนไฟล์เอง, ไม่มี AgentRun, ไม่มี verification
และสร้าง `PlanValidationDto(true, …)` — **ประกาศว่า valid โดยไม่ตรวจอะไรเลย**

แก้: ย้าย executor ทั้งสามตัวไปรันใน `SranDesktop.DocumentWorker` (source-link ไม่ใช่ reference —
worker ต้องไม่ลาก WPF/credential graph เข้ามา), เพิ่ม operation `plan.execute`,
VM เรียกผ่าน `IIsolatedPlanExecutor` แทน ผลลัพธ์ต้อง reopen ได้จริงจึงจะนับ
**ความเป็นส่วนตัวคงเดิม** (เนื้อหายังไม่ออกเครื่อง) แต่ไม่รันใน privileged process อีก

### D2 — MCP schema พังแล้ว downgrade เป็น permissive object (P1)
`McpConnectorService.GetCachedRemoteToolDefinitions()` catch `JsonException` แล้วแทนด้วย
`{"type":"object"}` = server ส่ง payload อะไรก็ได้ (เป็นข้อที่ prompt ระบุห้ามตรง ๆ)

แก้: `McpSchemaValidator` — schema พัง/ไม่ปลอดภัย → **ตัด tool ออก** ไม่แทนที่
+ กัน duplicate tool name ข้าม connector (ไม่ให้ตัวหลัง shadow ตัวที่ user เชื่อถือ)

**ความซื่อสัตย์:** validator เวอร์ชันแรกผมเข้มเกินไป — reject `{"type":"object"}`
ซึ่งเป็นวิธีประกาศ tool ที่ไม่รับ argument ตามสเปกจริง test เดิม
(`McpConnectorHealthSweepTests`) จับได้ แก้แล้วและเพิ่ม test ล็อกไว้

### D3 — worker scratch directory สะสม
`DocumentWorkerService` ลบแค่ leaf directory เหลือ shell ของ run/step/attempt ค้าง
(ปัญหาชนิดเดียวกับ sandbox job leak ที่แก้ใน Phase 3.1) แก้ด้วย prune ขึ้นไปจนถึง scratch root

### D4 — ClosedXML ปฏิเสธ scratch file ที่นามสกุลไม่ตรง
`out.xlsx.partial` เปิดไม่ได้ → เปลี่ยนเป็น `.sran-partial-out.xlsx` (คงนามสกุลจริง)

### D5 — OpenXML ต้องการ TEMP ที่ AppContainer redirect แต่ไม่สร้างให้
ทำให้ save สำเร็จแต่ reopen ล้มเหลว = ถูกตีความผิดว่าเอกสารเสีย
แก้ให้ worker สร้าง `Path.GetTempPath()` เองตอน start

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `Application/Models/IsolatedWorkerContracts.cs` | ใหม่ | worker request/response + `WorkerOutcomeClass` |
| `Application/Models/DocumentArtifact.cs` | ใหม่ | artifact lifecycle + validation report |
| `Application/Models/ConnectorTrust.cs` | ใหม่ | connector manifest + trust/hash policy |
| `Application/Interfaces/IIsolatedPlanExecutor.cs` | ใหม่ | แทน `ILocalPlanExecutor` ที่ UI เคยเรียกตรง |
| `Infrastructure/Workers/IsolatedWorkerHost.cs` | ใหม่ | dispatch + outcome classification |
| `Infrastructure/Workers/WorkerImageResolver.cs` | ใหม่ | เลือก image ด้วย capability ไม่ใช่ path |
| `Infrastructure/Workers/DocumentWorkerService.cs` | ใหม่ | staging, atomic publish, backup/rollback |
| `Infrastructure/Workers/IsolatedPlanExecutor.cs` | ใหม่ | plan fence ผ่าน worker |
| `Infrastructure/Mcp/Tools/DocumentTool.cs` | ใหม่ | document entry point เดียวผ่าน runtime |
| `Infrastructure/Mcp/Remote/McpSchemaValidator.cs` | ใหม่ | schema gate + namespace |
| `Infrastructure/Office/PptxBackbone.cs` | ใหม่ (แยกจาก executor) | แชร์ backbone ให้ worker source-link |
| `Infrastructure/Mcp/Tools/WindowsAppContainerProcessRunner.cs` | แก้ | `RunWorkerAsync` + stage worker image (allowlist ของ run_command ไม่ถูกขยาย) |
| `Infrastructure/Mcp/Tools/ProcessRunner.cs` | แก้ | `IsolatedWorkerLaunch` |
| `Infrastructure/Services/McpConnectorService.cs` | แก้ | ใช้ schema gate แทน downgrade |
| `Infrastructure/Office/LocalPptxPlanExecutor.cs` | แก้ | ใช้ `PptxBackbone` ร่วม |
| `Presentation/ViewModels/PlanBlockViewModel.cs` | แก้ | เรียก isolated executor แทน in-process |
| `Presentation/ViewModels/ChatViewModel.cs` | แก้ | wiring ตาม interface ใหม่ |
| `App/Hosting/AppHostBuilder.cs` | แก้ | DI ของ worker services + `DocumentTool` |
| `src/SranDesktop.DocumentWorker/` | ใหม่ (project) | worker exe: operations, validator, protocol |
| `Tests/Documents/DocumentWorkerIntegrationTests.cs` | ใหม่ | 7 integration ผ่าน sandbox จริง |
| `Tests/Mcp/McpSchemaValidatorTests.cs` | ใหม่ | 22 cases |
| `Tests/Mcp/ConnectorTrustPolicyTests.cs` | ใหม่ | 7 cases |
| `Tests/Security/AppContainerDotnetProbeTests.cs` | ใหม่ | environment probe |
| `Tests/Services/PlanBlockViewModel*Tests.cs` | แก้ | contract ใหม่ + E2E ผ่าน worker จริง |

---

## 5. Worker Isolation Evidence

| Scenario | Isolation | Quota | Cleanup | Result |
|---|---|---|---|---|
| DOCX create | AppContainer + Job Object, capability-free | deadline 2 นาที, output 2 MB | scratch ลบครบ | ผ่าน |
| XLSX create + formula | เดียวกัน | เดียวกัน | ครบ | ผ่าน |
| Edit + publish | worker เห็นเฉพาะ working dir ของตัวเอง | เดียวกัน | ครบ | ผ่าน |
| Worker image หาย | — | — | — | `CapabilityUnavailable` (fail closed, ไม่มี in-process fallback) |
| output นอก workspace | ปฏิเสธก่อน dispatch | — | — | `WorkspaceAccessDenied`, ไม่มีไฟล์ถูกสร้าง |
| Cancel กลางคัน | — | — | scratch ลบครบ | `PostDispatchUnknownOutcome` + ต้อง reconcile |
| Network policy | ขอ network → ปฏิเสธ | — | — | `NetworkPolicyUnsupported` (fail closed) |

หลัง full regression: sandbox job dirs = **0**, document scratch = **0**, worker process ค้าง = **0**

---

## 6. Document Evidence

| Format | Scenario | Action | Reopen | Validation | Result |
|---|---|---|---|---|---|
| DOCX | create (heading/paragraph/table) | worker | ✅ | contains_text + min_tables | **ผ่าน** (test เปิดไฟล์ยืนยันเองอีกชั้น) |
| DOCX | edit replace_text + publish | worker | ✅ | contains + absent_text | **ผ่าน** + backup เก็บไว้ |
| DOCX | edit ที่ validation ไม่ผ่าน | worker | ✅ | ExpectedTextMissing | **ไฟล์เดิมไม่ถูกแตะเลย** (byte-equal) |
| XLSX | create + formula + cell readback | worker | ✅ | sheets + cell value | **ผ่าน** |
| PPTX | create (title/bullets) | worker | ✅ | slides/blank/out-of-bounds | ผ่าน (CLI verified) |
| PDF | inspect | worker | ✅ | signature/xref/pages/encrypted | ผ่าน (validator unit) |

Validator เป็น deterministic ล้วน: reopen ด้วย parser จริง, blank slide, shape นอกขอบสไลด์,
sheet/cell ที่คาดหวัง, PDF ที่ประกาศ JavaScript/EmbeddedFile/Launch → **รายงานแต่ไม่ execute**

---

## 7. UnknownOutcome Evidence

| จุด | สภาพ | ผล |
|---|---|---|
| worker เขียน response ไม่ทัน | exit 0 แต่ไม่มี response.json | `PostDispatchUnknownOutcome` + `WorkerResponseMissing` (exit code ไม่นับเป็นหลักฐาน) |
| response ของ attempt เก่า | idempotency key/attempt ไม่ตรง | `stale_response=true` → UnknownOutcome |
| protocol version ไม่ตรง | — | UnknownOutcome |
| cancel หลัง dispatch | — | UnknownOutcome + `RequiresReconciliation=true` |
| worker บอกสำเร็จแต่ไม่มีไฟล์ | — | UnknownOutcome (`WorkerOutputMissing`) ไม่ใช่ clean failure |
| publish ล้มเหลวกลางทาง | — | UnknownOutcome + พยายาม restore backup + รายงานผล restore |

`DocumentTool` แปลง UnknownOutcome เป็น `ToolResult.FailedEffect(retryable:false)`
→ runtime ต้อง reconcile ก่อน retry (ไม่มี blind retry)

---

## 8. Security Result

| ข้อ | ผล |
|---|---|
| ไม่มี host fallback | ✅ worker image หาย = fail closed; allowlist ของ `run_command` ไม่ถูกขยาย |
| ไม่ inherit secret | ✅ worker ได้ env allowlist เดิม + request มีเฉพาะ field ที่ worker ต้องใช้ |
| ไม่ persist raw token | ✅ ไม่มี credential ใน worker path |
| launch เฉพาะ first-party image | ✅ เลือกด้วย **capability name** ไม่ใช่ path จาก payload; `output_file`/`source_file` ที่ payload ส่งมาถูก **ทับ** ด้วยชื่อที่ host กำหนด |
| ไม่มี corrupt artifact ที่นับเป็นสำเร็จ | ✅ validation ไม่ผ่าน → ไม่ publish, ลบ scratch |
| PDF active content | ✅ รายงานว่ามี แต่ไม่ execute/extract/follow |
| MCP output = untrusted | ✅ (ของเดิม) + schema gate ใหม่ |

---

## 9. Tests

| Test group | Passed | Failed | Skipped | Notes |
|---|---:|---:|---:|---|
| .NET ทั้งหมด | 1498 | 0 | 4 | skip = privileged symlink (Phase 3.1, ไม่มีสิทธิ์) |
| Document worker integration | 7 | 0 | 0 | **รัน worker จริงใน sandbox** |
| MCP schema validator | 22 | 0 | 0 | รวม zero-arg regression |
| Connector trust policy | 7 | 0 | 0 | รวม swapped-binary |
| Plan block VM (รวม E2E ผ่าน worker) | 9 | 0 | 0 | |
| WPF UiTests | 5 | 0 | 0 | |
| Solution build | ผ่าน | 0 | | รวม DocumentWorker project |

**Flake ที่พบและรายงานตรง ๆ:** `DurableTaskRuntimeTests.Non_Actionable_Request_Downgrades_Loudly_But_Still_Answers`
fail 1 ครั้งใน full run แล้วผ่านเมื่อรันซ้ำและรันเดี่ยว — ยังไม่ได้หาสาเหตุ (pre-existing, ไม่เกี่ยวกับ Phase 4)

---

## 10. สิ่งที่ **ไม่ได้ทำ** — MCP Isolated Worker

**สถานะ: ไม่ได้เริ่ม** local MCP stdio ยัง fail-closed เหมือนก่อน Phase 4

**เหตุผลทางเทคนิคที่ตรวจแล้ว:** MCP stdio ต้องการ **bidirectional interactive stdio** กับ process
ที่อยู่ยาว แต่ `WindowsAppContainerProcessRunner.LaunchLowboxAsync` ปิด `stdinWrite`
ทันทีหลัง `AssignProcessToJobObject` → ปัจจุบันเขียน stdin ไม่ได้เลย
ต้องเขียน transport ใหม่ที่: คง stdin เปิด, จัดการ framing, lifetime, cancellation,
crash detection, reconnect — งานก้อนใหญ่ระดับเดียวกับ Document Worker ทั้งก้อน

**สิ่งที่ทำแทนและใช้ได้จริง:** schema validation, namespace collision guard,
connector trust manifest + hash invalidation — ทั้งหมดไม่ต้องรอ transport

**ยังไม่มี:** initialize/capability negotiation, tools/list, tools/call, resources/*, prompts/*,
cancellation, progress notification, reconnect, mutation reconciliation, OAuth broker,
malicious MCP fixtures, MCP benchmark

---

## 11. Benchmark

| แหล่ง | tasks | verified | false success | corrupt | duplicate | recovered | user intervention |
|---|---:|---:|---:|---:|---:|---:|---:|
| Document worker (integration, sandbox จริง) | 7 | 7 | 0 | 0 | 0 | 1 (cancel→unknown) | 0 |
| Document worker (CLI probe) | 5 | 4 | 0 | 0 | 0 | 1 (validation reject) | 0 |
| Plan fence E2E ผ่าน worker | 1 | 1 | 0 | 0 | 0 | 0 | 0 |
| MCP schema/trust (unit) | 29 | 29 | 0 | — | — | — | — |
| **MCP protocol/mutation** | — | — | — | — | — | — | **Not Run** |
| **Live provider / external connector** | — | — | — | — | — | — | **Not Run** |

**จำนวน sample ต่ำกว่าเป้าที่ prompt ตั้งไว้มาก** (เป้า 15 ต่อ format + 20 read-only MCP ฯลฯ)
ห้ามตีความเป็น production reliability

---

## 12. Eight-Capability Impact

| Capability | Direct improvement | Reusable primitive | Still missing |
|---|---|---|---|
| Computer Use | — | outcome classification ใช้ร่วมกัน | (ตาม Phase 3.1) |
| Browser Automation | — | UnknownOutcome semantics เดียวกัน | (ตาม Phase 3.1) |
| Filesystem & Shell | worker ใช้ broker + AppContainer เดิม; scratch prune | isolated worker framework | privileged symlink gate |
| **Documents** | **create/edit/reopen/validate/atomic publish/rollback ใน isolation** | artifact lifecycle, validator, atomic publish | render-to-image verification, PDF create, compare/rollback operation |
| Subagents | — | worker dispatch ใช้ซ้ำได้ | document task ของ subagent ยังไม่ทดสอบ |
| Scheduled & Live | — | เดียวกัน | scheduled document job ยังไม่ทดสอบ |
| **MCP** | schema gate, namespace guard, trust+hash invalidation | ConnectorTrustPolicy, McpSchemaValidator | **isolated stdio worker ทั้งก้อน** |
| Web | — | untrusted observation boundary (เดิม) | — |

---

## 13. Remaining Risks

**P0**
1. MCP isolated stdio worker ยังไม่มี → local connectors ใช้ไม่ได้ (fail-closed ปลอดภัย แต่ฟีเจอร์ขาด)
2. Security Sign-off ยังไม่ผ่านจาก Phase 3.1 (privileged symlink 4 ตัว)

**P1**
3. Render-to-image verification ยังไม่มี (validator เป็น structural ล้วน — จับ blank/out-of-bounds ได้ แต่ไม่จับ visual overflow จริง)
4. Document benchmark sample ต่ำกว่าเป้ามาก
5. Scheduled/subagent document path ยังไม่มี E2E
6. Flake ที่ยังไม่ได้หาสาเหตุ (ข้อ 9)
7. PPTX edit มีโค้ดแต่ยังไม่มี integration test

**Environment limitation**
8. ไม่มี Developer Mode, จอเดียว, ไม่มี provider credential (คงเดิมจาก Phase 3.1)

---

## 14. Final Verdict

1. **Documents ใช้ isolated worker จริงหรือไม่** — ใช่ process แยกจริงใน AppContainer + Job Object
2. **DOCX/XLSX/PPTX/PDF ผ่านแค่ไหน** — DOCX/XLSX create+edit+reopen+validate ผ่านครบ; PPTX create+validate ผ่าน (edit มีโค้ดแต่ยังไม่มี integration test); PDF inspect/validate เท่านั้น (สร้างไม่ได้)
3. **Original ปลอดภัยเมื่อ crash หรือไม่** — ใช่ พิสูจน์ด้วย byte-equality หลัง edit ที่ validation ไม่ผ่าน + backup ทุกครั้งที่ทับไฟล์เดิม
4. **MCP local stdio ใช้ได้โดยไม่รัน privileged process หรือไม่** — **ยังใช้ไม่ได้** (fail-closed เหมือนเดิม)
5. **MCP protocol รองรับอะไรจริง** — remote HTTP เดิม + schema gate + namespace guard + trust/hash เท่านั้น; ไม่มี local stdio, ไม่มี resources/prompts/progress
6. **MCP mutation disconnect เกิด duplicate หรือไม่** — **ยังไม่ได้ทดสอบ** (ไม่มี worker)
7. **OAuth/credential isolation ผ่านหรือไม่** — **ยังไม่ได้ทำใน Phase นี้**
8. **มี document/MCP path ใด bypass runtime หรือไม่** — document: ไม่มีแล้ว (UI path ปิดแล้ว); MCP: ไม่มี เพราะ local ยัง fail-closed
9. **false success พบหรือไม่** — 0 ใน suite ที่รันได้ และปิดช่องที่เคยมี 2 จุด (hardcoded `PlanValidationDto(true)`, schema downgrade)
10. **Security Sign-off ได้หรือยัง** — **ยัง** ไม่เปลี่ยนจาก Phase 3.1
11. **ใกล้ Claude-like ขึ้นด้านใด** — เอกสารเป็น artifact ที่มี lifecycle จริง: ทำในที่ปลอดภัย → เปิดตรวจใหม่ → ยืนยัน → ค่อย publish และ "ไม่รู้ผล" ไม่ถูกกลบเป็นสำเร็จหรือความล้มเหลว
12. **พร้อม Phase 5 หรือไม่** — **ยังไม่ควร** ควรปิด MCP isolated worker ก่อน
13. **Blocker 3 อันดับแรก** — (1) interactive stdio transport สำหรับ AppContainer, (2) privileged Windows VM, (3) render verification + benchmark sample

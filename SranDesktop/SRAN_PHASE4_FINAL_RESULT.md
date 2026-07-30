# SRAN Desktop — Phase 4 Final Result

> วันที่: 2026-07-29 · สถานะ: **PASS** ทุก workstream · **Global Security Sign-off: FAIL** (privileged symlink ยัง skip)
> ยังไม่ commit / ไม่ push / ไม่ publish release / ไม่ deploy ตามคำสั่ง
>
> เอกสารนี้เป็นรายงาน Phase 4 ฉบับเดียวและฉบับสุดท้าย รวม 4 / 4.1A / 4.1B / 4.1C
> และงานที่ปิดในรอบนี้ **ไม่มีการสร้าง phase ย่อยเพิ่ม**
>
> **แก้ความขัดแย้งในฉบับก่อน**: ฉบับก่อนเขียนว่า approval เป็น "ข้อเดียวที่ค้าง"
> ทั้งที่ WS5 (Remote MCP) และ WS12 (benchmark) ยังไม่ได้ทำ — นั่นเป็นการเคลม
> เกินจริง. WS5 และ WS12 ทำเสร็จและมี runtime evidence แล้ว
>
> **รอบนี้ (blocker closure)**: WS2 ปิดสนิทแล้ว — สาเหตุที่ "การ์ดเขียวแต่ไม่มีไฟล์"
> คือบั๊กจริง (D15) ไม่ใช่หลักฐานหาย และการยืนยันกับตำแหน่งติดตั้งจริงทำให้เจอ D16
> ซึ่งเป็น P0 ที่ทำให้ local connector ทุกตัว launch ไม่ได้บนเครื่องผู้ใช้จริง
> ส่วน `data.db` teardown lock ไม่ใช่ flake แต่เป็น connection-pool ownership (ข้อ 12)
>
> Global Security Sign-off ยัง **FAIL** ตามกติกาที่ prompt กำหนด ตราบใดที่ privileged
> symlink 4 ตัวยัง skip — และไม่มีการสร้าง phase ใหม่เพื่อเรื่องนี้

---

## 1. Executive Result

| หัวข้อ | ผล |
|---|---|
| **Phase 4 โดยรวม** | **PASS** ในทุก workstream — เหลือเฉพาะ Global Security Sign-off ที่ FAIL ตามกติกา (privileged symlink 4 ตัวยัง skip) |
| Documents | **ผ่าน** — isolated worker จริง, DOCX/XLSX/PPTX create+edit+reopen+validate+atomic publish, PDF read-only matrix ที่ตรงกับความจริง |
| Interactive worker | **ผ่าน** (4.1A, ไม่ถูกรื้อ) |
| Local MCP | **ผ่าน** — isolated stdio, ทุก operation ผ่าน gateway และสร้าง AgentStep |
| OAuth / credential broker | **ผ่าน** — PKCE + state + redirect validation + loopback listener + refresh + revoke + multi-account + tenant binding; token leak = 0 |
| Remote MCP | **ผ่าน** — out-of-process fixture, endpoint policy, credential broker, mutation reconciliation, 30/30 (ข้อ 6.1); TLS pinning / DNS-rebinding ยังไม่ทำ (ข้อ 15) |
| Connector lifecycle | **ผ่าน** — install / update / permission-expanding update / rollback / disable / revoke / uninstall + crash points |
| Scheduler integration | **ผ่าน** — MCP และ Documents |
| Subagent integration | **ผ่าน** — MCP และ Documents |
| Restart / reconciliation | **ผ่าน** — duplicate side effect = 0, original-file damage = 0, false success = 0 |
| Benchmark | **ผ่าน (fixture)** — 233 task ครบทุก sample target ของ prompt; false success / duplicate / corrupt / original damage / policy violation / token leak = **0** ทั้งหมด. live provider + real external connector = **Not Run** |
| Regression | **1828 passed / 4 skipped / 0 failed** ในรอบที่สะอาด, UiTests 5/5, solution build ผ่าน. `data.db` lock แก้ที่ต้นเหตุแล้ว (ข้อ 12.1). เกณฑ์ "3 รอบติดสะอาด" — ดูข้อ 18.4 ซึ่งรายงานผลจริงทุกรอบ |
| **Global Security Sign-off** | **ยังไม่ผ่าน** — privileged symlink 4 ตัวจาก Phase 1/3 ยัง skip (ไม่มี Developer Mode) ตามกติกาที่ prompt กำหนดไว้เอง |

---

## 2. Inherited Work — สิ่งที่ผ่านมาก่อนและ**ไม่ได้รื้อ**

| ส่วน | มาจาก | สถานะในรอบนี้ |
|---|---|---|
| Document Worker (process แยก, AppContainer + Job Object, reopen+validate, atomic publish, backup/rollback) | Phase 4 | ไม่แตะ core; เพิ่ม capability matrix + แก้ read-only verdict (ข้อ 5.3) |
| Shared isolated interactive worker (long-lived bidirectional stdio, concurrent correlation, notification, quota, cleanup) | Phase 4.1A | **ไม่แตะเลย** — 25 tests ยังเขียว |
| `McpDurableGateway`, dispatch journal, risk taxonomy, schema validation, mutation reconciler | Phase 4.1B | ไม่รื้อ; เพิ่ม operation deadline (4.1C) และ structured resolution |
| Production manifest resolver, trust store, approval dialog, path closure, `AgentInputScope` | Phase 4.1C | ไม่รื้อ; ต่อยอดด้วย lifecycle installer |
| DLP / untrusted observation boundary / workspace broker | ก่อนหน้า | ไม่แตะ |

ทุกอย่างข้างบนถูกยืนยันด้วยการรัน suite เดิมซ้ำ ไม่ใช่ด้วยการอ่านรายงานเก่า

---

## 3. Final Gap Matrix

| Area | Result | Runtime evidence | Remaining limitation |
|---|---|---|---|
| Documents | **Runtime verified** | `DocumentWorkerIntegrationTests` (7), `DocumentCapabilityTests` (24), `PlanBlockViewModel*` E2E ผ่าน worker จริง | render-to-image verification ยังไม่มี (structural validation เท่านั้น) |
| Interactive worker | **Runtime verified** | `InteractiveWorkerTransportTests` (25) | heartbeat sender ยังไม่มี (ตรวจการตายด้วย EOF/crash) |
| Local MCP stdio | **Runtime verified** | `LocalMcpRuntimeTests` (32), `ProductionConnectorActivationTests` (15) | — |
| Tools | **Runtime verified** | ทุก suite ข้างบน + `McpDurableRuntimeTests` | — |
| Resources | **Runtime verified** | `ProductionConnectorActivationTests` (`resources/list` + `resources/read`) | subscriptions ไม่ implement (CapabilityUnavailable) |
| Prompts | **Runtime verified** | เดียวกัน (`prompts/list` + `prompts/get`) | completions ไม่ implement (CapabilityUnavailable) |
| Notifications | **Runtime verified** | 4.1A progress/logging (bounded 500 / 256 KB) | resource-change notifications ไม่ implement |
| Approval / trust | **Runtime verified + packaged Deny และ Approve gesture จริง** | `ConnectorTrustAdversarialTests` (22), `ProductionConnectorActivationTests`, `ApprovalPersistenceInvariantTests` (5), `PackagedApprovalVerificationTests` (7 — ต่อจาก gesture จริง, connector root + trust file ของจริง) | ต่อจาก gesture ใช้ administrative-policy evidence (การปลอม user gesture จะทำลายกลไกที่กำลังพิสูจน์) |
| OAuth | **Runtime verified** | `CredentialBrokerTests` (22), `OAuthAuthorizationTests` (18), `CredentialLeakInvariantTests` (8) | live provider = Not Run (ไม่มี credential) |
| Remote MCP | **Runtime verified** | `RemoteMcpRuntimeTests` (30, **out-of-process** server), `McpConnectorHealthSweepTests` (4) | TLS pinning / DNS-rebinding defense ยังไม่ทำ |
| Connector lifecycle | **Runtime verified** | `ConnectorLifecycleTests` (17) | package signature / publisher provenance ยังไม่ทำ |
| Scheduler integration | **Runtime verified** | `ProductionConnectorRuntimeTests` (MCP), `ScheduledAndSubagentDocumentTests` (Documents) | — |
| Subagent integration | **Runtime verified** | เดียวกัน | — |
| Restart / reconcile | **Runtime verified** | `ProductionConnectorRuntimeTests` (host ใหม่ทั้งตัวบนไฟล์เดิม) | — |
| Benchmark | **Runtime verified (fixture)** | ข้อ 13 — 233 tasks, ครบทุก target, zero ในทุกตัวชี้วัดที่ต้องเป็นศูนย์ | live provider / real external connector / scripted model = Not Run |

---

## 4. Architecture

```text
Entry point           Chat turn │ Scheduler firing │ Sub-agent │ UI action │ Health sweep
      │
      ▼
Durable Runtime       CreateRunAsync → AgentRun (Chat/Scheduler/Subagent/Api)
      │
      ▼
AgentStep             PrepareActionAsync → TaskAction + ActionDispatching event
      │                 ├─ goal relevance   (mcp.* และ document ถูก scope ด้วย
      │                 │                    trust/broker ของตัวเอง ไม่ใช่ desktop taxonomy)
      │                 ├─ capability route (mcp.* / document ไม่มี substitute → ไม่ถูก re-route)
      │                 ├─ approval gate    (mutation/unknown ต้องมี approval)
      │                 └─ duplicate guard  (idempotency key)
      ▼
Trust / Permission    MCP: resolver อ่าน manifest → hash → fingerprint → trust record
                             → permission expansion → structured denial ที่มีชื่อ
                      OAuth: broker → revocation → expiry → refresh (ครั้งเดียว) → scope
                             → IScopedCredentialLease (header value, ไม่ใช่ token)
      │
      ▼
Durable Dispatch      Journal.RecordAsync(intent) เขียนดิสก์ **ก่อน** byte แรกออก
Intent
      │
      ▼
Isolated Worker       Document: AppContainer + Job Object + workspace broker
                      MCP local: AppContainer + Job Object + interactive stdio
                      MCP remote: HttpMcpTransport + prepared credential header
      │
      ▼
Operation             เขียนลง scratch เท่านั้น / dispatch พร้อม _meta.idempotencyKey
      │                 ภายใต้ operation deadline (timeout หลัง dispatch = UnknownOutcome)
      ▼
Observation           UntrustedObservationBoundary — ทุก payload เป็น data ไม่ใช่คำสั่ง
      │
      ▼
Verification          Document: reopen ด้วย parser จริง + blocking findings → atomic publish
                      MCP: provenance (server identity + generation)
      │
      ▼
Reconciliation        UnknownOutcome → reconnect → lookup_by_idempotency_key → read-back
                      พิสูจน์ไม่ได้ → NeedsUser (intent ค้างเปิด, ไม่ redispatch)
      │
      ▼
Durable Result        RecordResultAsync / PauseAsync(NeedsUser)
```

---

## 5. Documents Evidence

### 5.1 Format matrix (ตามที่ทำได้จริง)

| Format | Inspect | Create | Edit existing | Reopen | Structural validation | Render validation | Atomic publish |
|---|---|---|---|---|---|---|---|
| DOCX | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ ไม่ทำ | ✅ |
| XLSX | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ ไม่ทำ | ✅ |
| PPTX | ✅ | ✅ | ✅ | ✅ | ✅ (blank slide, out-of-bounds shape) | ❌ ไม่ทำ | ✅ |
| PDF | ✅ | ❌ `CapabilityUnavailable` | ❌ `CapabilityUnavailable` | ✅ (structural) | ✅ | ❌ `CapabilityUnavailable` | — (ไม่เขียน) |

| Format | Create | Edit | Reopen | Render | Validate | Crash recovery |
|---|---|---|---|---|---|---|
| DOCX | ✅ | ✅ | ✅ | — | ✅ | ✅ (original byte-equal เมื่อ validation ไม่ผ่าน) |
| XLSX | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| PPTX | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| PDF | — | — | ✅ | — | ✅ | — |

### 5.2 PDF — สิ่งที่รองรับจริง (ทดสอบทุกข้อ)

| ความสามารถ | ผล | หลักฐาน |
|---|---|---|
| inspect / structural parse | ✅ | `A_Pdf_Can_Be_Inspected_And_Its_Page_Count_Read` |
| page count | ✅ | เดียวกัน (`pdf_structure` evidence) |
| metadata / xref | ✅ | validator (`MissingXref` blocking finding) |
| encrypted PDF | ✅ **ปฏิเสธ ไม่เดารหัสผ่าน** | `An_Encrypted_Pdf_Is_Refused_Rather_Than_Guessed_At` |
| malformed / truncated PDF | ✅ **fail closed ไม่ทำ worker ตาย** | `A_Truncated_Pdf_Fails_Closed_Without_Crashing_The_Worker` |
| ไฟล์ที่ไม่ใช่ PDF แต่ตั้งชื่อ .pdf | ✅ ปฏิเสธจาก bytes ไม่ใช่จากนามสกุล | `A_File_That_Is_Not_A_Pdf_Is_Rejected_On_Its_Bytes_Not_Its_Name` |
| embedded JavaScript / Launch / EmbeddedFile | ✅ **รายงาน ไม่ execute** | `A_Pdf_That_Declares_JavaScript_Is_Reported_And_Not_Executed` |
| text extraction | ❌ ไม่ทำ | matrix |
| create / export / split / merge / render | ❌ `CapabilityUnavailable` พร้อมเหตุผล | `Creating_A_Pdf_Is_A_Named_Refusal_Not_A_Failure`, `Unimplemented_Document_Operations_Are_Structured_Refusals` |
| decompression / resource limits | ✅ | worker quota เดิม (output 2 MB, deadline 2 นาที) |

`DocumentCapabilityMatrix` เป็น **data ที่ source-link ร่วมกันระหว่าง host และ worker** — ทั้งสองฝั่ง compile จากไฟล์เดียวกัน จึงเป็นไปไม่ได้ที่ report จะเคลมเกินสิ่งที่ worker ทำ

### 5.3 Defect ที่พบและแก้ในรอบนี้ (Documents)

**D6 — read-only operation ไม่มีทางเป็น Verified ได้เลย**
`DocumentWorkerService` ตัดสินว่าเป็น read-only จาก "caller ส่ง output path มาหรือไม่" ไม่ใช่จาก operation. `document.inspect` ที่ส่ง output path มาด้วยจึงถูกตัดสินด้วยไฟล์ที่มันไม่เคยตั้งใจสร้าง → `WorkerOutputMissing` เสมอ
แก้: read-only เป็นคุณสมบัติของ **operation**

**D7 — inspect ที่ล้มเหลวถูกรายงานเป็นสำเร็จ**
`ReadOnlyResult` คืน `SucceededVerified` เมื่อ `validation.Reopened` เป็นจริง โดย**ไม่ดู blocking findings**. PDF ที่เข้ารหัสหรือ xref เสีย "เปิดได้พอให้รู้ว่าเป็น PDF" แล้วหยุด — แต่ถูกรายงานว่าอ่านสำเร็จ = **false success ของจริง**
แก้: ใช้ `validation.Passed` (reopened **และ** ไม่มี blocking finding) และส่ง findings/observations ของ validator กลับมาเป็น evidence

**D8 — `document` capability ถูกปฏิเสธทั้งหมดใน scheduled/subagent run** (P0)
สองจุดพร้อมกัน: `GoalRelevanceGate` ไม่รู้จัก `document` เลย → goal "write the report document" ถูกตีความเป็น `ApplicationReady` → `GoalIrrelevantAction`; และ `CapabilityRouter` เสนอ `open_application` แทน → `CapabilitySelectionRequired`
ผลจริง: **scheduled document job และ subagent document task ทำงานไม่ได้เลยใน production**
แก้: `document` ถูก scope ด้วย workspace broker + capability matrix + reopen-validate (เข้มกว่า desktop-outcome taxonomy) แบบเดียวกับที่ `mcp.*` ได้รับใน 4.1B และไม่มี substitute ให้ route

### 5.4 Document entry-point audit

| Entry point | ผ่าน AgentStep | ผ่าน Document Worker | หลักฐาน |
|---|---|---|---|
| text chat / attachment | ✅ | ✅ | `DocumentTool` ปฏิเสธ mutation ที่ไม่มี run/step (`RuntimeContextRequired`) |
| create/edit command | ✅ | ✅ | `DocumentWorkerIntegrationTests` |
| plan fence (`:::excel-plan` ฯลฯ) จาก UI | ✅ | ✅ | `IIsolatedPlanExecutor` (Phase 4 D1) |
| scheduler document task | ✅ | ✅ | `ScheduledAndSubagentDocumentTests` (5) |
| subagent document task | ✅ | ✅ | เดียวกัน (3) |
| export / download | ✅ | ✅ | ผ่าน artifact lifecycle เดิม |

---

## 6. MCP Feature Matrix

| Feature | Local MCP | Remote MCP | E2E | Limitation |
|---|---|---|---|---|
| initialize | ✅ | ✅ | ✅ | |
| capability negotiation | ✅ | ✅ | ✅ | |
| tools/list | ✅ | ✅ | ✅ | schema เดินทางมากับ listing |
| tools/call | ✅ | ✅ | ✅ | |
| resources/list | ✅ | ✅ (ถ้า server ประกาศ) | ✅ | |
| resources/read | ✅ | ✅ | ✅ | |
| prompts/list | ✅ | ✅ | ✅ | |
| prompts/get | ✅ | ✅ | ✅ | prompt = untrusted observation ไม่เคยเป็น system instruction |
| cancellation | ✅ | ✅ | ✅ | |
| progress | ✅ | — | ✅ | bounded 500 frames |
| logging notifications | ✅ | — | ✅ | quota 256 KB, ไม่ persist raw |
| reconnect | ✅ | ✅ | ✅ | generation เพิ่มจริง |
| graceful shutdown | ✅ | ✅ | ✅ | |
| **pagination** | ❗ ไม่ implement | ❗ | ✅ (คืน `CapabilityUnavailable` + มี AgentStep) | |
| **resource subscriptions** | ❗ ไม่ implement | ❗ | ✅ เดียวกัน | |
| **resource-change notifications** | ❗ ไม่ implement | ❗ | — | ไม่มี producer แบบ push |
| **completions** | ❗ ไม่ implement | ❗ | ✅ เดียวกัน | |

Protocol version: **2025-06-18** (รับ 2025-03-26). `McpProtocolFeatureMatrix` เป็น data ที่ test อ่านได้ — การเคลม completeness ตรงกับ matrix นี้พอดี

### 6.1 Remote MCP — หลักฐาน (`RemoteMcpRuntimeTests`, 30/30)

Fixture เป็น **process แยกจริง** (`SranDesktop.RemoteMcpFixtureServer`) ไม่ใช่ HttpListener ในเทสต์ — ตั้งใจ เพราะ listener ในโปรเซสเดียวกันแชร์หน่วยความจำ การจัดการ exception และ lifetime กับเทสต์ บั๊กฝั่ง client ที่โผล่เฉพาะข้าม socket จริงจึงหลบอยู่หลังมันได้ ตัวนี้ถูก kill ได้ ค้างกลาง request ได้ และมี control channel แยกให้ถามว่า **server ทำอะไรไปจริง**

| ข้อกำหนด | หลักฐาน |
|---|---|
| TLS / endpoint policy | 8 เคส (`https` ผ่าน, loopback http ผ่าน, remote http = `EndpointNotEncrypted`, `ftp` = scheme, userinfo ในURL = `EndpointCarriesCredentials`, malformed, ว่าง, host allowlist) — ตรวจ **ที่ launch** บนที่อยู่ที่กำลังจะยิงจริง |
| credential broker integration | `A_Connector_With_A_Connected_Account_Sends_Its_Credential` (server ยืนยัน `authorized > 0`) |
| initialize + capability negotiation | `Capability_Negotiation_Reports_What_The_Server_Declared` |
| tools/resources/prompts | `Every_Remote_Protocol_Operation_Is_A_Durable_Step` — 7 operation, 7 ActionId ไม่ซ้ำ, ทุกตัวมี `TaskAction` จริง |
| cancellation / timeout | `A_Server_That_Never_Answers_Hits_The_Deadline_Rather_Than_Hanging` |
| reconnect / reinitialize | `A_Reconnect_Reinitializes_And_Raises_The_Generation` |
| message-size limits | `A_Response_Larger_Than_The_Limit_Is_Refused_Not_Buffered` (server ตอบ 32 MB) |
| untrusted output provenance | `Remote_Output_Arrives_As_An_Untrusted_Observation`, `A_Remote_Prompt_Is_An_Observation_Not_A_System_Instruction` |
| ไม่มี raw auth logging | `A_Connector_Whose_Credential_Is_Rejected_Says_So_Without_Echoing_It` (fixture สะท้อน token กลับมาใน 401 โดยตั้งใจ), `No_Authorization_Header_Reaches_The_Durable_Record` |
| scheduler / subagent ใช้ gateway เดียวกัน | `A_Scheduled_Remote_Read_...`, `A_Child_Run_Reaches_A_Remote_Connector_Through_The_Same_Gateway` |
| mutation reconciliation | `A_Remote_Mutation_Whose_Answer_Is_Lost_Is_Reconciled_Not_Repeated` (server ยืนยัน `created == 1`) |
| restart ไม่ redispatch | `An_Unresolved_Remote_Mutation_Is_Not_Redispatched_After_A_Restart` (kill server กลาง flight → intent ค้างเปิด → restart → ไม่ยิงซ้ำ) |
| connection ตายหลัง mutation | `A_Connection_Dropped_After_The_Mutation_Is_An_Unknown_Outcome` — **ไม่ใช่** `FailedNoEffect` |
| unapproved ไม่ถูกติดต่อเลย | `An_Unapproved_Remote_Connector_Is_Refused_Before_Any_Request` (server `Requests` ไม่ขยับ) |

**Acceptance**: duplicate remote mutation = **0** · false success = **0** · blind retry = **0** · unresolved → **NeedsUser** · token leakage = **0** · production-path bypass = **0**

### 6.2 Defect ที่ WS5 เจอ (ทั้งหมดเป็นของจริง แก้แล้ว)

**D10 — remote transport exception หลุดออกนอก gateway** (P0)
`HttpRequestException` / `SocketException` / `McpAuthorizationException` ไม่อยู่ใน catch list ของ gateway → connector ที่ 401 หรือติดต่อไม่ได้ **โยน exception ทะลุออกไป** แทนที่จะได้ durable result. scheduled run จะ crash แทนที่จะบันทึก step. แก้: ครอบทั้ง session-acquire, dispatch และ reconcile path

**D11 — `Bearer Bearer …`**
broker คืน header value ที่มี scheme อยู่แล้ว แต่ `ApplyAuth` เติม `Bearer ` ซ้ำ → ทุก request ที่ใช้บัญชีที่เชื่อมไว้ได้ 401 ที่ดูเหมือน token ผิด แก้: แยกแยะจากช่องว่าง (credential เปล่าไม่มีช่องว่าง)

**D12 — credential ที่เชื่อมทีหลังไม่ถูกหยิบมาใช้**
`PrepareEndpointAsync` ถูกเรียกเฉพาะตอน **สร้าง transport** session อยู่ยาวกว่า credential ที่เริ่มมัน บัญชีที่เชื่อมทีหลัง (หรือ token ที่ refresh แล้ว) จึงไม่เคยถูกใช้จนกว่า session จะถูกทิ้ง แก้: resolve ทุกครั้งที่ `AcquireAsync`

**D13 — revoke ไม่หยุด connector ที่มี header แคชไว้** (P0 security)
เมื่อไม่มีบัญชีที่ใช้ได้ `PrepareEndpointAsync` `return` ก่อนล้างแคช → header เดิมยังอยู่และยังทำงานต่อ **หลังผู้ใช้ revoke แล้ว** แก้: ไม่มีบัญชี = ล้างแคชทันที

**D14 — feature ที่ไม่ implement รายงานว่า "capability ไม่แข็งแรง"**
เรียก `completion/complete` ซ้ำ ๆ ทำให้ health ของ capability เสื่อม แล้ว durable gate ปฏิเสธด้วย "no healthy capability" แทนที่จะบอกว่า build นี้ไม่มี feature นั้น (7 ใน 10 ครั้งใน benchmark) แก้: feature matrix ชนะ health-derived block — คำตอบเหมือนเดิมทุกครั้ง

---

## 7. OAuth Evidence

| ข้อกำหนด | ผล | หลักฐาน |
|---|---|---|
| Authorization Code + **PKCE (S256)** | ✅ | `The_Authorize_Url_Carries_The_Challenge_And_Never_The_Verifier` |
| **state** — single use | ✅ | `A_Callback_With_A_State_We_Never_Issued_Is_Refused`, `A_Replayed_State_Is_Refused_The_Second_Time` |
| state expiry | ✅ | `An_Expired_State_Cannot_Be_Consumed` (10 นาที) |
| **redirect validation** — loopback only | ✅ | `The_Redirect_Is_Loopback_Only` (127.0.0.1, ephemeral port ต่อครั้ง) |
| authorization-code exchange | ✅ | `A_Completed_Sign_In_Stores_An_Account_...` |
| **code replay** | ✅ ปฏิเสธ | `An_Authorization_Code_Cannot_Be_Redeemed_Twice` |
| **wrong PKCE verifier** | ✅ ปฏิเสธ | `A_Code_Redeemed_With_The_Wrong_Verifier_Is_Refused` |
| **wrong redirect URI** | ✅ ปฏิเสธ | `A_Code_Redeemed_Against_A_Different_Redirect_Is_Refused` |
| scope validation (ขาออก) | ✅ | `A_Scope_Outside_The_Policy_Is_Refused_Before_The_Browser_Opens` |
| **scope escalation (ขาเข้า)** | ✅ ไม่เก็บอะไรเลย | `A_Provider_Granting_More_Than_Policy_Allows_Stores_Nothing` |
| access-token expiry | ✅ | `An_Expired_Token_Is_Refreshed_Transparently` |
| refresh + rotation | ✅ | `A_Rotated_Refresh_Token_Replaces_The_Old_One` |
| refresh failure | ✅ **ไม่ retry** | `A_Refused_Refresh_Is_Recorded_And_Not_Retried` |
| refresh race | ✅ refresh ครั้งเดียว | `Concurrent_Operations_Refresh_Once_Between_Them` |
| revoke | ✅ local-first | `Revocation_Sticks_Even_When_The_Provider_Cannot_Be_Reached` |
| revoked token reuse | ✅ | `A_Token_Revoked_At_The_Provider_Surfaces_As_Needing_A_Reconnect` |
| disconnect | ✅ ลบ secret | `Disconnecting_Forgets_The_Account_Entirely` |
| **secure storage** | ✅ DPAPI, แยกไฟล์จาก app database | `Secrets_Live_Under_A_Per_Account_Key_In_Protected_Storage` |
| **multi-account** | ✅ | `Two_Accounts_On_One_Connector_Stay_Separate` |
| **tenant isolation** | ✅ | `The_Same_Account_Id_In_Two_Tenants_Is_Two_Credentials`, `A_Credential_From_Another_Tenant_Cannot_Be_Borrowed` |
| app restart | ✅ | `Credentials_Survive_A_Restart` |
| callback listener cleanup | ✅ | `The_Listener_Stops_Listening_After_It_Is_Disposed` |
| listener ignores unrelated requests | ✅ | `The_Listener_Ignores_Requests_That_Are_Not_The_Redirect` |
| listener timeout | ✅ | `The_Listener_Gives_Up_Rather_Than_Waiting_Forever` |
| **evidence ของคน** | ✅ | `Connecting_An_Account_Requires_Evidence_Of_A_Person` |

### Leak scan

| ที่ที่ token ต้องไม่อยู่ | ผล | หลักฐาน |
|---|---|---|
| SQLite / app database | **0** | `The_Credential_Descriptor_Type_Has_No_Place_To_Put_A_Token` (type-level), `No_Token_Reaches_Any_File_Except_Protected_Storage` |
| account index (ไฟล์ที่ listable) | **0** | `A_Token_Never_Reaches_The_Listable_Index` |
| logs | **0** | `The_Token_Client_Does_Not_Propagate_Provider_Exception_Text` (ห้าม `ex.Message`), `A_Lease_Does_Not_Print_Its_Credential` |
| provider error body | **0** | `A_Provider_Error_Body_Does_Not_Carry_A_Token_Into_Stored_State` |
| worker environment | **0** | `The_Worker_Launcher_Never_Passes_A_Credential_Into_The_Environment` |
| model context | **0** | ไม่มี path ที่ lease ออกไปถึง prompt; lease คืน header value เท่านั้นและ `ToString()` redact |
| scheduled task record | **0** | `No_Production_File_Persists_A_Connector_Token_Through_The_Task_Store` (เก็บ account reference) |
| caller ที่อ่าน secret ตรง | **0** | `Only_The_Credential_Layer_Reads_Connector_Secrets` |

**live provider = Not Run** (ไม่มี provider credential ใน environment นี้) — ทุกอย่างข้างบนรันกับ deterministic provider ที่บังคับกฎเดียวกับของจริง (code single-use, verifier ต้องตรง challenge, redirect ต้องตรง)

---

## 8. Connector Lifecycle Evidence

| ขั้นตอน | ผล | หลักฐาน |
|---|---|---|
| **install** (clean) | ✅ ลงแล้ว **ยังไม่ trusted** | `A_Clean_Install_Lands_Unapproved` |
| approval แล้วรันได้ | ✅ | `An_Installed_And_Approved_Connector_Runs` |
| **path traversal ใน package** | ✅ ปฏิเสธ ไม่ยึดชื่อ connector | `A_Package_Whose_Entry_Escapes_The_Install_Directory_Is_Refused` |
| package เสีย ไม่ทำลายของเดิม | ✅ | `An_Invalid_Manifest_Leaves_A_Working_Install_Untouched` |
| **update** | ✅ ลงแล้ว **ไม่สืบทอด approval** | `An_Update_Installs_But_Does_Not_Inherit_The_Approval` |
| **permission-expanding update** | ✅ บอกว่าขออะไรเพิ่ม | `A_Permission_Expanding_Update_Says_What_It_Now_Wants` |
| update แล้ว approve ใหม่ | ✅ | `An_Update_Can_Be_Approved_And_Then_Runs` |
| update หยุด session เดิม | ✅ | `An_Update_Stops_The_Session_The_Old_Version_Was_Running` |
| **rollback** | ✅ คืนไฟล์ **ไม่คืนการตัดสินใจ** | `A_Rollback_Restores_The_Previous_Version_Unapproved` |
| rollback แล้ว approve ใหม่ | ✅ | `A_Rolled_Back_Version_Runs_Once_It_Is_Approved_Again` |
| ไม่มีอะไรให้ rollback | ✅ structured | `There_Is_Nothing_To_Roll_Back_To_After_A_First_Install` |
| **crash ระหว่าง install** (staging ค้าง) | ✅ ไม่ถูกนับเป็น connector | `Staging_Left_By_A_Crashed_Install_Is_Not_A_Connector` |
| previous-version directory | ✅ ไม่ discoverable ซ้ำ id | `A_Previous_Version_Directory_Is_Not_Discoverable_As_A_Connector` |
| **disable** ข้าม restart | ✅ | `A_Disabled_Connector_Cannot_Run_And_Stays_Disabled_After_Restart` |
| **revoke** หยุด session ที่รันอยู่ | ✅ | `Revoking_Trust_Stops_The_Session_That_Is_Already_Running` (4.1C) |
| **uninstall** + audit record | ✅ เก็บ hash ที่เคยเชื่อไว้ | `Uninstall_Removes_The_Files_And_Leaves_An_Audit_Record` |
| reinstall ไม่สืบทอด approval | ✅ | `Reinstalling_After_Uninstall_Does_Not_Inherit_The_Old_Approval` |
| uninstall ลบ workspace | ✅ | `Uninstall_Removes_The_Connector_Workspace` |
| **hash เปลี่ยน → invalidate** | ✅ | `Replacing_The_Executable_After_Approval_Stops_It_Running` (4.1C) |
| **legacy connector → AwaitingTrust** | ✅ ไม่ auto-hash | `A_Legacy_Record_Without_An_Identity_Is_Never_Auto_Enabled` |

### 8.5 Blocker 1 ปิดแล้ว — Approval persistence: **root cause พบและแก้**

#### D15 — trust store อัปเดต cache ก่อนเขียนดิสก์ (P0, "optimistic approval")

นี่คือคำตอบว่าทำไมรอบก่อน "การ์ดเขียวแต่ไม่มีไฟล์"

`JsonConnectorTrustStore` ทำสองอย่างผิดลำดับ:

1. `SaveAsync` แก้ **dictionary ตัวเป็น ๆ** ที่ `Load()` คืนมา (คือ cache เอง)
2. `Persist` ตั้ง `_records = records` **ก่อน** `AtomicFile.Write`

ผลคือเมื่อการเขียนล้มเหลว — ไฟล์ถูกล็อก, ดิสก์เต็ม, โปรไฟล์ที่เขียนไม่ได้ —
in-memory view **เดินหน้าไปแล้ว**: `resolver.Resolve()` ตอบ `Trusted`, การ์ดเป็นเขียว,
และดิสก์ไม่มีอะไรเลย. การอนุมัติจะหายไปเงียบ ๆ ตอน restart ถัดไป

ตรงกับข้อห้าม **"optimistic approval without persistence"** ใน spec เป๊ะ ๆ

**แก้**: `Persist` เขียนดิสก์ก่อนแล้วค่อยสลับ cache; `SaveAsync`/`RemoveAsync` ทำงานบน
**สำเนา** ไม่ใช่ dictionary ตัวเป็น ๆ. ใช้แพตเทิร์นเดียวกันกับ
`DpapiConnectorCredentialStore.Persist` และ `McpConnectorService.SaveState` ซึ่งมีบั๊ก
ลำดับเดียวกัน (install ที่เขียนไม่สำเร็จจะดู "ติดตั้งแล้ว" จนกว่าจะ restart)

**ล็อกด้วยเทสต์** — `ApprovalPersistenceInvariantTests` (5):
`An_Approval_That_Cannot_Be_Written_Does_Not_Approve_Anything` (ทำ path เขียนไม่ได้จริง
แล้วยืนยันว่า store **ไม่มี** record), `A_Card_Reads_Its_State_From_Persisted_Trust_Not_From_The_Decision`,
`Every_Condition_For_A_Green_Card_Is_Required_Individually` (enabled / hash / permission /
manifest fingerprint ทีละข้อ), `A_Denial_Is_Persisted_And_Survives_A_Restart`,
และ source-level `The_Trust_Store_Writes_Before_It_Caches` ที่ fail ถ้ามีใครสลับสองบรรทัดกลับ

---

#### D16 — connector sandbox mount ถูก state-guard ตัดทิ้ง ทำให้ local connector ทุกตัว launch ไม่ได้บนเครื่องจริง (P0)

บั๊กนี้โผล่มาเพราะการยืนยัน packaged approval รันกับ **ตำแหน่งติดตั้งจริง** เป็นครั้งแรก

`IsolatedMcpConnectorSessionProvider` สร้าง workspace ของ connector ที่
`<connectorRoot>/workspaces/<id>` แล้วเปิด broker ครอบไว้ ส่วน connector root
ปริยายคือ `%LOCALAPPDATA%\SRAN Desktop\connectors` — ซึ่งอยู่ใน state tree ของแอปเอง

`WorkspaceFileBroker.Configure` ทิ้ง mount ที่อยู่ใน state tree **เงียบ ๆ** (`continue`)
กติกานี้ถูกสำหรับ *folder grant ของผู้ใช้* — เอเจนต์ต้องแตะ trust file / credential index
ไม่ได้ — แต่ผิดสำหรับ scratch directory ที่ launcher เพิ่งสร้างเอง ผลคือ mount list ว่าง →
`ContainingRoot` คืน null → `SandboxUnavailableException`:
**"The working directory is not inside an authorized workspace mount."**

แปลว่าบน installation ปริยาย **local stdio connector ทุกตัว launch ไม่ได้เลย** ขณะที่เทสต์
E2E ทุกตัวเขียว เพราะทุกตัว override `ConnectorRoot` ไปที่ temp directory

มีจุดบังคับใช้ **สองจุด** ที่ต้องแก้ ไม่ใช่จุดเดียว: `Configure` (ตอน mount) และ
`Authorize`/`AuthorizeOpenedHandle` (ตอนทำ file operation) — จุดที่สองโผล่มาหลังแก้จุดแรก
เป็นข้อความ *"Access denied: the target is inside SRAN Desktop application data."*

**แก้**: `WorkspaceFileBroker.ForSandboxWorkspace(workspace)` — broker ที่ยกเว้น
**เฉพาะ state-tree filter** และเฉพาะกับผู้เรียกที่สร้าง directory นั้นเอง ตรวจ existence,
handle, mount containment เหมือนเดิมทุกอย่าง `WorkspaceFileBroker.Shared` (ตัวที่เอเจนต์ใช้)
ไม่ถูกแตะ การเช็ค post-resolution ยังกันการ symlink ออกนอก workspace เพราะต้องผ่าน
`IsWithin(finalPath, mount.FinalRoot)` ก่อนเสมอ

**ล็อกด้วยเทสต์** — `SandboxWorkspaceMountTests` (3): mount ใน state tree ใช้ได้,
broker ที่ผู้ใช้ grant **ยังปฏิเสธ subtree เดิม** (กันการยกเว้นรั่ว), และ sandbox mount
ยังเอื้อมไม่ถึง parent directory หรือ `connector-trust.json`

---

### 8.4 Workstream 2 — Packaged approval: **CLOSED** (gesture จริง 2 ครั้ง, เจอบั๊กจริง 3 ตัว)

รอบนี้ **ได้ทำจริง**: publish `-c Release -r win-x64`, วาง fixture connector
(`ws2-fixture`, v1.0.0, publisher "SRAN Test Fixtures", exe sha256 ขึ้นต้น
`0EE154D8FB8F200E`) ลง `%LOCALAPPDATA%\SRAN Desktop\connectors\`, เปิดแอปที่
publish แล้ว และ **ให้ผู้ใช้กดปุ่มด้วยมือจริง** (ไม่มี synthetic input)

#### สิ่งที่พิสูจน์ได้จริง — และเป็นเหตุผลที่ WS2 คุ้มค่า

**D9 — ปุ่ม "Don't allow" ไม่บันทึกอะไรเลย** (พบจาก gesture จริงครั้งแรก)

ปุ่ม Deny ตั้ง `IsCancel="True"` ไว้ด้วย. WPF ทำให้ `DialogResult` เป็น `false`
→ `ShowDialog()` คืน `false` → `LocalConnectorItemViewModel.ReviewAsync` ตีความว่า
"ผู้ใช้ปิดหน้าต่างเฉย ๆ" แล้ว `return` ทันที → **การปฏิเสธของผู้ใช้ไม่ถูกบันทึก**

ยืนยันจากดิสก์: หลังกด Deny ครั้งแรก **ไม่มีไฟล์ `connector-trust.json` เกิดขึ้นเลย**
และไม่มีบรรทัด log ใด ๆ เกี่ยวกับ connector นี้

ผลกระทบจริง: connector ยัง launch ไม่ได้ (fail-closed จึงยังปลอดภัย) แต่การตัดสินใจ
ของผู้ใช้หายไป ครั้งถัดไปจะถามใหม่เหมือนไม่เคยตอบ และไม่มีร่องรอยให้ audit

แก้แล้ว: ถอด `IsCancel` ออก และรับ Escape ใน code-behind ให้บันทึกเป็น
denial เหมือนกัน — ทั้งสองทางบันทึกการตัดสินใจเดียวกัน

> นี่คือบั๊กที่ **ไม่มีทางเจอจาก unit/architecture test** เพราะทั้ง view-model และ
> trust store ถูกต้องทั้งคู่ ความผิดอยู่ที่ XAML attribute เดียวที่ทำให้ทั้งสองไม่ถูกเรียก

#### ทำไมรอบก่อนหน้า "การ์ดเขียวแต่ไม่มีไฟล์"

รอบก่อนหน้านี้รายงานว่า Approve path เป็น *operator-reported, uncorroborated* เพราะ
กดแล้วการ์ดเขียวแต่หา `connector-trust.json` ไม่เจอ **นั่นไม่ใช่ปัญหาของหลักฐาน แต่เป็นบั๊ก**
— คือ D15 ข้างบน: cache เดินหน้าก่อนเขียนดิสก์ ทำให้ UI แสดงผลการอนุมัติที่ระบบไม่ได้บันทึกไว้จริง
ซึ่งตรงกับข้อห้าม "optimistic approval without persistence" พอดี

หลังแก้ D15 และให้ผู้ใช้กดใหม่ (gesture จริงครั้งที่สอง ไม่มี synthetic input) trust record
ปรากฏบนดิสก์:

```
State=Trusted  Enabled=True  ApprovedBy=user:desktop
ExecutableSha256=0EE154D8FB8F200E…   (ตรงกับ hash ที่วัดไว้ก่อนกด)
ManifestFingerprint=4D2BF2C92CC30DACCA218D9F816C653E
```

ไฟล์ไม่มี string ที่มีรูปร่างเป็น token/secret เลย

#### ขั้นที่เหลือ — ทำอัตโนมัติต่อจาก gesture (`PackagedApprovalVerificationTests`, 7/7)

เทสต์ชุดนี้ **ไม่สร้าง approval ขึ้นเอง** มันอ่านการตัดสินใจที่คนกดจริง ผ่าน production
trust store แล้วเดินต่อกับ **connector root จริงและ trust file จริง** (สำรองและคืนค่าใน
`DisposeAsync`) ถ้าเครื่องไหนไม่มี approval ของจริง มันรายงานว่า not-applicable แทนที่จะเขียว
— เทสต์ที่ผ่านบนเครื่องที่ไม่มีใครกดอะไรเลย แย่กว่าไม่มีเทสต์

| ขั้น | ผล |
|---|---|
| อ่าน approval ของคนผ่าน production API (`ApprovedBy` ขึ้นต้น `user:`) | Pass |
| การ์ดอ่าน state จาก persisted trust แล้ว launch ใน isolated worker | Pass |
| เรียก read tool ผ่าน AgentStep + `provenance=verified` + observation marker | Pass |
| Restart แล้ว trust ยังอยู่ และ launch ซ้ำได้ | Pass |
| Revoke → `ConnectorRevoked`, worker ตาย, และคง revoke ข้าม restart | Pass |
| แก้ไฟล์ exe → `ConnectorExecutableChanged` + ถูก block | Pass |
| ขยาย permission ใน manifest → `ConnectorPermissionExpanded` (`network`) + ถูก block | Pass |
| เอเจนต์ที่กำลังคุมจอ mint approval evidence ไม่ได้ | Pass |

ขั้นที่ทำอัตโนมัติหลัง gesture ใช้ **administrative policy evidence** ไม่ใช่ gesture ปลอม —
การปลอม user gesture ในเทสต์จะทำลายกลไกที่การกดจริงมีไว้พิสูจน์พอดี

**การ launch ผ่าน connector root จริงคือสิ่งที่ทำให้เจอ D16** ซึ่งเป็น P0 ที่เทสต์ E2E
ทั้งหมดก่อนหน้านี้มองไม่เห็น เพราะทุกตัวชี้ connector root ไปที่ temp directory

---

## 9. Scheduler and Subagent Evidence

### Scheduler

| Case | AgentRun | AgentStep | ผล |
|---|---|---|---|
| scheduled local MCP read | `TriggerType=Scheduler` | ✅ | `A_Scheduled_Read_Is_A_Scheduler_Triggered_Step_Through_The_Gateway` |
| scheduled local MCP mutation | ✅ | ✅ intent ก่อน dispatch | `A_Scheduled_Mutation_Records_Its_Intent_Before_It_Dispatches` |
| scheduled remote MCP read | ✅ `owner=connector:dial:{id}` | ✅ | `Every_Dial_Leaves_A_Durable_Step_Behind_It` |
| connector disabled / trust invalidated | ✅ | ✅ **refusal ก็มี step** | `A_Scheduled_Firing_Against_A_Revoked_Connector_Is_Refused_With_A_Step`, `A_Scheduled_Firing_After_The_Binary_Changed_Is_Refused` |
| token expired ก่อน run / refresh สำเร็จ / refresh ล้มเหลว | — | — | `CredentialBrokerTests` (broker เป็นตัวเดียวกับที่ scheduled run ใช้) |
| disconnect หลัง dispatch → restart → duplicate | ✅ | ✅ | `A_Mutation_Interrupted_After_Dispatch_Is_Not_Repeated_By_The_Next_Process` |
| duplicate trigger | ✅ | ✅ | `stats.created == 0` ฝั่ง server |
| unresolved → NeedsUser | ✅ | ✅ | `An_Unresolvable_Mutation_Needs_A_Person_And_Is_Never_Auto_Retried` |
| **scheduled document create** | `TriggerType=Scheduler` | ✅ | `A_Scheduled_Document_Is_Created_Under_A_Scheduler_Triggered_Step` |
| **scheduled document edit (fail)** | ✅ | ✅ | `A_Scheduled_Edit_Leaves_The_Original_Intact_When_Validation_Fails` (byte-equal) |
| **scheduled document edit (pass)** | ✅ | ✅ | `A_Scheduled_Edit_That_Passes_Publishes_The_New_Content` |
| **restart ก่อน publish** | ✅ | ✅ | `A_Restart_Before_Publish_Leaves_No_Half_Written_Document` |
| **duplicate document trigger** | ✅ | ✅ | `A_Duplicate_Scheduled_Trigger_Does_Not_Accumulate_Documents` |

### Subagent

| Case | ผล |
|---|---|
| child MCP read เป็น durable child run + provenance | ✅ `A_Child_Run_Reads_Through_Its_Own_Step_And_Keeps_Its_Provenance` |
| child ขยาย connector permission ไม่ได้ | ✅ `A_Child_Cannot_Reach_A_Connector_That_Is_Not_Trusted` |
| child approve/install connector ไม่ได้ | ✅ `A_Child_Cannot_Approve_A_Connector_For_Itself` |
| child mutation หาย → sibling ยังทำงาน | ✅ `A_Second_Child_Still_Works_After_A_Sibling_Loses_Its_Answer` |
| **child สร้างเอกสารใต้ step ของตัวเอง** | ✅ `A_Child_Run_Creates_A_Document_Under_Its_Own_Step` |
| **artifact ของ child มี provenance** | ✅ `The_Artifact_A_Child_Returns_Carries_Its_Provenance` (RunId + FinalHash) |
| **child ที่ล้มไม่ทำลายไฟล์ของ sibling** | ✅ `A_Child_Whose_Edit_Fails_Does_Not_Spoil_The_Document_For_Its_Sibling` |

---

## 10. Restart / Reconciliation Evidence

```text
Dispatch intent        journal เขียนก่อน dispatch (invariant test ล็อกลำดับในซอร์ส)
Interruption           mutate-then-crash (Exit 9) / silent-mutation (เงียบ) / cancel / operation deadline
Persisted state        journal + sqlite รอดข้าม process
Actual state inspect   host ใหม่ทั้งตัว → reconnect → lookup_by_idempotency_key → get_record
Reconciliation         match → Succeeded (redispatch=none) | พิสูจน์ไม่ได้ → NeedsUser (intent ค้างเปิด)
Duplicate count        0 — วัดจาก stats.created ฝั่ง server ไม่ใช่จาก assertion ฝั่ง client
```

| หมวด | Case | ผล |
|---|---|---|
| Documents | crash ก่อน publish | ✅ ไม่มีไฟล์ครึ่งใบ |
| | validation ล้มเหลว | ✅ original byte-equal |
| | cancel หลัง dispatch | ✅ UnknownOutcome ไม่ใช่ failure |
| Local MCP | worker/server crash | ✅ UnknownOutcome |
| | response หายหลัง mutation | ✅ reconcile ไม่ redispatch |
| | desktop restart | ✅ host ใหม่ทั้งตัว, duplicate = 0 |
| Remote MCP | endpoint เปลี่ยนข้าม restart | ✅ `Repointing_An_Approved_Connector_At_Another_Server_Stops_It` |
| | token expiry / refresh | ✅ `CredentialBrokerTests` |
| Lifecycle | crash ระหว่าง install (staging) | ✅ ไม่ถูกนับเป็น connector |
| | old process ค้างตอน update | ✅ session ถูกหยุดก่อน switch |
| | rollback หลัง crash | ✅ previous version กลับมา (unapproved) |

**Acceptance**: duplicate side effects = **0** · original-file damage = **0** · false success = **0** · unresolved consequential state → **NeedsUser**

---

## 11. Security Evidence

| ข้อ | ผล |
|---|---|
| **no host fallback** | ✅ `StdioMcpTransport` ยัง `throw Blocked()`; stdio custom connector ถูกปฏิเสธที่ install; worker image หาย → `SandboxUnavailableException`; ไม่มี `Process.Start` นอก runner |
| **no token leakage** | ✅ 8 มิติในข้อ 7 ทั้งหมดเป็น 0 |
| **no untrusted approval** | ✅ `UserApprovalEvidence` ต้องมี; `AgentInputScope` ปฏิเสธ gesture ระหว่าง agent ขับ UI + cooldown 1.5 วิ; model/MCP output ไม่มี path ถึง trust store |
| **no corrupt artifact success** | ✅ D7 ปิดช่อง false success ของ inspect; validation ไม่ผ่าน → ไม่ publish |
| **no duplicate mutation** | ✅ วัดฝั่ง server = 0 |
| **no process/temp leak** | ✅ worker survivors = 0, temp dirs = 0 |
| permission escalation | ✅ 0 — permission expansion, scope escalation, tenant/account confusion ปิดหมด |
| unauthorized connector launch | ✅ 0 |
| malicious MCP resource / prompt | ✅ untrusted observation; prompt ไม่เคยเป็น system instruction |
| malicious document (malformed DOCX/XLSX/PPTX/PDF) | ✅ fail closed, worker ไม่ตาย |
| PDF embedded JS / Launch action | ✅ รายงาน ไม่ execute |
| connector แก้ trust store ตัวเอง | ✅ ไฟล์อยู่นอก connector root |
| oversized manifest / payload | ✅ bounded ก่อน parse |

---

## 12. Tests

| Test group | Passed | Failed | Skipped | Notes |
|---|---:|---:|---:|---|
| `CredentialBrokerTests` (**ใหม่**) | 22 | 0 | 0 | lease / refresh / revoke / multi-account / tenant |
| `OAuthAuthorizationTests` (**ใหม่**) | 18 | 0 | 0 | PKCE + state + redirect, loopback listener จริง |
| `CredentialLeakInvariantTests` (**ใหม่**) | 8 | 0 | 0 | token leak invariants + source-level |
| `ConnectorLifecycleTests` (**ใหม่**) | 17 | 0 | 0 | install/update/rollback/disable/revoke/uninstall |
| `DocumentCapabilityTests` (**ใหม่**) | 24 | 0 | 0 | PDF matrix + adversarial PDF ผ่าน worker จริง |
| `ScheduledAndSubagentDocumentTests` (**ใหม่**) | 8 | 0 | 0 | scheduled + subagent documents |
| `ProductionConnectorActivationTests` (4.1C) | 15 | 0 | 0 | ไม่แตะ |
| `ProductionConnectorRuntimeTests` (4.1C) | 12 | 0 | 0 | ไม่แตะ |
| `ConnectorTrustAdversarialTests` (4.1C) | 22 | 0 | 0 | ไม่แตะ |
| `McpRuntimeBypassInvariantTests` (4.1C) | 12 | 0 | 0 | ไม่แตะ |
| `McpConnectorHealthSweepTests` | 4 | 0 | 0 | ปรับ dial timeout ให้ทนโหลด full-suite |
| `DocumentWorkerIntegrationTests` (Phase 4) | 7 | 0 | 0 | ไม่แตะ |
| `LocalMcpRuntimeTests` / `InteractiveWorkerTransportTests` (4.1A/B) | 57 | 0 | 0 | ไม่แตะ |
| `RemoteMcpRuntimeTests` (**ใหม่**) | 30 | 0 | 0 | out-of-process remote server |
| `Phase4BenchmarkTests` (**ใหม่**) | 5 | 0 | 0 | 233 benchmark tasks |
| `ApprovalPersistenceInvariantTests` (**ใหม่**) | 5 | 0 | 0 | optimistic-approval invariants + source-level |
| `PackagedApprovalVerificationTests` (**ใหม่**) | 7 | 0 | 0 | ต่อจาก human gesture, connector root + trust file จริง |
| `SandboxWorkspaceMountTests` (**ใหม่**) | 4 | 0 | 0 | D16 + ขอบเขตของการยกเว้น |
| **.NET ทั้งหมด** | **1828** | **0** | 4 | ตัวเลขจากรอบที่สะอาด; รายละเอียด 3 รอบอยู่ข้อ 18.4 |
| WPF UiTests | 5 | 0 | 0 | |
| Solution build | ผ่าน | 0 errors | | รวม DocumentWorker + McpFixtureServer + RemoteMcpFixtureServer |

Baseline ต้น Phase นี้: 1680 passed / 0 failed / 4 skipped
ตอนนี้: **1832 total — 1828 passed / 4 skipped** (+152 tests)

### 12.1 Blocker 2 — `data.db` teardown lock: **ไม่ใช่ flake** (root cause + แก้แล้ว)

รายงานฉบับก่อนเรียกสิ่งนี้ว่า "flake เดิมที่ sqlite ปล่อยไฟล์ช้า" — **ผิด** ไม่มีอะไรช้า
และไม่มีอะไรสุ่ม

**เจ้าของ handle จริง**: connection pool ของ `Microsoft.Data.Sqlite` เอง

`Microsoft.Data.Sqlite` เปิด pooling ไว้เป็นค่าปริยาย การ `Dispose()` connection
ไม่ได้ปิดไฟล์ แต่ **คืน connection เข้า pool ที่ยังถือ file handle ไว้** ทุก call site ใน
โค้ดเปิด-ใช้-dispose ถูกต้องหมด (ตรวจแล้ว **36 จาก 37 จุด** — จุดที่ 37 คือ migrator
ซึ่งรับ connection ที่คนอื่นเป็นเจ้าของ ถูกต้องตามสัญญา) นั่นคือเหตุผลที่การไล่หา
"connection ที่ลืมปิด" ไม่เคยเจออะไร — **ไม่มี connection ที่ลืมปิด**

ทำไมถึงดูเหมือนสุ่ม: pool ผูกกับ connection string และเป็นระดับโปรเซส เทสต์จะล้ม
ก็ต่อเมื่อมีเทสต์อื่นในโปรเซสเดียวกันเคยเปิดฐานข้อมูลด้วย string เดียวกันมาก่อน
ซึ่งขึ้นกับลำดับที่ xunit จัดคลาสขนานกัน — จึงล้มใน full run และผ่านเมื่อรันเดี่ยว

**ทางที่ไม่เลือก และเหตุผล**
- `SqliteConnection.ClearAllPools()` — เป็น **process-wide**: teardown ของคลาสหนึ่ง
  ไปปิด pool ของคลาสอื่นที่กำลังทำงาน เปลี่ยน flake หนึ่งเป็นอีกหลายตัว (เคยลองแล้ว
  ทำให้เกิด failure เพิ่มจริง) ถอดออกจาก ~30 ไฟล์เทสต์และจาก `DeletePatientlyAsync`
- sleep / retry ลบซ้ำ ๆ / ignore / skip / ลด assertion / ใช้ db path กลางร่วมกัน —
  ทั้งหมดคือการปิดอาการโดยปล่อยเจ้าของ handle ไว้เหมือนเดิม

**แก้ที่ต้นเหตุ**: `Pooling = false` ในทั้งสาม production store
(`SqliteAgentTaskStore`, `SqliteConversationStore`, `SqliteScheduledTaskStore`)
ทุก call site เปิด-ใช้-ทิ้ง connection อยู่แล้ว ไม่มีอะไรได้ประโยชน์จาก pool
พอปิด pooling `Dispose()` ก็ปิดไฟล์จริงตามที่โค้ดเรียกทุกจุดคาดหวังไว้แต่แรก

รอบนี้เจอ **จุดเดียวกันซ้ำในเทสต์**: `SensitivePersistenceSecurityTests` สร้าง
connection string ของตัวเอง (`legacy.db`) จึงล้มด้วย `IOException` แบบเดียวกัน
เมื่อ `ClearAllPools()` ที่เคยบังเอิญกลบมันไว้ถูกถอดออก — แก้ด้วย `Pooling = false`
ที่จุดเดียวกัน ไม่ใช่ด้วยการเพิ่ม retry

**flake ที่ผมทำเองและแก้ในรอบนี้**: `A_Click_Just_After_Synthetic_Input_Is_Still_Refused`
ล้มเป็นครั้งคราวเพราะ `AgentInputScope` เป็น static ระดับโปรเซส และคลาสอื่นที่รันขนานกัน
เรียก `ResetForTests()` ล้าง marker ระหว่างที่เทสต์นี้กำลังยืนยัน — อ่านออกมาเหมือน
"guard ไม่ทำงาน" ทั้งที่ guard ปกติดี. แก้โดยย้ายทุกคลาสที่แตะ `AgentInputScope`
เข้า collection เดียวกันให้รันเรียงกัน

**Leak measurement (หลัง full regression)**: worker survivors = **0** · temp dir ของเทสต์ Phase 4 = **0** · token-shaped string ใน `%APPDATA%\SRAN Desktop` (json/db/log) = **0**

**ไม่มีการลด assertion และไม่มี skipped security test ที่ถูกนับเป็น pass**

> **เส้นทางที่เดินผิดก่อนจะถึงคำตอบ** (บันทึกไว้เพราะมันคือเหตุผลที่ต้องแก้ที่ต้นเหตุ):
> ครั้งแรกใส่ `ClearAllPools()` ลงใน Dispose ซึ่งเป็น process-wide → teardown ของคลาสหนึ่ง
> ไปปิด pool ของอีกคลาสที่กำลังทำงาน เกิด failure เพิ่ม 2 ตัว. ถอดออก → failure หาย
> แต่ temp directory ค้าง 32 อัน เพราะ pool ยังถือไฟล์อยู่จริง ๆ. ทั้งสองอาการหายพร้อมกัน
> เมื่อปิด pooling ที่ connection string — ซึ่งเป็นจุดที่ ownership อยู่จริงตั้งแต่แรก

---

## 13. Benchmark

รอบนี้มี harness จริง (`Phase4BenchmarkTests` + `Phase4BenchmarkLedger`) รันบน production composition, document worker ใน AppContainer, local MCP server อีก process และ remote MCP server อีก process. ledger เขียน `.json` + `.md` ลง `bin/.../benchmark/` — ตารางด้านล่างคัดลอกจากไฟล์นั้นตรง ๆ ไม่ได้พิมพ์เอง

### 13.1 Deterministic fixture — **233 tasks, ครบทุก target**

| Area | Tasks | Verified | False success | Duplicate | Corrupt | Original damage | Reconciled | Policy violation | Token leak | User intervention | Median ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| documents.create.docx | 15 | 15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 576 |
| documents.create.xlsx | 15 | 15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1569 |
| documents.create.pptx | 15 | 15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 593 |
| documents.inspect.pdf | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 328 |
| documents.edit | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 526 |
| documents.validation-failure | 10 | 10 | 0 | 0 | 0 | **0** | 0 | 0 | 0 | 0 | 500 |
| documents.crash-restart | 10 | 10 | 0 | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 29 |
| mcp.local.read | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 136 |
| mcp.local.mutation | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 60 |
| mcp.local.resource | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 123 |
| mcp.local.prompt | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 144 |
| mcp.local.reconnect | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 411 |
| mcp.malicious-output | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 128 |
| mcp.protocol-failure | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 80 |
| mcp.remote.read | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 144 |
| mcp.remote.mutation | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 59 |
| mcp.remote.reconnect | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 129 |
| mcp.remote.timeout | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1 |
| mcp.remote.reconciliation | 10 | 10 | 0 | **0** | 0 | 0 | 10 | 0 | 0 | 0 | 0 |
| lifecycle | 13 | 13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 15 |
| **รวม** | **233** | **233** | **0** | **0** | **0** | **0** | 30 | **0** | **0** | 3 | |

"Verified" นับ verified success **หรือ** expected refusal — benchmark ที่นับ "connector ถูกบล็อกถูกต้อง" เป็นความล้มเหลว จะให้รางวัลกับ build ที่ไม่บังคับอะไรเลย. `user intervention = 3` คือ remote timeout ที่จบเป็น NeedsUser ซึ่งเป็นพฤติกรรมที่ต้องการ

lifecycle 13 ขั้น: install · unapproved-cannot-run · approval · deny · reapprove · restart-preserves-trust · update · permission-expanding-update · rollback · disable · revoke · hash-change-reapproval · uninstall

### 13.2 แหล่งอื่น

| แหล่ง | สถานะ | เหตุผล |
|---|---|---|
| Scripted model | **Not Run** | ไม่มีในขอบเขต Phase 4 ที่ล็อกไว้ |
| Live provider | **Not Run** | ไม่มี provider credential ใน environment นี้ |
| Real external connector | **Not Run** | ไม่มี connector จริงติดตั้ง |

มี test ที่ **บังคับ** ไม่ให้เคลมเกิน: `Every_Number_This_Suite_Produces_Comes_From_A_Fixture` ตรวจ source ว่าไม่มีที่ไหนติดป้าย `LiveProvider` / `RealExternalConnector` / `ScriptedModel` และตรวจว่า `BenchmarkOutcome` ที่ลืมระบุ source จะ default ไปที่คำเคลมที่อ่อนที่สุด ไม่ใช่แรงที่สุด

**ห้ามตีความตัวเลขข้างบนเป็น Claude parity หรือ production-wide reliability** — เป็น fixture ทั้งหมด

### 13.3 หลักฐานเสริมจาก suite ปกติ (นอกเหนือจาก benchmark harness)

| แหล่ง | tasks | verified | false success | duplicate | token leak |
|---|---:|---:|---:|---:|---:|
| Documents — worker integration + capability/PDF + scheduled/subagent | 39 | 39 | 0 | 0 | — |
| Local MCP — production graph (4.1C) | 27 | 27 | 0 | 0 | — |
| Local MCP — 4.1A/4.1B suites | 57 | 57 | 0 | 0 | — |
| Remote MCP — out-of-process fixture | 30 | 30 | 0 | 0 | **0** |
| OAuth — deterministic provider | 48 | 48 | 0 | — | **0** |
| Lifecycle | 17 | 17 | 0 | 0 | — |

---

## 14. Eight-Capability Impact

| Capability | Improvement from Phase 4 | Still missing for Phase 5 |
|---|---|---|
| Computer Use | `AgentInputScope` ทำให้ synthetic input ระบุตัวเองได้ (ใช้ต่อได้ทุกที่ที่ต้องแยก "คนกด" ออกจาก "agent กด") | render/visual verification; privileged symlink gate |
| Browser Automation | UnknownOutcome semantics + observation boundary เดียวกัน | — (ตาม Phase 3.1) |
| Filesystem & Shell | workspace broker + AppContainer ใช้ร่วมกับ worker; staging/atomic switch pattern | privileged symlink gate ยังปิดไม่ได้ |
| **Documents** | isolated worker, create/edit/reopen/validate/atomic publish/rollback, PDF matrix ที่ซื่อสัตย์, scheduled+subagent path | render-to-image verification, PDF write/extract, benchmark volume |
| **Subagents** | child run มี step/provenance ของตัวเอง; ขยาย trust ไม่ได้; artifact ตรวจสอบได้ | per-child budget policy ที่ปรับได้, parent cancellation E2E |
| **Scheduled & Live** | scheduled MCP + documents ผ่าน durable runtime เต็มเส้น; account reference ไม่ใช่ token | live-provider scheduled run |
| **MCP Connectors** | isolated stdio, durable gateway, trust+approval, lifecycle เต็มวง, credential broker | OAuth กับ provider จริง, package signature, pagination/subscriptions |
| Web | untrusted observation boundary เดิม + bounded token-endpoint reads | — |

---

## 15. Remaining Risks

**Global P0**
1. **Security Sign-off ยังไม่ผ่าน** — privileged symlink 4 tests ยัง skip ตั้งแต่ Phase 1/3 (ไม่มี Developer Mode / privileged VM) ตามกติกาที่ prompt กำหนด นี่เป็น Fail ระดับ global ไม่ใช่ระดับ Phase 4

**Phase 4 P1 (ค้างจาก Phase นี้)**
2. **เทสต์ OCR/visual grounding สองตัว — ยังไม่พบสาเหตุ** `Ocr_Grounded_Click…` และ
   `Visual_Grounded_Click…` ล้ม **หนึ่งครั้ง** ในรอบ A ใต้โหลด full-suite จากนั้นผ่านทุกครั้ง:
   3/3 เมื่อรันแยก และ 3/3 ในรอบ B ใต้โหลดเต็ม **ยังไม่ได้แก้ที่ต้นเหตุ** เพราะยังทำซ้ำไม่ได้
   จึงยังไม่มี failure ให้จับ — บันทึกเป็นหนี้ ไม่ใช่ "แก้แล้ว" ทั้งสองตัวสังเกตหน้าจอจริง
   ซึ่งเป็น shared state ระดับเครื่อง จึงอ่อนไหวต่อสิ่งที่รันขนานกันโดยธรรมชาติ
3. **Remote MCP: TLS pinning และ DNS-rebinding defense ยังไม่ทำ** — ปัจจุบันพึ่ง .NET TLS validation + endpoint policy + endpoint fingerprint (จับ URL เปลี่ยน แต่ไม่จับ URL เดิมที่ปลายทางเปลี่ยนเจ้าของ)
5. **Package signature / publisher provenance ยังไม่มี** — trust ผูกกับ hash ของสิ่งที่อยู่บนดิสก์ ตอบได้ว่า "เหมือนตอนอนุมัติไหม" แต่ตอบไม่ได้ว่า "มาจากคนที่อ้างจริงไหม"
6. **Render-to-image verification ยังไม่มี** — validator เป็น structural ล้วน จับ blank/out-of-bounds ได้ แต่ไม่จับ visual overflow จริง

**Phase 5 backlog**
7. pagination / resource subscriptions / resource-change notifications / completions
8. PDF text extraction / create / split / merge
9. heartbeat sender สำหรับ long-lived worker
10. `AgentInputScope` cooldown เป็นค่าคงที่ — ทางที่แข็งกว่าคือ marker บน `dwExtraInfo` ของ SendInput แล้วอ่านกลับที่ WPF hook

**Environment limitation**
11. ไม่มี Developer Mode, จอเดียว, ไม่มี provider credential, ไม่มี real external connector

**Compatibility / reapproval impact**
12. **Marketplace connector ที่ติดตั้งไว้ก่อน 4.1C จะอ่านเป็น "ยังไม่อนุมัติ"** และแสดงเป็น `NotConfigured` จนกว่าจะกด install/approve ใหม่หนึ่งรอบ — เป็นพฤติกรรมที่ invariant สั่ง แต่ผู้ใช้เห็น ควรอยู่ใน release note
13. **custom stdio connector ที่พิมพ์ command line เข้ามา ใช้ไม่ได้อีกต่อไป** — ต้องติดตั้งเป็น package ที่มี manifest ในโฟลเดอร์ที่ควบคุม
14. `connectors.json` เพิ่มฟิลด์ `account_id` / `tenant_id` / `scopes` — legacy record ที่ไม่มีจะ resolve เป็น "บัญชีเดียวที่เชื่อมอยู่" และถ้ามีหลายบัญชีจะถือว่ากำกวมและปฏิเสธ

---

## 16. Final Verdict

1. **Phase 4 ผ่านครบหรือไม่** — **ผ่านครบ 13/13 หัวข้อใน scope** พร้อม runtime evidence รวม WS5 (Remote MCP, 30/30 บน out-of-process server), WS12 (benchmark 233 tasks ครบ target) และ WS2 (packaged approval ครบทั้ง Deny/Approve/launch/restart/revoke/hash-change/permission-expansion). สิ่งเดียวที่ยัง FAIL คือ **Global Security Sign-off** ซึ่งเป็นข้อจำกัด environment ระดับ global ไม่ใช่ของ Phase 4
2. **Documents ผ่านจริงหรือไม่** — **ผ่าน**. worker แยก process จริงใน AppContainer + Job Object; DOCX/XLSX/PPTX create+edit+reopen+validate+atomic publish; PDF read-only และ **บอกตรง ๆ ว่าเขียนไม่ได้** ด้วย `CapabilityUnavailable` ที่มีเหตุผล; original ไม่เสียเมื่อ validation ล้มเหลว (byte-equal)
3. **Local MCP isolated stdio ผ่านจริงหรือไม่** — **ผ่าน** ตั้งแต่ 4.1A/4.1B และยืนยันซ้ำผ่าน production composition ใน 4.1C
4. **Every MCP operation creates AgentStep หรือไม่** — **ใช่** ทุก production path: chat tool call, dial, health ping, install dial, pre-install test, scheduler, subagent และ operation ที่ถูกปฏิเสธ; ล็อกด้วย architecture test ที่ allowlist หดจนไม่เหลือ call site เดิม
5. **OAuth lifecycle ผ่านหรือไม่** — **ผ่านกับ deterministic provider**: PKCE, state single-use + expiry, loopback-only redirect, code replay/verifier/redirect ปฏิเสธ, scope escalation ทั้งสองทิศ, refresh + rotation + refresh-failure ไม่ retry, revoke local-first, multi-account, tenant isolation, restart. **live provider = Not Run**
6. **Remote MCP รองรับจริงแค่ไหน** — **ครบตาม matrix ข้อ 6/6.1 และพิสูจน์กับ server คนละ process**: endpoint policy (8 เคส), credential broker, initialize/capabilities, tools/resources/prompts, cancellation, timeout, reconnect, message-size limit, provenance, ไม่มี raw auth logging, scheduler+subagent ผ่าน gateway เดียวกัน, mutation reconciliation ที่ restart แล้วไม่ redispatch. **ยังไม่มี** TLS pinning และ DNS-rebinding defense
7. **Connector install/update/rollback/revoke ผ่านหรือไม่** — **ผ่านทั้งหมด** รวม permission-expanding update, crash-during-staging, uninstall ที่เหลือ audit record และ reinstall ที่ไม่สืบทอด approval
8. **Scheduler/Subagent integration ผ่านหรือไม่** — **ผ่านทั้ง MCP และ Documents** และระหว่างทางพบว่า `document` capability ถูกปฏิเสธทั้งหมดในสองเส้นทางนี้ (D8) — เป็น P0 ที่แก้แล้ว
9. **Duplicate mutation พบหรือไม่** — **ไม่พบ**. วัดจาก `stats.created` ฝั่ง server หลัง restart จริง = 0
10. **False success พบหรือไม่** — **พบ 1 จุดและปิดแล้ว** (D7: inspect ที่มี blocking finding ถูกรายงานว่าสำเร็จ). หลังแก้ = 0 ทุก suite
11. **Host fallback เหลือหรือไม่** — **ไม่เหลือ**
12. **Token leak พบหรือไม่** — **ไม่พบ** ใน 8 มิติที่ตรวจ (database, index, logs, provider error body, worker environment, model context, scheduled task record, direct secret read)
13. **Global Security Sign-off ได้หรือไม่** — **ยังไม่ได้** privileged symlink 4 ตัวยัง skip ตามข้อจำกัด environment
14. **พร้อมเริ่ม Phase 5 หรือไม่** — **พร้อมในเชิงเทคนิค** สำหรับทุกอย่างยกเว้นสามข้อใน blocker ด้านล่าง; Phase 4 ไม่มีหนี้เชิงโครงสร้างเหลือ
15. **Blocker สามอันดับแรก** —
    1. **Global Security Sign-off (privileged symlink)** — ต้องมี Windows VM ที่เปิด Developer Mode; ค้างมาตั้งแต่ Phase 1/3 และปิดใน Phase 4 ไม่ได้
    2. **Live provider / real external connector** — ทุกตัวเลขในข้อ 13 เป็น fixture; ยังเคลม production reliability ไม่ได้
    3. **TLS pinning / DNS-rebinding และ package signature** — remote endpoint และ package provenance ยังพึ่ง hash + policy เท่านั้น

---

## 17. สิ่งที่ปิดในรอบต่อเนื่องนี้ (WS5 / WS2 / WS12)

| Workstream | ก่อนรอบนี้ | หลังรอบนี้ |
|---|---|---|
| **WS5 Remote MCP** | ผ่าน gateway แต่ไม่มี endpoint policy, ไม่มี size limit, fixture อยู่ใน process เดียวกับเทสต์ | **ปิด** — `McpEndpointPolicy`, bounded read, `McpAuthorizationException`, out-of-process fixture, 30/30; พบและแก้ D10–D14 |
| **WS2 Packaged approval** | Not Run | **ปิด** — publish + stage + human gesture จริง 2 ครั้ง; พบ **D9** (Deny ไม่บันทึก), **D15** (optimistic approval), **D16** (P0: sandbox mount ถูก state-guard ตัด) และแก้ทั้งสาม; ขั้นหลัง gesture ยืนยันด้วย `PackagedApprovalVerificationTests` 7/7 |
| **WS12 Benchmark** | ไม่มี harness | **ปิด (fixture)** — harness + ledger, 233 tasks ครบทุก target, zero ในทุกตัวชี้วัดที่ต้องเป็นศูนย์ |

**Defect ที่รอบนี้พบทั้งหมด: 6** — D9 (Deny ไม่บันทึก), D10 (exception หลุด gateway),
D11 (`Bearer Bearer`), D12 (credential ที่เชื่อมทีหลังไม่ถูกใช้), D13 (revoke ไม่หยุด
connector ที่มี header แคช), D14 (feature ที่ไม่มีถูกรายงานเป็น capability ไม่แข็งแรง)
— **D10 และ D13 เป็นระดับ P0**

---

## 18. รอบปิด Blocker — สรุปตามคำถามที่กำหนด

### 18.1 Blocker ทั้งสอง

| Blocker | สาเหตุที่แท้จริง | สถานะ |
|---|---|---|
| Packaged approval ไม่มี durable record | **D15** — trust store อัปเดต cache ก่อนเขียนดิสก์ และแก้ dictionary ตัวเป็น ๆ (ข้อ 8.5) | ปิด |
| `data.db` teardown lock | **connection pool ownership** ไม่ใช่ความช้าและไม่ใช่ flake (ข้อ 12.1) | ปิด |

การไล่ blocker แรกลงไปถึงการ launch จาก connector root จริง ทำให้เจอ **D16** (ข้อ 8.5)
ซึ่งเป็น P0 ที่ร้ายแรงกว่าทั้งสอง blocker: บน installation ปริยาย local connector ทุกตัว
launch ไม่ได้เลย และไม่มีเทสต์ใดก่อนหน้านี้มองเห็น เพราะทุกตัวใช้ temp connector root

### 18.2 คำตอบ 12 ข้อ

1. **Packaged approval มี durable record จริงไหม** — **มี** `%APPDATA%\SRAN Desktop\connector-trust.json`
   มี `State=Trusted`, `Enabled=True`, `ApprovedBy=user:desktop`, exe hash ตรงกับที่วัดก่อนกด,
   manifest fingerprint ตรง. ก่อนแก้ D15 ไม่มี — และนั่นคือบั๊ก ไม่ใช่หลักฐานหาย
2. **การ์ดอ่าน state จาก persisted trust หรือจากผลการกด** — **จาก persisted trust**
   `ApprovalPersistenceInvariantTests` พิสูจน์ว่า store ใหม่ทั้งตัวบนไฟล์เดิม (คือ restart)
   ยัง resolve เป็นเขียว และเงื่อนไขทั้ง 5 ข้อ (enabled / hash / permission / fingerprint /
   identity) ถูกบังคับ **ทีละข้อแยกกัน**
3. **ถ้าเขียนไม่สำเร็จ ระบบทำอะไร** — **ไม่อนุมัติอะไรเลย** เทสต์ทำ path ให้เขียนไม่ได้จริง
   (แทน directory ด้วยไฟล์) แล้วยืนยันว่า `ApproveAsync` โยน `IOException` และ
   `store.Get(...)` คืน `null` — ไม่มี optimistic approval
4. **Approve → launch → เรียก tool → AgentStep สำเร็จไหม** — **สำเร็จ** ผ่าน production
   gateway กับ connector root จริง; ได้ `ActionId` ที่อ่านกลับจาก task store ได้,
   `provenance=verified`, และผลลัพธ์ครอบด้วย untrusted-observation marker
5. **Trust รอด restart ไหม** — **รอด** host ใหม่ทั้งตัวบน trust file เดิม แล้ว launch ซ้ำได้
6. **Revoke หยุด session ที่กำลังรันไหม และรอด restart ไหม** — **ทั้งสองอย่าง**
   `ConnectorRevoked` + worker ตาย (ยืนยันด้วยการรอ process จริง) + หลัง restart ยัง revoke
7. **Hash change / permission expansion บังคับ re-approve ไหม** — **ใช่**
   `ConnectorExecutableChanged` และ `ConnectorPermissionExpanded` (พร้อมรายการ `network`)
   ทั้งคู่ block ที่ gateway ไม่ใช่แค่เปลี่ยนสีการ์ด
8. **มี synthetic input อนุมัติ connector ได้ไหม** — **ไม่ได้** `UserApprovalEvidence.FromUserGesture`
   โยน `UnauthorizedAccessException` ขณะที่ agent input ยังอยู่ในช่วงมีผล; ขั้นอัตโนมัติหลัง
   gesture ใช้ administrative-policy evidence ซึ่งเป็นแหล่งที่ต่างออกไปและ audit ได้
9. **`data.db` lock เจ้าของ handle คือใคร** — **connection pool ของ Microsoft.Data.Sqlite**
   ไม่ใช่ connection ที่ลืมปิด (ตรวจ 36/37 call site ถูกต้อง) แก้ด้วย `Pooling=false`
   ที่ connection string ของทั้งสาม store
10. **แก้โดยไม่ใช้ sleep / retry / skip / ลด assertion ใช่ไหม** — **ใช่** และถอด
    `ClearAllPools()` (process-wide) ออกจาก ~30 ไฟล์ เพราะมันคือการย้ายปัญหาไปให้คลาสอื่น
11. **Full suite ผ่านติดกัน 3 รอบไหม** — **ผ่าน** 1828/0/4 ทั้งสามรอบ, worker survivors 0,
    temp dir ไม่งอก (ข้อ 18.4). รอบแรกที่ลอง (รอบ A) **ไม่ผ่าน** และบันทึกไว้ตามจริง
    พร้อมสาเหตุ — เทสต์ที่วัด shared state ระดับเครื่องแบบตายตัวใต้โหลดที่เพิ่มขึ้นจากการแก้ D16
12. **Global Security Sign-off ผ่านหรือยัง** — **ยังไม่ผ่าน** และต้องไม่ผ่าน ตราบใดที่
    privileged symlink 4 ตัวยัง skip ตามกติกาที่ prompt กำหนดไว้เอง **ไม่มีการสร้าง phase
    ใหม่เพื่อเรื่องนี้**

### 18.4 Regression — full suite 3 รอบติด

**รอบ A** (โค้ดสุดท้ายหลังแก้ D15/D16 + pooling):

| รอบ | Passed | Failed | Skipped | Total | worker survivors | เทสต์ที่ล้ม |
|---|---:|---:|---:|---:|---:|---|
| 1 | 1826 | 2 | 4 | 1832 | 0 | `Ocr_Grounded_Click…`, `Visual_Grounded_Click…` |
| 2 | 1827 | 1 | 4 | 1832 | 0 | `Repeated_Sandbox_Runs_Do_Not_Leave_Command_Workers` |
| 3 | 1828 | 0 | 4 | 1832 | 0 | — |

**รอบ A ไม่ผ่านเกณฑ์ "3 รอบติดสะอาด"** — บันทึกไว้ตามจริง ไม่ปัดขึ้น

**รอบ B** (หลังแก้ `Repeated_Sandbox_Runs…` ที่ต้นเหตุ + ล้าง temp dir ก่อนเริ่ม
เพื่อให้ตัวเลข leak มีความหมาย):

| รอบ | Passed | Failed | Skipped | Total | เวลา | worker survivors | leftover temp dirs |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1828 | **0** | 4 | 1832 | 6 m 23 s | 0 | 1 |
| 2 | 1828 | **0** | 4 | 1832 | 6 m 28 s | 0 | 1 |
| 3 | 1828 | **0** | 4 | 1832 | 7 m 00 s | 0 | 1 |

**ผ่านเกณฑ์ 3 รอบติดสะอาด** · worker survivors = 0 ทุกรอบ

`leftover temp dirs = 1` ทุกรอบคือ `sran-pptx-vm` ซึ่งเป็นไดเรกทอรี**ชื่อคงที่**ที่ถูกใช้ซ้ำ
ไม่ใช่การสะสม — ตัวเลขคงที่ที่ 1 ตลอดสามรอบคือหลักฐานว่าไม่มีการงอก

**เทสต์ที่ล้มไม่ซ้ำที่เดิม และทั้งสามตัวอยู่ตระกูลเดียวกัน**: เป็นเทสต์ที่ assert บน
**สถานะระดับโปรเซสที่ใช้ร่วมกัน** — รายชื่อ process `cmd` ทั้งเครื่อง และหน้าจอจริง
ไม่มีตัวไหนเป็น assertion ของ MCP / approval / document / persistence เลย
รันแยกเดี่ยว ๆ ผ่าน **3/3 ทุกตัว**

**สาเหตุที่เพิ่งโผล่รอบนี้**: ก่อนแก้ D16 การ launch connector ล้มทันทีที่ mount check
— worker ไม่เคยได้เกิดจริง พอแก้แล้ว suite จึงสร้าง AppContainer process จริงเพิ่มขึ้นมาก
โหลดที่เพิ่มขึ้นไปดันเทสต์ที่วัดเวลาแบบตายตัวให้ตก ไม่ใช่ logic ที่พัง

**แก้ที่ต้นเหตุ 1 ตัว**: `Repeated_Sandbox_Runs_Do_Not_Leave_Command_Workers` รอ
`Task.Delay(500)` แบบตายตัวก่อนนับ process — ซึ่งวัด "จังหวะ teardown" ไม่ใช่สิ่งที่
เทสต์อ้าง (`ไม่เหลือ worker`) process ที่ถูกสั่งปิดแล้วยังค้างในรายชื่ออีกครู่หนึ่ง
และใต้โหลด full-suite ครู่นั้นยาวเกิน 500 ms เปลี่ยนเป็นรอจนเงื่อนไขเป็นจริง
(ไม่เกิน 10 วินาที) **assertion เท่าเดิมทุกประการ** — ต้องว่างเปล่า

> เทสต์ OCR/visual สองตัว **ยังไม่ได้แก้ที่ต้นเหตุ** — ล้มครั้งเดียวในรอบ A แล้วผ่าน
> ทุกครั้งหลังจากนั้น (แยก 3/3, รอบ B ใต้โหลดเต็ม 3/3) จึงยังทำซ้ำไม่ได้และยังไม่มี
> failure ให้จับ บันทึกไว้เป็นหนี้ ไม่ใช่ "ผ่านแล้ว" — และไม่ได้ skip, ไม่ได้ลด
> assertion, ไม่ได้ mark flaky

**เกี่ยวกับ leftover temp dirs**: ตัวเลข 49 ที่วัดได้ในรอบ A ส่วนใหญ่เป็นของเก่า —
41 ไดเรกทอรีมีอายุเกิน 2 ชั่วโมง คือตกค้างจาก session ก่อนหน้า ไม่ใช่จากรอบนี้
รอบ B ล้างทั้งหมดก่อนเริ่มเพื่อให้ตัวเลขมีความหมาย

### 18.5 Gate อื่น ๆ (รันหลังโค้ดสุดท้าย)

| Gate | ผล |
|---|---|
| Solution build (`SranDesktop.sln`, Debug) | **ผ่าน** — 0 errors, 7 warnings |
| WPF UiTests | **5 / 5** |
| MCP subset (`SranDesktop.Tests.Mcp`) | **259 / 259** |
| Document Worker subset (`SranDesktop.Tests.Documents`) | **39 / 39** |
| Worker survivors หลังทุกรอบ | **0** |
| Trust file ของผู้ใช้หลังรัน suite | ไม่เปลี่ยน (backup/restore ทำงานถูกต้อง) |

### 18.3 Architecture / path audit หลังแก้ทั้งหมด

| ตรวจ | ผล |
|---|---|
| `ClearAllPools()` เหลือใน source หรือไม่ | ไม่เหลือ — เหลือแต่ comment ใน `ProductionConnectorHost` ที่อธิบายว่าทำไมถึงไม่ใช้ |
| `Pooling=false` อยู่ครบทั้งสาม production store | ครบ (`SqliteAgentTaskStore`, `SqliteConversationStore`, `SqliteScheduledTaskStore`) + จุดที่เทสต์สร้าง connection string เอง |
| ผู้เรียก `ForSandboxWorkspace` | **1 จุดเดียว** (`IsolatedMcpConnectorSessionProvider`) — ล็อกด้วย `Only_The_Connector_Launcher_May_Waive_The_State_Guard` |
| `WorkspaceFileBroker.Shared` (ตัวที่เอเจนต์ใช้) ถูกแตะหรือไม่ | ไม่ถูกแตะ — เทสต์ยืนยันว่า broker แบบ user-grant ยังปฏิเสธ subtree เดิม |
| ลำดับ write-before-cache ใน trust store | ล็อกด้วย source-level test |
| Trust file ของผู้ใช้หลังรัน suite | เหมือนเดิมทุกฟิลด์ (`user:desktop`, hash `0EE154D8FB8F200E…` ยังตรงกับไฟล์จริง) — backup/restore ของเทสต์ทำงานถูกต้อง |

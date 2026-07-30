# SRAN Desktop — Phase 4.1B: MCP Runtime Integration และ Mutation Reconciliation

> วันที่: 2026-07-28 · สถานะ: **PASS (ตามขอบเขตที่ระบุในข้อ 10)**
> ยังไม่ commit / ไม่ push ตามคำสั่ง
> เอกสารนี้ **เขียนทับ** รายงาน 4.1B ฉบับ PARTIAL PASS ที่เกิดจาก run ก่อนหน้า

---

## 0. Executive Result

| Acceptance gate | ผล |
|---|---|
| ทุก MCP operation สร้าง AgentStep | **ผ่าน** |
| `PrepareAction` / `RecordResult` ถูกใช้จริง | **ผ่าน** (ยืนยันทั้งพฤติกรรมและ architecture test) |
| Scheduler MCP E2E | **ผ่าน** |
| Subagent MCP E2E | **ผ่าน** |
| initialize / tools / resources / prompts E2E | **ผ่าน** |
| reconnect / reinitialize | **ผ่าน** (generation ใหม่จริง) |
| schema validation | **ผ่าน** (คงจาก run ก่อน) |
| no-argument schema ยังใช้ได้ | **ผ่าน** (คงจาก run ก่อน) |
| connector trust / hash enforcement | **ผ่าน** (7 กรณีตาม prompt + legacy migration + approve) |
| mutation disconnect reconciliation | **ผ่าน** |
| duplicate side effect | **0** (วัดจากฝั่ง server ไม่ใช่จาก assertion ฝั่ง client) |
| false success | **0** |
| unresolved mutation → NeedsUser | **ผ่าน** + intent ค้างเปิดข้าม restart |
| process / temp leak | **0** (job dirs 0, worker survivors 0, temp dirs 0) |
| host fallback | **0** |
| full regression | **ผ่าน** — 1625 passed / 0 failed / 4 skipped + UiTests 5/5 |

**ขอบเขตที่ต้องอ่านก่อนตีความว่า "ผ่าน"**: ทั้งหมดนี้ครอบคลุม **local MCP (stdio ใน AppContainer)** ซึ่งเป็นหัวข้อของ Phase 4.1A/4.1B. Connector แบบ **remote HTTP** ใน `McpConnectorService` **ยังไม่ได้ย้ายมาที่ durable gateway** — ดูข้อ 10.1

---

## 1. สิ่งที่ inherited จาก accidental partial implementation

run ก่อนหน้าข้ามเส้น phase ไปทำบางส่วนของ 4.1B แล้ว ของเหล่านี้ **ไม่ถูกรื้อ** และผ่าน regression เดิมทั้งหมด:

| ของที่มีอยู่แล้ว | สภาพตอนรับช่วง | ทำอะไรต่อ |
|---|---|---|
| `IsolatedStdioMcpTransport` (NDJSON JSON-RPC บน worker 4.1A) | ใช้งานได้ + 32 E2E ผ่าน | **ไม่แตะ** ใช้ต่อทั้งดุ้น |
| `McpRemoteClient` capability/version negotiation | ใช้งานได้ | เพิ่ม `ServerIdentity`, `NegotiatedProtocolVersion`, `_meta.idempotencyKey` |
| `McpSchemaValidator` | ใช้งานได้ | **ไม่แตะ** |
| `McpMutationReconciler` + `McpToolRiskPolicy` | reconciler ใช้ได้; risk policy เป็น heuristic ล้วน | reconciler ใช้ต่อ (เรียกจาก gateway); risk policy ถูก **แทนที่** ด้วย `McpRiskPolicy` ที่มี connector-policy validation |
| `ConnectorTrustPolicy` (Phase 4) | มี hash + state แต่ **ไม่เคยถูกเรียกก่อน launch** | ขยาย + ผูกเข้ากับ launch จริง |
| `SranDesktop.McpFixtureServer` | server จริง 10+ tools | เพิ่ม idempotency key, `lookup_by_idempotency_key`, `stats`, profile `mutate-then-crash`, backend seeding |
| `LocalMcpRuntimeTests` (32 tests) | เขียว | **ไม่แตะ** ยังเขียว 32/32 |

**ช่องว่างที่ inherited มาและเป็นเหตุผลที่ 4.1B ยังไม่ผ่าน:**
1. ไม่มี MCP operation ไหนเดินผ่าน `DurableTaskRuntime` เลย — ไม่มี AgentStep, ไม่มี PrepareAction/RecordResult
2. `ConnectorTrustPolicy` เป็น dead code — ไม่มีใครเรียกก่อน `StartInteractiveWorkerAsync`
3. ไม่มี dispatch-intent journal → restart แล้วไม่รู้ว่า mutation ไหนค้าง
4. scheduler / subagent ไม่มีเส้นทาง MCP ของตัวเอง
5. risk classification เป็น heuristic ล้วน ไม่มี connector policy validation และไม่แยก `IdempotentRead` / `LocalMutation`
6. unsupported protocol feature ไม่มี structured response — เงียบ

---

## 2. Phase 4.1B Gap Matrix (ประเมิน ณ ตอนรับช่วง)

| หัวข้อ | Implemented + runtime-verified | Implemented แต่ยังไม่ E2E | Partial | Not implemented | Unsupported by protocol version ที่เลือก |
|---|---|---|---|---|---|
| Interactive transport / isolation | ✅ (4.1A, 25 tests) | | | | |
| initialize + capability/version negotiation | ✅ | | | | |
| tools/list, tools/call | ✅ | | | | |
| resources/list, resources/read | ✅ | | | | |
| prompts/list, prompts/get | ✅ | | | | |
| progress / logging notification | ✅ | | | | |
| cancellation (transport + protocol) | ✅ | | | | |
| reconnect (transport generation) | ✅ | | | | |
| schema validation / namespacing | ✅ | | | | |
| Mutation reconciler (class เดี่ยว) | ✅ | | | | |
| Connector trust policy | | ✅ dead code | | | |
| **Durable runtime integration** | | | | ❌ | |
| **Dispatch intent journal / restart safety** | | | | ❌ | |
| **Scheduler MCP** | | | | ❌ | |
| **Subagent MCP** | | | | ❌ | |
| **Risk taxonomy 6 ชั้น + policy validation** | | | ⚠️ heuristic เท่านั้น | | |
| **Structured CapabilityUnavailable** | | | ⚠️ มีเฉพาะ capability ที่ server ไม่ประกาศ | | |
| pagination / subscriptions / resource-change / completions | | | | | ❗ ไม่ implement (2025-06-18 รองรับ แต่ architecture ยังไม่พร้อม) |
| remote HTTP connector ผ่าน durable path | | | | ❌ | |

---

## 3. สิ่งที่แก้เพิ่มเติมใน run นี้

### 3.1 ไฟล์ใหม่

| ไฟล์ | หน้าที่ |
|---|---|
| `Application/Models/McpRuntimeContracts.cs` | `McpOperationKind`, `McpRiskClass` (6 ชั้น), `McpOperationStatus`, `McpDispatchPhase`, `McpDispatchIntent`, `McpOperationRequest/Result`, **`McpProtocolFeatureMatrix`** (feature matrix เป็น data ที่ test อ่านได้ ไม่ใช่ prose) |
| `Application/Services/McpRiskPolicy.cs` | classification ที่ **connector policy เป็นผู้กำหนด**; server annotation ทำได้แค่ *เข้มขึ้น* เท่านั้น |
| `Application/Interfaces/IMcpDurableGateway.cs` | `IMcpDispatchJournal` + `IMcpDurableGateway` (Execute / ExecuteOwned / ExecuteAttached / ReconcileOpenIntents) |
| `Infrastructure/Mcp/Remote/McpDispatchJournal.cs` | journal ไฟล์ atomic เขียน **ก่อน** dispatch |
| `Infrastructure/Mcp/Remote/McpDurableGateway.cs` | **ประตูเดียว** ของ MCP ทั้งระบบ |
| `Infrastructure/Mcp/Remote/IMcpConnectorSessionProvider.cs` | session + generation + server identity |
| `Infrastructure/Mcp/Remote/IsolatedMcpConnectorSessionProvider.cs` | launcher (manifest → AppContainer) + session cache + reconnect |
| `Infrastructure/Mcp/Remote/LocalMcpConnectorTool.cs` | `IMcpTool` ของ connector ที่ **ไม่มี protocol logic ของตัวเอง** |
| `Tests/Mcp/McpDurableRuntimeTests.cs` | 21 tests (server จริง + sqlite จริง) |
| `Tests/Mcp/McpSchedulerSubagentTests.cs` | 6 tests |
| `Tests/Mcp/McpRiskPolicyTests.cs` | 24 tests |
| `Tests/Security/McpRuntimeBypassInvariantTests.cs` | 9 architecture invariants |

### 3.2 ไฟล์ที่แก้

| ไฟล์ | การแก้ | เหตุผล |
|---|---|---|
| `Application/Models/ConnectorTrust.cs` | เพิ่ม `AwaitingTrust`, `ConnectorInstallationPolicy`, manifest fingerprint, approved vs requested capabilities, `MigrateLegacy`, `Approve` | trust ต้องผูกกับ binary + ตำแหน่ง + ชุดสิทธิ์ที่อนุมัติ ไม่ใช่แค่ path |
| `Application/Services/DurableTaskRuntime.cs` | `AgentSafetyPolicy.Classify` เข้าใจ `mcp.*`; `CapabilityRouter` ไม่ filter MCP ออก | `ToolCategory.Network` เดิมทำให้ทุก connector call ตกที่ `ReversibleLowRisk` และข้าม approval |
| `Application/Services/RuntimeActionPolicy.cs` | `mcp.*` scoped โดย connector trust ไม่ใช่ desktop-outcome taxonomy | ไม่งั้น `resources/list` ถูกปฏิเสธเพราะไม่เกี่ยวกับ `OutcomeKind.TextValue` |
| `Infrastructure/Mcp/Remote/McpRemoteClient.cs` | `ServerIdentity`, `NegotiatedProtocolVersion`, `CallToolAsync(..., idempotencyKey)` ผ่าน `_meta` | key ต้องไม่เข้าไปใน `arguments` เพราะจะผิด schema และเปลี่ยน fingerprint ของ call ที่กำลังจะระบุตัวตน |
| `App/Hosting/AppHostBuilder.cs` | ลงทะเบียน journal + session provider + gateway + installation policy | gateway ที่ไม่มีใคร compose คือ gateway ที่ไม่มีอะไรวิ่งผ่าน |
| `McpFixtureServer/Program.cs` | idempotency key, `lookup_by_idempotency_key`, `stats`, `mutate-then-crash`, backend seeding | ตาม requirement ข้อ 7 |
| `Tests/Mcp/ConnectorTrustPolicyTests.cs` | เขียนใหม่ 17 tests | ครอบคลุม 7 กรณีที่ prompt ระบุ |

---

## 4. Runtime path before / after

**Before (ที่ inherited มา)**

```text
[ไม่มีใครเรียก]  ConnectorTrustPolicy.Evaluate
[ไม่มีใครเรียก]  McpMutationReconciler

McpConnectorService.ExecuteRemoteToolAsync
  → McpRemoteClient.CallToolAsync  (HTTP)
  → ToolResult
      ↑ ไม่มี trust, ไม่มี risk class, ไม่มี intent, ไม่มี reconcile

local stdio → StdioMcpTransport → throw Blocked()   (fail-closed)
IsolatedStdioMcpTransport → มีแต่ใน test เท่านั้น
```

**After**

```text
AgentRun (Chat | Scheduler | Subagent | Api-lifecycle)
└─ McpDurableGateway.ExecuteAsync / ExecuteOwnedAsync / ExecuteAttachedAsync
   1. PrepareActionAsync            → AgentStep (TaskAction) + ActionDispatching event
                                      ├─ goal relevance (mcp.* scoped by connector)
                                      ├─ capability route
                                      ├─ approval gate (mutation/unknown = ต้องมี approval)
                                      ├─ ReconciliationRequired gate (ห้าม side effect ต่อ)
                                      └─ DuplicateActionPrevented (idempotency key ระดับ runtime)
   2. Feature matrix                → CapabilityUnavailable ถ้า build ไม่ implement
   3. ConnectorTrustPolicy.Evaluate → Blocked ถ้า hash/path/manifest/permission ไม่ตรง
   4. McpRiskPolicy.Classify        → 6 ชั้น + retry rules (connector policy เป็นตัวตัดสิน)
   5. Journal.FindOpenByFingerprint → ถ้ามี intent ค้าง = ไป reconcile ไม่ใช่ dispatch
   6. Journal.RecordAsync(intent)   → เขียนลงดิสก์ *ก่อน* byte แรกออก
   7. SessionProvider.Acquire/Reconnect (AppContainer + Job Object 4.1A)
      └─ capability gate จาก initialize
   8. dispatch (tools/resources/prompts/...) + `_meta.idempotencyKey`
   9. Observation  → UntrustedObservationBoundary.ProtectIfUntrusted
  10. Verification → provenance check (server identity + generation)
      ├─ ok                → Succeeded
      ├─ pre-dispatch fail → FailedNoEffect (retry ได้)
      ├─ read + lost answer→ FailedNoEffect (retry ได้หลังตรวจ provenance)
      └─ mutation + unknown→ Reconnect/reinitialize
                             → lookup_by_idempotency_key → match = Succeeded (ไม่ redispatch)
                             → read-back                 → Succeeded / FailedNoEffect
                             → พิสูจน์ไม่ได้              → NeedsUser + intent ค้างเปิด
  11. RecordResultAsync / PauseAsync(NeedsUser)
```

`StdioMcpTransport` เดิม **ยัง `throw Blocked()`** — ไม่มี host fallback

---

## 5. AgentStep evidence

ทุกข้อวัดจาก `SqliteAgentTaskStore` และ `TaskEvent` จริง ไม่ใช่จาก return value อย่างเดียว

| Operation | AgentStep (TaskAction) | `ActionDispatching` event | Test |
|---|---|---|---|
| initialize | ✅ | ✅ | `Every_Protocol_Operation_Runs_As_An_AgentStep(Initialize)` |
| tools/list | ✅ | ✅ | เดียวกัน |
| resources/list | ✅ | ✅ | เดียวกัน |
| prompts/list | ✅ | ✅ | เดียวกัน |
| tools/call | ✅ | ✅ | `Tools_Call_Resources_Read_And_Prompts_Get_Each_Leave_Their_Own_Step` |
| resources/read | ✅ | ✅ | เดียวกัน |
| prompts/get | ✅ | ✅ | เดียวกัน |
| reconnect / reinitialize | ✅ | ✅ | `Reconnect_Reinitializes_And_Raises_The_Generation` |
| cancellation | ✅ (แปลงเป็น dispatch stage) | ✅ | ครอบด้วย 4.1A + `ClassifyDispatchFailureAsync` |
| **operation ที่ถูกปฏิเสธ** | ✅ | ✅ | `A_Refusal_Before_Dispatch_Still_Leaves_A_Durable_Step` |
| connector lifecycle (ไม่มี chat turn) | ✅ run ของตัวเอง (TriggerType=`Api`) | ✅ | `An_Owned_Run_Is_Created_For_Connector_Work_Nobody_Asked_For` |

**Architecture test ที่ fail ถ้ามีใคร bypass** — `McpRuntimeBypassInvariantTests` (9 tests):

1. ไม่มีไฟล์ production ไหนเรียก `CallToolAsync/ListToolsAsync/ListResourcesAsync/ReadResourceAsync/ListPromptsAsync/GetPromptAsync` นอก allowlist 5 ไฟล์ (แต่ละไฟล์มีเหตุผลกำกับ)
2. `McpDurableGateway` ต้องมี `PrepareActionAsync` + `RecordResultAsync` + `ConnectorTrustPolicy.Evaluate` + `_journal.RecordAsync` + `ReconcileAsync` จริง
3. **ลำดับ**: `_journal.RecordAsync(intent)` ต้องอยู่ *ก่อน* `_sessions.AcquireAsync` ในซอร์ส
4. มีแต่ session provider เท่านั้นที่ `new IsolatedStdioMcpTransport(`
5. มีแต่ session owner เท่านั้นที่ `new McpRemoteClient(`
6. `StdioMcpTransport` ต้องยังมี `Blocked(`
7. `LocalMcpConnectorTool` ต้องเรียก `ExecuteAttachedAsync` และห้าม `new McpRemoteClient(`
8. `AppHostBuilder` ต้องลงทะเบียน gateway + journal + installation policy จริง
9. allowlist ต้องไม่มี entry ที่ไฟล์หายไปแล้ว

**เหตุผลที่มี attached mode**: ใน chat turn `ChatOrchestrator` เป็นคนเรียก `PrepareAction`/`RecordResult` ให้ tool call ของโมเดลอยู่แล้ว ถ้า gateway prepare ซ้ำจะได้ AgentStep สองอันสำหรับ operation เดียวและกิน budget ของ step สองครั้ง — `ExecuteAttachedAsync` จึงผูกกับ step ที่มีอยู่ และถ้า **ไม่มี** durable run ambient เลย มันจะ fallback ไป `ExecuteOwnedAsync` ทำให้ไม่มีเส้นทางไหนที่ MCP วิ่งโดยไม่มี step

---

## 6. Scheduler / Subagent evidence

### Scheduler (`McpSchedulerSubagentTests`)

| Requirement | Test | ผล |
|---|---|---|
| scheduled read → run TriggerType=Scheduler + AgentStep | `A_Scheduled_Read_Runs_As_A_Scheduler_Triggered_AgentStep` | ✅ |
| scheduled mutation → validate trust + persist intent | `A_Scheduled_Mutation_Persists_Its_Intent_Before_Dispatch` | ✅ intent ใน journal, risk=`ExternalMutation`, fingerprint ตรง |
| disconnect after dispatch → restart → duplicate = 0 | `A_Scheduled_Mutation_Interrupted_After_Dispatch_Does_Not_Repeat_On_Restart` | ✅ |

การ restart ในเทสต์คือ **ของจริงในระดับ state**: สร้าง `SqliteAgentTaskStore` / `DurableTaskRuntime` / `McpDispatchJournal` / session provider ใหม่ทั้งหมด บนไฟล์ database และ journal เดิม แล้วยิงคำสั่งเดิมซ้ำ ผลคือ:
- ไม่ถูกรายงานเป็น `Succeeded` ใหม่
- server รอบใหม่รายงาน `stats.created == 0` → **ไม่มีการ redispatch**

### Subagent

| Requirement | Test | ผล |
|---|---|---|
| parent → child MCP read เป็น durable child run | `A_Child_Run_Reads_Through_Its_Own_Durable_Step_With_Provenance` | ✅ `ParentRunId` ตรง, TriggerType=Subagent, `ChildRunCreated` event บน parent, action อยู่ใต้ child |
| child ขยาย connector permission ไม่ได้ | `A_Child_Cannot_Reach_A_Connector_Its_Parent_Was_Not_Allowed` | ✅ `ConnectorRevoked` — trust เป็นสมบัติของ connector ไม่ใช่ของผู้เรียก |
| child mutation disconnect → NeedsUser | `A_Child_Whose_Mutation_Is_Lost_Needs_A_User_And_Leaves_Siblings_Intact` | ✅ status=NeedsUser, child run Paused/NeedsUser |
| parent เก็บผลลัพธ์บางส่วนของ sibling ไว้ได้ | เดียวกัน | ✅ read child ยัง Verified, ไม่ Failed |
| no duplicate mutation | เดียวกัน | ✅ intent ค้างเปิด 1 อัน ไม่มี redispatch |

---

## 7. Protocol feature matrix

Protocol version ที่ implement: **2025-06-18** (รับ 2025-03-26 ด้วย) — version อื่นถูกปฏิเสธด้วย `VersionMismatch`

Matrix นี้เป็น **data** ใน `McpProtocolFeatureMatrix` ที่ test อ่านได้ (`The_Feature_Matrix_Never_Claims_Something_It_Also_Calls_Unsupported`) ไม่ใช่ข้อความในรายงาน

| Feature | สถานะ | E2E | ผ่าน durable runtime |
|---|---|---|---|
| initialize | ✅ implemented | ✅ | ✅ |
| capability negotiation | ✅ | ✅ | ✅ |
| version negotiation | ✅ | ✅ | ✅ |
| tools/list | ✅ | ✅ | ✅ |
| tools/call | ✅ | ✅ | ✅ |
| resources/list | ✅ | ✅ | ✅ |
| resources/read | ✅ | ✅ | ✅ |
| prompts/list | ✅ | ✅ | ✅ |
| prompts/get | ✅ | ✅ | ✅ |
| cancellation | ✅ | ✅ | ✅ |
| progress notification | ✅ | ✅ | ✅ (แยกจาก response, bounded 500) |
| logging notification | ✅ | ✅ | ✅ (drain + quota 256 KB, ไม่ persist raw) |
| reconnect / reinitialize | ✅ | ✅ | ✅ (generation ใหม่จริง) |
| graceful shutdown | ✅ | ✅ | ✅ |
| **tools/list pagination** | ❗ **ไม่ implement** | ✅ (ทดสอบว่าคืน CapabilityUnavailable) | ✅ มี AgentStep |
| **resource subscriptions** | ❗ **ไม่ implement** | ✅ เดียวกัน | ✅ |
| **resource-change notifications** | ❗ **ไม่ implement** | — | — |
| **completions** | ❗ **ไม่ implement** | ✅ เดียวกัน | ✅ |

ทั้งสี่รายการนี้ **อยู่ใน 2025-06-18** แต่ architecture ยังไม่พร้อม (ต้องมี producer แบบ push + cursor state ต่อ session) จึง:
- คืน `McpOperationStatus.CapabilityUnavailable` แบบมีโครงสร้าง พร้อมเหตุผลเป็นข้อความ
- **ไม่ ignore เงียบ**
- **ไม่เคลม protocol completeness** — `SupportedVersions` และ `Implemented` เป็น set ที่ตรวจสอบได้
- ระบุไว้ใน matrix นี้และใน `McpProtocolFeatureMatrix.Unsupported`

---

## 8. Mutation reconciliation evidence

### Fixture server รองรับครบตาม requirement

| ความสามารถที่ prompt ขอ | ใน `SranDesktop.McpFixtureServer` |
|---|---|
| create object | `create_record` |
| read object by ID | `get_record` |
| lookup by idempotency fingerprint | `lookup_by_idempotency_key` (อ่านจาก `params._meta.idempotencyKey`) |
| disconnect after mutation | profile `mutate-then-crash` (commit แล้ว `Exit(9)`) และ `silent-mutation` (commit แล้วไม่ตอบ) |
| delayed response | `slow` |
| duplicate delivery | `duplicate` |
| *(เพิ่ม)* วัด side effect | `stats` → จำนวน mutation ที่ process นี้ทำจริง |

### Flow ที่พิสูจน์แล้ว

```text
Persist intent (journal, ก่อน dispatch)
→ Dispatch mutation (+ _meta.idempotencyKey)
→ Server mutates state
→ Connection lost                      (Exit(9) กลาง tools/call)
→ McpDispatchException.OutcomeUnknown  (stage ≥ Writing)
→ Reconnect / reinitialize             (generation ใหม่)
→ Query actual server state            (lookup_by_idempotency_key → get_record)
→ Match fingerprint
→ Complete without redispatch          (evidence: redispatch=none)
```

| Acceptance | หลักฐาน |
|---|---|
| duplicate side effect = **0** | วัดจาก **ฝั่ง server**: `stats.created == 0` บน session ใหม่หลัง reconcile — ไม่ใช่แค่ assert ว่า client ไม่เรียกซ้ำ (`A_Mutation_Whose_Answer_Is_Lost_Is_Reconciled_Not_Repeated`, `A_Scheduled_Mutation_Interrupted_After_Dispatch_Does_Not_Repeat_On_Restart`, `An_Open_Intent_Blocks_A_Second_Dispatch_Of_The_Same_Mutation`) |
| false success = **0** | ไม่มีเส้นทางไหนคืน `Succeeded` โดยไม่ได้อ่าน state กลับ; `ProvenanceMismatch` ดัก server ที่เปลี่ยนตัวตนกลางคัน |
| unresolved state = **NeedsUser** | `An_Unresolvable_Mutation_Becomes_NeedsUser_And_Stays_Open` — และ intent **ยังเปิดค้าง** ใน journal เพื่อให้ restart ถัดไปยังเห็น |
| no automatic replay | `MayRetryAfterDispatch == false` สำหรับทุกคลาสที่ไม่ใช่ read (`McpRiskPolicyTests.Retry_Rules_Follow_From_The_Class_Not_From_The_Call_Site`) + duplicate guard ด้วย fingerprint |

**ข้อจำกัดที่ต้องพูดตรง ๆ**: sandbox ของ 4.1A จงใจ **ไม่ sync งานเขียนของ long-lived server กลับ host** (เพื่อไม่ให้ server เขียนทับไฟล์ผู้ใช้) ดังนั้น state ที่ต้องอยู่รอดข้าม process ของ fixture ถูก seed เข้ามาจาก workspace ตอน startup — ซึ่งเป็นภาพเดียวกับ MCP server จริงที่มี database ของตัวเองอยู่นอกตัว process. สิ่งที่ **เป็นของจริงทั้งหมด** คือ: intent เขียนก่อน dispatch, การจำแนก unknown outcome, reconnect/reinitialize ที่ generation เพิ่มจริง, การเรียก lookup ไปยัง server ใน sandbox จริง, และการนับ mutation จากฝั่ง server

---

## 9. Connector trust evidence

`ConnectorTrustPolicy.Evaluate(manifest, installationPolicy)` ถูกเรียก **ทุกครั้งก่อน launch** ใน `McpDurableGateway.RunPipelineAsync` (ข้อ 3 ของ pipeline) ไม่ใช่ครั้งเดียวตอน install

| กรณีที่ prompt ระบุ | Rejection code | Test |
|---|---|---|
| trust ไม่มี | `ConnectorUntrusted` | `Every_Non_Approved_State_Refuses_To_Launch` |
| executable identity ไม่ทราบ (legacy ไม่มี hash) | `ConnectorIdentityUnknown` → state `AwaitingTrust` | `A_Connector_With_No_Recorded_Identity_Is_Never_Auto_Trusted` |
| executable hash เปลี่ยน | `ConnectorExecutableChanged` | `Replacing_The_Executable_After_Trust_Invalidates_It`, `A_Connector_Whose_Executable_Changed_Never_Starts` (E2E ผ่าน gateway) |
| executable path นอก controlled installation location | `ConnectorPathNotControlled` | `An_Executable_Outside_The_Controlled_Location_Cannot_Launch` |
| manifest เปลี่ยนหลัง approval | `ConnectorManifestChanged` | `Changing_The_Declaration_After_Approval_Invalidates_It`, `Repointing_The_Manifest_At_A_Different_Binary_Is_Caught` |
| permission เพิ่มโดยไม่อนุมัติใหม่ | `ConnectorPermissionExpanded` | `A_Connector_Asking_For_More_Than_Was_Approved_Cannot_Launch` (unit) + E2E ผ่าน gateway |
| disabled connector | `ConnectorDisabled` | `Every_Non_Approved_State_Refuses_To_Launch` |
| revoked trust | `ConnectorRevoked` | เดียวกัน + `A_Refusal_Before_Dispatch_Still_Leaves_A_Durable_Step` |
| executable replacement (ไฟล์หาย) | `ConnectorExecutableMissing` | `A_Missing_Executable_Fails_Closed` |
| connector ไม่ได้ติดตั้ง | `ConnectorNotInstalled` | `An_Uninstalled_Connector_Is_Refused_Rather_Than_Dialed` |

**Legacy migration**: `MigrateLegacy` ย้ายไป `AwaitingTrust` และ **จงใจไม่คำนวณ hash** — การคำนวณ hash ตอนนั้นคือการอนุมัติ binary อะไรก็ตามที่บังเอิญอยู่บนดิสก์ ณ ตอนนั้น (`Migrating_A_Legacy_Connector_Moves_It_To_AwaitingTrust_Without_Hashing`) และ `Approve` เป็นจุดเดียวที่คำนวณ + persist identity + manifest fingerprint (`An_Explicit_Approval_Records_The_Identity_And_Then_Allows_Launch`)

---

## 10. Risk classification และ default reconciliation

ตาราง default ใหม่ — retry rules อยู่ในคลาส `McpRiskAssessment` ไม่ได้อยู่ที่ call site เพื่อไม่ให้ call site ใหม่คิดกฎที่หลวมกว่าขึ้นมาเอง

| Risk class | pre-dispatch retry | post-dispatch retry | provenance validation | reconcile ถ้า unknown | ต้อง approval |
|---|---|---|---|---|---|
| `ReadOnly` (policy-validated) | ✅ | ✅ | ✅ | — | ไม่ |
| `IdempotentRead` (ชื่อ read-shaped, ไม่มี policy) | ✅ | ✅ | ✅ | — | ไม่ |
| `LocalMutation` | ✅ | ❌ | — | ✅ | ✅ |
| `ExternalMutation` | ✅ | ❌ | — | ✅ | ✅ |
| `Destructive` | ✅ | ❌ | — | ✅ | ✅ |
| `Unknown` | ✅ | ❌ | — | ✅ | ✅ |

**หลักที่ทั้งหมดวางอยู่บน**: server บอกอะไรเกี่ยวกับตัวเองไม่เคยทำให้คำตัดสิน *หลวมลง*
- `readOnlyHint: true` โดยไม่มี connector policy รับรอง → evidence `server_hint=read_only_rejected_unvalidated`, risk ไม่เปลี่ยน (`A_Server_Claiming_Read_Only_Does_Not_Get_A_Read_Only_Verdict` — `wipe_everything` ยังเป็น `Destructive`)
- `destructiveHint: true` → รับ เพราะทำให้เข้มขึ้นเท่านั้น
- connector policy ที่ประกาศ `delete_everything` เป็น read-only → ยังได้ `Destructive` และ `PolicyValidated=false` (`A_Policy_Claiming_A_Delete_Is_Read_Only_Does_Not_Get_Away_With_It`)
- verification tool มาจาก **policy ที่อนุมัติเท่านั้น** ไม่ใช่จาก server (`The_Verification_Tool_Comes_From_The_Policy_Not_From_The_Server`) — ไม่งั้น server เสนอ verifier ที่ตอบ "ใช่" เสมอได้
- `Unknown` ≠ `ReadOnly`: ไม่มี policy = ชื่อ read-shaped ได้มากสุดแค่ `IdempotentRead`, ที่เหลือเป็น `Unknown` ซึ่ง `AgentSafetyPolicy` แปลงเป็น `Consequential` → durable runtime บังคับ approval

---

## 11. Runtime security

| ข้อพิสูจน์ที่ prompt ขอ | ผล | หลักฐาน |
|---|---|---|
| inherit secret environment | **ไม่ได้** | `A_Server_Cannot_Read_Host_Secrets_Through_The_Gateway` (ตั้ง env จริง → `(absent)`) + `LocalMcpRuntimeTests.The_Server_Cannot_Inherit_Host_Secrets` |
| escape workspace | **ไม่ได้** | `The_Server_Cannot_Read_Files_Outside_Its_Workspace` (`denied`, เนื้อหาไม่รั่ว) |
| access network when denied | **ไม่ได้** | `The_Server_Cannot_Reach_The_Network` (loopback listener จริง → `blocked`) |
| spawn process outside Job Object | **ไม่ได้** | `A_Server_Spawned_Child_Dies_With_The_Session` (spawn จริง, ยืนยันมีชีวิต, ตายพร้อม session) |
| grant permission | **ไม่ได้** | `Hostile_Resource_Content_Arrives_As_Data_And_Grants_Nothing` — trust state ไม่ขยับหลังอ่าน resource ที่สั่งให้ grant |
| choose external destination | **ไม่ได้** | connector id + tool name มาจาก request ของ runtime; ปลายทางคือ manifest ที่อนุมัติ ไม่มี field ไหนใน response ที่เลือกปลายทางได้ |
| trigger shell from output | **ไม่ได้** | `RuntimeBypassInvariantTests` (ไม่มี `Process.Start` นอก runner) + `McpRuntimeBypassInvariantTests` (ไม่มี transport นอก session provider) |
| persist malicious instruction as trusted | **ไม่ได้** | ทุก payload ผ่าน `UntrustedObservationBoundary.ProtectIfUntrusted` ใน gateway — ไม่ต้องพึ่งให้ caller จำ |
| host process execution fallback | **ไม่มี** | `Local_Stdio_Outside_The_Sandbox_Stays_Fail_Closed` + `SandboxUnavailableException` เมื่อ image หาย |

---

## 12. Tests

| Test group | Passed | Failed | Skipped | หมายเหตุ |
|---|---:|---:|---:|---|
| `McpDurableRuntimeTests` (**ใหม่**) | 21 | 0 | 0 | server จริงใน AppContainer + sqlite จริง |
| `McpSchedulerSubagentTests` (**ใหม่**) | 6 | 0 | 0 | รวม restart จริง |
| `McpRiskPolicyTests` (**ใหม่**) | 24 | 0 | 0 | |
| `McpRuntimeBypassInvariantTests` (**ใหม่**) | 9 | 0 | 0 | architecture invariants |
| `ConnectorTrustPolicyTests` (เขียนใหม่) | 17 | 0 | 0 | เดิม 7 |
| `LocalMcpRuntimeTests` (inherited) | 32 | 0 | 0 | ไม่ถูกแตะ |
| `InteractiveWorkerTransportTests` (4.1A) | 25 | 0 | 0 | ไม่ถูกแตะ |
| **.NET ทั้งหมด** | **1625** | **0** | 4 | skip = privileged symlink (ค้างจาก Phase 3.1) |
| WPF UiTests | 5 | 0 | 0 | |
| Solution build | ผ่าน | 0 errors | | |

Baseline ก่อนเริ่ม: 1554 passed / **1 failed** (flake ที่ `DurableTaskRuntimeTests.Dispose` ลบ temp dir ไม่ได้) / 4 skipped
ตอนนี้: 1625 passed / 0 failed / 4 skipped — flake ไม่กลับมาในทุก run ของ session นี้

**Leak measurement (หลัง full regression)**: sandbox job dirs = **0**, worker survivors = **0**, temp dirs ของเทสต์ใหม่ = **0**

> บั๊กที่เจอระหว่างทาง (ในเทสต์ ไม่ใช่ product): เทสต์เรียก `BuildGateway()` ซ้ำโดยไม่ dispose session provider ตัวเก่า ทำให้ session ค้างและทิ้ง sandbox job directory ไว้ 1–2 อันต่อ run แก้แล้ว วัดได้ 0

---

## 13. Remaining gaps

**P1**

1. **Remote HTTP connector ยังไม่ผ่าน durable gateway** — `McpConnectorService.ExecuteRemoteToolAsync` ยังเรียก `McpRemoteClient` ตรง ๆ สำหรับ connector แบบ HTTP. ที่ยอมรับได้ในตอนนี้เพราะ local stdio ใน service นั้น **fail-closed อยู่แล้ว** (invariant test ล็อกไว้) ดังนั้น "MCP ที่รันในเครื่อง" ผ่าน gateway 100% แต่ **acceptance gate "every MCP operation creates AgentStep" ยังไม่ครอบคลุม HTTP** — ต้องทำ session provider ฝั่ง HTTP แล้วย้าย `ExecuteRemoteToolAsync` มาที่ `ExecuteAttachedAsync` (มี allowlist entry ใน invariant test กำกับไว้ พร้อมเหตุผล)
2. **ยังไม่มี UI ให้ผู้ใช้ approve local connector** — `AppHostBuilder` ลงทะเบียน gateway จริงแล้ว แต่ manifest resolver คืน `null` เสมอ (ทุก id → `ConnectorNotInstalled`) เพราะหน้าจอที่บันทึก executable identity ตอนอนุมัติเป็นงานเฟสถัดไป. ผลคือ **path นี้พร้อมและ fail-closed แต่ยังไม่มี connector จริงวิ่ง** ในแอป
3. **หลาย MCP operation ใน run เดียว** ขึ้นกับรูปของ plan — `PrepareAction` เลือก step ถัดไปจาก plan ถ้า plan มี step เดียวและ verify แล้ว operation ที่สองจะได้ `NoReadyStep` เป็นพฤติกรรมปกติของ runtime (chat ใช้ `update_plan` เพิ่ม step) แต่ควรมี helper ให้ connector workflow ยาว ๆ ไม่ต้องพึ่งโมเดล

**P2**

4. pagination / subscriptions / resource-change notifications / completions ยังไม่ทำ (รายงานเป็น `CapabilityUnavailable` ตรง ๆ ตามข้อ 7)
5. heartbeat ยังไม่มีตัวส่งจริง (ค้างจาก 4.1A) — session ตรวจการตายผ่าน EOF/crash แทน
6. bounded request queue ยังเป็น implicit (จำกัดด้วย stderr quota + frame cap + notification cap)
7. resource URI ยังไม่มี allowlist scheme ฝั่ง client — พึ่ง sandbox เป็นหลัก
8. OAuth / credential broker สำหรับ local connector ยังไม่ทำ
9. `_failedTemporaryCleanups` fallback ของ 4.1A ยังเป็นทางกลับสุดท้ายเมื่อ delete race แพ้ (ตอนนี้วัดได้ 0 แต่กลไกยังอยู่)

**Environment limitation**

10. คงเดิมจาก Phase 3.1 — ไม่มี Developer Mode (privileged symlink 4 tests ยัง skip), จอเดียว, ไม่มี provider credential

---

## 14. Final verdict

1. **ทุก MCP operation เป็น AgentStep จริงหรือไม่** — **ใช่ สำหรับ local MCP** ทุก operation รวมทั้งที่ถูกปฏิเสธ และรวม connector-lifecycle ที่ไม่มี chat turn; ยืนยันจาก `TaskAction` + `ActionDispatching` event บน sqlite จริง **ยังไม่ครอบคลุม remote HTTP connector** (ข้อ 13.1)
2. **`PrepareAction` / `RecordResult` ถูกใช้จริงหรือแค่มี** — **ใช้จริง** ทั้งใน owned mode (gateway เรียกเอง) และ attached mode (orchestrator เรียก, gateway ผูกเข้ากับ step เดิมเพื่อไม่ให้นับซ้ำ) และมี architecture test ที่ fail ถ้าใครหลุดออกจากเส้นนี้
3. **Scheduler / Subagent ผ่านหรือไม่** — **ผ่านทั้งคู่** รวม disconnect-after-dispatch → restart → duplicate = 0 และ child NeedsUser โดย sibling ไม่พัง
4. **mutation ที่คำตอบหายซ้ำหรือไม่** — **ไม่ซ้ำ** วัดจากฝั่ง server (`stats.created == 0`) ไม่ใช่จากคำสัญญาฝั่ง client; ไม่มี auto-replay path สำหรับอะไรที่ไม่ใช่ read
5. **unresolved กลายเป็น NeedsUser หรือไม่** — **ใช่** และ intent ค้างเปิดใน journal เพื่อให้ restart ถัดไปยังเห็นว่ามีเรื่องค้าง
6. **connector trust บังคับตอน launch จริงหรือไม่** — **ใช่ ทุกครั้ง** ครบ 7 กรณีที่ prompt ระบุ + legacy migration ที่ไม่ auto-trust
7. **risk classification เชื่อ server หรือไม่** — **ไม่** server hint ทำให้เข้มขึ้นได้อย่างเดียว; `ReadOnly` ต้องมาจาก connector policy ที่ผู้ใช้อนุมัติเท่านั้น
8. **protocol completeness เคลมเกินจริงไหม** — **ไม่** feature matrix เป็น data ที่ test ตรวจ; 4 feature ที่ไม่ทำคืน `CapabilityUnavailable` แบบมีโครงสร้าง
9. **process / temp leak** — **0** ทุกตัวชี้วัดหลัง full regression
10. **host fallback** — **ไม่มี** `StdioMcpTransport` ยัง `throw Blocked()`, image หาย → `SandboxUnavailableException`
11. **blocker สามอันดับแรกของเฟสถัดไป** — (1) ย้าย remote HTTP connector มาที่ gateway, (2) UI approve local connector ที่บันทึก executable identity, (3) OAuth / credential broker สำหรับ connector ที่ต้องใช้ความลับ

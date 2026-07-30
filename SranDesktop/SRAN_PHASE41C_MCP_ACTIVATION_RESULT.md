# SRAN Desktop — Phase 4.1C: MCP Runtime Activation และ Complete Path Closure

> วันที่: 2026-07-29 · สถานะ: **PASS**
> ยังไม่ commit / ไม่ push / ไม่ publish ตามคำสั่ง
>
> ข้อจำกัดเดียวที่ต้องอ่านก่อนตีความว่า "ผ่าน": หน้าจอ approval มีหลักฐานระดับ
> unit + architecture test และเชื่อมกับ trust store จริงแล้ว แต่ **ยังไม่ได้กด
> Approve ด้วยมือบนแอปที่ publish** — ดูข้อ 14.1

---

## 1. Executive Result

| Acceptance gate | ผล |
|---|---|
| **Path closure** — production MCP operation ทุกครั้งผ่าน `McpDurableGateway` | **ผ่าน** |
| MCP operation ทุกครั้งสร้าง AgentStep | **ผ่าน** |
| direct transport bypass | **0** |
| scheduler bypass | **0** |
| subagent bypass | **0** |
| **Activation** — production manifest resolver คืน trusted launch descriptor หลัง approval | **ผ่าน** |
| unapproved connector fail closed | **ผ่าน** |
| real approved local connector launch แบบ isolated | **ผ่าน** |
| AppHostBuilder production-graph E2E | **ผ่าน** (46 tests) |
| **Trust** — executable hash / manifest fingerprint บังคับที่ launch | **ผ่าน** |
| permission expansion invalidate trust | **ผ่าน** |
| legacy connector → `AwaitingTrust` | **ผ่าน** |
| disabled / revoked connector launch ไม่ได้ | **ผ่าน** |
| active session หยุดเมื่อ revoke | **ผ่าน** |
| **Runtime** — tools / resources / prompts ผ่าน gateway | **ผ่าน** |
| mutation restart reconciliation | **ผ่าน** |
| duplicate side effect | **0** (วัดจากฝั่ง server) |
| false success | **0** |
| unresolved state → NeedsUser | **ผ่าน** |
| **Integration** — scheduler / subagent E2E | **ผ่าน** |
| process / temp leak | **0** |
| host fallback | **ไม่มี** |
| full regression | **1680 passed / 0 failed / 4 skipped** + UiTests 5/5 |

**ประโยคเดียวที่สรุปเฟสนี้**: 4.1B สร้าง durable MCP path ที่ทำงานได้จริง แต่ production composition ผูก manifest resolver เป็น `_ => null` — ทุก connector id จึงตอบ `ConnectorNotInstalled` และ **ไม่มี MCP operation ใดเลยที่วิ่งผ่าน gateway ในแอปจริง** ขณะที่เทสต์ทั้งชุดเขียว เพราะเทสต์ประกอบ resolver ของตัวเอง. 4.1C ปิดช่องนี้ทั้งหมด: resolver จริง, trust store จริง, หน้าจอ approval จริง, และย้าย `McpConnectorService` (marketplace/remote HTTP) เข้ามาที่ gateway จนไม่เหลือ protocol call นอก gateway เลย

---

## 2. Previous Remaining Gaps (จาก 4.1B) และสถานะปัจจุบัน

| # | ช่องว่างที่ 4.1B ระบุ | สถานะหลัง 4.1C |
|---|---|---|
| 1 | Remote HTTP connector (`McpConnectorService`) ไม่ผ่าน durable gateway | **ปิดแล้ว** — dial / tools-list / ping / tool-call / test-connection ผ่าน gateway ทั้งหมด; `McpConnectorService` ไม่มี protocol call เหลืออยู่เลย และถูก **ถอดออกจาก allowlist** ของ architecture test |
| 2 | manifest resolver คืน `null` เสมอใน production | **ปิดแล้ว** — `ConnectorManifestResolver` จริง อ่าน package จาก controlled root, hash, fingerprint, เทียบ trust |
| 3 | ไม่มี UI ให้ผู้ใช้ approve local connector | **ปิดแล้ว** — `ConnectorApprovalDialog` + `LocalConnectorItemViewModel` + section ใน `ConnectorMarketplaceView` |
| 4 | ไม่มี trusted local connector ที่ launch ผ่าน full application path | **ปิดแล้ว** — `ProductionConnectorActivationTests` รัน fixture server จริงผ่าน `AppHostBuilder` |
| 5 | เคลมไม่ได้ว่า every MCP operation creates AgentStep | **เคลมได้แล้ว** ในขอบเขต production code (ดูข้อ 3) |
| 6 | หลาย MCP operation ใน run เดียวติด `NoReadyStep` / clarification gate | **ปิดแล้ว** — `DurableRunRequest.OperationIsFullySpecified` (ดูข้อ 4.3) |

**ช่องว่างใหม่ที่เจอระหว่างทำ 4.1C** (ไม่ได้อยู่ใน prompt แต่เป็นบั๊กจริง):

7. **MCP request ไม่มี timeout** — `IsolatedStdioMcpTransport` ไม่มี deadline. server ที่รับ request แล้วเงียบ (profile `silent-mutation`) ทำให้ run ค้างตลอดกาล — แย่กว่าทั้งสองผลลัพธ์ที่มันรออยู่ระหว่างกลาง. เจอเพราะเขียนเทสต์ `silent-mutation` ซึ่ง 4.1B ไม่เคยเรียกใช้. แก้ที่ gateway: `McpDurableGateway.DefaultOperationTimeout` (2 นาที, ปรับได้ด้วย `Sran:McpOperationTimeoutSeconds`) และ timeout **หลัง dispatch = UnknownOutcome → reconcile** ไม่ใช่ retry

---

## 3. Complete Call-Site Matrix

### 3.1 Production call sites

| Call site | User-facing | Current path | AgentRun | AgentStep | Uses Gateway | Action ที่ทำ |
|---|---|---|---|---|---|---|
| `RemoteMcpTool.ExecuteAsync` (model tool call) | ✅ | `→ McpConnectorService.ExecuteRemoteToolAsync → gateway.ExecuteAttachedAsync` | run ของ chat turn | ✅ (step ของ orchestrator) | ✅ | **ย้าย** จาก `McpRemoteClient` ตรง |
| `LocalMcpConnectorTool.ExecuteAsync` | ✅ | `→ gateway.ExecuteAttachedAsync` | attached / owned | ✅ | ✅ | คงเดิม (4.1B) |
| `McpConnectorService.ExecuteRemoteToolAsync` | ✅ | `gateway.ExecuteAttachedAsync` (tools/call) | attached / owned | ✅ | ✅ | **ย้าย** |
| `McpConnectorService.RefreshRemoteToolsAsync` → `ConnectOneAsync` | ✅ (health dot) | `gateway.ExecuteOwnedAsync` (tools/list) | `Api`, owner `connector:dial:{id}` | ✅ | ✅ | **ย้าย** |
| `McpConnectorService.PingAsync` | ✅ | `→ ConnectOneAsync` → gateway | เดียวกัน | ✅ | ✅ | **ย้าย** |
| `McpConnectorService.InstallAsync` / `InstallCustomAsync` (dial หลัง install) | ✅ | `→ ConnectOneAsync` → gateway | เดียวกัน | ✅ | ✅ | **ย้าย** + บันทึก trust |
| `McpConnectorService.TestConnectionAsync` | ✅ | `gateway.ExecuteOwnedAsync` ภายใต้ `TransientConnectorGrants` | `Api`, owner `connector:test:{id}` | ✅ | ✅ | **ย้าย**; stdio = ปฏิเสธ |
| `McpConnectorService.UninstallAsync` | ✅ | `sessions.ReleaseAsync` + `trustStore.RemoveAsync` | — | — | — | ไม่ใช่ protocol op |
| `ConnectorOAuthCoordinator.HandleCallbackAsync` → `InstallAsync` / `PingAsync` | ✅ | ผ่าน `McpConnectorService` → gateway | ✅ | ✅ | ✅ | **ย้าย** (+ evidence) |
| `McpToolHost.Resolve` / `GetToolDefinitions` | ✅ | cache-only (sync) | — | — | — | ไม่แตะเครือข่าย |
| Scheduler → `ChatOrchestrator` → `IMcpToolHost` | ✅ | `RemoteMcpTool` / `LocalMcpConnectorTool` → gateway | `Scheduler` | ✅ | ✅ | ได้ผลจากการย้ายข้างบน |
| `SpawnAgentTool` → `FilteredToolHost` → tools | ✅ | เดียวกัน | `Subagent` (child run) | ✅ | ✅ | เดียวกัน |
| `IsolatedMcpConnectorSessionProvider` | — | initialize/handshake | gateway บันทึกเป็น step ของมันเอง | ✅ | (เป็นเจ้าของ session) | allowlist |
| `McpMutationReconciler` | — | เรียกจาก gateway path เท่านั้น | ✅ | ✅ | ✅ | allowlist |
| `McpRemoteClient` | — | protocol client | — | — | — | allowlist; ctor แบบรับ URL เปลี่ยนเป็น `internal` |
| `StdioMcpTransport` | — | `throw Blocked()` | — | — | — | fail-closed คงเดิม |

### 3.2 Test-only fixtures (ระบุชัดตามที่ prompt ขอ)

| Fixture | ใช้ทำอะไร | production reuse ได้ไหม |
|---|---|---|
| `SranDesktop.McpFixtureServer` | MCP server จริงสำหรับ E2E | **ไม่** — เป็น project แยก ไม่ถูก reference จาก production assembly |
| `ConnectorPackage` (Tests/Mcp) | ติดตั้ง fixture server เป็น package จริงใน controlled root | **ไม่** — อยู่ใน test assembly |
| `ProductionConnectorHost` (Tests/Mcp) | เรียก `AppHostBuilder.Build` จริง แล้ว override เฉพาะ *ที่อยู่ของไฟล์* | **ไม่ใช่ bypass** — ไม่มีการประกอบ service graph เอง |
| `StdioEchoServer` | legacy stdio fixture | ไม่มี production path ที่ launch stdio นอก isolated worker แล้ว |

**การตรวจซ้ำด้วย grep บนซอร์สจริง** — protocol call (`CallToolAsync/ListToolsAsync/ListResourcesAsync/ReadResourceAsync/ListPromptsAsync/GetPromptAsync`) ในทั้ง `Application` + `Infrastructure` + `Presentation` + `App`:

```
8  Infrastructure/Mcp/Remote/McpDurableGateway.cs
1  Infrastructure/Mcp/Remote/McpMutationReconciler.cs
8  Infrastructure/Mcp/Remote/McpRemoteClient.cs
```

transport/client construction:

```
IsolatedMcpConnectorSessionProvider.cs:67   new HttpMcpTransport(
IsolatedMcpConnectorSessionProvider.cs:86   new IsolatedStdioMcpTransport(
IsolatedMcpConnectorSessionProvider.cs:245  new McpRemoteClient(
McpRemoteClient.cs:27                       new HttpMcpTransport(   (ctor ภายใน, internal)
```

ไม่มี MCP operation ที่ไม่ผ่าน gateway เหลืออยู่

---

## 4. Runtime path — before / after

### 4.1 Before (สภาพจริง ณ ต้น 4.1C)

```text
[ไม่มีใครสร้าง]  LocalMcpConnectorTool          ← กล่องที่ผ่าน gateway ไม่ถูก compose
[คืน null เสมอ]  manifest resolver ใน AppHostBuilder  (_ => null)

model tool call
  → McpToolHost.Resolve → RemoteMcpTool
  → McpConnectorService.ExecuteRemoteToolAsync
  → McpRemoteClient.CallToolAsync            ← HTTP ตรง
      ↑ ไม่มี AgentStep, ไม่มี trust, ไม่มี intent, ไม่มี reconcile

health sweep / ping / install dial / test connection
  → McpRemoteClient ตรง เช่นกัน

scheduler / subagent → เส้นเดียวกับ chat → เส้นเดียวกัน → bypass เหมือนกัน
```

### 4.2 After

```text
Entry point (chat | scheduler | subagent | UI | health sweep | install | test)
└─ McpDurableGateway.ExecuteAsync / ExecuteOwnedAsync / ExecuteAttachedAsync
   1. PrepareActionAsync            → AgentStep (TaskAction) + ActionDispatching event
   2. Protocol feature matrix       → CapabilityUnavailable ถ้า build ไม่ implement
   3. sessions.Resolve(connectorId) → ConnectorManifestResolver:
                                      manifest file → schema validate → canonicalize
                                      → controlled-root check → SHA-256 → fingerprint
                                      → เทียบ trust record → permission-expansion check
                                      → LaunchDescriptor หรือ structured denial (มีชื่อ)
   4. ConnectorTrustPolicy.Evaluate → gate ที่สองบน manifest ที่ resolve แล้ว
   5. McpRiskPolicy.Classify        → 6 ชั้น จาก approved declaration เท่านั้น
   6. Journal.FindOpenByFingerprint → intent ค้าง = ไป reconcile ไม่ dispatch
   7. Journal.RecordAsync(intent)   → เขียนดิสก์ *ก่อน* byte แรกออก
   8. SessionProvider.Acquire       → stdio: AppContainer + Job Object (4.1A)
                                      http : HttpMcpTransport + endpoint authority
   9. dispatch ภายใต้ deadline      → timeout หลัง dispatch = UnknownOutcome
  10. UntrustedObservationBoundary  → ทุก payload เป็น data ไม่ใช่คำสั่ง
  11. provenance check (server identity + generation)
  12. reconcile ถ้า unknown         → lookup_by_idempotency_key → read-back → NeedsUser
  13. RecordResultAsync / PauseAsync(NeedsUser)
```

### 4.3 การแก้ที่ durable runtime (ไม่ใช่ MCP โดยตรง แต่จำเป็น)

`DurableTaskRuntime.CreateRunAsync` ส่ง run เข้าสถานะ `WaitingForClarification` เมื่อ skill router เห็นว่า goal กำกวม. connector operation ระบุครบแล้วตั้งแต่ก่อนสร้าง run (connector id + method + arguments) — การผ่าน gate นั้นจึงหยุดงานด้วยคำถามที่ไม่มีใครตอบได้ (`prompts/get` ล้มด้วย `MissingInformation` ก่อนแก้).
เพิ่ม `DurableRunRequest.OperationIsFullySpecified`; gateway ตั้งค่านี้เป็น `true` สำหรับ owned run. **ไม่ได้ปิด gate** — เฉพาะ run ที่ไม่มีอะไรให้ route เท่านั้นที่ข้าม

---

## 5. Manifest Resolver

`Infrastructure/Mcp/Connectors/ConnectorManifestReader.cs` + `ConnectorManifestResolver.cs`

### 5.1 Manifest schema (`connector.manifest.json` ใน package directory)

| Field | ครอบคลุมข้อกำหนด |
|---|---|
| `connector_id`, `name`, `version`, `publisher` | ConnectorId / Name / Version / Publisher |
| `executable` (relative) → canonicalized absolute | Executable path |
| SHA-256 คำนวณตอน resolve | Executable hash |
| `ComputeFingerprint(executableHash)` | Manifest fingerprint |
| `install_source` | Install source |
| `transport` (`stdio` / `http`), `server_url` | Transport |
| `protocol_version` | Protocol version |
| `workspace_access` | Requested workspace mounts |
| `requires_network` | Requested network access |
| `credentials` | Requested credentials |
| `tools` / `resources` / `prompts` | Tool/resource/prompt capabilities |
| `risk_summary` | Risk summary |
| `capabilities` + 3 ข้อบน = `ConnectorPermissionSet` | Permission set |
| `created_at` / trust record `LastValidatedUtc` | CreatedAt / LastValidatedAt |
| `read_only_tools` / `local_mutation_tools` / `external_mutation_tools` / `destructive_tools` / `verification_tools` | risk policy ที่ผู้ใช้อนุมัติ (แหล่งเดียวที่ทำให้ tool เป็น ReadOnly ได้) |

### 5.2 ลำดับการทำงาน (ตรงตาม 10 ข้อที่ prompt ระบุ)

1. locate: `{connectorRoot}/{connectorId}/` — connector id ที่มี `/ \ :` หรือเป็น `.`/`..` ถูกปฏิเสธก่อนแตะดิสก์
2. parse: ขนาดถูกเช็คก่อนอ่าน (`MaximumManifestBytes` = 64 KB), comment/trailing comma = ปฏิเสธ
3. validate schema: field ที่ขาด, id ไม่ตรงชื่อโฟลเดอร์, transport/protocol ที่ไม่รองรับ → refusal มีชื่อ
4. canonicalize: `Path.GetFullPath(Combine(installDir, declared))`
5. reject outside: ผลลัพธ์ต้องยังอยู่ใต้ package dir (`..\..\Windows\System32\cmd.exe` → `ConnectorPathNotControlled`) **และ** package ต้องอยู่ใน controlled root
6. compute executable hash: SHA-256
7. compute manifest fingerprint: hash ของ declaration ทั้งก้อน (รวม permission set + risk classification)
8. compare stored trust: state / enabled / version / publisher / install source / endpoint
9. detect permission expansion: `ConnectorPermissionSet.ExpansionsOver` → รายการที่เกินถูกส่งกลับไปแสดงบนหน้าจอ approval
10. return `ConnectorResolution` = LaunchDescriptor **หรือ** structured denial

**ไม่มี path ไหนคืน trusted connector จาก connector id อย่างเดียว** — `Resolve` อ่านไฟล์และ hash ใหม่ทุกครั้ง ไม่มี cache ของ verdict

### 5.3 Denial codes ที่ resolver ผลิต

`ConnectorNotInstalled`, `ConnectorUntrusted`, `ConnectorAwaitingTrust`, `ConnectorDisabled`, `ConnectorRevoked`, `ConnectorIdentityUnknown`, `ConnectorPathNotControlled`, `ConnectorExecutableMissing`, `ConnectorExecutableUnreadable`, `ConnectorExecutableChanged`, `ConnectorExecutablePathChanged`, `ConnectorEndpointChanged`, `ConnectorVersionChanged`, `ConnectorPublisherChanged`, `ConnectorInstallSourceChanged`, `ConnectorManifestChanged`, `ConnectorPermissionExpanded`, `ConnectorManifestMalformed`, `ConnectorManifestTooLarge`, `ConnectorManifestInvalid`, `ConnectorManifestIdMismatch`, `ConnectorTransportUnsupported`, `ConnectorProtocolUnsupported`

เหตุผลที่แยกละเอียดขนาดนี้: "manifest เปลี่ยน" ไม่บอกผู้ใช้ว่าควร re-approve หรือควรตกใจ

---

## 6. Approval และ Trust Flow

```text
Connector discovered      IConnectorManifestResolver.Discover()
→ Manifest inspected      ConnectorManifestReader (strict parse, bounded, traversal-checked)
→ Identity calculated     SHA-256 ของ executable + fingerprint ของ declaration
→ Permissions shown       ConnectorApprovalDialog (ข้อ 6.1)
→ User approves/denies    UserApprovalEvidence.FromUserGesture  ← มินต์ได้เฉพาะจาก gesture จริง
→ Trust record persisted  JsonConnectorTrustStore (atomic, นอก connector root)
→ Connector enabled       resolver คืน LaunchDescriptor ครั้งถัดไป
```

### 6.1 สิ่งที่หน้าจอ approval แสดง (ครบตาม prompt)

connector name · publisher (ไม่มี = "Unidentified publisher") · version · install source · executable path (หรือ server URL) · executable/manifest fingerprint แบบย่อจัดกลุ่ม 4 ตัวอักษร · workspace access · network access · credential access · tool risk summary · external side effects · update/reapproval policy · **badge `NEW` บนสิทธิ์ที่การอนุมัติเดิมไม่ครอบคลุม** · เหตุผลที่ต้อง re-approve · `Inspect details` (ค่าเต็มทุกฟิลด์รวม hash เต็ม)

Actions: **Approve** · **Approve this version** · **Deny** · **Disable** · **Revoke trust** · **Inspect details**

### 6.2 ห้ามตามที่ prompt ระบุ — กลไกที่บังคับจริง

| ข้อห้าม | กลไก |
|---|---|
| Auto mode approve connector | `IConnectorTrustAdministration` ทุก method ต้องการ `UserApprovalEvidence`; ไม่มี overload ที่ไม่ต้องการ |
| model คลิก approval | `AgentInputScope` — `Win32Input.SendInput` (จุดเดียวที่ synthetic input ทั้งหมดผ่าน) + `UiaClickTool` + `UiaSetValueTool` มาร์กช่วงเวลาที่ agent ขับ UI; `FromUserGesture` โยน `UnauthorizedAccessException` ถ้าอยู่ในช่วงนั้นหรือภายใน cooldown 1.5 วินาที (SendInput คืนค่าก่อนคลิกถึงปลายทาง) |
| approval จาก MCP output | ไม่มี field ใดใน response ที่ไปถึง trust store; `Hostile_Resource_Content_Arrives_As_Data_And_Grants_Nothing` (4.1B) + `A_Child_Cannot_Approve_A_Connector_For_Itself` |
| hidden background flow | resolver + gateway อ่านอย่างเดียว; ไม่มี auto-approve path; `ConnectorManifestResolver` ไม่มี write method |

**Protected consent policy**: หน้าจอนี้คือกรณีที่ prompt เตือนไว้โดยตรง — agent มี click tool และ dialog มีปุ่ม Allow. `AgentInputScope` มาร์กที่ **จุดผลิต** ไม่ใช่ที่ call site (มาร์กที่ห้า call site คือมาร์กที่หายไปที่หก) และ `ConnectorApprovalDialog.Decide` แปลง gesture ที่ถูกปฏิเสธเป็น "ยังไม่มีการตัดสินใจ" พร้อมเหตุผล ไม่ใช่ deny เงียบ ๆ

---

## 7. Trust Invalidation

| เหตุ | Denial code | ทดสอบ |
|---|---|---|
| executable hash เปลี่ยน | `ConnectorExecutableChanged` | `Replacing_The_Executable_After_Approval_Stops_It_Running` (E2E), `Replacing_The_Executable_After_Approval_Breaks_The_Approval` |
| executable path เปลี่ยน | `ConnectorExecutablePathChanged` | ครอบด้วย manifest fingerprint |
| manifest เปลี่ยน (รวมการจัดชั้นความเสี่ยง) | `ConnectorManifestChanged` | `Even_Narrowing_The_Declaration_Requires_A_New_Decision`, `Reclassifying_A_Destructive_Tool_As_Read_Only_Does_Not_Slip_Through` |
| version เปลี่ยน | `ConnectorVersionChanged` | `A_New_Version_Of_The_Same_Connector_Is_Not_Covered_By_The_Old_Approval` |
| workspace permission ขยาย | `ConnectorPermissionExpanded` | `Asking_For_More_Than_Was_Approved_Requires_A_New_Decision` |
| network permission ขยาย | `ConnectorPermissionExpanded` | `A_Connector_That_Starts_Asking_For_The_Network_Needs_A_New_Decision` (E2E) |
| credential requirement ขยาย | `ConnectorPermissionExpanded` | เดียวกัน |
| publisher / provenance เปลี่ยน | `ConnectorPublisherChanged` | `A_Different_Publisher_On_The_Same_Package_Invalidates_It` |
| install source เปลี่ยน | `ConnectorInstallSourceChanged` | `Claiming_A_Different_Install_Source_After_Approval_Breaks_It` |
| connector files ถูกแทนที่ | `ConnectorExecutableChanged` / `ConnectorExecutableMissing` | ข้างบน |
| endpoint (HTTP) เปลี่ยน | `ConnectorEndpointChanged` | `Repointing_An_Approved_Connector_At_Another_Server_Stops_It` (E2E) |
| disable / revoke | `ConnectorDisabled` / `ConnectorRevoked` | `Disabling_A_Connector_Survives_A_Restart`, `Revoking_Trust_Stops_The_Session_That_Is_Already_Running` |

**เมื่อ invalidate เกิดขึ้น**:
1. reject new operations — `Resolve` คืน denial, gateway คืน `Blocked` **พร้อม AgentStep**
2. stop/drain active session — `JsonConnectorTrustStore.TrustChanged` → `IsolatedMcpConnectorSessionProvider.OnTrustChanged` → `ReleaseAsync` (async โดยตั้งใจ: revocation ต้องไม่ block รอ server ที่กำลังเขียน)
3. pending mutation ตาม dispatch state — intent ที่ยังเปิดอยู่ **ไม่ถูกปิด**; `ReconcileOpenIntentsAsync` บน connector ที่ revoke แล้วคืน `NeedsUser` และปล่อย intent เปิดค้าง
4. durable evidence คงอยู่ — journal ไม่ถูกลบตอน revoke; trust record เก็บ hash เดิมไว้เพื่อให้ audit ตอบได้ว่าอะไรเคยถูกเชื่อ
5. reapproval requirement แสดงบน card + dialog (`ReapprovalReason`, `PreviousDenialCode`)
6. **ไม่มี auto-relaunch** — session ถูก drop และไม่มี path ไหนสร้างใหม่โดยไม่ผ่าน `Resolve`

---

## 8. Real Local Connector E2E

`Tests/Mcp/ProductionConnectorActivationTests.cs` — 15 tests, `Tests/Mcp/ProductionConnectorHost.cs`

### 8.1 การประกอบ — ทำไมถึงเป็นของจริง

`ProductionConnectorHost` เรียก **`SranDesktop.App.Hosting.AppHostBuilder.Build(args)`** ตัวจริง สิ่งเดียวที่ override คือ *ที่อยู่ของไฟล์*:

```
--Sran:ConnectorRoot=<temp>/connectors
--Sran:DataDirectory=<temp>/data
--Sran:ConnectorTrustFile=<temp>/data/connector-trust.json
```

ทั้งสามตัวเป็น **deployment seam จริง** (managed install บนแชร์อ่านอย่างเดียว / portable install / redirected profile) ไม่ใช่ hook ที่มีไว้ให้เทสต์. gateway, resolver, trust store, session provider, journal, durable runtime, sqlite ทั้งหมดมาจาก DI ของแอป

> เหตุผลที่ยืนยันข้อนี้: 4.1B ผ่านทุกเทสต์ด้วย graph ที่เทสต์ประกอบเอง ขณะที่ graph จริงคืน `null` ทุก connector — การเทสต์ composition ของตัวเองคือสิ่งที่ปิดบังช่องนั้นไว้

### 8.2 Package ที่ติดตั้ง

`ConnectorPackage.Install` คัดลอก `SranDesktop.McpFixtureServer` (image จริง) เข้า `{connectorRoot}/{id}/` แล้วเขียน `connector.manifest.json` ที่ประกาศ:
manifest ถูกต้อง · executable ที่ระบุตัวด้วย hash · no-argument read tool (`ping`) · argument read tool (`get_record`) · mutation tool (`create_record`) · resource (`fixture://readme`) · prompt (`summarize`) · progress notification · disconnect-after-dispatch (profile `mutate-then-crash` / `silent-mutation`) · persistent state สำหรับ reconciliation (`lookup_by_idempotency_key`, `stats`)

**การติดตั้งไม่ approve อะไรเลย** — package ที่เพิ่งลงคือ `ConnectorUntrusted`

### 8.3 เส้นทางที่ผ่านครบ

```text
Discover connector    → The_Production_Resolver_Finds_An_Installed_Package
Show approval         → ConnectorApprovalRequest ประกอบจาก declaration + hash + fingerprint
User approval         → ApproveAsync(UserApprovalEvidence.FromUserGesture(...))
Persist trust         → The_Trust_Record_Survives_A_Restart_And_Names_What_Was_Approved
Launch isolated       → AppContainer + Job Object (session provider ตัวเดียวกับ 4.1A)
Initialize            ┐
tools/list            │
tools/call            ├ An_Approved_Connector_Runs_The_Whole_Protocol_Through_The_Gateway
resources/list+read   │   (7 operations, 7 ActionId ที่ไม่ซ้ำกัน, ทุกตัวมี TaskAction จริงใน sqlite)
prompts/list+get      ┘
Record AgentSteps     → GetActionAsync(result.ActionId) ไม่เป็น null ทุกตัว
Shutdown              → DisposeAsync ของ host ปิด session + คืน handle sqlite
```

`An_Installed_But_Unapproved_Connector_Fails_Closed` และ `An_Unapproved_Connector_Is_Refused_And_Still_Leaves_A_Step` พิสูจน์ฝั่งตรงข้าม: ไม่ approve = ไม่ run แต่ยังมีร่องรอย

---

## 9. Mutation Restart Evidence

`Tests/Mcp/ProductionConnectorRuntimeTests.cs`

```text
AgentStep created           ScheduledRunAsync + ApproveActionAsync
→ Intent persisted          journal เขียนก่อน dispatch (invariant test ล็อกลำดับในซอร์ส)
→ Mutation dispatched       create_record + _meta.idempotencyKey
→ Server changes state      fixture commit
→ Connection lost           profile mutate-then-crash (Exit(9)) / silent-mutation (เงียบ)
→ UnknownOutcome            stage ≥ Writing → OutcomeUnknown; timeout หลัง dispatch ก็เช่นกัน
→ Desktop runtime restarts  host.DisposeAsync() แล้วสร้าง ProductionConnectorHost ใหม่บนไฟล์เดิม
→ Trust revalidated         resolver อ่าน manifest + hash ใหม่ทั้งหมด
→ MCP session recreated     generation ใหม่
→ Server state queried      lookup_by_idempotency_key → get_record
→ Fingerprint matched
→ Step completed without redispatch
```

| Acceptance | หลักฐาน |
|---|---|
| created object count = **1** | `CreatedCountOnAFreshServerAsync` ถาม `stats.created` จาก **ฝั่ง server** หลัง reconcile — ได้ 0 (ไม่มี mutation ใหม่เกิดขึ้นในรอบที่สอง) |
| duplicate dispatch = **0** | `A_Mutation_Interrupted_After_Dispatch_Is_Not_Repeated_By_The_Next_Process` |
| false success = **0** | ไม่มีเส้นทางไหนคืน `Succeeded` โดยไม่อ่าน state กลับ; `ReconciledWithoutRedispatch` เป็นจริงเฉพาะเมื่อสำเร็จผ่านการอ่านกลับ |
| durable event ordering | `The_Intent_Is_Recorded_Before_The_Session_Is_Even_Acquired` (source invariant) + journal บนดิสก์ |
| unresolved → NeedsUser | `An_Unresolvable_Mutation_Needs_A_Person_And_Is_Never_Auto_Retried` (`Retryable == false`, intent เปิดค้าง) |

**เคสเพิ่มตามที่ prompt ขอ**

| เคส | ผล | ทดสอบ |
|---|---|---|
| connector revoked ก่อน reconciliation | `NeedsUser`, intent เปิดค้าง | `A_Connector_Revoked_Before_Reconciliation_Leaves_The_Intent_Open` |
| connector hash เปลี่ยนก่อน restart | `Blocked` / `ConnectorExecutableChanged` | `A_Scheduled_Firing_After_The_Binary_Changed_Is_Refused` |
| connector disabled หลัง dispatch | `Blocked` / `ConnectorDisabled` ข้าม restart | `Disabling_A_Connector_Survives_A_Restart` |
| server state ตรวจไม่ได้ | `NeedsUser` (`ReconciliationUnreachable` / `ReconciliationTimedOut`) ไม่ใช่ retry | `An_Unresolvable_Mutation_...`, gateway reconcile deadline |

---

## 10. Scheduler Evidence

| Requirement | ทดสอบ | ผล |
|---|---|---|
| scheduler AgentRun | `A_Scheduled_Read_Is_A_Scheduler_Triggered_Step_Through_The_Gateway` | `TriggerType == Scheduler` |
| resolve trusted connector | เดียวกัน | ผ่าน production resolver |
| create AgentStep | เดียวกัน | `GetActionAsync(ActionId)` ไม่เป็น null |
| dispatch ผ่าน gateway | ทุกเทสต์ในไฟล์ | ✅ |
| verify result / persist history | sqlite จริง | ✅ |
| prevent duplicate after restart | `A_Mutation_Interrupted_After_Dispatch_...` | duplicate = 0 |
| scheduled read-only | `A_Scheduled_Read_...` | ✅ |
| scheduled mutation | `A_Scheduled_Mutation_Records_Its_Intent_Before_It_Dispatches` | risk `ExternalMutation`, fingerprint ตรง |
| connector AwaitingTrust | `An_Installed_But_Unapproved_Connector_Fails_Closed` + `A_Migrated_Legacy_Record_Becomes_Runnable_Only_After_A_Person_Approves` | ✅ |
| connector disabled | `Disabling_A_Connector_Survives_A_Restart` | ✅ |
| hash changed | `A_Scheduled_Firing_After_The_Binary_Changed_Is_Refused` | ✅ |
| disconnect after dispatch | `A_Mutation_Whose_Answer_Is_Lost_Is_Reconciled_Rather_Than_Repeated` | ✅ |
| app restart | เดียวกัน (host ใหม่ทั้งตัว) | ✅ |
| duplicate trigger | `stats.created == 0` | ✅ |
| revoked connector | `A_Scheduled_Firing_Against_A_Revoked_Connector_Is_Refused_With_A_Step` | ✅ |

---

## 11. Subagent Evidence

| Requirement | ทดสอบ | ผล |
|---|---|---|
| durable child AgentRun | `A_Child_Run_Reads_Through_Its_Own_Step_And_Keeps_Its_Provenance` | `ParentRunId` ตรง, `TriggerType == Subagent` |
| scoped connector permission | `A_Child_Cannot_Reach_A_Connector_That_Is_Not_Trusted` | trust เป็นสมบัติของ connector ไม่ใช่ของผู้เรียก |
| create MCP AgentStep | เทสต์แรก | action อยู่ใต้ child run ไม่ใช่ parent |
| use gateway | ทั้งหมด | ✅ |
| preserve provenance | เทสต์แรก | ✅ |
| return structured result | ทั้งหมด | `McpOperationResult` |
| not broaden trust | `A_Child_Cannot_Approve_A_Connector_For_Itself` | trust state ไม่ขยับ |
| not approve/install connector | เดียวกัน | ไม่มี capability name ใดที่ไปถึง trust store |
| child read operation | เทสต์แรก | ✅ |
| child mutation | `A_Second_Child_Still_Works_After_A_Sibling_Loses_Its_Answer` | ✅ |
| child disconnect → NeedsUser/reconcile | เดียวกัน | ✅ |
| second child succeeds | เดียวกัน | sibling ยัง `Succeeded` |
| partial result preserved | เดียวกัน | ✅ |
| parent cancellation | ครอบด้วย durable runtime (4.1B) | — |
| child tries broader tool | `A_Child_Cannot_Reach_A_Connector_That_Is_Not_Trusted` | ✅ |

---

## 12. Security Evidence

`Tests/Security/ConnectorTrustAdversarialTests.cs` (22 tests) + `Tests/Security/McpRuntimeBypassInvariantTests.cs` (12 tests)

| การโจมตี | ผล | ทดสอบ |
|---|---|---|
| manifest path traversal | ปฏิเสธ, ไม่แสดงเป็น declaration | `A_Manifest_Pointing_Outside_Its_Own_Package_Is_Refused` |
| executable outside controlled directory | ปฏิเสธ | เดียวกัน + `Approving_A_Connector_Outside_The_Managed_Location_Is_Refused` |
| executable replacement after approval | ปฏิเสธ | `Replacing_The_Executable_After_Approval_Breaks_The_Approval` |
| hash mismatch | ปฏิเสธ | เดียวกัน |
| manifest permission expansion | ปฏิเสธ + แจกแจงรายการ | `Asking_For_More_Than_Was_Approved_Requires_A_New_Decision` |
| **risk reclassification** (delete → read-only) | ปฏิเสธ | `Reclassifying_A_Destructive_Tool_As_Read_Only_Does_Not_Slip_Through` |
| fake publisher | ปฏิเสธ | `Claiming_A_Different_Publisher_After_Approval_Breaks_It` |
| impersonate another connector's approval | ปฏิเสธ | `A_Manifest_Naming_A_Different_Connector_Cannot_Borrow_Its_Approval` |
| legacy state auto-enable | ไม่เกิด | `A_Legacy_Record_Without_An_Identity_Is_Never_Auto_Enabled` |
| connector แก้ trust store ของตัวเอง | ไม่ถึงไฟล์ | `A_Connector_Rewriting_The_Trust_File_Gains_Nothing_It_Can_Reach` |
| trust store เสียหาย | อ่านเป็น "ไม่มีอะไรถูกอนุมัติ" | `An_Unreadable_Trust_Store_Means_Nothing_Is_Approved` |
| **agent คลิก approval dialog เอง** | ปฏิเสธ | `An_Approval_Cannot_Be_Taken_While_The_Agent_Is_Driving_The_Screen`, `A_Click_Just_After_Synthetic_Input_Is_Still_Refused` |
| malicious MCP prompt ขอ approval | ไม่มีผล | `A_Child_Cannot_Approve_A_Connector_For_Itself` + observation boundary |
| malicious resource สั่ง launch tool | ไม่มีผล | `Hostile_Resource_Content_Arrives_As_Data_And_Grants_Nothing` (4.1B, ยังเขียว) |
| server ขอ workspace กว้างขึ้น | ปฏิเสธ | permission expansion |
| connector ใช้ network ทั้งที่ถูกปฏิเสธ | ปฏิเสธ | `The_Server_Cannot_Reach_The_Network` (4.1B) |
| connector spawn descendant | ตายพร้อม Job Object | `A_Server_Spawned_Child_Dies_With_The_Session` (4.1B) |
| malformed manifest | refusal ไม่ใช่ exception | `A_Malformed_Manifest_Is_A_Refusal_Not_An_Exception` |
| oversized manifest | ปฏิเสธก่อน parse | `An_Oversized_Manifest_Is_Rejected_Before_It_Is_Parsed` |
| connector id เป็น path | ปฏิเสธก่อนแตะดิสก์ | `A_Connector_Id_That_Is_A_Path_Cannot_Escape_The_Managed_Root` |
| server สร้าง connector ในโฟลเดอร์ workspace ของตัวเอง | ไม่ถูกนับเป็น install | `The_Sandbox_Workspace_Directory_Is_Not_Mistaken_For_An_Install` |
| pre-install test ใช้ launch local binary | ปฏิเสธ | `A_Test_Grant_Cannot_Be_Used_To_Run_A_Local_Executable` |
| pre-install grant ค้างเป็น approval | ไม่ค้าง | `A_Test_Grant_Disappears_When_Its_Handle_Is_Released` |

| Acceptance | ผล |
|---|---|
| unauthorized launch | **0** |
| trust escalation | **0** |
| host fallback | **0** (`StdioMcpTransport` ยัง `throw Blocked()`) |
| secret inheritance | **0** (4.1B invariants ยังเขียว) |
| process / temp leak | **0** (วัดหลัง full regression: worker survivors 0, temp dirs 0) |

**การกระชับ attack surface ที่ทำเพิ่ม**: `McpRemoteClient` ctor ที่รับ URL เป็น `string` เปลี่ยนจาก `public` เป็น `internal` — constructor ที่แปลงสตริงเป็น live connection คือทางลัดที่สั้นที่สุดรอบ gateway และไม่มี production call site ไหนต้องใช้มันแล้ว

### 12.1 Architecture tests ที่ fail ถ้ามีใคร bypass

`McpRuntimeBypassInvariantTests` — 12 tests (เดิม 9):

1. ไม่มีไฟล์ production ไหนเรียก protocol call นอก allowlist (**allowlist หด**: `McpConnectorService.cs` ถูกถอดออก)
2. gateway ใช้ `PrepareActionAsync` / `RecordResultAsync` / `ConnectorTrustPolicy.Evaluate` / `_journal.RecordAsync` / `ReconcileAsync` จริง
3. `_journal.RecordAsync(intent)` อยู่ *ก่อน* `_sessions.AcquireAsync` ในซอร์ส
4. มีแต่ session provider ที่ `new IsolatedStdioMcpTransport(`
5. มีแต่ session provider ที่ `new McpRemoteClient(` (**เดิมอนุญาต `McpConnectorService` ด้วย**)
6. `StdioMcpTransport` ต้องยังมี `Blocked(`
7. `LocalMcpConnectorTool` เรียก `ExecuteAttachedAsync` และไม่สร้าง client เอง
8. `AppHostBuilder` ลงทะเบียน gateway + journal + installation policy
9. allowlist ไม่มี entry ที่ไฟล์หายไปแล้ว
10. **ใหม่** — `AppHostBuilder` ต้อง compose `ConnectorManifestResolver(` + `IConnectorTrustStore` + `IConnectorTrustAdministration` และ **ห้ามมี `_ => null,`** (regression นี้เกิดขึ้นจริงใน 4.1B)
11. **ใหม่** — ต้องมี `ConnectorApprovalDialog` ที่ mint `UserApprovalEvidence.FromUserGesture(` และมี VM ที่เปิดมันได้
12. **ใหม่** — `Win32Input` ต้องมี `AgentInputScope.Touch(`, UIA tools ต้องมี `AgentInputScope.Enter(`, และ approval ต้องเรียก `IsAgentInputInFlight(`

---

## 13. Tests

| Test group | Passed | Failed | หมายเหตุ |
|---|---:|---:|---|
| `ProductionConnectorActivationTests` (**ใหม่**) | 15 | 0 | AppHostBuilder จริง + fixture server จริงใน AppContainer |
| `ProductionConnectorRuntimeTests` (**ใหม่**) | 12 | 0 | mutation/restart/scheduler/subagent บน production graph |
| `ConnectorTrustAdversarialTests` (**ใหม่**) | 22 | 0 | resolver / trust store / approval gate |
| `McpRuntimeBypassInvariantTests` (ขยาย 9→12) | 12 | 0 | architecture invariants |
| `McpConnectorHealthSweepTests` (เขียนใหม่) | 4 | 0 | production graph + loopback MCP server จริง |
| `StdioConnectorTests` (เขียนใหม่) | 8 | 0 | legacy stdio record ไม่กลายเป็นของที่รันได้ |
| `McpDurableRuntimeTests` / `McpSchedulerSubagentTests` / `McpRiskPolicyTests` / `ConnectorTrustPolicyTests` / `LocalMcpRuntimeTests` (4.1A/4.1B) | ไม่ถูกแตะ | 0 | ยังเขียวทั้งหมด |
| **.NET ทั้งหมด** | **1680** | **0** | 4 skipped = privileged symlink (ค้างจาก Phase 3.1, ไม่มี Developer Mode) |
| WPF `UiTests` | 5 | 0 | |
| Solution build | ผ่าน | 0 errors | |

Baseline ก่อนเริ่ม 4.1C: 1625 passed / 0 failed / 4 skipped
ตอนนี้: **1684 total — 1680 passed / 0 failed / 4 skipped** (+59 tests)

**Leak measurement (หลัง full regression)**: worker survivors = **0**, temp dir ของเทสต์ 4.1C = **0**, sandbox job dirs ใหม่ = **0**

> **บั๊กใน test teardown ที่เจอและแก้**: การลบ temp root ครั้งเดียวแพ้การแข่งกับ job object ที่กำลังปล่อย connector image และ virus scanner ที่กำลังอ่าน DLL ที่เพิ่งคัดลอก — เหลือไดเรกทอรีค้าง 1 อันหลัง run แรก ซึ่งจะไปโผล่เป็น "leak" ในตัววัดที่เทสต์ชุดนี้มีไว้ทำให้ซื่อสัตย์พอดี. แก้ด้วย `ProductionConnectorHost.DeletePatientlyAsync` (รอ 40×200ms + บังคับ finalizer หนึ่งครั้ง) วัดซ้ำได้ 0

> **หมายเหตุเรื่อง flake ที่ต้องพูดตรง ๆ**: ใน run แรกของ full regression มี 3 เทสต์ตก — `DurableTaskRuntimeTests.Non_Actionable_Request_...` (ลบ `data.db` ไม่ได้ — flake เดิมที่ 4.1B บันทึกไว้) และ `UnifiedRuntimeEntryPointTests` สองตัวที่ต้องใช้หน้าต่างจริงของ ReliabilityFixture (OCR อ่านได้แค่ title bar). ทั้งสามตัว **ผ่านใน run ถัดมาโดยไม่มีการแก้โค้ดใด ๆ** และจุดที่ล้มอยู่ **ก่อน** การส่ง input ทุกครั้ง (ที่ขั้น capture/OCR/template-match) ซึ่งไม่มีไฟล์ที่เฟสนี้แก้อยู่บนเส้นทางนั้นเลย — เป็นสภาพเดสก์ท็อประหว่างรัน ไม่ใช่ผลจาก 4.1C

---

## 14. Remaining Risks

**P1**

1. **`ConnectorApprovalDialog` ยังไม่มี live GUI verification** — มี unit/architecture test ครอบ view-model, evidence gate และการเชื่อมต่อ แต่ยังไม่ได้กด Approve ด้วยมือบนแอปที่ publish แล้ว (เทียบกับข้อจำกัด RS-001 ที่ค้างจาก runtime stabilization)
2. **Marketplace HTTP connector ที่ผู้ใช้ติดตั้งไว้ก่อน 4.1C จะกลายเป็น "ยังไม่อนุมัติ"** — install record ไม่มี trust record จึงถูกปฏิเสธด้วย `ConnectorUntrusted` และแสดงเป็น `NotConfigured`. นี่คือพฤติกรรมที่ prompt สั่ง (legacy ต้อง re-approve) แต่ผู้ใช้จะต้องกด install/approve อีกครั้งหนึ่งรอบ — ควรมีข้อความอธิบายใน release note
3. **`ConnectorManifestResolver.Resolve` hash executable ทุกครั้ง** — สำหรับ binary ขนาดใหญ่และ tool call ถี่ ๆ นี่คือ I/O ต่อ operation. ยอมรับได้ตอนนี้ (session ถูก cache, resolve เกิดต่อ operation ไม่ใช่ต่อ byte) แต่ถ้าเป็นคอขวดควรแคชด้วย (path, size, mtime, fileId) ไม่ใช่ด้วย connector id

**P2**

4. pagination / resource subscriptions / resource-change notifications / completions ยังไม่ implement — คืน `CapabilityUnavailable` แบบมีโครงสร้าง (คงจาก 4.1B)
5. OAuth / credential broker สำหรับ local connector ยังไม่ทำ — เป็นหัวข้อของ 4.2
6. `AgentInputScope` cooldown เป็นค่าคงที่ 1.5 วินาที — ถ้าเครื่องช้ามาก คลิกที่ agent สั่งอาจมาถึงหลัง cooldown. ยังปลอดภัยกว่าไม่มี แต่ทางที่แข็งกว่าคือ marker บน `dwExtraInfo` ของ `SendInput` แล้วอ่านกลับที่ WPF hook (งานเฟสถัดไป)
7. HTTP connector ไม่มี executable identity ให้ hash — identity คือ endpoint fingerprint. connector ที่ URL เดิมแต่ server เปลี่ยนเจ้าของตรวจไม่ได้จากฝั่งนี้ (ต้องพึ่ง TLS/pinning ซึ่งยังไม่ทำ)
8. `ConnectorRiskPolicy` มาจาก manifest ของ package เอง — ผู้ใช้อนุมัติมันตอน approval และ fingerprint ล็อกไว้ แต่ยังไม่มี catalog-level signature ที่ยืนยัน publisher

**Environment limitation**

9. คงเดิม — ไม่มี Developer Mode (privileged symlink tests ยัง skip), จอเดียว, ไม่มี provider credential

---

## 15. Final Verdict

1. **every MCP operation creates AgentStep หรือไม่** — **ใช่** สำหรับ production code ทั้งหมด: chat tool call, connector dial, health ping, install dial, pre-install test, scheduler, subagent, และ operation ที่ถูกปฏิเสธ. ยืนยันจาก `TaskAction` + `ActionDispatching` บน sqlite จริง และล็อกด้วย architecture test ที่ allowlist หดลงจนไม่มี call site แบบเดิมเหลือ
2. **production resolver ใช้งานได้จริงหรือไม่** — **ใช่**. `ConnectorManifestResolver` อ่าน package จาก controlled root, validate schema, canonicalize + กัน traversal, hash, fingerprint, เทียบ trust, ตรวจ permission expansion และคืน launch descriptor หรือ denial ที่มีชื่อ. มี architecture test ที่ **fail ถ้ามีใครใส่ `_ => null,` กลับเข้าไป**
3. **ผู้ใช้ approve connector ได้จริงหรือไม่** — **ได้**. `ConnectorApprovalDialog` แสดงครบทุกฟิลด์ที่ prompt ระบุ พร้อม Approve / Approve-this-version / Deny / Disable / Revoke / Inspect และบันทึกผลลง trust store ที่อยู่รอด restart. *ข้อจำกัดที่ต้องพูดตรง ๆ*: ยังไม่ได้กดด้วยมือบนแอปจริง (ข้อ 14.1)
4. **trusted local connector launch isolated ได้จริงหรือไม่** — **ได้**. fixture MCP server รันใน AppContainer + Job Object ผ่าน `AppHostBuilder` ตัวจริง และเดินครบ initialize / tools / resources / prompts / mutation
5. **untrusted connector launch ได้หรือไม่** — **ไม่ได้**. `ConnectorUntrusted` ก่อนถึง transport; unapproved / awaiting / disabled / revoked / identity-unknown ล้วนปฏิเสธ **พร้อมทิ้ง AgentStep ไว้**
6. **hash/manifest change invalidate trust หรือไม่** — **ใช่ ทั้งคู่** และแยกชื่อได้ 8 แบบ (executable / path / endpoint / version / publisher / install source / permission / manifest) เพื่อให้ผู้ใช้รู้ว่าควร re-approve หรือควรตกใจ
7. **scheduler/subagent ใช้ gateway จริงหรือไม่** — **ใช่**. ทั้งสองเข้าผ่าน `IMcpToolHost` → tool → gateway เส้นเดียวกับ chat; child run มี step ของตัวเอง, provenance ครบ, และขยาย trust ไม่ได้
8. **mutation restart สร้าง duplicate หรือไม่** — **ไม่**. วัดจาก `stats.created` ฝั่ง server หลัง restart จริง (host ใหม่ทั้งตัวบนไฟล์เดิม) = 0. ไม่มี auto-replay path สำหรับอะไรที่ไม่ใช่ read
9. **host fallback เหลือหรือไม่** — **ไม่เหลือ**. `StdioMcpTransport` ยัง `throw Blocked()`; custom stdio connector ที่พิมพ์ command line เข้ามาถูกปฏิเสธตั้งแต่ `InstallCustomAsync`; pre-install test ของ stdio ถูกปฏิเสธ; image หาย → `SandboxUnavailableException`
10. **พร้อม Phase 4.2 OAuth / Connector Lifecycle หรือไม่** — **พร้อม**. trust record, approval flow, invalidation, และ endpoint authority (`IConnectorEndpointAuthority`) เป็นที่วางของ credential broker ได้ตรง ๆ; `CredentialRequirements` มีอยู่ใน manifest และแสดงบนหน้าจอ approval แล้ว รอเพียงตัว broker
11. **blocker สามอันดับแรก** —
    1. **live GUI verification ของหน้าจอ approval** — ทุกอย่างอื่นในเฟสนี้มีหลักฐาน runtime; อันนี้มีแต่หลักฐานระดับโค้ด
    2. **credential broker สำหรับ connector ที่ต้องใช้ความลับ** (4.2) — ตอนนี้ HTTP connector ใช้ DPAPI/portal JWT ผ่าน `IConnectorEndpointAuthority` ส่วน local connector ยังรับ credential ไม่ได้เลย
    3. **publisher signature / catalog provenance** — trust ผูกกับ hash ของสิ่งที่อยู่บนดิสก์ ซึ่งตอบว่า "เหมือนตอนที่คุณอนุมัติไหม" ได้ แต่ยังตอบไม่ได้ว่า "สิ่งที่คุณอนุมัติมาจากคนที่มันอ้างจริงไหม"

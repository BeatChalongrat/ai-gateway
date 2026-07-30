# SRAN Desktop — Phase 4.1A: Interactive Isolated Worker Transport

> วันที่: 2026-07-28 · สถานะ: **PASS**
> ยังไม่ commit / ไม่ push ตามคำสั่ง
> local MCP stdio **ยัง fail-closed** ตามข้อกำหนด (จะเปิดใน Phase 4.1B)

---

## 1. Executive Result

| หัวข้อ | ผล |
|---|---|
| Interactive transport (long-lived, bidirectional) | **ผ่าน** — 25/25 runtime tests บน worker process จริงใน AppContainer |
| stdin คงเปิดตลอด session | **ผ่าน** — 20 requests ต่อเนื่องบน process เดียว (PID ไม่เปลี่ยน) |
| Concurrent correlation + out-of-order | **ผ่าน** |
| Notification แยกจาก response | **ผ่าน** |
| Dispatch boundary (pre/post) | **ผ่าน** — แยก `FailedNoEffect` กับ `UnknownOutcome` ได้จริง |
| Crash/restart recovery ระดับ transport | **ผ่าน** |
| Process/temp leak | **0** (sandbox job dirs 0, worker survivors 0) |
| Regression | **ผ่าน** — .NET 1523 passed / 0 failed / 4 skipped, UiTests 5/5, solution build ผ่าน |
| local MCP fail-closed | **คงเดิม** (`throw Blocked()` ยังอยู่) |
| พร้อมต่อ Phase 4.1B (MCP Protocol Runtime) | **พร้อม** |

---

## 2. Architecture Before / After

**Before**
```text
AgentStep → RunWorkerAsync → AppContainer process
             ├─ stdin ปิดทันทีหลัง AssignProcessToJobObject
             ├─ 1 request = 1 process
             └─ ผลลัพธ์อ่านจากไฟล์หลัง process จบ
```

**After** (เพิ่มเส้นทางที่สอง — เส้นทางเดิมไม่ถูกแตะ)
```text
AgentStep
→ StartInteractiveWorkerAsync   (AppContainer + Job Object + quota เดิม)
→ InteractiveIsolatedProcess    (stdin เปิดค้าง, stdout/stderr อ่าน async)
→ InteractiveWorkerSession
      ├─ InteractiveFrameCodec  (length-prefixed JSON, cap 4 MB)
      ├─ correlation map        (request id → DispatchStage)
      ├─ notification channel   (แยกจาก response)
      ├─ cancellation           (protocol cancel + bounded grace)
      ├─ generation binding     (frame รุ่นเก่าถูกทิ้ง)
      └─ durable events         (13 ชื่อ)
→ InteractiveRequestResult      (Outcome + Stage + RequiresReconciliation)
```

---

## 3. Files Changed

| File | Change | Reason |
|---|---|---|
| `Mcp/Tools/WindowsAppContainerProcessRunner.cs` | เพิ่ม `StartInteractiveWorkerAsync` + `LaunchInteractive` + nested `InteractiveIsolatedProcess`; ทำ `SafeJobHandle`/`SafeSidHandle` เป็น internal | คง stdin เปิด โดยไม่แตะ batch path และไม่ขยาย allowlist ของ `run_command` |
| `Application/Models/InteractiveWorkerContracts.cs` | ใหม่ | session state machine, `DispatchStage`, `InteractiveRequestOutcome`, frame, event names |
| `Infrastructure/Workers/InteractiveFrameCodec.cs` | ใหม่ | framing ที่ไม่ผูกกับ MCP + ปฏิเสธ length ที่เกิน cap ก่อน allocate |
| `Infrastructure/Workers/InteractiveWorkerSession.cs` | ใหม่ | correlation, dispatch boundary, cancellation, stderr quota, cleanup |
| `Infrastructure/Workers/WorkerImageResolver.cs` | เพิ่ม capability `interactive-fixture` | image สำหรับ acceptance |
| `src/SranDesktop.InteractiveFixtureWorker/` | ใหม่ (project) | fixture worker แบบ deterministic (source-link codec ตัวจริง) |
| `Tests/Workers/InteractiveWorkerTransportTests.cs` | ใหม่ | 25 runtime tests ผ่าน sandbox จริง |
| `Tests/SranDesktop.Tests.csproj` | เพิ่ม target stage fixture image | ให้ resolver หา image เจอ |
| `SranDesktop.sln` | เพิ่ม 2 projects | DocumentWorker + InteractiveFixtureWorker |

---

## 4. Isolation Evidence

| ข้อ | หลักฐาน |
|---|---|
| AppContainer จริง | ใช้ `AcquireProfile` + `SecurityCapabilities` ตัวเดียวกับ batch path |
| Job Object | `CreateConstrainedJob` (kill-on-close, active-process 16, mem 512/768 MB) |
| CPU cap | `ApplyCpuQuota()` เรียก **หลัง** handshake — ตั้งตอน thread ยัง suspended ทำให้ CLR ใน lowbox startup ล้ม (บทเรียนจาก batch path) |
| Host secrets | test ตั้ง env จริงแล้วให้ worker อ่าน → ได้ `(absent)` |
| Network denied | worker ต่อ loopback listener จริง → `blocked` |
| Explicit mounts | mount ที่อนุญาตถูก stage **เข้า** อย่างเดียว ไม่ sync กลับ — long-lived server เขียนทับไฟล์ผู้ใช้ไม่ได้ |
| working dir นอก mount | ปฏิเสธก่อน launch |
| executable ไม่มาจาก model | เลือกด้วย capability name; `dotnet.exe` ถูก validate แยกจาก allowlist ของ `run_command` |
| no host fallback | image หาย → `SandboxUnavailableException` (fail closed) |
| process tree cleanup | fixture spawn child จริง (ยืนยันว่ามีชีวิตก่อน teardown) → ตายทั้งคู่หลัง dispose |
| stderr quota | flood ~800 KB ต่อ quota 256 KB → session ตัดจบ ไม่บล็อก host |

---

## 5. Framing และ Correlation Evidence

| Scenario | ผล |
|---|---|
| 20 sequential requests | ผ่าน, PID เดิมตลอด |
| concurrent + out-of-order | slow(700ms) ออกก่อน, fast ตอบก่อน, ทั้งคู่ได้ payload ของตัวเอง |
| notification ระหว่างรอ response | 2 notification เก็บแยก, response ยังเป็น `done` |
| ack → DispatchStage.Acknowledged | ผ่าน (durable event `DispatchAcknowledged`) |
| duplicate response | ตัวที่สองถูกทิ้ง, caller ได้ `first`, session ใช้ต่อได้ |
| late response (หลัง cancel) | ถูกทิ้ง, session ยังใช้งานได้ |
| frame ต่าง generation | ถูกทิ้ง → request timeout เป็น unknown แทนที่จะถูกตอบผิด |
| malformed frame | session → `ProtocolFailed`, host ไม่ crash |
| oversized frame | ปฏิเสธจาก **declared length** ไม่ allocate |
| large payload 512 KB | ผ่าน (ข้าม pipe read หลายรอบ) |

---

## 6. Dispatch Boundary Evidence

| สถานการณ์ | Stage | Outcome | ต้อง reconcile |
|---|---|---|---|
| cancel ก่อนเขียน | `Queued` | `CancelledBeforeDispatch` | **ไม่** |
| session ตายแล้วส่งใหม่ | `Queued` | `FailedNoEffect` | **ไม่** |
| worker หายกลางการเขียน | `Writing` | `UnknownOutcome` | ใช่ |
| timeout หลังส่ง | `Dispatched`/`Acknowledged` | `UnknownOutcome` | ใช่ |
| cancel หลังส่ง (worker ไม่สนใจ) | `Dispatched` | `CancelledAfterDispatchUnknown` | ใช่ |
| worker crash หลังรับ request | `Dispatched` | `UnknownOutcome` | ใช่ |
| worker ตอบ error | `Completed` | `FailedByWorker` | **ไม่** (รู้ผลแล้ว) |

การเขียน frame ตั้งใจ **ไม่ cancellable** — ยกเลิกกลางการเขียนจะทำให้ stream เสียสำหรับ request อื่นทั้งหมด

---

## 7. Crash / Restart Evidence

| Scenario | ผล |
|---|---|
| crash ก่อน handshake | `StartAsync` throw, session ไม่เคยเป็น Ready |
| crash หลัง ready (ระหว่าง request) | `UnknownOutcome` + event `UnknownOutcomeDetected` |
| host restart กลาง request | in-flight → `UnknownOutcome`; worker process ตายตาม; session ใหม่ได้ generation ใหม่ (> เดิม) และใช้งานได้ |
| force terminate | `Terminate()` ฆ่าทั้ง Job Object |
| duplicate/late/stale response | ถูก reject ทั้งหมด |

---

## 8. Cleanup และ Soak

Soak: 5 sessions × 5 requests ต่อเนื่อง

| ตัวชี้วัด | ผล |
|---|---|
| sandbox job directories ค้าง | **0** |
| worker process ค้าง (fixture/document) | **0** (ยืนยันด้วย `Win32_Process` command-line match) |
| scratch directories | 0 |
| `FailedTemporaryCleanups` counter | 0 |

---

## 9. Tests

| Test group | Passed | Failed | Skipped | Notes |
|---|---:|---:|---:|---|
| Interactive transport (sandbox จริง) | 25 | 0 | 0 | ไม่มี in-process mock |
| .NET ทั้งหมด | 1523 | 0 | 4 | skip = privileged symlink (Phase 3.1) |
| WPF UiTests | 5 | 0 | 0 | |
| Solution build | ผ่าน | 0 | | |

ครอบคลุม 24 scenario ที่ prompt กำหนด ยกเว้นที่ระบุในข้อ 11

---

## 10. Security Regression

| ข้อ | ผล |
|---|---|
| local MCP ยัง fail-closed | ✅ `StdioMcpTransport` ยัง `throw Blocked()` |
| `run_command` allowlist ไม่ถูกขยาย | ✅ `ValidateExecutable` เดิมไม่ถูกแตะ |
| batch worker path ไม่ถูกแตะ | ✅ `RunWorkerAsync` เดิมทำงานเหมือนเดิม (document tests เขียว) |
| secret ใน log/durable | ✅ malformed frame ไม่ log raw body; durable event มีแค่ id/stage |
| architecture invariant test | ✅ `RuntimeBypassInvariantTests` ยังเขียว (ไม่มี tool ใหม่เรียก `Process.Start`) |

---

## 11. Remaining Risks

**P1**
1. **Heartbeat ยังไม่มีตัวส่งจริง** — มี `LastHeartbeatUtc` + frame type `heartbeat` แต่ยังไม่มี timer ฝั่ง host และไม่มี heartbeat-timeout test เจตนา (session detect การตายผ่าน EOF/crash แทน ซึ่งครอบคลุมกรณีจริงส่วนใหญ่) — ควรเติมใน 4.1B
2. **Durable Runtime integration ยังไม่ต่อ** — transport emit event ผ่าน callback แล้ว แต่ยังไม่ผูกกับ `DurableTaskRuntime` เป็น AgentStep จริง (Workstream 11 ทำได้เพียงบางส่วน) จะต่อพร้อม MCP tool ใน 4.1B
3. **backpressure ยังเป็น implicit** — จำกัดด้วย stderr quota + frame cap + notification cap 1000 แต่ยังไม่มี bounded request queue ที่ปฏิเสธเมื่อคิวยาว
4. Reconnect อัตโนมัติยังไม่มี (session ตาย = สร้างใหม่) — ตั้งใจ เพราะ auto-reconnect ระหว่างมี mutation ค้างเสี่ยงทำซ้ำ

**Environment limitation**
5. คงเดิมจาก Phase 3.1 (ไม่มี Developer Mode / จอเดียว / ไม่มี provider credential)

---

## 12. Final Verdict

1. **transport เป็น long-lived bidirectional จริงหรือไม่** — **ใช่** stdin เปิดค้าง, 20 requests บน process เดียว, stdout/stderr อ่าน async ตลอด session
2. **worker รัน isolated จริงหรือไม่** — **ใช่** AppContainer + Job Object เดียวกับ Document Worker; พิสูจน์ด้วย env ไม่รั่ว, network ถูกบล็อก, descendant ตายตาม job
3. **post-dispatch disconnect เป็น UnknownOutcome หรือไม่** — **ใช่** ทุกกรณี (write ขาด, timeout, crash, cancel หลังส่ง) และ pre-dispatch แยกเป็น `FailedNoEffect` ชัดเจน
4. **concurrent requests รองรับหรือไม่** — **ใช่** correlation ด้วย request id, out-of-order ตอบถูกตัว
5. **notification รองรับหรือไม่** — **ใช่** แยก channel, ไม่ปนกับ response
6. **restart recovery เชื่อถือได้ระดับใด** — ระดับ transport เชื่อถือได้ (in-flight → unknown, worker ตายตาม host, generation ใหม่กัน frame เก่า); ระดับ durable run **ยังไม่ต่อ** — ต้องทำใน 4.1B
7. **process/temp leak มีหรือไม่** — **ไม่มี** (0 ทุกตัวชี้วัดหลัง soak)
8. **พร้อมต่อ MCP Protocol Runtime หรือไม่** — **พร้อม** framing เป็น payload-opaque อยู่แล้ว MCP วางทับได้โดยไม่แก้ transport
9. **blocker สามอันดับแรก** — (1) ผูก transport เข้ากับ DurableTaskRuntime เป็น AgentStep จริง, (2) heartbeat/bounded queue, (3) privileged Windows VM (คงค้างจาก Phase 3.1)

# SRAN Desktop — Click Path Reliability Result (R1 readiness + focus-retry, both click tools)

**สถานะ:** ✅ build + test เขียวทั้งหมด (main **1313** = 1292 + 21 ใหม่ · UiTests **5** · 0 fail)
**scope ที่เลือก:** R1 readiness wait + focus-retry ให้ **ทั้ง `uia_click` และ `computer_click`** + R5/classification ครอบ click codes — **ไม่ซ้ำ semantic ladder** (มีแล้ว)

---

## 🔎 finding ที่ทำให้เลือก scope นี้ (ต่างจาก prompt เดิม)
- **"Click control" ที่แดงสด = `uia_click`** (Dict: `tool.name.uia_click`), ไม่ใช่ `computer_click` ("Click")
- `uia_click` **มี semantic pattern ladder อยู่แล้ว** (Toggle→Invoke→SelectionItem, UiaClickTool.cs:142-162) + amber `ExecutedUnverified` (:123) + `ForceForegroundWindow` (:202)
- `computer_click` coordinate path **มี amber อยู่แล้ว** (`FromComputerAction` เมื่อ OsInputAccepted)
- **ที่ขาดจริงทั้ง 2 ทูล:** readiness wait (R1) + การรอ/ยืนยัน foreground ก่อน physical dispatch
→ จึงไม่ duplicate ladder แต่เติม **R1 + focus-confirm** ตรงจุด dispatch

---

## ไฟล์ที่แก้

| ไฟล์ | แก้ | reuse (กัน duplicate) |
|---|---|---|
| `Mcp/Tools/UiaClickTool.cs` | เพิ่ม R1 ใน **PhysicalClick**: `ForceForegroundWindow` + `WaitForAsync(InputReady, allowApplicationReceiver, InputReadyWait)` แทน blind `Sleep(100)`; timeout → `FailedEffect(retryable)` | reuse `ComputerTypeTool.InputReadyWait` + `ApplicationReadinessService` |
| `Mcp/Tools/ComputerClickTool.cs` | เพิ่ม R1 ใน **coordinate path** (เมื่อมี `application`): force foreground + `WaitForAsync(InputReady)` ก่อน `ValidateLive`; timeout → retryable Fail | reuse `InputReadyWait` เดียวกัน |
| `ViewModels/AgentExecutionViewModel.cs` | `FailureKey` เพิ่ม arm click codes (disabled/noClickable/osRejected/timeout/Covered) | reuse `HumanizeFailure`/`FailureKey` เดิม (R5) |
| `Localization/Dict.cs` | +4 keys (`disabled`,`noClickable`,`osRejected`,`timeout`) ครบ TH+EN | — |
| `Services/TransientFailurePolicy.cs` | + `CaptureStale` ใน retryable set | reuse policy เดิมจาก 2a |
| `Tests/Agent/RoundClickPathTests.cs` | **ใหม่** 21 tests | — |

### กลไก R1-click (ทั้ง 2 ทูล)
```
Win32Input.ForceForegroundWindow(hWnd);        // active raise
readiness = WaitForAsync(InputReady, allowApplicationReceiver:true, InputReadyWait); // poll ~100ms ถึง 6s
if (!readiness.Ready) return retryable(readiness.FailureCode);  // FocusLost/ApplicationNotReady
```
- **focus-retry = การ poll foreground ใน WaitForAsync** (force ก่อน แล้ว confirm ด้วย poll จนถึง 6s) — ไม่ต้องมี loop แยก
- **พร้อมอยู่แล้ว → ผ่านทันที** (poll แรกคืน Ready)
- **สำคัญ:** gate เฉพาะ **physical dispatch** — pattern click (Invoke/Toggle) ของ `uia_click` **ไม่ถูก gate** (ทำงาน background ได้เหมือนเดิม ไม่ regress)

## R3′-click
คงเดิม (มีอยู่แล้ว): dispatch สำเร็จแต่ verify ไม่ได้ → amber (`ExecutedUnverified`/`FromComputerAction`) ไม่ใช่แดง — ไม่แตะ

## ladder rungs (สุดท้าย)
- `uia_click`: **Toggle → Invoke → SelectionItem** (pattern) → **Physical click** (มี R1 ใหม่) — *มีอยู่แล้ว ไม่แก้ ladder*
- `computer_click`: **semantic (TargetAcquisitionRuntime)** → **coordinate** (มี R1 ใหม่) — *ไม่แก้ ladder*

---

## FailureCode ของ click: retryable vs non-retryable

**retryable (auto-retry ได้ — เข้า `TransientFailurePolicy`):**
`FocusLost` (physical click ขึ้น foreground ไม่ได้), `ApplicationNotReady`/`StaleWindow` (readiness timeout), `TargetNotFound`, **`CaptureStale`** (เพิ่มใหม่ — capture เก่า ต้อง re-observe)

**non-retryable (แดงทันที + human-error):**
- `OsInputRejected` — Windows ปฏิเสธ input (hard)
- `WrongWindow` — target PID เปลี่ยน/ผิดหน้าต่าง (logic)
- `disabled` — ปุ่มถูกปิด (retry ไม่ช่วย)
- `no clickable area` / `ElementAtPointMismatch` — target ผิด/ไม่มีพื้นที่คลิก
- `UnboundInput` — args ไม่ครบ

## R5-click (human-error) — map ครบ
disabled → "ปุ่ม/ช่องนี้ถูกปิดใช้งาน…" · no clickable area → "…ไม่มีพื้นที่ให้คลิก" · OsInputRejected → "Windows ไม่รับการคลิก" · Timeout → "ตัวควบคุม…ไม่ตอบสนอง" · ForegroundWindowMismatch/PointCovered/HostWindow → validate · CaptureStale → stale · WrongWindow/FocusLost/TargetNotFound/ApplicationNotReady → เดิม (R5) — **ไม่มี code ดิบโผล่บนการ์ด**

---

## behavior change ที่ควรรู้ (ไม่ถือ regress ตาม prompt)
`uia_click` physical click: เดิมคลิกเสมอ (amber). ตอนนี้ถ้า **บังคับ foreground ไม่ได้ใน 6s** → red **FocusLost (retryable)** แทนคลิกมั่วเข้าไปในหน้าต่างที่ไม่ active. เคสปกติ (ขึ้น foreground เร็ว) = ผ่านทันที → amber เหมือนเดิม. FocusLost เป็น red ที่ prompt อนุญาต + retryable (R4 อนาคตยุบได้)

## Build / Test
```
dotnet build SranDesktop.sln  → Build succeeded
dotnet test  SranDesktop.sln  → main 1313 / UiTests 5 / 0 fail
```
คง typing (R2), amber (R3′), human-error (R5), retrying (2b), STA guard — ไม่ regress

---

## ต้อง live-validate (คลิกจริงบน GPT-4o)
1. **`uia_click` physical:** คลิก control ที่ต้องดึงหน้าต่างขึ้นก่อน → ควรขึ้น foreground + คลิกได้ (ไม่แดง FocusLost ถ้าแอปพร้อม); ถ้าแอปช้า → รอถึง 6s แทนแดงทันที
2. **`computer_click` coordinate + application:** คลิกพิกัดในแอปที่เพิ่งเปิด → force foreground + รอ ready แทน ForegroundWindowMismatch ทันที
3. **pattern click ยัง background ได้** (ไม่ถูก gate) — ยืนยันไม่ช้าลง
4. human-error ของ click (disabled/timeout/rejected) แสดงเป็นไทยบนการ์ด

> **หมายเหตุ:** ปิด instance live-validation เก่า (PID 45888, build เก่าไม่มี click fix) เพื่อปลดล็อก DLL ตอน build — **fresh Debug build พร้อม relaunch** สำหรับ validate click path (บอกได้ถ้าให้เปิดให้)

**หยุดรอรีวิว**

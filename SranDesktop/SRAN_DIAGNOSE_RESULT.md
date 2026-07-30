# SRAN Desktop — วินิจฉัย OsInputRejected ("Windows didn't accept the click")

**โหมด:** วินิจฉัย — **ยังไม่แก้โค้ด** (ไม่ได้เพิ่ม logging ด้วย เพราะการ์ดที่ล้ม **มีหลักฐานพอแล้ว** — ดูข้อ 4)

---

## 1. เงื่อนไขจริงที่เซ็ต OsInputRejected (path:line)

**ผู้ผลิต OsInputRejected มีที่เดียว:** `Mcp/Tools/ComputerClickTool.cs:158`
```csharp
FailureCode: input.OsInputAccepted ? "EffectNotVerified" : "OsInputRejected",
```
→ คืน `FailedComputerAction` (แดง) เมื่อ `!input.OsInputAccepted` (ComputerClickTool.cs:167-168, coordinate path)

**`OsInputAccepted` นิยามที่** `Native/Win32Input.cs:12-14`
```csharp
public bool OsInputAccepted =>
    CursorPositioned && RequestedInputEvents > 0
    && AcceptedInputEvents == RequestedInputEvents;
```

**ต้นทางจริงใน `ClickCore`** (`Win32Input.cs:373-411`) — ล้มได้ 2 ทาง:
1. **`SetCursorPos(x,y)` คืน false** → `CursorPositioned=false` → return ทันที (`:385-388`), เก็บ `Marshal.GetLastWin32Error()`
2. **`SendInput` รับ event < ที่ส่ง** → `AcceptedInputEvents != clicks*2` (`:399-410`), เก็บ GetLastError

> **สำคัญ:** ทั้ง `SetCursorPos` และ `SendInput` ถูกปฏิเสธด้วย **UIPI/ACCESS_DENIED (error 5)** ในเงื่อนไขเดียวกัน — caller (medium IL, uiAccess=false) ส่ง input ตอนที่ **foreground เป็นหน้าต่าง integrity สูงกว่า** หรือระบบล็อก input

**หมายเหตุ label:** การ์ด "Click control" = `uia_click`, แต่ `uia_click` **ไม่เคยผลิต OsInputRejected** — `PhysicalClick` คลิกแล้วคืน `ExecutedUnverified` (amber) เสมอ ไม่เช็ค OsInputAccepted (`UiaClickTool.cs:206-212`). **OsInputRejected มาจาก `computer_click` (coordinate path) เท่านั้น** → โมเดล (GPT-4o) น่าจะลอง `uia_click` (ไม่มี pattern → physical → amber) แล้ว fallback ไป `computer_click` พิกัด → แดง OsInputRejected

## 2. H2 — integrity / UIPI

- **manifest:** `src/SranDesktop.App/app.manifest:8` → `requestedExecutionLevel level="asInvoker" uiAccess="false"` → SRAN = **Medium IL, ไม่มี uiAccess** → **ไม่มีสิทธิ์ข้าม UIPI**
- **ไม่มี integrity logic ในโค้ดเลย** (grep ทั้ง Infrastructure — เจอแค่ UpdateService "Mandatory" ที่ไม่เกี่ยว) → ไม่มีการตรวจ IL ของ target, ไม่ยกระดับ, ไม่จัดการ AppContainer
- **กลไก:** medium+uiAccess=false **ส่ง input ไป window ที่ integrity สูงกว่าไม่ได้**. Win11 Notepad = **AppContainer (IL ต่ำกว่า medium)** → ตามกฎ medium→low **ควรส่งได้** → ดังนั้น *integrity ของ Notepad เอง ไม่น่าบล็อกโดยตรง*
- **แต่:** ถ้า foreground จริงตอน inject **ไม่ใช่ Notepad** (เป็นหน้าต่าง IL สูงกว่า) → ACCESS_DENIED → เชื่อมกับ H3

**สรุป H2:** โครงสร้างสิทธิ์ (uiAccess=false) ทำให้ SRAN **ไม่มีทางลัดข้าม UIPI** ได้เลย แต่จะเป็นต้นตอโดยตรงก็ต่อเมื่อ target/foreground เป็น IL สูงกว่า — ต้องดู `win32_error` จริง (ข้อ 4)

## 3. H3 — foreground

- `ForceForegroundWindow` (`Win32Input.cs:304-335`) ใช้ AttachThreadInput + SetForegroundWindow, **คืน `GetForegroundWindow()==hWnd`** (:334) — เป็น workaround ที่ **ล้มเงียบได้บน Win11** (foreground lock)
- **coordinate path ไม่ได้เช็ค return ของ ForceForegroundWindow โดยตรง** — แต่ R1 ที่เพิ่งเพิ่ม (`ComputerClickTool.cs`) เรียก `WaitForAsync(InputReady)` ซึ่ง **ต้องการ foreground** (`ApplicationReadinessService.cs:106-107`, level InputReady = `visible && foreground && accessibility && receiver`) — **เฉพาะเมื่อส่ง arg `application`**
- **ตรรกะชี้ขาด:**
  - ถ้า readiness ผ่าน (มี `application`) แต่ยังได้ OsInputRejected → foreground **ถูกยืนยันแล้ว** → **ไม่ใช่ H3** → ชี้ H1/H2
  - ถ้าโมเดลไม่ส่ง `application` → ไม่มี readiness → OsInputRejected อาจเป็น H3 (SendInput ไป window ที่ไม่ใช่ target)
- **ช่องโหว่ readiness:** InputReady เช็ค `foreground` (GetForegroundWindow==hWnd) แต่ **ไม่ได้เช็คว่า "inject ได้จริง"** (UIPI/integrity) → readiness ผ่านได้แม้ SendInput จะถูกปฏิเสธ

## 4. แยก H1 ให้ขาด + หลักฐานที่มีอยู่แล้ว

**การ์ดที่ล้ม มี evidence พอวินิจฉัย H2 vs H3 อยู่แล้ว** — `ComputerClickTool.cs` FailureReason/Evidence log:
- `cursor_positioned=<true/false>` (Win32Input.cs:161 reason)
- `send_input=<accepted>/<requested>`
- `win32_error=<code>` (อยู่ใน FailureReason)
- + readiness evidence (foreground/hwnd) ถ้ามี `application`

**อ่านค่าจากการ์ดที่ผู้ใช้เจอสด → แยกได้ทันที:**
| อาการในการ์ด | แปลผล |
|---|---|
| `cursor_positioned=false` + `win32_error=5` | **SetCursorPos ถูก UIPI บล็อก** → foreground เป็น IL สูงกว่า / ไม่ใช่ Notepad (H2/H3) |
| `cursor_positioned=true, send_input=0/2` + `win32_error=5` | **SendInput ถูกปฏิเสธ** → UIPI ตอน inject |
| `win32_error=0` แต่ยัง reject | อื่น ๆ (นอก UIPI) — ต้องดูเพิ่ม |

**เทสชี้ขาด H1 (Store-specific)** — ให้ผู้ใช้รันเทียบ (รันสะอาดเหมือนเดิม):
1. เปิด **WordPad** หรือหน้าต่าง **Win32 แท้** (เช่น `write.exe`/regedit/คลาสสิก) แล้วสั่ง `computer_click` พิกัดในนั้น
2. เทียบกับ Win11 Store-Notepad
- **Win32 ผ่าน + Store-Notepad ถูกปฏิเสธ → ยืนยัน H1** (พฤติกรรม Store/AppContainer)
- **ทั้งคู่ถูกปฏิเสธ → ไม่ใช่ H1** → เป็น H2/H3 (foreground/integrity ทั่วไป)

> **สมมุติฐานที่น่าจะจริงที่สุดจากหลักฐาน static:** เนื่องจาก **UIA read ทำงาน** (accessibility ปกติ) แต่ **synthetic input ถูกปฏิเสธเป็นระบบ** → ชี้ไปทาง **UIPI/foreground-injection ไม่ใช่ accessibility** เป็นหลัก ต้องดู `win32_error` + comparative test เพื่อฟันธง H1 vs H2/H3

---

## ทิศทางแก้ที่เสนอ (ยังไม่ลงมือ — เลือกตามผลข้อ 4)

### ถ้า H1/H2 (Store app / UIPI-integrity) — SendInput จะไม่มีวันผ่านกับ target นั้น
- **หลัก:** ให้ click ladder **พึ่ง UIA activation (InvokePattern/Toggle/SelectionItem) เป็นหลัก** กับ target ที่ SendInput ถูกปฏิเสธ — pattern **ไม่ใช้ SendInput** ทำงานข้าม UIPI/foreground ได้ (`uia_click` มี rung นี้แล้ว; `computer_click` ควร detect OsInputRejected → fallback ไป semantic แทนซ้ำพิกัด)
- typing มี R2 ladder ที่เลี่ยง SendInput อยู่แล้ว (ValuePattern/window-message) → click ควรมี parity
- **อย่าให้ R4 (อนาคต) retry SendInput ซ้ำ ๆ** กับ target ที่เป็น UIPI-blocked (จะแดงวนไม่จบ) → OsInputRejected **ควรคง non-retryable** ถ้าต้นตอคือ integrity (ปัจจุบันก็ non-retryable อยู่แล้ว — ถูกต้อง)
- (หนัก, ไม่แนะนำก่อน) uiAccess=true ต้อง sign + ติดตั้งใน Program Files — เกินความจำเป็นถ้า UIA pattern แก้ได้

### ถ้า H3 (foreground) — OsInputRejected เป็น transient
- ทำให้ `ForceForegroundWindow` + readiness **ยืนยัน foreground-acquired จริงก่อนบอก InputReady** (เพิ่ม re-force + confirm loop; ปัจจุบัน InputReady เช็ค GetForegroundWindow==hWnd แต่ ForceForegroundWindow อาจล้มเงียบ)
- เพิ่ม post-force verification: ถ้า SendInput ถูกปฏิเสธ → re-force foreground + retry ครั้งเดียวใน tool
- กรณีนี้ **OsInputRejected ควรเป็น retryable** (ใส่ใน TransientFailurePolicy) — **ต่างจากกรณี H1/H2** → *ต้องรู้ต้นตอก่อนตัดสิน retryable*

---

## 5. หมายเหตุแยก (ประเมินตามที่ขอ)

**(ก) "Screenshot" ขึ้น "Windows didn't accept the click" — mapping กว้างไป (bug ที่ผมทำใน click round)**
`AgentExecutionViewModel.cs:247` R5 map ใช้ `Has("rejected")` (substring) → **จับ error ใด ๆ ที่มีคำว่า "rejected"** (เช่น consent/permission "rejected", screenshot ที่ล้มด้วยเหตุอื่น) แล้วโชว์ข้อความ click ผิดบริบท
→ **ควรแก้:** เปลี่ยนเป็น match code แบบเป๊ะ (`code == "OsInputRejected"` หรือ `StartsWith("OsInputRejected")`) แทน `Has("rejected")` — กัน false mapping ข้าม action type

**(ข) OsInputRejected ควร retryable ไหม** — **ขึ้นกับต้นตอ (ข้อ 4):**
- ต้นตอ = H1/H2 (integrity/AppContainer) → **ไม่ควร retryable** (SendInput จะแดงวน) → fallback ไป UIA pattern แทน
- ต้นตอ = H3 (foreground) → **ควร retryable** + fix foreground
→ ปัจจุบัน non-retryable (ถูกสำหรับ H1/H2) — **อย่าเพิ่งเปลี่ยนจนรู้ต้นตอ**

---

## ต้องการจากผู้ใช้เพื่อฟันธง (ก่อนแก้)
1. **ค่าจากการ์ดที่ล้มสด:** `cursor_positioned`, `send_input=n/m`, `win32_error` (กด expand การ์ด → Output) — บอกได้ทันทีว่า SetCursorPos หรือ SendInput ถูกบล็อก + error 5 ไหม
2. **comparative test:** `computer_click` พิกัดบน **WordPad/Win32 แท้** vs **Store-Notepad** → ยืนยัน H1

**หยุดรอรีวิว** — ไม่แตะ ladder/classification/foreground จนกว่าจะรู้ H1/H2/H3 จริงจากข้อ 1-2

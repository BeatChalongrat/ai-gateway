# SRAN Desktop — OsInputRejected Probe Result

**โหมด:** เก็บหลักฐาน OS-level — **ไม่แตะ behavior** (ladder/classification/retry ไม่แก้). แก้เฉพาะ diagnostic surfacing (A) + SetLastError (C) + probe test (B)
**Test:** main **1314** · UiTests **5** · 0 fail (probe no-op เมื่อไม่มี `SRAN_PROBE=1`)

---

## 🎯 ฟันธง: **H1 และ H2 ตกไป · H3 (foreground) คือทางหลัก**

### ตารางผล probe (งาน B — direct Win32, ไม่มี LLM/computer-use)
```
target        | launched | foreground_acquired | cursor_positioned | accepted/requested | win32_error
store-notepad | yes      | True                | True              | 2/2                | 0
win32-charmap | yes      | True                | True              | 2/2                | 0
```
> รอบแรก store-notepad ได้ `no-window` เพราะ Win11 Store-Notepad เปิดผ่าน **broker** — `Process.MainWindowHandle` = 0; แก้โดยหา HWND จาก **title** (EnumWindows) แล้วได้ผลด้านบน. `write.exe` (WordPad) ถูกลบใน Win11 นี้ → ใช้ `charmap.exe` เป็น Win32 medium-IL baseline

### อ่านผล
- **Store-Notepad รับ SendInput ครบ 2/2, `win32_error=0`, `cursor_positioned=true`** เมื่อ `foreground_acquired=true`
  → **H1 (AppContainer บล็อก synthetic input) ตกไป** — AppContainer **ไม่ได้** บล็อก SendInput โดยเนื้อแท้
  → **H2 (integrity/UIPI ACCESS_DENIED) ตกไป** — ไม่มี error 5, inject เข้า Store-Notepad ได้ปกติ
- **charmap (Win32) ก็ 2/2** — pipeline inject ของ SRAN ทำงานถูกต้องกับทั้ง Win32 และ Store app **เมื่อ target เป็น foreground จริง**

### ทำไมชี้ H3
ตัวแปรเดียวที่ทำให้ probe **สำเร็จ** ต่างจาก live-failure คือ **`foreground_acquired=true`** — probe บังคับ + ยืนยัน foreground ก่อน (และไม่มีแอปอื่นแย่ง focus เพราะปิด SRAN แล้ว). ใน live: agent รันอยู่ใน **หน้าต่าง SRAN ที่เป็น active app** → ตอน `computer_click` ยิง SendInput, target Notepad **อาจไม่ได้เป็น foreground จริง** (ForceForegroundWindow ล้มเงียบ / ยิงก่อน foreground settle / ไม่ได้เรียก force เลยถ้าไม่มี arg `application`) → OS ปฏิเสธ/ยิงผิดที่

> **ข้อจำกัดของ probe (พูดตรง):** probe **พิสูจน์ว่ากลไก inject ทำงาน** (หักล้าง H1/H2) แต่ **ไม่ได้ reproduce live-failure** (เพราะ env ต่าง: ปิด SRAN + บังคับ foreground). การยืนยัน H3 ให้ 100% ต้องดู **ค่า `foreground_acquired` / `win32_error` จากการ์ด live จริง** — ซึ่ง **งาน A เพิ่งทำให้เห็นแล้ว**

---

## งาน A — diagnostic surfacing (diff)
`Mcp/Tools/ComputerClickTool.cs` (coordinate path) — เพิ่ม structured fields ลง Evidence (เห็นในการ์ด expand):
```diff
+ var foregroundAtDispatch = Win32Input.GetForegroundWindow().ToInt64();
+ var foregroundAcquired = capture!.WindowHandle is { } targetHwnd && foregroundAtDispatch == targetHwnd;
  ...
  Evidence: new[] {
      $"capture_id={capture!.CaptureId}",
-     $"cursor_positioned={input.CursorPositioned}",
-     $"send_input={input.AcceptedInputEvents}/{input.RequestedInputEvents}"
+     $"cursor_positioned={input.CursorPositioned...}",
+     $"send_input_accepted={input.AcceptedInputEvents}",
+     $"send_input_requested={input.RequestedInputEvents}",
+     $"win32_error={input.LastWin32Error}",
+     $"foreground_hwnd={foregroundAtDispatch}",
+     $"target_hwnd={capture.WindowHandle...}",
+     $"foreground_acquired={foregroundAcquired...}"
  },
```
→ live click ที่ล้ม **จะโชว์ foreground_acquired + win32_error ในการ์ดทุกครั้ง** (ไม่ต้องเดา)

## งาน C — SetLastError (diff)
`Native/Win32Input.cs` — `SendInput` มี `SetLastError=true` อยู่แล้ว (:72); เพิ่มให้:
```diff
- [DllImport("user32.dll")] private static extern bool SetCursorPos(int x, int y);
+ [DllImport("user32.dll", SetLastError = true)] private static extern bool SetCursorPos(int x, int y);
- [DllImport("user32.dll")] internal static extern bool SetForegroundWindow(IntPtr hWnd);
+ [DllImport("user32.dll", SetLastError = true)] internal static extern bool SetForegroundWindow(IntPtr hWnd);
```
→ `win32_error` หลัง SetCursorPos ล้ม **แม่นแล้ว** (สำคัญ: ClickCore capture GetLastError ตอน SetCursorPos คืน false)

## งาน B — probe test
`Tests/Agent/OsInputProbeTests.cs` — guarded ด้วย `SRAN_PROBE=1` (ปกติ no-op, suite เขียว). วิธีรัน:
```powershell
$env:SRAN_PROBE='1'
dotnet test src\SranDesktop.Tests\SranDesktop.Tests.csproj -c Debug --filter FullyQualifiedName~OsInputProbeTests
# ผลเขียนที่ %TEMP%\sran_probe.txt
```
NOTE: คลิกซ้ายเดียวใน**หน้าต่างของ target เอง** (throwaway) แล้วปิด — ไม่มี computer-use, ไม่มี LLM

---

## ทิศทางแก้ที่ตรงจุด (ยังไม่ลงมือ — H3)
1. **ยืนยัน foreground จริงก่อน dispatch:** `ForceForegroundWindow` ปัจจุบันคืน bool แต่ coordinate path ไม่ได้ hard-check; readiness InputReady เช็ค `GetForegroundWindow==hWnd` แต่เฉพาะเมื่อมี arg `application`
   → ให้ **coordinate click เรียก force + ยืนยัน foreground เสมอ** (ไม่ใช่เฉพาะมี `application`), และถ้า `foreground_acquired=false` → **re-force + retry ครั้งเดียวใน tool** ก่อนยอมแพ้
2. **OsInputRejected ควรเป็น retryable** (เพราะต้นตอ = foreground transient ไม่ใช่ integrity) → ใส่ใน `TransientFailurePolicy` เพื่อให้ R4 ยุบได้ — **ต่างจากสมมุติฐาน H1/H2 ที่ผมเคยบอกว่าไม่ควร retry** (probe หักล้างแล้ว)
3. **ทางที่ทนสุด (คู่ขนาน):** ให้ ladder พึ่ง **UIA InvokePattern** (ไม่ใช้ SendInput/foreground เลย) เป็น rung หลัก — `uia_click` มี; `computer_click` ควร fallback ไป semantic เมื่อ foreground ยึดไม่ได้
4. **แก้ mapping กว้าง (จาก diagnosis):** `FailureKey` `Has("rejected")` → เปลี่ยนเป็น match code เป๊ะ (`OsInputRejected`) กัน Screenshot/consent โชว์ข้อความ click ผิดบริบท

---

## หมายเหตุ
- ปิด instance live-validation (SRAN) ตอน build+probe (ปลด DLL lock + กัน foreground แย่ง) — **fresh build พร้อม relaunch** (บอกได้ถ้าให้เปิด)
- **แนะนำ next:** relaunch แล้ว **สั่ง click ล้มบน Notepad 1 ครั้ง → กด expand การ์ด → อ่าน `foreground_acquired` / `win32_error`** (งาน A) เพื่อ **ยืนยัน H3 จาก live จริง** ก่อนลงมือแก้ตามข้อ 1-4

**หยุดรอรีวิว** — ไม่แตะ ladder/foreground/classification จนกว่าจะยืนยัน H3 จากการ์ด live

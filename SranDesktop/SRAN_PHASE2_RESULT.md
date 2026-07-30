# SRAN Desktop — Phase 2 Result (evidence polish)

> อ้างอิงแผน: `SRAN_UIA_FIRST_ARCH_PLAN.md` §4 → **Phase 2 — ยืนยัน type UIA-first + evidence polish**
> สถานะ: ✅ เสร็จตามรายการจริงในแผน · behavior-neutral · build/test เขียว · **ไม่แตะ Phase 3-4** · หยุดรอรีวิว

---

## 1. รายการ Phase 2 ที่ทำ (ยึด "รายการจริงในแผน" ตามที่ prompt กำชับ)

แผน §4 Phase 2 ระบุ 2 อย่าง:
1. *"ตรวจว่า `computer_type` Rung1-2 ถูกลองเสมอ (คงโค้ด, **เพิ่ม evidence token `no_foreground_rung=true` เมื่อสำเร็จก่อน Rung3**)"* → ✅
2. *"Verify: test เดิม `ComputerTypeTool` ครอบ Rung1/2; เพิ่ม assert ว่าไม่แตะ `ForceForegroundWindow` เมื่อ ValuePattern สำเร็จ"* → ✅ ทำได้เท่าที่ deterministic ได้ (ดู §4 ข้อจำกัด)

| # | รายการ | สถานะ |
|---|---|---|
| 1 | `computer_type`: เพิ่ม `no_foreground_rung=` evidence token | ✅ |
| 2 | `uia_set_value`: token เดียวกัน (cross-tool consistency ตามเจตนา) | ✅ |
| 3 | Rung1-2 ยังลองเสมอ — ยืนยันว่า**ไม่แตะ logic** ของ ladder | ✅ (คงเดิม) |
| V | deterministic test คลาสสิฟาย no-foreground rung | ✅ (+8) |

---

## 2. ไฟล์ที่แก้ + diff ย่อ (evidence-only, ไม่แตะ logic)

### 2.1 `src/SranDesktop.Infrastructure/Mcp/Tools/ComputerTypeTool.cs`
เพิ่ม token ใน evidence list (หลัง `injection_method=`) + predicate ที่ testable:
```diff
                 "injection_method=" + (injectionMethod ?? "none"),
+                "no_foreground_rung=" + IsNoForegroundRung(injectionMethod).ToString().ToLowerInvariant(),
                 "value_read_back=" + exact.ToString().ToLowerInvariant(),
```
```csharp
+ internal static bool IsNoForegroundRung(string? injectionMethod) =>
+     string.Equals(injectionMethod, "value_pattern", OrdinalIgnoreCase)
+     || string.Equals(injectionMethod, "valuepattern", OrdinalIgnoreCase)
+     || string.Equals(injectionMethod, "window_message", OrdinalIgnoreCase);
```
> raw `injection_method=` **คงไว้** เพื่อ debug (ตรงเจตนา "คง raw ไว้ ไม่ทิ้ง"). token ใหม่แค่ทำให้อ่านง่ายว่ารุ่นที่ส่งข้อความสำเร็จเป็น **no-foreground** (ValuePattern/window_message) หรือ **keystroke** (clipboard/SendInput)

### 2.2 `src/SranDesktop.Infrastructure/Mcp/Tools/UiaSetValueTool.cs`
`VerifiedResult` เพิ่ม token เดียวกัน (เรียก `ComputerTypeTool.IsNoForegroundRung(method)` — มี cross-reference อยู่แล้วผ่าน `LooksLikeCredential`) → typing สองตัวโชว์ evidence คลาสเดียวกัน (เจตนา "ความสม่ำเสมอข้าม tool"):
```diff
             [$"uia_method={method}",
+                $"no_foreground_rung={ComputerTypeTool.IsNoForegroundRung(method).ToString().ToLowerInvariant()}",
                 "value_read_back=true",
```

### 2.3 `src/SranDesktop.Tests/Agent/ComputerToolTests.cs`
เพิ่ม `IsNoForegroundRung_Classifies_Delivery_Method` [Theory ×8]: value_pattern/ValuePattern/window_message→true; clipboard_paste/sendinput/sendinput_keyboard/none/null→false

---

## 3. พิสูจน์ว่า behavior-neutral จริง (สำคัญที่สุดของ Phase 2)

**Verification pipeline อ่าน evidence เฉพาะ allow-list เท่านั้น** — token ใหม่ไม่อยู่ในนั้นเลย:
- ค่า text/observed verify จาก `ActualReadTokens = { "read_back_value=", "observed_value=", "display_value=" }` เท่านั้น (`SemanticOutcomeRuntime.cs:578-579`, ใช้ที่ `:529-538`)
- window identity: `hwnd=`/`pid=`/`"window"` (`SemanticOutcomeRuntime.cs:570-573`)
- `"no_foreground_rung=true|false"` **ไม่มี substring** ที่ตรงกับ token เหล่านี้ (ไม่มี `window`, `hwnd=`, `pid=`, `read_back_value=`) → **inert** ต่อ success/fail/retryable
- success/fail ยังคำนวณจาก `verified`/`nativeAccepted`/`receiverConfirmed` เดิม — **ไม่แตะ** (R3′ amber `ReadBackUnavailable`/`EffectMismatch` เหมือนเดิม)

Regression check: test เดียวที่ assert evidence ของ typing (`RealWorldSkillRuntimeTests.cs:347` `.Contain("value_read_back=true")`) เป็น **subset check** และอยู่บน fake runtime → ไม่กระทบ

---

## 4. ข้อจำกัด / สิ่งที่ **ไม่** ทำ (ตรงไปตรงมา)

### 4.1 test "ไม่แตะ ForceForegroundWindow เมื่อ ValuePattern สำเร็จ" — deterministic ไม่ได้
`ComputerTypeTool.ExecuteAsync` ต้องมี **real `AutomationElement` (sealed, mock ไม่ได้) + Win32 static + real HWND/PID** → rung ladder เป็น **live-only** (เหมือน Phase 1). deterministic ได้แค่ **predicate** `IsNoForegroundRung` (ทำแล้ว). การพิสูจน์ว่า ValuePattern สำเร็จแล้วไม่แตะ foreground ต้อง **live-validate** (ดู §6). — สอดคล้องกับที่ Phase 1 เจอ; ถ้าต้องการ coverage กว่านี้ต้องมี real-GUI harness (นอก scope)

### 4.2 **ไม่แตะ `MessageBubble.xaml`** → STA guard ไม่ถูก trigger
เจตนาใน prompt พูดถึง "จัดกลุ่ม diagnostic fields บนการ์ด" (foreground_acquired/win32_error/hwnds ฯลฯ) — **แต่นั่นไม่ใช่รายการใน Phase 2 ของแผน** และจะต้องแก้ XAML/การ render. prompt กำชับ *"ยึดรายการจริงในแผนเป็นหลัก"* + *"ถ้าเริ่มรู้สึกว่าต้องแก้ logic/หลุด scope ให้หยุด"* → ผมจึง **ไม่ทำ card-grouping ในเฟสนี้** (ไม่มี XAML change, STA/UiTests 5 เขียวโดยไม่ต้องขยาย). ถ้าต้องการ card diagnostic-grouping จริง ๆ เสนอทำเป็น **เฟส presentation แยก** (จะแตะ `MessageBubble.xaml` + ต้องขยาย STA binding test + mutation-discipline ตามกติกา) — บอกได้เลยถ้าเอา

---

## 5. Build / Test
| ชุด | ผล |
|---|---|
| `dotnet build src/SranDesktop.Tests` | ✅ 0 error |
| main suite | ✅ **1334 passed** (1326 + 8 ใหม่), 0 failed |
| UiTests (STA) | ✅ **5 passed** (ไม่แตะ XAML) |
| affected (ComputerTool/UiaSetValueSupport/RealWorldSkillRuntime) | ✅ 54 passed |

**Full-solution build** ยังล้มเฉพาะ file-lock copy เข้า `App/bin` เพราะ `SranDesktop.exe` รันอยู่ — ไม่ใช่ compile error (เหมือน Phase 0/1)

**ไม่ regress:** R2 ladder (คงเดิม) / R3′ amber / R5 human-error / 2b UI / STA — ไม่แตะ logic

---

## 6. Checklist live-validate (ผู้ใช้ทำเอง — ปิดแอป→build→เปิดใหม่ก่อน)
- [ ] "เปิด Notepad แล้วพิมพ์ข้อความ" → ดู evidence/Output บนการ์ด: ควรเห็น `no_foreground_rung=true` (เพราะ Notepad edit รับ ValuePattern/window_message โดยไม่ต้อง foreground) และ `injection_method=` เดิมยังอยู่
- [ ] พิมพ์ในแอปที่ต้อง keystroke (เช่น control ที่ ValuePattern/window_message ไม่ติด) → `no_foreground_rung=false`, `injection_method=clipboard_paste|sendinput`
- [ ] ยืนยันผลเขียว/amber เหมือนเดิม — token ใหม่ไม่เปลี่ยนสถานะการ์ด (behavior-neutral)

---

## 7. ยืนยันขอบเขต
- ✅ Phase 2 (evidence token + cross-tool consistency + predicate test) — เสร็จตามรายการจริงในแผน
- ❌ **ไม่แตะ Phase 3** (router substitute — Beat เลื่อนเป็นเฟสแยกแล้ว)
- ❌ **ไม่แตะ Phase 4** (wire `TransientFailurePolicy` retry-engine)
- ❌ **ไม่แตะ `MessageBubble.xaml`** / card diagnostic-grouping (นอก plan Phase 2 — เสนอเป็นเฟส presentation แยกถ้าต้องการ)

> **หยุดที่นี่ — รอรีวิว + ผล live-validate**

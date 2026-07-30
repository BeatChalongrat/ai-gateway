# SRAN Desktop — Phase 1 Result (UIA-point-first) + Item-2 decision needed

> อ้างอิงแผน: `SRAN_UIA_FIRST_ARCH_PLAN.md` → Phase 1 (option ก, UIA-point-first)
> สถานะ: ✅ **Item 1 (core, กลไก ก) เสร็จ + test เขียว** · ✅ Item 3 ครอบโดย Item 1 · 🔜 **Item 2 (router swap) = Beat อนุมัติเลื่อนไปเฟสแยก (2026-07-25)** — เหตุผล §3
>
> **การตัดสินใจ Item 2:** Beat เลือก *"เลื่อน Item 2 ไปเฟสแยก (แนะนำ)"* → Phase 1 ปิดที่ Item 1 + Item 3; router substitute จะทำเป็นเฟสของมันเอง (เดิม Phase 3 ในแผน) พร้อม reconcile safety-test อย่างระวัง. **ไม่แตะ router/safety-test ในเฟสนี้.**

---

## สรุปผู้บริหาร
- **Item 1 (UIA-point-first ใน `computer_click`)** = แกน "กลไก ก" ตามแผน — **ทำเสร็จ**. bare-x/y click จะ **activate element ที่จุดนั้นด้วย UIA pattern ก่อน (ไม่ต้อง foreground)** แล้วตก coordinate เฉพาะเมื่อไม่มี pattern จริง. reuse pattern ladder เดิม (ไม่เขียน engine ซ้ำ).
- **Item 3 (plain-chat)** = **ได้ฟรีจาก Item 1** เพราะ UIA-point-first อยู่ระดับ tool → ทำงานทั้ง plain-chat และ agent-mode โดยไม่ต้องยัด durable-runtime เข้า plain-chat (ตรงกับเหตุผลที่แผนเลือก option ก).
- **Item 2 (router substitute แทน reject)** = **หยุด** เพราะพบ 3 อุปสรรคจริง (แผนจัดเป็น Phase 3, การ swap ตามตัวอักษร infeasible, และชนกับ safety-regression test). ต้องให้ Beat ตัดสินใจก่อน — ผมจะไม่แก้ safety test เอง.

---

## 1. Item 1 — UIA-point-first (เสร็จ)

### กลไก
`computer_click` สาขา bare-x/y (`ComputerClickTool.cs`): หลัง `ValidateLive` ผ่าน (safety เดิมคงไว้) และ **เฉพาะ single left-click** → เรียก `_targets.TryActivateAtPointAsync(capture, x, y)`:
- ถ้า **activate สำเร็จ** (มี UIA pattern ที่จุดนั้น และ element เป็นของ process เดียวกับ capture) → คืนผลแบบ `FromComputerAction` (verified/amber ตามผล effect) **โดยไม่แตะ foreground / ไม่ยิง SendInput** — evidence มี `activation=uia_point_pattern`, `coordinate_fallback=false`
- ถ้า **ไม่มี pattern** (`NoPatternAtPoint`) → ตกลงไปเส้น coordinate `Win32Input.Click` เดิมทุกประการ (canvas / เกม / custom-draw)

### ไฟล์ที่แก้ + diff ย่อ

**`src/SranDesktop.Infrastructure/Native/Uia/UiaTargetRuntime.cs`**
- เพิ่ม method บน `ITargetAcquisitionRuntime`: `TryActivateAtPointAsync(capture, x, y, ct)`
- impl `ActivateAtPoint`: `AutomationElement.FromPoint` → guard (PID ตรง capture, enabled, on-screen) → `InvokeFirstPattern` → อ่าน before/after state → `TargetActivationResult`; ทุก error/ไม่มี pattern → `NoPatternAtPoint`
- **reuse ไม่เขียนซ้ำ:** extract pattern ladder เดิมใน `Activate` (Toggle→Invoke→SelectionItem→ExpandCollapse) ออกเป็น `InvokeFirstPattern(element, out method)` แล้ว **`Activate` เรียกตัวเดียวกัน** (พฤติกรรม `Activate` ไม่เปลี่ยน — ยืนยันด้วย RS-001/RuntimeStabilization tests เขียว)
```diff
-            string method;
-            if (element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle)) { ...Toggle(); method="TogglePattern"; }
-            else if (...Invoke...) { ... }  else if (...SelectionItem...) { ... }
-            else if (...ExpandCollapse...) { ...Expand(); }  else { return Failed("NoSemanticAction", ...); }
+            if (!InvokeFirstPattern(element, out var method))
+                return Failed("NoSemanticAction", "The target no longer exposes a supported UIA pattern.");
```

**`src/SranDesktop.Infrastructure/Mcp/Tools/ComputerClickTool.cs`**
- เพิ่ม `internal static ShouldTryPatternFirst(button, clickType)` = true เฉพาะ `left`+`single`
- ในสาขา bare-x/y หลัง `ValidateLive`: บล็อก UIA-point-first (ดูกลไกด้านบน) ก่อน `Win32Input.Click`

**`src/SranDesktop.Tests/Agent/GeneralTargetAccuracyTests.cs`**
- `FakeTargetRuntime` implement `TryActivateAtPointAsync` (default = `NoPatternAtPoint`, นับ `PointActivateCalls`)

### VM-level tests ที่เพิ่ม (6, deterministic)
- `PatternFirst_Only_For_Single_Left_Click` [Theory ×5]: left+single→true; left+double/triple, right, middle → false
- `Point_Activation_Seam_Reports_No_Pattern_By_Default`: seam ถูกเรียก + คืน `TargetActivated=false`/`NoPatternAtPoint` → tool ตก coordinate

### ⚠️ ข้อจำกัด test (ตรงไปตรงมา)
happy-path จริง (มี UIA pattern ที่จุด → invoke) **เป็น live-only**: `System.Windows.Automation.AutomationElement` เป็น sealed (mock ไม่ได้) และ `ValidateLive` ผูกกับ Win32 static → **ไม่มีทาง unit-test end-to-end แบบ deterministic**. จึง test เฉพาะ **gate logic (left+single) + fallback seam** ที่ deterministic ได้; ส่วน pattern-invoke จริงต้อง **live-validate โดยผู้ใช้** (ตรงกับที่แผน Phase 1 ระบุ). ถ้าต้องการ coverage มากกว่านี้ต้องมี real-GUI harness (นอก scope Phase 1).

---

## 2. Item 3 — plain-chat (ครอบโดย Item 1)
UIA-point-first อยู่ **ในตัว tool** ไม่ใช่ใน router → มีผลทั้ง plain-chat (ไม่มี durable runtime) และ agent-mode เท่ากัน. **ไม่ต้อง**ยัด durable-runtime/router เข้า plain-chat (ซึ่งจะเป็นการรื้อสถาปัตยกรรมใหญ่และเสี่ยง). นี่คือเหตุผลที่แผนเลือก option ก เป็นแกน. ✅

---

## 3. Item 2 — router substitute แทน reject: **หยุดรอการตัดสินใจ** ⛔

ทำไมไม่ทำเลยตามที่ prompt สั่ง — 3 อุปสรรคจริง (ยึด `path:line`):

### 3.1 แผนที่อนุมัติจัด router-swap เป็น **Phase 3** ไม่ใช่ Phase 1
`SRAN_UIA_FIRST_ARCH_PLAN.md` §4: *"Phase 3 — Router: transparent-swap แทน reject … เสี่ยงกว่า → เฟสแยก, ต้อง live-validate agent-mode เต็ม. อาจ defer."* — prompt Phase 1 บอก *"ห้ามล้ำเข้า Phase 2-4"* จึงขัดกันเอง

### 3.2 การ "substitute computer_click → uia_click" ตามตัวอักษร **infeasible**
- ที่ `ChatOrchestrator.cs:479` tool ถูก resolve **ตามชื่อที่โมเดลเรียก** แล้ว execute (`:585`); ที่ `DurableTaskRuntime.cs:536-550` route ที่ `Allowed=false` แค่ `Block(...)` → PrepareAction `Proceed=false` → orchestrator **ปฏิเสธ** (`ChatOrchestrator.cs:557-569`). **ไม่มี machinery ที่รัน SelectedCapability ให้** — มีแต่ reject
- `uia_click`/`uia_set_value` **ต้องการ `element_id` + `snapshot_id`** จาก `read_screen_elements` ก่อน (`UiaClickTool.cs:48-61`) ซึ่งโมเดล **ไม่ได้ส่งมา** ตอนเรียก `computer_click(x/y)` → swap ชื่อ tool ตรง ๆ จะได้ call ที่ arg ไม่ครบ. การ "แปลง" ต้องสังเคราะห์ snapshot+element ใหม่ = เครื่องจักรใหม่ก้อนใหญ่ (เกิน Phase 1 ก้อนเล็ก)

### 3.3 ชนกับ **safety-regression test** ที่จงใจล็อกพฤติกรรม verification-first
`RuntimeStabilizationTests.cs:479-497` `Runtime_Selects_Uia_Value_Before_Generic_Type` **assert ตรง ๆ** ว่า: model เรียก `computer_type` → router คืน `Allowed=false`, `FailureCode="CapabilitySelectionRequired"`, `SelectedCapability="uia_set_value"`. การทำ Item 2 = **ต้องแก้ test นี้** (และแตะ invariant ตระกูล RS-001 anti-false-success). ผมจะ **ไม่แก้ safety-regression test เอง** โดยไม่ได้รับอนุมัติ — นั่นคือ change ที่ต้องยืนยันก่อน

> **หมายเหตุสำคัญ:** หลัง Item 1 แล้ว ผลเสียของ bounce ใน'agent-mode ลดลงมาก — เพราะ router redirect ไป `uia_click` (structured path ที่ verify ได้) เป็น 1 redirect ไม่ใช่ "การ์ดแดง"; loop จะเกิดเฉพาะโมเดลอ่อนที่ดื้อเรียก `computer_click` ซ้ำ. ส่วน plain-chat ไม่มี router อยู่แล้ว Item 1 คุมครบ.

---

## 4. Build / Test
| ชุด | ผล |
|---|---|
| `dotnet build src/SranDesktop.Tests` | ✅ 0 error |
| main suite | ✅ **1326 passed** (1320 + 6 ใหม่), 0 failed |
| UiTests | ✅ **5 passed** |
| affected (GeneralTargetAccuracy/RS001/RuntimeStabilization/RoundClickPath) | ✅ 206 passed |

**Full-solution build** ยังล้มเฉพาะ file-lock copy เข้า `App/bin` เพราะ `SranDesktop.exe` รันอยู่ (เหมือน Phase 0) — ไม่ใช่ compile error. ปิดแอปก่อน build App ให้ครบ

**ไม่ regress:** R2 ladder / R3′ amber / R5 human-error / 2b UI / STA — ไม่แตะ; `Activate` refactor พฤติกรรมเดิม (tests ยืนยัน)

---

## 5. Checklist live-validate (ผู้ใช้ทำเอง — ปิดแอป→build→เปิดใหม่ก่อน)

**agent-mode:**
- [ ] "เปิด Notepad แล้วคลิกปุ่ม/เมนู (เช่น ปุ่มบน toolbar)" → คาดหวัง: ใช้ UIA path, **ไม่มีการ์ด "Windows didn't accept the click"**, ผลเขียว/amber ไม่ใช่แดง
- [ ] สังเกตว่า **ไม่มี** foreground แย่งกับ SRAN/Click-to-Do ระหว่างคลิก (UIA-point-first ไม่ raise foreground)

**plain-chat (พิสูจน์ Item 3):**
- [ ] แชทธรรมดา (ไม่ใช่ agent task) สั่งคลิก control ในแอปเป้าหมาย → ควรได้ UIA-point-first เหมือนกัน (พิสูจน์ว่า tool-level ครอบ plain-chat)

**fallback:**
- [ ] คลิกบน canvas/พื้นที่วาด (ไม่มี UIA element) → ตก coordinate ตามเดิม (ยังคลิกได้)

---

## 6. ยืนยันขอบเขต
- ✅ Item 1 (UIA-point-first) — **เสร็จ**
- ✅ Item 3 (plain-chat) — ครอบโดย Item 1
- ⛔ Item 2 (router substitute) — **หยุดรอตัดสินใจ** (§3)
- ❌ **ไม่แตะ Phase 2** (evidence polish type) / **Phase 4** (wire `TransientFailurePolicy` retry-engine) — ตาม prompt

> **หยุดที่นี่ — รอ Beat ตัดสินใจเรื่อง Item 2 (§3) + ผล live-validate ก่อนไปต่อ**

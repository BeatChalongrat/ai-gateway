# SRAN Desktop — Phase 0 Result (UIA-first / structured-first)

> อ้างอิงแผน: `SRAN_UIA_FIRST_ARCH_PLAN.md` → **Phase 0 — Steering + bug fix (behavior-neutral)**
> สถานะ: ✅ ครบตาม Phase 0 · build/test เขียว · **ไม่แตะ Phase 1-4** · หยุดรอรีวิว + live-validate

---

## 1. รายการ Phase 0 ที่ทำครบ (ตามแผน §4 Phase 0)

| # | รายการในแผน | สถานะ |
|---|---|---|
| 1 | (ข) สลับลำดับ register: uia_*/read_screen_elements **ก่อน** computer_* | ✅ |
| 2 | (ข) รีไรต์ description `computer_click`/`computer_type` เน้น UIA-first | ✅ |
| 3 | แก้ mapping bug `Has("rejected")` → match FailureCode เป๊ะ | ✅ |
| V | VM-level tests (deterministic) ครอบทั้ง 3 | ✅ (+6 tests) |

---

## 2. ไฟล์ที่แก้ + diff ย่อ

### 2.1 `src/SranDesktop.Presentation/ViewModels/AgentExecutionViewModel.cs` (ม- bug fix)
`FailureKey(...)` — เดิม `if (Has("rejected")) return "tool.error.osRejected";` จับ **substring** "rejected" → policy/consent codes (`CapabilityPolicyRejected`, `CapabilitySelectionRequired`, permission "rejected") ถูก map เป็น "Windows didn't accept the input" ผิดบริบท

```diff
-        if (Has("rejected")) return "tool.error.osRejected"; // OsInputRejected
+        // Match the OS-rejection code EXACTLY. A substring "rejected" also matches
+        // policy/consent codes (CapabilityPolicyRejected, CapabilitySelectionRequired,
+        // permission "rejected"), which must NOT show "Windows didn't accept the input" —
+        // e.g. a non-click action that a policy blocked would wrongly read as a failed click.
+        if (string.Equals(code, "OsInputRejected", StringComparison.OrdinalIgnoreCase))
+            return "tool.error.osRejected";
```
> `code` ที่เข้ามาคือ FailureCode token ที่ extract แล้ว (จาก `ComputerAction.FailureCode` หรือ token ก่อน `:` แรก) — real code = `"OsInputRejected"` (`ComputerClickTool.cs:165`) จึง match เป๊ะได้ตรง ๆ; รูปประโยค `"OsInputRejected: ..."` ก็ยัง extract → `"OsInputRejected"` เหมือนเดิม

### 2.2 `src/SranDesktop.Infrastructure/Mcp/Tools/ComputerClickTool.cs` (description)
```diff
-    "Activate a described UI target ... Prefer target_description ... Legacy x/y image
-     coordinates are a compatibility fallback and are never preferred over semantic activation.";
+    "Activate a UI target ... STRUCTURED-FIRST: prefer read_screen_elements + uia_click
+     (element ids), or pass target_description here so a UI Automation pattern
+     (Invoke/Toggle/Select/Expand) can activate the control WITHOUT stealing foreground.
+     Legacy x/y image coordinates are the LAST RESORT — only for targets with no UI
+     Automation (canvas, games, custom-drawn controls) ...";
```

### 2.3 `src/SranDesktop.Infrastructure/Mcp/Tools/ComputerTypeTool.cs` (description)
```diff
-    "Set text in a control bound to ... The exact value is read back ...";
+    "Set text in a control bound to ... STRUCTURED-FIRST: it tries a UI Automation
+     ValuePattern / window message (no foreground needed) before any keystroke fallback.
+     Prefer read_screen_elements + uia_set_value when you already have element ids. ...";
```
> schema เดิมทุกตัว — เปลี่ยน **ข้อความ description อย่างเดียว** ไม่แตะ params/behavior

### 2.4 `src/SranDesktop.App/Hosting/AppHostBuilder.cs` (register order)
ลำดับใหม่ (structured-first): `computer_screenshot` → **`get_computer_state`, `read_screen_elements`, `uia_click`, `uia_set_value`** → `computer_click`, `computer_type`, `computer_key`, `computer_scroll`, `computer_drag`, `computer_zoom`, `open_application`
- ย้าย `var readiness` และ `var uiaSnapshots` ขึ้นก่อนบล็อก register (dependency ยังถูกต้อง — เป็น singleton เดียวกัน)
- **ทำไมมีผล:** `McpToolHost.GetToolDefinitions()` (`McpToolHost.cs:37-50`) ส่ง tool ตามลำดับ `registry.All` (insertion order) ตรง ๆ → โมเดลเห็น uia_* ก่อน computer_*
- **ปลอดภัย:** consumer อื่นของ `registry.All` ไม่ขึ้นกับลำดับ — `CapabilityInventory.Synchronize` (set), `SpawnAgentTool` (filter by name), `ChatViewModel._nestedProgressTool` (`OfType<>().FirstOrDefault()` unique type)

---

## 3. VM-level tests ที่เพิ่ม (6 tests, deterministic)

### `src/SranDesktop.Tests/Agent/RoundClickPathTests.cs` (mapping fix)
- `OsInputRejected_Code_Still_Maps_To_OsRejected` — real OS rejection ยัง map ถูก (ไม่ regress)
- `NonOs_Rejections_Do_Not_Map_To_OsRejected` [Theory ×4]: `CapabilityPolicyRejected`, `CapabilitySelectionRequired`, `Consent rejected by user`, `The action was rejected by policy` → **ต้องไม่** เป็น `tool.error.osRejected`

### `src/SranDesktop.Tests/Agent/McpToolTests.cs` (ordering invariant)
- `ToolHost_Preserves_Registration_Order` — ล็อก invariant ว่า `GetToolDefinitions()` คงลำดับ register (สิ่งที่ทำให้ reorder ใน AppHostBuilder มีผลถึงโมเดล)

> **หมายเหตุความตรงไปตรงมา:** ลำดับจริงใน `AppHostBuilder` (uia ก่อน computer) ยืนยันด้วย code review + invariant test ข้างบน — ไม่ได้เขียน DI-integration test ประกอบ host ทั้งตัว เพราะ composition root ดึง native/WPF services หนักเกินสัดส่วนของ Phase 0 และเสี่ยง flaky; การเขียน brittle test ที่แกล้งว่าคุม AppHostBuilder จะหลอกมากกว่าช่วย

---

## 4. Build / Test

| ชุด | ผล |
|---|---|
| `dotnet build src/SranDesktop.Tests` | ✅ 0 error (recompile Presentation/Infrastructure/Application ด้วยการแก้) |
| main suite `SranDesktop.Tests` | ✅ **1320 passed**, 0 failed (เดิม 1314 + 6 ใหม่) |
| `SranDesktop.UiTests` | ✅ **5 passed**, 0 failed |

**Full-solution build (`SranDesktop.sln`)** ล้ม 4 error — **ทั้งหมดเป็น file-lock (MSB3021/MSB3027) ตอน copy DLL เข้า `App/bin` เพราะ `SranDesktop.exe` กำลังรันอยู่ (PID 34228)** ไม่ใช่ compile error (0 CS errors ทุก project รวม `AppHostBuilder.cs` ที่แก้). ต้องปิดแอปก่อน build App ให้ครบ (ดู §5).

**ไม่ regress:** R2 ladder / R3′ amber / R5 human-error / 2b retrying UI / STA guard — ไม่ถูกแตะ; test เดิมทั้งหมดผ่าน

---

## 5. Checklist live-validate สำหรับผู้ใช้ (ต้องทำเอง — ไม่มี computer-use แย่ง foreground)

> ต้อง **ปิด SranDesktop.exe (PID เดิม) ให้สุด → build → เปิดใหม่** ก่อน (การแก้ description/order อยู่ในไบนารีใหม่; publish/รันใหม่ต้อง full restart)

- [ ] **[พิสูจน์ mapping fix — สำคัญสุด]** ทำ action ที่ **ไม่ใช่ click** ให้ล้มด้วย policy/consent (เช่น ปฏิเสธ consent หรือให้ router block) → การ์ด error ต้อง **ไม่** ขึ้นข้อความทำนอง "Windows ไม่รับการคลิก / didn't accept the click" อีก (ต้องเป็นข้อความ generic/บริบทถูก)
- [ ] **[OS rejection ยังถูก]** เคสที่ Windows ปฏิเสธ input จริง (`OsInputRejected`) → ยังขึ้นข้อความ osRejected ตามเดิม
- [ ] **[steering]** สั่งงานเดสก์ท็อปธรรมดา (เช่น Notepad/Calculator: พิมพ์/กดปุ่ม) แล้วดู tool cards — โมเดลควรเอน ไปทาง `read_screen_elements` → `uia_click`/`uia_set_value` หรือ `computer_click` แบบ `target_description` มากขึ้น และเรียก `computer_click` แบบ bare x/y น้อยลง (Phase 0 เป็นการ "เกลี้ยกล่อม"; การ **บังคับ** UIA-first คือ Phase 1)
- [ ] ยืนยันแอปเปิด/แชททำงานปกติหลัง restart (ไม่มี startup regression จากการ reorder register)

---

## 6. ยืนยันขอบเขต

- ✅ ทำ **เฉพาะ Phase 0** (steering description + register order + mapping bug fix)
- ❌ **ไม่ได้แตะ Phase 1** (UIA-point-first ใน `computer_click` — ยังไม่แทรก hit-test ก่อน `Win32Input.Click`)
- ❌ **ไม่ได้แตะ Phase 2** (evidence polish type)
- ❌ **ไม่ได้แตะ Phase 3** (router transparent-swap — `CapabilityRouter` ยัง reject เหมือนเดิม)
- ❌ **ไม่ได้แตะ Phase 4** (wire `TransientFailurePolicy` เข้า live retry)

> **หยุดที่นี่ — รอรีวิว + ผล live-validate ก่อนเริ่ม Phase 1**

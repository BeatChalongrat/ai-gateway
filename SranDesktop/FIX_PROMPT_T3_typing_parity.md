# Fix Prompt — T3: Claude-desktop-class typing (same model must succeed)

**เป้าหมายเดียว (วัดผลได้):** ด้วย **โมเดลเดิม (gpt-4o-mini)** งาน "เปิด Notepad แล้วพิมพ์ข้อความ"
ต้องทำได้ครบ: เปิดแอปครั้งเดียว → พิมพ์ลง control จริง → **"Fill in field" เขียว** → DoD ครบ →
จบใน step ที่จำกัด แบบเดียวกับลูป "type → ยืนยันด้วยการสังเกต → ไปต่อ" ของ Claude desktop

**รากปัญหา (จากเทสสด env สะอาด):** Win11 Notepad = RichEditD2DPT ไม่รับ UIA ValuePattern.SetValue,
fallback keyboard ก็ไม่ verify เพราะ read-back ผูก UIA/TextPattern ที่ควบคุมนี้ไม่ให้ค่า →
`uia_set_value` และ `computer_type` fail ทั้งคู่ แล้ววน harness พึ่ง read-back จาก call เดียวกัน
มากเกินไป (ไม่มีทาง verify แบบ vision อย่าง Claude desktop) และไม่มีทางฉีดข้อความเข้า HWND ตรง

วิธีทำงาน: T3 เป็นงานที่ต้อง **iterate กับ Notepad จริง** (Opus รัน GUI ไม่ได้) — แก้ + build/test เขียว +
publish → ผมเทสสด → feedback กลับ วนจนเขียว แต่ละส่วนมี "ทางเลือกให้ลองตามลำดับ"

build/test: `dotnet build SranDesktop/SranDesktop.sln` / `dotnet test SranDesktop/SranDesktop.sln`

---

## PART 1 — ทำให้ข้อความ "ลงจริง" บน RichEdit (ไม่พึ่ง UIA SetValue)

ไฟล์หลัก: `SranDesktop.Infrastructure/Mcp/Tools/UiaSetValueTool.cs`,
`SranDesktop.Infrastructure/Native/Win32Input.cs`, `ComputerTypeTool.cs`

**ก่อน:** พิมพ์ = ValuePattern.SetValue (RichEdit ไม่รับ) → fallback focus+Ctrl+A+SendInput TypeText
ซึ่งบน Win11 Notepad ข้อความไม่ลง/verify ไม่ได้ และไม่มีทางส่งเข้า control ตรง

**หลัง:** เพิ่ม **injection ladder** ลองตามลำดับ จนอันใดอันหนึ่งสำเร็จ (แต่ละชั้นผูกกับ HWND/PID
ของ control จาก snapshot เสมอ — กัน target ผิดหน้าต่างตอนมีหลาย Notepad):
1. ValuePattern.SetValue (คงไว้ เผื่อ control ที่รองรับ)
2. **Clipboard-paste เข้า control ที่โฟกัส**: เซ็ต clipboard = text → ForceForegroundWindow(hwnd) +
   ยืนยัน foreground (retry 3×80ms) + element.SetFocus + ส่ง Ctrl+V — paste เชื่อถือได้กว่า
   per-char บน RichEdit มาก (คืน clipboard เดิมหลังเสร็จ)
3. **Message-based ตรงเข้า HWND** (ไม่ต้องแย่ง foreground): เพิ่มใน Win32Input —
   `PostMessage/SendMessage` `WM_CHAR` ทีละตัว หรือ `EM_REPLACESEL`/`WM_PASTE` ส่งเข้า
   control HWND (หา child edit HWND จริงถ้า top window ไม่ใช่ตัวรับ)
4. SendInput TypeText (fallback สุดท้ายที่มีอยู่)

ต้องคง credential block + WrongWindow/same-PID เดิม และ **หา HWND ของ edit control จริง**
(ไม่ใช่ top-level window) ก่อนฉีด

---

## PART 2 — ยืนยันแบบ vision-first (รับหลักฐานจากการสังเกตอิสระ ไม่ผูก read-back call เดียว)

ไฟล์: `SranDesktop.Application/Services/SemanticOutcomeRuntime.cs` (SemanticOutcomeMatcher +
HasReadBackEvidence), และ typing tools ให้แนบ evidence ที่ถูกต้อง

**ก่อน:** TextValue verify ผ่านก็ต่อเมื่อ tool "พิมพ์" คืน read-back จาก control ใน call เดียวกัน
(`value_read_back=true`/`read_back_value=`) → ถ้า UIA read ค่าไม่ได้ = fail แม้ข้อความลงจริง

**หลัง:** ยอมรับหลักฐานหลายแหล่งแบบ Claude desktop "type → observe → confirm":
1. รับเมื่อ **การสังเกตอิสระถัดมา** (`read_screen_elements`/TextPattern ของ **control เดิม (same HWND)**)
   มีข้อความที่คาด (normalized/contains) — ไม่จำเป็นต้องมาจาก call ที่พิมพ์
2. สำหรับ TextValue ความเสี่ยงต่ำ: รับ "OS input accepted + control โฟกัส/ foreground ยืนยันแล้ว +
   observation หลังพิมพ์เห็นข้อความ" เป็น verified
3. ขยาย HasReadBackEvidence ให้รับหลักฐานจาก post-type observation (เช่น token
   `observed_value_contains=...` ที่ผูก HWND เดิม)

ห้ามผ่อนจนกลายเป็น false-success: หลักฐานต้องผูก **HWND/PID เดิม** และมีการสังเกตจริง —
แค่ "โมเดลบอกว่าพิมพ์แล้ว" ยังไม่พอ (คงสัญญา no-false-success)

---

## PART 3 — route งาน computer-use ไปโมเดล vision ที่แรงสุด (booster ไม่ใช่ไม้ค้ำ)

ไฟล์: จุดเลือกโมเดล/Auto (ฝั่ง client `ChatViewModel`/settings หรือ gateway Auto-router)

**ก่อน:** Auto เลือก gpt-4o-mini ให้ทุกงาน รวมงานเดสก์ท็อปที่ต้องใช้ vision/ควบคุมจอ

**หลัง:** เมื่อ durable task ใช้ tool หมวด Screen/Computer/UIA → auto-select โมเดล multimodal
ที่แรงสุดที่มี (เช่น GPT-4o) แทน mini; ถ้า Auto อยู่ฝั่ง gateway ให้เพิ่ม client override สำหรับ
งานเดสก์ท็อป และระบุในรายงานว่าจุดตัดสินอยู่ไฟล์ไหน

**สำคัญ:** นี่คือ booster — **Part 1+2 ต้องทำให้ mini สำเร็จเองได้** ("โมเดลเดิมต้องทำได้เท่ากัน")
Part 3 แค่ทำให้ดียิ่งขึ้น ห้ามใช้ Part 3 เป็นข้ออ้างข้าม Part 1/2

---

## PART 4 — anti-flail (จำเป็นเพื่อให้ "โมเดลเดิม" ไม่วนจนพัง)

ไฟล์: `DurableTaskRuntime.cs` (PrepareActionAsync / anti-churn)

**ก่อน:** โมเดลอ่อน flail — เปิด Notepad ซ้ำรัว ๆ, สร้างไฟล์มั่ว, ยิง Alt+F4, วน uia_set_value จน step 100

**หลัง:**
1. **ห้ามเปิดแอปซ้ำ**เมื่อ ApplicationReady ของแอปนั้น verified แล้ว (block open_application ซ้ำ,
   ชี้ให้ไปขั้นถัดไป)
2. anti-churn ครอบ **ทุก typing capability รวม uia_set_value**: ล้มเหลวรวม N ครั้ง/step →
   บังคับ re-observe 1 ครั้ง + เปลี่ยน strategy 1 ครั้ง แล้ว **pause แบบมีสถานะชัด** (ไม่วนต่อ)
3. บล็อก action ทำลาย (Alt+F4/close/quit window) ระหว่าง task ที่ยังไม่ถึงเป้า

---

## Definition of Done ของ T3 (เกณฑ์รับ)

- Unit/integration (net8.0-windows tests): injection ladder เลือกชั้นถูก + fallback ครบ; matcher
  รับ post-type observation ของ same-HWND เป็น verified; anti-churn ครอบ uia_set_value; block
  re-open เมื่อ ready; block destructive keys
- build + test เขียวทั้งหมด, ไม่ claim สำเร็จถ้าเทสไม่ผ่าน
- **เกณฑ์สด (ผมทดสอบ):** ด้วย gpt-4o-mini, "เปิด Notepad แล้วพิมพ์ X" →
  Open app ครั้งเดียว ✓ → **Fill in field เขียว** → DoD ครบ → รายงานสำเร็จพร้อม evidence จริง →
  ข้อความ X ปรากฏใน Notepad จริง — ภายใน ≤ ~12 steps ไม่วนยาว

**หมายเหตุ:** Part 1 อาจต้องลอง 2-3 ชั้นกว่าจะเจอตัวที่ลงจริงบน Win11 Notepad — ทำ ladder ให้ครบ
แล้วเราจะวน publish→เทสสด จนได้ชั้นที่เวิร์ก แก้ครั้งเดียวให้ครอบทางเลือกไว้ จะได้ไม่ต้องวนหลายรอบ

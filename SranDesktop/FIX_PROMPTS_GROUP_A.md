# Fix Prompts — Group A (Correctness: ปลดล็อกให้งานทำจบได้)

รากปัญหาที่เจอจากเทสจริง: agent เปิดแอปไม่ได้เพราะ durable runtime ปฏิเสธ `open_application`
ด้วยเหตุผล `GoalIrrelevantAction` ทำให้ทั้ง task ตัน แม้ใช้ GPT-4o
สาเหตุ 2 ชั้น: (1) `GoalRelevanceGate` ไม่ยอมให้ enabling action, (2) `OutcomeCompiler`
ไม่ได้สร้าง `ApplicationReady` outcome อย่างคงเส้นคงวา (แล้วแต่การผันคำ)

วิธีใช้: วางทีละ prompt ใน Claude Code (Opus) ที่ root repo, build+test ให้เขียวก่อนไปอันถัดไป
`dotnet build SranDesktop/SranDesktop.sln` / `dotnet test SranDesktop/SranDesktop.sln`

---

## PROMPT A1 — ให้ open_application/observation เป็น "enabling action" ที่อนุญาตเสมอ

**ก่อนแก้ (ปัจจุบัน):** `SranDesktop/src/SranDesktop.Application/Services/RuntimeActionPolicy.cs`
เมธอด `GoalRelevanceGate.Evaluate` ตัดสิน relevance จาก `outcome.Kind` แบบตายตัว —
ถ้า outcome ปัจจุบันเป็น `TextValue`/`FileState`/`OperationProcessed` แล้วโมเดลสั่ง
`open_application` จะถูกปฏิเสธด้วย `GoalIrrelevantAction` ทั้งที่การเปิดแอปเป็น precondition
ที่จำเป็นและไม่ทำลาย state ใด ๆ → task เปิดแอปไม่ได้เลย ตันทันที

**หลังแก้ (เป้าหมาย):** `open_application` (และ observation tools) ได้รับอนุญาตเสมอในฐานะ
enabling precondition ไม่ว่า outcome ปัจจุบันจะเป็นชนิดใด — agent จึงเปิดแอปได้เพื่อไปสู่
outcome ถัดไป โดยยังไม่ผ่อนเกตอันตราย (side-effect จริงยังถูกกำกับตามเดิม)

```
งาน:
1. ใน RuntimeActionPolicy.cs → GoalRelevanceGate.Evaluate:
   หลังบล็อกเช็ค mock_/fake_/update_plan และหลังบล็อก IsObservationTool
   ให้เพิ่มก่อนถึง `outcome.Kind switch`:
     if (toolName == "open_application")
         return Allowed("Opening an application is a universal enabling precondition.");
   เหตุผล: open_application เป็น read-mostly enabling action (แค่ทำให้แอปพร้อม)
   ไม่เคยทำลาย state จึงต้องอนุญาตเสมอ ไม่ผูกกับ outcome.Kind
2. ตรวจ CapabilityRouter.Route (คลาส CapabilityRouter ใน DurableTaskRuntime.cs):
   ให้แน่ใจว่ามันก็ไม่ปฏิเสธ open_application ด้วยเหตุ irrelevant เมื่อ outcome ปัจจุบัน
   เป็น TextValue/FileState — ถ้ามีตรรกะแบบนั้น ให้ยกเว้น open_application (และ observation)
   เป็น always-allowed enabling capability เช่นกัน
3. ห้ามแตะ AgentSafetyPolicy / side-effect classification — การอนุญาตนี้เป็นเรื่อง
   "relevance" เท่านั้น ไม่ใช่การข้าม consent/approval ของ action ที่มี side effect

ข้อห้าม: อย่าทำให้ tool ที่มี side effect จริง (type/click/write) กลายเป็น always-allowed
— เฉพาะ open_application และ observation tools เท่านั้น

Acceptance:
- unit test ใหม่ใน SranDesktop.Tests: เมื่อ outcome.Kind = TextValue และ toolName =
  "open_application" → GoalRelevanceGate.Evaluate ต้องคืน Allowed
- unit test: computer_type บน outcome ที่ไม่เกี่ยว ยังถูก Denied เหมือนเดิม (กัน regression ของ guard)
- build + test เขียวทั้งหมด
```

---

## PROMPT A2 — OutcomeCompiler ต้องสร้าง ApplicationReady outcome อย่างคงเส้นคงวา

**ก่อนแก้ (ปัจจุบัน):** `SranDesktop/src/SranDesktop.Application/Services/SemanticOutcomeRuntime.cs`
คลาส `OutcomeCompiler.Compile`. Regex `OpenApplication` (บรรทัด ~22-24) จับ
`(?:open|launch|start|เปิด)\s+(?<app>...)$` — มี **`$` ปิดท้าย** จึงจับได้เฉพาะเมื่อชื่อแอป
อยู่ท้าย clause. ประโยค "เปิด Notepad พิมพ์ข้อความ ..." (ไม่มีตัวคั่นหลังชื่อแอป) จะ match
ไม่ติด → ไม่มี ApplicationReady outcome → outcome แรกกลายเป็น TextValue ที่ไม่มี precondition
(นี่คือสาเหตุที่ GPT-4o ตัน แต่ "เปิด Notepad แล้วพิมพ์..." ของ mini รอดเพราะ "แล้ว" คั่นพอดี)

**หลังแก้ (เป้าหมาย):** ไม่ว่าจะผันประโยคยังไง ถ้าเป็นงานที่ต้องใช้ desktop app
คอมไพเลอร์จะสร้าง `ApplicationReady` outcome ให้เป็น precondition เสมอ →
แผน deterministic, agent เปิดแอปได้เสมอเป็นก้าวแรก

```
งาน:
1. แก้ regex OpenApplication ให้ไม่ต้องยึดท้าย clause: เอา `$` ออก และจับชื่อแอปเป็น
   token แรกหลังคำว่า open/launch/start/เปิด (เช่นหยุดที่ช่องว่างถัดไปหรือ token คำกริยา
   ตัวถัดไป) เพื่อให้ "เปิด Notepad พิมพ์ ..." ยังดึง app="Notepad" ได้
   - ระวังอย่าให้จับกินคำกริยาถัดไป (เช่น อย่าได้ app="Notepad พิมพ์ข้อความ")
2. เพิ่ม safety net ใน Compile: ถ้ามี clause ประเภท text-entry / operation / file ที่อ้างถึง
   desktop app (target ไม่ว่าง) แต่ยังไม่มี ApplicationReady outcome สำหรับ app นั้นใน
   knownApplications → ให้ AddOutcome(ApplicationReady) แทรกก่อนหน้า outcome นั้นโดยอัตโนมัติ
   (ต่อยอดจากบล็อก IsTextEntry ที่ทำแบบนี้อยู่แล้วเมื่อ application != null —
   ขยายให้ครอบคลุมกรณี app มาจากการ "เปิด X" ที่ parse สำเร็จแล้วด้วย)
3. คงพฤติกรรมเดิมของงานที่ไม่ใช่ desktop (เช่น คำนวณล้วน ๆ, งานไฟล์ที่ใช้ write_file ตรง)
   — อย่าบังคับสร้าง ApplicationReady ให้งานที่ไม่ต้องเปิดแอป

ข้อห้าม: อย่าเปลี่ยน public API ของ Compile / CompiledOutcomeGraph

Acceptance:
- unit test: Compile ของ "เปิด Notepad พิมพ์ข้อความ \"X\" แล้วบันทึกไฟล์ชื่อ a.txt"
  → outcomes[0].Kind == ApplicationReady และ expectedApplication == "Notepad"
- unit test: Compile ของ "เปิด Notepad แล้วพิมพ์ ..." (เคสที่เคยรอด) → ยังได้ ApplicationReady เหมือนเดิม
- unit test: งานคำนวณล้วน ("คำนวณ 23*47") → ไม่มี ApplicationReady ที่ไม่จำเป็น
- build + test เขียว
```

---

## PROMPT A3 — ให้ agent เลือก write_file แทนการสู้ Save As dialog

**ก่อนแก้ (ปัจจุบัน):** เมื่อ outcome เป็น "บันทึกไฟล์ชื่อ X ที่ Y" agent (โดยเฉพาะโมเดลอ่อน)
พยายามขับ Save As dialog เอง — กด Ctrl+S / Alt+F / คลิกสุ่ม / powershell — ล้มเหลวซ้ำ ๆ
หลาย step (เทสจริง mini เสีย ~10 step กับขั้นนี้) สุดท้ายรอดเพราะบังเอิญ fallback ไป create_file

**หลังแก้ (เป้าหมาย):** สำหรับ FileState outcome ที่ "เนื้อหาไฟล์รู้ล่วงหน้า" (ข้อความที่ผู้ใช้
กำหนด) agent จะใช้ `write_file` เขียนไฟล์ตรง ๆ แบบ deterministic ก่อนเป็นอันดับแรก และใช้
GUI Save As เฉพาะเมื่อเนื้อหาถูกสร้างในแอปและ reproduce เองไม่ได้ → จบขั้น save ใน 1 step

```
งาน:
1. หาที่กำหนด routing/priority ของ capability ต่อ OutcomeKind.FileState:
   - GoalRelevanceGate / CapabilityRouter / SuggestCapabilities (ใน DurableTaskRuntime.cs)
   ให้ write_file/edit_file เป็น capability ที่ "แนะนำอันดับแรก" สำหรับ FileState เมื่อ
   ทราบเนื้อหาไฟล์ (outcome.ExpectedValue มีค่า หรือ text ถูกกำหนดใน goal)
2. เพิ่มคำแนะนำใน system prompt / skill ของงาน desktop-file:
   "หากต้องบันทึกไฟล์ที่มีเนื้อหากำหนดไว้แล้ว ให้ใช้ write_file เขียนตรงไปยัง path เป้าหมาย
    ไม่ต้องขับ Save As dialog; ใช้ Save As เฉพาะเมื่อเนื้อหาถูกสร้างในแอปและ reproduce ไม่ได้"
   - หา system prompt builder ของ desktop agent (ฝั่ง gateway หรือ client) แล้วแทรกส่วนนี้
     ถ้า prompt อยู่ฝั่ง server ให้ระบุไฟล์/ตำแหน่งที่ควรแก้ในรายงานแทนการเดา
3. ตรวจว่า path resolution ถูกต้อง: "ไว้ที่ Desktop" / "working folder" ต้อง resolve ไปโฟลเดอร์
   ที่ผู้ใช้ระบุจริง ไม่ใช่ home dir (เทสจริงเซฟลง C:\Users\Beat Beat แทน working folder)
   — ผูกกับ IToolScopeResolver / FolderGrantValidator

ข้อห้าม: อย่าปิดความสามารถขับ Save As dialog ทิ้ง (ยังจำเป็นบางเคส) — แค่จัดลำดับให้
write_file มาก่อนเมื่อเหมาะสม

Acceptance:
- unit test: FileState outcome ที่มี ExpectedValue → SuggestCapabilities/route แนะนำ
  write_file เป็นอันดับต้น
- unit/integration test: path "Desktop" และ "working folder" resolve ไปโฟลเดอร์ที่ถูกต้อง
- build + test เขียว
- (ถ้าแก้ prompt ฝั่ง client) มี test ยืนยัน string คำแนะนำถูกฉีดเข้า system prompt
```

---

**ลำดับ:** A1 → A2 → A3 (A1 เป็น safety-net ปลดตันทันที, A2 แก้รากที่ต้นทาง, A3 ลด step เสีย)
**ทุก prompt:** build+test เขียวก่อนถือว่าเสร็จ, ห้าม claim สำเร็จถ้า test ไม่ผ่าน,
และให้รายงาน "ก่อน/หลัง" ของแต่ละไฟล์ที่แก้
**หลังแก้ครบ:** รันเทสจริงซ้ำ (เปิด Notepad+พิมพ์+เซฟ) ด้วยทั้ง GPT-4o และ gpt-4o-mini
เพื่อยืนยันว่าทั้งคู่เปิดแอปได้และทำงานจบ

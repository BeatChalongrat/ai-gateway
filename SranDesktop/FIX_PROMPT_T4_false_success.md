# Fix Prompt — T4: อุด false-success (สำคัญสูงสุด)

**อาการที่เจอสด:** สั่ง "เปิด Notepad แล้วพิมพ์ SRAN T3 ladder works" → tool พิมพ์ล้มเหลว
("Type text ❌"), **Notepad ว่างจริง (0 characters)** แต่ runtime รายงาน
**"Runtime verification: The persisted Definition of Done is satisfied by verified evidence."**
→ นี่คือ false-success ละเมิดสัญญาข้อ 12 ที่เป็นจุดแข็งหลักของ SRAN ต้องอุดก่อนทำอย่างอื่น

**รากของรู (วิเคราะห์จากโค้ด):** ใน `SemanticOutcomeMatcher.Match` (SemanticOutcomeRuntime.cs)
การ verify TextValue สร้าง `searchable = string.Join("\n", evidence.Append(raw.Content))` แล้ว
เช็ค `ContainsExactValue(searchable, ExpectedValue)` — **แต่ `searchable` รวม "ข้อความที่ตั้งใจพิมพ์"
(จาก args/intent/evidence description) อยู่แล้ว** เพราะ action คือ "พิมพ์ SRAN T3 ladder works"
ดังนั้น ExpectedValue ("SRAN T3 ladder works") จึง "เจอ" ใน searchable เสมอ **แม้ไม่เคยอ่านค่าจริง
จาก control** → verify ผ่านแบบหลอก ซ้ำร้าย `HasReadBackEvidence` ยังผ่านได้ถ้า string มีคำว่า
`"exact"` (บรรทัด ~560) ซึ่ง evidence เชิงบรรยายมีคำนี้บ่อย

build/test: `dotnet build SranDesktop/SranDesktop.sln` / `dotnet test SranDesktop/SranDesktop.sln`

---

## PROMPT T4

```
ไฟล์หลัก: SranDesktop/src/SranDesktop.Application/Services/SemanticOutcomeRuntime.cs
          (SemanticOutcomeMatcher.Match, HasReadBackEvidence, ContainsExactValue)
+ typing/observation tools ที่ปล่อย evidence (UiaSetValueTool, ComputerTypeTool,
  ReadScreenElementsTool) — ให้แยก "ค่าที่อ่านได้จริง" ออกจาก "ค่าที่ตั้งใจ"

หลักการเดียวที่ต้องยึด: **การ verify ค่าข้อความ ต้องมาจากการอ่านสถานะจริงของ control
(post-state read-back) ที่แยกออกจาก intent/args เท่านั้น — ห้าม match ExpectedValue กับ
blob รวมที่มีข้อความตั้งใจปนอยู่**

งาน:
1. เพิ่ม token หลักฐานเฉพาะสำหรับ "ค่าที่อ่านได้จริง" เช่น read_back_value=<actual> และ/หรือ
   observed_value=<actual> ที่ **ปล่อยได้เฉพาะเมื่อ tool อ่านค่าจริงจาก control** (WM_GETTEXT/
   TextPattern/DOM) — ไม่ใช่การ echo ข้อความที่ป้อน
   - UiaSetValueTool: read_back_value = ค่าที่อ่านกลับจริงหลัง inject (ที่มีอยู่แล้ว) เท่านั้น
   - ReadScreenElementsTool (observation): เมื่อ list control ให้แนบ observed_value=<ค่าจริงของ control>
     ผูกกับ hwnd= ของ control นั้น
   - computer_type: ถ้าอ่าน read-back ไม่ได้ → ห้ามปล่อย token ค่าอ่านจริง (ปล่อยแค่ os_input_accepted)

2. เปลี่ยนการ verify ExpectedValue ให้ match เฉพาะกับ "ค่าที่อ่านได้จริง":
   - ดึงค่าจาก read_back_value= / observed_value= (ผูก hwnd เดิม) เท่านั้น แล้วเทียบกับ ExpectedValue
     (normalized/exact ตามเดิม)
   - **ห้าม** ใช้ ContainsExactValue กับ searchable ทั้งก้อนอีกต่อไป (นั่นคือรูหลัก)
   - ถ้าไม่มีค่าที่อ่านจริง → EffectNotObserved / AwaitingObservation (ไม่ใช่ verified)

3. อุด HasReadBackEvidence:
   - ลบเงื่อนไข value.Contains("exact") ทิ้ง (หลวมเกิน)
   - ให้ผ่านเฉพาะเมื่อมี read_back_value= / observed_value= ที่เป็นค่าอ่านจริง (หรือ hash= ของไฟล์จริง)

4. ตรวจ Part 2 (post-type observation) ให้สอดคล้อง: observation จะ verify TextValue ได้ก็ต่อเมื่อ
   observed_value ที่อ่านจาก control (same hwnd) match ExpectedValue จริง — ไม่ใช่แค่มี hwnd= + คำว่า
   ข้อความปรากฏใน description

ข้อห้าม:
- ห้ามผ่อนให้ verified ผ่านจาก text ที่ tool ประกาศเอง/echo จาก args/intent
- คง WrongWindow/same-PID, credential block, และ path อื่น (FileState=hash, ProcessState=exit0) ตามเดิม
- อย่าทำให้เคสที่ verify ถูกต้องอยู่แล้ว (read_back_value จริง) กลายเป็น fail

Acceptance (regression เคสที่เจอสด — สำคัญสุด):
- unit test [reproduce bug]: TextValue outcome, ExpectedValue="SRAN T3 ladder works",
  action=computer_type ที่ล้มเหลว (ไม่มี read_back_value) + observation ที่ description บังเอิญมี
  ข้อความ "SRAN T3 ladder works" แต่ **ไม่มี observed_value จริง** → ต้อง NOT verified
- unit test: observation ที่มี observed_value=<ExpectedValue จริง> ผูก hwnd เดิม → verified
- unit test: read_back_value ที่ไม่ match ExpectedValue → EffectValueMismatch
- unit test: HasReadBackEvidence ไม่ผ่านเพราะคำว่า "exact" อีกต่อไป
- เทสเดิมทั้งหมด (false-success suite, RS001, BlindRetry, T1-T3) ยังเขียว
- build + test เขียวทั้งหมด
```

---

**หลังแก้ + publish:** ผมจะรันรอบสะอาด (ปิด Notepad ก่อน, ไม่เปิดเองจน task จบ) —
คาดหวัง: ถ้าพิมพ์ไม่ลง → runtime ต้องรายงาน **"not complete / not verified"** อย่างซื่อสัตย์
(ไม่ใช่ satisfied) พร้อม TaskId นั่นคือสัญญาข้อ 12 กลับมาแข็ง

**ลำดับหลังจากนี้:** T4 (อุด false-success) → แล้วค่อยกลับไปทำ typing (T3 ladder) ให้ลงจริง →
Group B (clarification) — เพราะ "ตอบตรง" สำคัญกว่า "ทำได้" เสมอ

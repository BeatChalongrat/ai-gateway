# Fix Prompt — T5: false-success defense-in-depth + build-version banner

ทำได้ทันทีโดยไม่ต้องรอ live test 2 เรื่อง ตรงกับสิ่งที่เพิ่งเจอ:
- **A) อุด false-success แบบ belt-and-suspenders** — T4 แก้ที่ `SemanticOutcomeMatcher.Match` แล้ว
  แต่ข้อความ "satisfied by verified evidence" ออกจาก `EvaluateCompletionAsync` ซึ่งเป็น **คนละ path**
  (ผ่าน MatchingEvidence / SatisfyDefinitionOfDone / outcome.Status) — ต้อง audit ให้แน่ว่า path นี้
  ไม่มีรูอิสระ + เพิ่ม invariant ปิดท้ายไม่ให้ประกาศ complete ถ้าไม่มีหลักฐานค่าอ่านจริง
- **B) ป้ายเวอร์ชันบิลด์ใน UI** — แก้ปัญหาที่เพิ่งงงว่ากำลังรัน binary ใหม่หรือเก่า (publish ไม่ restart
  process) ให้เห็นด้วยตาว่าตรงกับ publish ล่าสุดไหม

build/test: `dotnet build SranDesktop/SranDesktop.sln` / `dotnet test SranDesktop/SranDesktop.sln`

---

## PART A — final completion invariant (กัน false-success ทุก path)

```
ไฟล์: SranDesktop/src/SranDesktop.Application/Services/DurableTaskRuntime.cs
      (EvaluateCompletionAsync, MatchingEvidence, SatisfyDefinitionOfDone)

งาน:
1. Audit MatchingEvidence + SatisfyDefinitionOfDone: ตรวจว่ามันมาร์ค DoD item ว่า Satisfied
   โดยดู "ชนิดหลักฐาน (VerificationType)" อย่างเดียวหรือไม่ โดยไม่เช็ค "ค่าจริง" — ถ้าใช่ = รูเดียวกับ T4
   ให้บังคับว่า item ที่ผูกค่า (ExpectedValue) ต้อง match ค่าอ่านจริง (read_back_value=/observed_value=/
   display_value=) ด้วย Reuseส่วนที่ T4 เพิ่ม (ExtractActualReadValues) — ห้ามยอมรับจาก content/intent

2. เพิ่ม invariant ปิดท้ายใน EvaluateCompletionAsync ก่อน return Completed=true:
   - สำหรับทุก required outcome ชนิด TextValue/FileState ที่มี ExpectedValue → ต้องมี evidence ที่
     "ค่าที่อ่านจริง match ExpectedValue" อย่างน้อยหนึ่งชิ้น มิฉะนั้น return not-complete
     (FailureCode ใหม่ เช่น CompletionValueUnverified) — เป็นด่านสุดท้ายไม่ให้ประกาศสำเร็จลอย
   - โยงกับ persistedEvidence (ที่โหลดอยู่แล้ว) ไม่ใช่ข้อความจากโมเดล

3. เพิ่ม unit test ระดับ completion (ไม่ใช่แค่ matcher):
   - required TextValue outcome + ExpectedValue + evidence ที่ "ไม่มีค่าอ่านจริง" (มีแต่ content echo)
     → EvaluateCompletionAsync ต้อง return not-complete (ไม่ใช่ satisfied) — reproduce เคสสด
   - เคสมี read_back_value=<expected> จริง → complete ได้ตามเดิม
   - RS001 + false-success suite เดิมยังเขียว

ข้อห้าม: อย่าทำให้เคส complete ที่ถูกต้อง (มีค่าอ่านจริง) กลายเป็น fail; อย่าแตะ path ที่ไม่เกี่ยว
(ProcessState=exit0, FileState=hash จริง)
```

---

## PART B — build-version banner (เห็นด้วยตาว่ารัน binary ไหน)

```
เป้าหมาย: แก้ปัญหาที่เพิ่งเจอ — publish แล้วแต่ process เก่ายังรัน ทำให้เทสผิด binary โดยไม่รู้ตัว

งาน:
1. ฝัง build metadata ตอน build: ใน .csproj ของ SranDesktop.App เพิ่ม property ให้มี
   version + build timestamp (+ git short hash ถ้าทำได้ผ่าน MSBuild หรือ SourceRevisionId)
   ที่อ่านได้ตอน runtime (เช่น AssemblyInformationalVersion)
2. แสดงบน UI แบบไม่รบกวน: มุมล่าง sidebar หรือใน settings/about — string เล็ก ๆ เช่น
   "build 2026-07-23 15:50 · <hash>" อ่านจาก assembly ตอน startup
3. (option ที่มีประโยชน์มาก) log บรรทัดเดียวตอน startup: "SRAN started — build <timestamp> <hash>"
   เพื่อ cross-check กับเวลา publish

Acceptance:
- เปิดแอปเห็นป้ายเวอร์ชัน/timestamp ตรงกับบิลด์
- build + test เขียว
```

---

## คิวถัดไป (หลัง T5 + ยืนยัน false-success บน live แล้ว)

1. **T3 typing ให้ลงจริง** — ต้อง iterate กับ Notepad จริง (Opus แก้ ladder → ผมเทสสด → บอกว่า
   `uia_method=` ชั้นไหนลง หรือ AwaitingObservation → จูนต่อ) ยังเป็นงานที่ค้าง
2. **Group B — clarification stall** — โจทย์ซับซ้อนยังตันที่ clarification ก่อนถึงขั้นทำจริง

---

**ลำดับแนะนำ:** A (ปิด false-success ให้สนิททุก path — สำคัญสุด) → B (version banner — เล็กแต่ช่วย
ตัดปัญหา stale-binary ถาวร) หลังจากนั้นค่อยวน T3 live iteration + Group B

**ย้ำ workflow:** ทุกครั้งหลัง publish = **full quit (tray/Task Manager) + relaunch** ก่อนเทส
Part B จะช่วยให้ยืนยันได้ด้วยตาว่ารันตัวใหม่จริง

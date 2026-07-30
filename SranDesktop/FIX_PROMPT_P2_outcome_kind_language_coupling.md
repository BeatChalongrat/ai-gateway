# Fix Prompt — P2: OutcomeKind ผูกกับภาษาอังกฤษ (ทำให้ verification อ่อนลงทั้งระบบเมื่อใช้ภาษาไทย)

**สถานะ ณ ตอนเขียน:** P1 (plan ขึ้น Completed ทั้งที่ step ล้ม) แก้ไปแล้ว 5/6 งาน
เหลือ full regression uncontended กำลังรัน ระหว่างวิเคราะห์ P1 พบรูที่ใหญ่กว่าเดิม
และ**ยังไม่ได้แก้** — นี่คือใบสั่งงานสำหรับรูนั้น

---

## สิ่งที่ P1 ค้นพบ (จากรายงานของรอบที่แล้ว)

ไล่ว่าทำไม outcome "ดู docker ที่ว่ามีอะไรรันอยู่บ้าง" ถูก mark satisfied ได้
คำตอบไม่ใช่ narrative ของโมเดล แต่เป็น chain นี้:

```
1. SemanticOutcomeCompiler.InferKind แมตช์ keyword ภาษาอังกฤษเท่านั้น
   (browser|website|form|navigate|download, run|build|command|terminal|shell,
    file|folder|directory|save …)
   goal ภาษาไทย "ดู docker ที่ว่ามีอะไรรันอยู่บ้าง" ไม่แมตช์อะไรเลย
   → คอมไพล์เป็น OutcomeKind.Generic

2. NormalizeResult ลัดวงจร:
   SideEffectClass == ReadOnly && result.Success
   && outcome.Kind is StateObserved or Generic or ProcessState or FileState
   ⇒ effectObserved = true; toolReportedVerified = true
   → tool read-only ตัวไหนที่สำเร็จ ก็ถูกเครดิตว่า "สังเกตเห็น effect ที่ตั้งใจ" แล้ว

3. SemanticOutcomeMatcher.Match capability gate เป็น `_ => true` สำหรับ Generic
   → ทุก capability มีสิทธิ์ verify outcome นั้น

4. outcome.ExpectedValue เป็น null → T4 read-back gate (ExtractActualReadValues) ไม่เคยทำงาน

5. get_computer_state ("Check open windows") จึงแมตช์ → stepCompleted
   → RecordResultAsync เขียน step.Status = Verified, step.FailureCode = null,
     outcome.Status = Verified
   → ทับ Failed ที่ docker ps เพิ่งเขียนไว้ก่อนหน้าหนึ่ง call

6. EvaluateCompletionAsync ประเมินอย่างซื่อสัตย์บน state ที่ถูกทับไปแล้ว → Completed
   PlanStepViewModel.IsDone อิง Verified → goal ถูกขีดฆ่า
```

รอบที่แล้วแก้ข้อ 3 (capability gate) และข้อ 5-6 (ห้าม step ที่ไม่เกี่ยวมา verify,
ห้าม Completed เมื่อมี action ล้มค้าง, UI พูดความจริง) ไปแล้ว

**ข้อ 1 กับข้อ 2 ยังอยู่ครบ** — และข้อ 1 คือรูที่ใหญ่ที่สุดในทั้งหมด

---

## ทำไมข้อ 1 ถึงร้ายแรงกว่าทุกอย่างที่เจอมา

แอปนี้ผู้ใช้พิมพ์ภาษาไทย **goal ภาษาไทยแมตช์ keyword อังกฤษไม่ได้สักตัว
ทุก task จึงคอมไพล์เป็น `Generic`** ซึ่งเป็น kind ที่ verification อ่อนที่สุด

แปลว่า semantic outcome system ทั้งระบบ — DoD, capability gate, read-back,
T4 ที่อุดไปแล้ว — **ทำงานเต็มประสิทธิภาพเฉพาะกับผู้ใช้ที่พิมพ์อังกฤษ**
ผู้ใช้ไทยได้ verification ที่อ่อนที่สุดมาตลอดโดยไม่มีใครรู้

และนี่อธิบายว่าทำไม audit ถึงมองไม่เห็น:

| | benchmark | ผู้ใช้จริง |
|---|---|---|
| ภาษา goal | อังกฤษ | ไทย |
| OutcomeKind | เฉพาะเจาะจง | Generic เสมอ |
| เส้นทางโค้ด | เข้ม | อ่อน |

**benchmark 238 tasks กับการใช้งานจริงเดินคนละเส้นทาง แยกกันด้วยภาษา**
ตัวเลข "false success = 0" ใน Phase 4/5 จึงยังจริง แต่จริงเฉพาะเส้นทางอังกฤษ

**ปัญหาเชิงออกแบบที่ต้องแก้ไปพร้อมกัน:** ตอนนี้ "เดา kind ไม่ได้" → `Generic` → **ตรวจหลวมลง**
ตรรกะนี้กลับด้าน ของที่เราไม่รู้ว่าคืออะไร ต้องตรวจ**เข้มขึ้น** ไม่ใช่หลวมลง

build/test: `dotnet build SranDesktop/SranDesktop.sln -c Debug` /
`dotnet test SranDesktop/SranDesktop.sln`
(รันทีละครั้ง อย่าให้ซ้อน)

---

## PROMPT P2

```
ไฟล์หลัก:
  SranDesktop/src/SranDesktop.Application/Services/SemanticOutcomeRuntime.cs
    (SemanticOutcomeCompiler.InferKind, NormalizeResult, SemanticOutcomeMatcher.Match)
  + จุดที่สร้าง outcome.ExpectedValue

บริบท: รอบที่แล้ว (P1) แก้ capability gate, block Completed เมื่อมี action ล้มค้าง,
และทำให้ UI พูดความจริงไปแล้ว ห้ามทำให้ของพวกนั้นพัง
รอบนี้แก้ต้นทาง: kind inference ผูกกับภาษา และ read-only short-circuit

หลักการสองข้อที่ต้องยึด:
  1. **การตัดสินว่า outcome เป็นชนิดไหน ห้ามขึ้นกับภาษาที่ผู้ใช้พิมพ์**
  2. **เดาไม่ได้ = ตรวจเข้มขึ้น ไม่ใช่หลวมลง** (fail closed)

ข้อห้าม:
  - ห้ามแก้ด้วยการเติม keyword ภาษาไทยเข้าไปใน regex เดิม
    นั่นแค่ย้ายปัญหาไปภาษาที่สาม และจะพังอีกกับภาษาญี่ปุ่น/จีน/ผู้ใช้ที่พิมพ์ผสม
  - ห้ามทำให้ Generic แปลว่า "ผ่านง่าย" ต่อไป

งาน:

0. **ยืนยันสถานะก่อนแก้** — เขียน test ที่ให้ goal เดียวกันสองภาษา
   (เช่น "list running docker containers" กับ "ดู docker ที่ว่ามีอะไรรันอยู่บ้าง")
   แล้ว assert ว่าได้ OutcomeKind เดียวกัน
   test นี้ต้อง **แดงก่อนแก้** ถ้าเขียวแปลว่าวินิจฉัยผิด ให้หยุดแล้วรายงาน
   พร้อมกันนั้นตรวจว่าข้อ 2 (NormalizeResult read-only short-circuit)
   ยังมีผลอยู่จริงหลังการแก้รอบที่แล้วหรือไม่ — รายงานมาด้วย

1. เปลี่ยนที่มาของ OutcomeKind จาก "keyword ในข้อความ goal" เป็นสัญญาณที่ไม่ขึ้นกับภาษา
   ทางที่ควรพิจารณา (เลือกและให้เหตุผล ไม่ต้องทำทุกทาง):
   a. อนุมานจาก capability/tool ที่แผนตั้งใจใช้จริง (structured ไม่ใช่ prose)
   b. ให้ตัวที่สร้างแผนระบุ kind ออกมาเป็น field ตรง ๆ แทนการให้ระบบเดาจากข้อความ
   c. ถ้ายังต้องอนุมานจากข้อความ ต้องผ่านชั้นที่ normalize ภาษาก่อน
      และต้องมี fallback ที่เข้มตามหลักการข้อ 2
   เกณฑ์เลือก: ทางไหนทำให้ผลลัพธ์ไม่ขึ้นกับภาษาโดยโครงสร้าง ไม่ใช่โดยการเติม dictionary

2. กลับตรรกะ Generic ให้ fail closed
   - Generic (หรือ kind ที่อนุมานไม่ได้) ต้องต้องการหลักฐาน **มากกว่าหรือเท่ากับ**
     kind ที่เฉพาะเจาะจง ห้ามน้อยกว่า
   - โดยเฉพาะ: ห้าม tool ที่ไม่เกี่ยวกับ outcome มา verify มันได้
     (รอบที่แล้วปิด capability gate ไปแล้ว — ตรวจว่ายังปิดอยู่หลังแก้ข้อ 1)

3. อุด NormalizeResult read-only short-circuit (ข้อ 2 ของ chain)
   - "tool read-only สำเร็จ" ไม่เท่ากับ "สังเกตเห็น effect ที่ตั้งใจ"
   - effectObserved ต้องมาจากการที่ผลของ tool **เกี่ยวข้องกับ outcome นั้นจริง**
     ไม่ใช่แค่ tool คืน Success
   - ยึดหลักเดียวกับ T4: แยก "หลักฐานที่อ่านได้จริงและเกี่ยวข้อง"
     ออกจาก "tool ทำงานเสร็จโดยไม่ error"

4. เพิ่ม test ภาษาไม่อังกฤษเข้า suite ถาวร
   - อย่างน้อย: ไทย + อีกหนึ่งภาษาที่ไม่ใช่ตัวอักษรละติน + เคสพิมพ์ผสมไทย-อังกฤษ
   - ครอบทั้ง 8 capability ไม่ใช่แค่ shell
   - เคส regression หลัก: goal ไทย + tool ที่ไม่เกี่ยวสำเร็จ + tool ที่เกี่ยวล้ม
     → outcome ต้องไม่ Verified และ run ต้องไม่ Completed

5. รายงานผลกระทบต่อ audit
   - นับว่า benchmark 238 tasks ปัจจุบันมีกี่ตัวที่เดินเส้นทาง Generic
   - ถ้าคำตอบคือ "แทบไม่มี" ให้ระบุไว้ตรง ๆ ว่าตัวเลข false success = 0
     ไม่ครอบเส้นทางที่ผู้ใช้จริงเดิน และเสนอว่าต้องเพิ่ม benchmark ชุดภาษาอะไรบ้าง

เกณฑ์ผ่าน:
  - test ข้อ 0 แดงก่อนแก้ เขียวหลังแก้
  - goal เดียวกันสองภาษา → OutcomeKind เดียวกัน และเส้นทาง verification เดียวกัน
  - repro จริงในแอป: ถามภาษาไทยให้ทำงานที่ tool หลักจะล้ม
    → outcome ต้องไม่ถูกขีดฆ่า และ header ต้องไม่ขึ้น Completed
  - เคสตรงข้ามไม่พัง: goal ไทยที่ tool ทำงานสำเร็จจริง → ต้อง Verified/Completed ตามปกติ
  - ของที่ P1 แก้ไว้ต้องยังเขียวทั้งหมด
  - `dotnet build ... -c Debug` 0 errors
  - `dotnet test ...` เขียวทั้งชุด ไม่มี test ใหม่ที่ skip
```

---

## งานพ่วง (เล็ก ทำหลัง P2 หรือคู่ขนานก็ได้)

**A. `publish/win-x64` ค้างที่ 23 ก.ค.**
`SranDesktop.exe` / `SranDesktop.Infrastructure.dll` ใน publish ลงวันที่ 2026-07-23
ขณะที่ `bin/Debug` เป็น 2026-07-30 → shortcut ที่ผู้ใช้กดเปิดรันโค้ดเก่ากว่าหนึ่งสัปดาห์
ทำให้การทดสอบทุกครั้งที่ผ่านมาไม่ได้ทดสอบโค้ดปัจจุบัน
ต้องมีขั้นตอน publish ที่บังคับให้ artifact ตรงกับ source ที่ build
และควรให้แอปแสดง build id/วันที่ที่มุมใดมุมหนึ่ง เพื่อให้เห็นทันทีว่ากำลังรันตัวไหน

**B. `shell_execution_mode` ยังไม่มีปุ่มใน UI**
ตอนนี้เปลี่ยนได้ทางเดียวคือแก้ `%APPDATA%\SRAN Desktop\settings.json` ตอนแอปปิด
(เพราะ `JsonSettingsService.Save()` เขียนทับทั้งไฟล์จาก object ในหน่วยความจำ)
ควรเพิ่ม dropdown ต่อจากของเดิมใน `ComposerControl.xaml` ที่มี "Skip all approvals" อยู่
พร้อมข้อความเตือนความเสี่ยงเมื่อเลือก host

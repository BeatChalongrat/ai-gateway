# Fix Prompt — P1: plan ขึ้น "Completed" ทั้งที่ step ล้ม (false success ชั้น plan)

**อาการที่เจอสด (GPT-4o, 2026-07-30):** ถาม "ดู docker ที่ว่ามีอะไรรันอยู่บ้าง"

```
ดู docker ที่ว่ามีอะไรรันอยู่บ้าง | plan v1 | Completed      ← header บอกเสร็จ
  ✓ ~~ดู docker ที่ว่ามีอะไรรันอยู่บ้าง~~                    ← outcome ขีดฆ่า
  ✗ Run command  docker ps   → This step didn't succeed     ← งานจริงล้ม
  ✓ Check open windows       → 138 ms
```

แล้วโมเดลตอบว่า "ดูเหมือนว่าตอนนี้ไม่มีสิทธิ์ใช้คำสั่ง docker บนเครื่องของคุณโดยตรง
ฉันแนะนำให้คุณเปิด Terminal บนเครื่องของคุณเองและใส่คำสั่งนี้"

→ **run จบเป็น Completed, goal ถูกขีดฆ่า, แต่คำถามของผู้ใช้ไม่เคยถูกตอบจากข้อมูลจริง**
ตัว tool ซื่อสัตย์แล้ว (รายงาน ✗ ถูกต้อง) แต่ชั้นที่สรุปผลข้างบนไม่ฟัง — นี่คือ false-success
ตระกูลเดียวกับ T4 แค่ย้ายจากชั้น runtime verification ขึ้นมาที่ชั้น plan/run status
ละเมิดสัญญาข้อ 12 เหมือนกัน

**หมายเหตุสำคัญ:** เคสนี้ **ไม่ใช่บั๊กของ sandbox** — `docker ps` ล้มเพราะ sandbox lane
ไม่มี docker ซึ่งถูกต้องตามดีไซน์ บั๊กคือ "ล้มแล้วยังขึ้น Completed"

---

## รากของรู (ยืนยันจาก source แล้ว)

**จุดที่ 1 — guard ตรวจแค่ "ยังไม่รู้ผล" ไม่ตรวจ "รู้ว่าล้ม"**

`DurableTaskRuntime.CompleteConversationAsync` (บรรทัด ~1371-1414):

```csharp
// A conversation-mode turn can still have dispatched a real side effect
// ... so the unresolved case ends as NeedsUser
var unresolved = await FindUnresolvedSideEffectAsync(snapshot, ct);
if (unresolved is not null) { /* → UnknownOutcome → NeedsUser */ }
snapshot.Task.Status = unresolved is null
    ? AgentTaskStatus.Completed          // ← ล้มแบบชัดเจนก็มาถึงบรรทัดนี้
    : snapshot.Task.Status;
```

`FindUnresolvedSideEffectAsync` มองหา action ที่ผลลัพธ์ **ไม่รู้** (killed relay, timeout,
inconclusive postcondition) — แต่ `docker ps` ที่ล้มแบบชัดเจน (FailedEffect / non-zero exit)
ถือว่า **"resolved" แล้ว** guard เลยปล่อยผ่าน → `Completed`

comment ในโค้ดเขียนเองว่า "Closing that as a completed run claiming 'no side effect' is exactly
the false success this runtime exists to prevent" — เจตนาถูก แต่ครอบแค่ครึ่งเดียว

**จุดที่ 2 — UI ก็อปสถานะมาแสดงตรง ๆ ไม่แยกแยะ**

`AuthoritativePlanView.From` (`AgentRuntimeModels.cs:255-264`) →
`snapshot.Task.Status.ToString()` เข้า `TaskStatus`

`MessageViewModel.UpdatePlan` (`MessageViewModel.cs:46`):

```csharp
PlanHeader = $"{plan.Goal} | plan v{plan.PlanVersion} | {plan.TaskStatus}";
```

ไม่มีการแยก "Completed แล้วบรรลุ goal" ออกจาก "Completed ทั้งที่มี step ล้มค้างอยู่"

**จุดที่ 3 (ต้องไปยืนยันเอง) — outcome ถูก mark satisfied ได้ยังไง**

`plan.Outcomes.Where(item => item.Required)` แล้วขีดฆ่าตาม `outcome.Status`
ต้องไล่ว่า outcome "ดู docker..." ถูกทำให้ satisfied ด้วยหลักฐานอะไร — สงสัยว่า
goal เชิงข้อมูล ("ดู/เช็ค/บอกหน่อยว่า X") ถูก satisfy ได้ด้วย **ข้อความสรุปของโมเดลเอง**
หรือด้วย step อื่นที่ไม่เกี่ยว (`Check open windows`) ซึ่งเป็นรูเดียวกับที่ T4 อุดไป
คือ "หลักฐานที่ตั้งใจ" ปนกับ "หลักฐานที่อ่านได้จริง"

build/test: `dotnet build SranDesktop/SranDesktop.sln -c Debug` /
`dotnet test SranDesktop/SranDesktop.sln`
(รันทีละครั้ง — การรัน `dotnet test` ซ้อนกันทำให้ test ที่ assert บน machine-wide state พังแบบไม่เกี่ยวกับโค้ด)

---

## PROMPT P1

```
ไฟล์หลัก:
  SranDesktop/src/SranDesktop.Application/Services/DurableTaskRuntime.cs
    (CompleteConversationAsync ~1371, FindUnresolvedSideEffectAsync, EvaluateCompletionAsync ~1177)
  SranDesktop/src/SranDesktop.Application/Models/AgentRuntimeModels.cs
    (AuthoritativePlanView.From ~255)
  SranDesktop/src/SranDesktop.Presentation/ViewModels/MessageViewModel.cs (UpdatePlan ~44)
  + ตัวที่ตัดสินว่า outcome satisfied (SemanticOutcomeRuntime / DoD evaluation)

บริบท: T4 อุด false-success ที่ชั้น "tool ล้มแต่ runtime verify ผ่าน" ไปแล้ว
งานนี้คือรูเดียวกันที่ชั้นถัดขึ้นมา: "step ล้มแต่ run จบเป็น Completed และ goal ถูกขีดฆ่า"

หลักการเดียวที่ต้องยึด:
  **Completed ต้องแปลว่า "goal บรรลุแล้ว" ไม่ใช่ "ลูปหยุดแล้ว"**
  ถ้ามี required action ที่ล้มและไม่มี action ที่สำเร็จตามมาทำงานเดียวกันได้สำเร็จ
  run นั้นห้ามจบเป็น Completed เด็ดขาด

ข้อห้าม (สำคัญ):
  - ห้ามแก้โดยทำให้ tool "ดูเหมือนสำเร็จ"
  - ห้ามแก้แค่ใน ViewModel (นั่นคือซ่อนอาการ ไม่ใช่ปิดรู) — ต้องแก้ที่ runtime เป็นหลัก
    แล้วค่อยให้ UI สะท้อนความจริง
  - ห้ามทำให้ทุก tool call ที่ล้มบล็อก completion — โมเดลลองผิดแล้วสำเร็จด้วยวิธีอื่น
    เป็นพฤติกรรมที่ถูกต้องและต้องยังจบเป็น Completed ได้

งาน:

0. **เขียน test ที่ fail ก่อนแก้โค้ด** — reproduce เคสนี้:
   conversation-mode run, required action หนึ่งตัวจบด้วย FailedEffect, ไม่มี recovery
   → assert ว่า Status != Completed
   ถ้า test ผ่านตั้งแต่ยังไม่แก้ แปลว่าวินิจฉัยผิด ให้หยุดแล้วรายงานว่าจริง ๆ แล้วอะไรทำให้ Completed

1. เพิ่มการตรวจ "failed & unrecovered" คู่กับ "unresolved" ใน CompleteConversationAsync
   - เขียน FindFailedUnrecoveredActionAsync (หรือขยาย FindUnresolvedSideEffectAsync
     ให้คืนเหตุผลสองแบบแยกกัน)
   - นิยาม unrecovered: action ที่ล้ม และไม่มี action ที่สำเร็จตามหลังซึ่ง
     บรรลุ required outcome เดียวกัน (ผูกด้วย outcome id / capability ไม่ใช่ชื่อ tool)
   - ปลายทางที่ถูกต้องคือ NeedsUser (ผู้ใช้ต้องรู้ว่ายังไม่ได้คำตอบ) — เดินผ่าน MoveAsync
     ตาม state machine เท่านั้น ห้าม jump สถานะตรง ๆ
     ตรวจก่อนว่า transition ที่ใช้ legal ไหม (มีตัวอย่างที่ UnknownOutcome → NeedsUser)

2. ไล่จุดที่ 3 ให้จบ: outcome "ดู docker..." ถูก mark satisfied ด้วยหลักฐานอะไร
   - ถ้าถูก satisfy ด้วยข้อความสรุปของโมเดลเอง หรือด้วย step ที่ไม่เกี่ยวข้อง → ปิดรูนั้น
   - goal เชิงข้อมูล ("ดู/เช็ค/ลิสต์/บอกหน่อยว่า X") ต้อง satisfy ได้ก็ต่อเมื่อมี
     หลักฐานจาก tool ที่ผลิตข้อมูลนั้นจริง ไม่ใช่จาก narrative ของโมเดล
   - ยึดหลักเดียวกับ T4: แยก "ค่าที่อ่านได้จริง" ออกจาก "ค่าที่ตั้งใจ/ที่เล่า"

3. ให้ UI พูดความจริง (หลังแก้ runtime แล้ว)
   - header ต้องไม่ขึ้น Completed เมื่อ run จบด้วย NeedsUser
   - outcome ที่มี required action ล้มค้างอยู่ ห้ามขีดฆ่า
   - ถ้าจบแบบมี step ล้ม ต้องเห็นได้จาก header ว่ายังไม่เสร็จ ไม่ใช่ต้องกางการ์ดถึงจะรู้

4. เพิ่ม source-level invariant test (แนวเดียวกับ RuntimeBypassInvariantTests)
   ที่กันไม่ให้อนาคตมีเส้นทางไหนตั้ง AgentTaskStatus.Completed โดยไม่ผ่าน
   การตรวจ failed/unresolved — behavioural test จับ regression แบบนี้ไม่ได้

เกณฑ์ผ่าน (ต้องครบทุกข้อ):
  - test ข้อ 0 แดงก่อนแก้ เขียวหลังแก้
  - repro จริง: เปิดแอป (sandbox lane) ถาม "ดู docker ที่ว่ามีอะไรรันอยู่บ้าง"
    → header ต้องไม่ขึ้น Completed และ goal ต้องไม่ถูกขีดฆ่า
  - เคสตรงข้ามต้องไม่พัง: run ที่ tool แรกล้มแล้ว tool ที่สองสำเร็จตาม goal
    → ต้องยังจบเป็น Completed ปกติ (เขียน test คุมด้วย)
  - `dotnet build SranDesktop/SranDesktop.sln -c Debug` 0 errors
  - `dotnet test SranDesktop/SranDesktop.sln` เขียวทั้งชุด ไม่มี test ใหม่ที่ skip
  - รายงานกลับมาว่า outcome ถูก mark satisfied ด้วยหลักฐานอะไร (คำตอบของข้อ 2)
    แม้จะแก้เสร็จแล้วก็ต้องเขียนไว้ เพราะเป็นข้อมูลที่ audit ยังไม่มี
```

---

## หมายเหตุสำหรับคนรีวิว

รูนี้ **benchmark ทั้ง 238 tasks มองไม่เห็น** เพราะทุกตัววัดที่ชั้น tool/runtime ลงไป
ไม่มีตัวไหนถามว่า "สถานะที่ผู้ใช้เห็นตรงกับผลจริงไหม" — ตัวเลข false success = 0
ใน Phase 4/5 จึงยังจริงอยู่ แต่ไม่ครอบเคสนี้

หลังปิดรูนี้ ควรเพิ่ม benchmark category ใหม่: **plan/run status fidelity**
— วัดว่าสถานะที่แสดงตรงกับผลจริงกี่เปอร์เซ็นต์ แยกจาก tool-level false success

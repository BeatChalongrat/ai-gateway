# SRAN Desktop — Agent Capability Audit (read-only)

**ขอบเขต:** อ่าน codebase `SranDesktop/` (WPF .NET 8, Clean Architecture, WebView2) ไม่แก้ไฟล์
**คำถามหลัก:** SRAN ทำได้ตามวิสัยทัศน์ "AI Work Agent ที่เข้าใจผลลัพธ์ ลงมือทำจริงบน Windows ตรวจว่าสำเร็จจริง ฟื้นตัวได้ ทำงานยาวจนจบ" หรือยัง — ให้คะแนนแต่ละหัวข้อเต็ม 10

## สรุปผู้บริหาร

โครงสร้างนี้ถูกออกแบบมา **ตรงกับ 12 ข้อนี้แบบตั้งใจ** ไม่ใช่ chat wrapper ธรรมดา หัวใจคือ `DurableTaskRuntime` (2,249 บรรทัด) + `ChatOrchestrator` ที่บังคับ contract: goal → outcome → plan → action (idempotent) → **independent verification** → Definition of Done → completion โดยมี SQLite checkpoint, reconciliation, และตัวนับ `falseSuccessPrevented`/`duplicatesPrevented` จริง มีเทสต์ ~799 เคสใน 104 ไฟล์

**จุดแข็งที่สุด:** การพิสูจน์ผลลัพธ์แบบอิสระ (ไม่เชื่อคำกล่าวอ้างของโมเดล), durability/resume, และการควบคุม Windows จริง (Win32 SendInput + UIA + semantic/vision targeting)

**จุดที่ควรปรับเพื่อทัดเทียม Claude/ChatGPT desktop:**
1. **Prompt-injection boundary ต่อไม่ครบ** — นิยามไว้แต่ไม่ถูกเรียกใช้ในเครื่องมือเว็บ/เชลล์
2. **ไม่ stream ข้อความตอบระหว่าง durable active** — ผู้ใช้ไม่เห็น token ไหลแบบ Claude/ChatGPT
3. **GoalInterpreter เป็น regex/keyword** — ถ้าประโยคไม่ตรงคำ trigger ระบบ verification ทั้งชุดจะเงียบ (fallback เป็นแชตธรรมดา)

**คะแนนรวมเฉลี่ย: 8.2 / 10**

---

## คะแนนราย 12 หัวข้อ

### 1. เข้าใจเป้าหมายและข้อห้ามครบ — 7/10
`GoalInterpreter.Interpret()` แยก goal, constraints, non-goals, risk, mode และสร้าง Definition of Done; ข้อห้ามจับด้วย regex `ห้าม / must not / do not / without`. `OutcomeCompiler` แปลง goal เป็น outcome ที่ตรวจสอบได้
**ข้อจำกัด:** เป็น keyword/regex + hardcode สองภาษา (ไทย/อังกฤษ), regex ตัดที่เครื่องหมายวรรคตอนทำให้ข้อห้ามหลายอนุประโยคขาด, ถ้าประโยคไม่มี ActionTerm จะถือว่า "ไม่ actionable" แล้ว durable runtime จะไม่ทำงานเลย → เจตนาผู้ใช้ที่ซับซ้อนอาจหลุด
**ควรปรับ:** เพิ่ม pass สกัด goal/prohibition ด้วยโมเดล (structured output) เสริม regex

### 2. ถามเฉพาะข้อมูลที่จำเป็น — 6/10
มี clarification จริง: ถามเมื่ออ้างอิงเป้าหมายไม่ชัด (`ไฟล์นี้/โปรแกรมนี้`) หรือ skill resolution กำกวม (`Ambiguous` + คะแนน gap ไม่ปลอดภัย) และถามครั้งเดียว ไม่ถามรก
**ข้อจำกัด:** ตัวจับ "ข้อมูลขาด" แคบมาก (แทบเฉพาะคำว่า this file/this program) — เคสที่ขาด budget, path ปลายทาง, รูปแบบ output มักไม่ถูกถาม → ระบบมักเดาแทนถาม
**ควรปรับ:** ขยาย missing-information detection (ปลายทาง, ขอบเขต, รูปแบบผลลัพธ์)

### 3. วางแผนหลายขั้น — 8/10
`TaskPlan → Milestone → Step` มี dependency, `PromoteReadySteps`, `update_plan` tool, และ `PlanCoverageValidator` ที่ **ปฏิเสธแผน** ที่ไม่ครอบคลุม outcome (`PlanProposalRejected`) แผนใหม่ต้อง supersede แผนเก่าและผ่าน coverage ก่อน
**ข้อจำกัด:** แผนเริ่มต้นจาก template (`OutcomeCompiler`) ค่อนข้างตายตัว พึ่งโมเดลเสนอ `update_plan` เพื่อได้แผนละเอียด

### 4. เลือกเครื่องมือที่แม่นที่สุด — 8/10
`CapabilityRouter.Route()` ให้คะแนน candidate ตาม modality/base score + `GoalRelevanceGate` ปฏิเสธเครื่องมือที่ไม่เกี่ยวกับ outcome + เช็ค capability health (unhealthy ≥3 ครั้ง = ห้าม dispatch) มี `RoutingDecisionRejected` เป็นหลักฐาน
**ข้อจำกัด:** การจับคู่ outcome-kind ↔ tool เป็นตาราง hardcode ต่อ tool ใหม่ต้องแก้หลายจุด

### 5. ใช้ Windows และแอปจริง — 9/10
ของจริง: `Win32Input` ใช้ `SendInput` P/Invoke, `UiaWalker`/`UiaTargetRuntime` เดิน UI Automation tree, `computer_click` เลือกเป้าแบบ semantic + vision fallback + validate window, `ApplicationReadinessService` เช็คแอปพร้อมรับ input จริง ไม่ใช่ mock
**ข้อจำกัด:** ผูกกับ Windows เท่านั้น (ตามดีไซน์) — วิชวล targeting ยังพึ่ง capture id/coordinate เป็น fallback

### 6. อ่านผลจริง ไม่สร้าง evidence เอง — 9/10 ⭐
`SemanticOutcomeMatcher.Match()` เป็นตัวเด่น: ถ้า `toolReportedVerified=false` → `EffectNotObserved`; ปฏิเสธ `update_plan` เป็นหลักฐาน (`PlanProposalIsNotEvidence`); ต้องมีหลักฐานจริง เช่น `hwnd=`/`pid=`, exit code 0, read-back ของค่าที่พิมพ์ การ verify มาจาก `ToolResult.Effect`/`ComputerAction` (การสังเกตจริง) **ไม่ใช่คำกล่าวอ้างของโมเดล**
**ข้อจำกัด:** คุณภาพหลักฐานขึ้นกับว่าแต่ละ tool รายงาน Effect ครบแค่ไหน

### 7. แก้ปัญหาโดยไม่หลุดเป้าหมาย — 8/10
สถานะ `Recovering`, บังคับ **fresh observation ก่อน retry** action ที่ล้มเหลว (`FreshObservationRequired`), บล็อก retry ซ้ำแบบไร้ข้อมูล (`BlindRetryBlocked`) เว้นแต่เปลี่ยน args/tool/strategy/plan, มี `CompensationCoordinator` วาง rollback, และ goal ถูก pin ไว้ใน context ทุกรอบ (`[durable_task_invariants]`)
**ข้อจำกัด:** retry budget/action budget เป็นค่าคงที่ ไม่ปรับตามความเสี่ยง

### 8. หยุดและทำต่อได้ — 9/10 ⭐
Checkpoint ลง SQLite ทุก action, `StartOrResumeAsync` กู้งานค้าง, budget เป็น **soft cap** (ถึงลิมิตแล้ว "pause + Continue" ไม่ทิ้งงาน), `CheckpointIntegrity` เช็คความสมบูรณ์, resume ที่มี action ค้างจะเข้าโหมด `ReconciliationRequired` ก่อนทำ side-effect ใหม่
**ข้อจำกัด:** —

### 9. ป้องกัน action ซ้ำและผิดหน้าต่าง — 8/10
Idempotency key (`taskId|stepId|tool|argsHash`) + `DuplicateSideEffectPrevented` (กันซ้ำถ้า dispatched/verified แล้ว), `ForegroundTargetTracker` + เกต `WrongWindow` (args ต้องระบุแอปที่คาดหวัง), target validation ก่อนคลิก
**ข้อจำกัด:** เกต WrongWindow เช็คด้วย "args มีชื่อแอปเป็น substring" ซึ่งหลอกได้ง่ายกว่าการยึด HWND/PID เป็นหลักเสมอ

### 10. ตรวจทุก Definition of Done — 9/10 ⭐
`EvaluateCompletionAsync` ไล่ตรวจ **ทุกชั้น**: step verified → required outcome verified → skill verifier → DoD ต่อ milestone → DoD ต่อ goal โดยแต่ละ item ต้องมี evidence ที่ตรง `VerificationType` ไม่งั้น `NotVerifiable`; ทุก guard เพิ่มตัวนับ `falseSuccessPrevented`
**ข้อจำกัด:** การจับคู่ evidence↔DoD ใช้ heuristic keyword บางส่วน

### 11. แสดงหลักฐานและประวัติการทำงาน — 8/10
`TaskEvent` เป็น event log ครบ (GoalInterpreted, ActionDispatching, VerificationCompleted, DuplicateSideEffectPrevented, ฯลฯ), `TaskEvidence` เก็บหลักฐานราย action, `ToolExecution` log ลง store (redact แล้ว), และ `AuthoritativePlanEvent` ส่งแผน+หลักฐานขึ้น UI (เปิดโดยดีฟอลต์)
**ข้อจำกัด:** ต้องเช็คว่า UI ผูกหลักฐานให้ผู้ใช้กดดูย้อนหลังได้จริงครบทุก event

### 12. ไม่รายงานสำเร็จจนกว่างานจะสำเร็จจริง — 9/10 ⭐
สำเร็จได้ทางเดียวคือผ่าน `EvaluateCompletionAsync` ครบทุกชั้น; ระหว่างนั้นสถานะเป็น `VerificationPending` และคำตอบสุดท้ายสร้างโดย `BuildVerifiedFinalResponse` ที่แนบ evidence ids; ถ้ายังไม่ครบจะตอบว่า "ยังไม่เสร็จ" พร้อม TaskId มี `UnverifiedActionMessage` สำหรับ action ที่ยังไม่ verify
**ข้อจำกัด:** ในโหมดแชตธรรมดา (durable ไม่ทำงาน) การป้องกัน false-success จะอ่อนลงเหลือแค่ marker ระดับ tool

---

## บั๊ก / จุดที่ควรปรับ (เรียงตามผลกระทบ)

**1. [Security] Untrusted-observation boundary ต่อไม่ครบ**
`UntrustedObservationBoundary` จัดคลาส `web_*`, `browser_*`, `run_command`, `read_clipboard` เป็น "เนื้อหาภายนอกที่เชื่อไม่ได้" แต่ `Protect()` ถูกเรียกจริงแค่ใน `ReadClipboardTool` และ `RemoteMcpTool` เท่านั้น — `WebFetchTool`, `WebSearchTool`, `BrowserReadPageTool`, `RunCommandTool` **ไม่ห่อ output** ด้วย marker ทำให้เนื้อหาเว็บ/เชลล์ไหลเข้าโมเดลแบบไม่ติดป้าย (เสี่ยง prompt injection) โดยเฉพาะใน path แชตธรรมดา
→ *ควรเรียก `UntrustedObservationBoundary.Protect()` ในเครื่องมือเหล่านั้น*

**2. [UX parity] ไม่ stream ข้อความระหว่าง durable active**
`ChatOrchestrator` บรรทัด 216: `if (!pendingUnverifiedAction && durableContext is null) yield return delta;` → เมื่อ durable runtime ทำงาน (คือกรณี agent จริงเกือบทั้งหมด) **จะไม่ส่ง content delta ทีละ token** ผู้ใช้เห็นแต่คำตอบสุดท้ายที่ประกอบเสร็จ ต่างจาก Claude/ChatGPT desktop ที่ข้อความไหลตลอด → ความรู้สึก "ช้า/ค้าง"
→ *สตรีมเป็นข้อความ draft แล้วค่อย reconcile ตอนจบ หรือสตรีมพร้อมป้าย "ยังไม่ยืนยัน"*

**3. [Robustness] GoalInterpreter เปราะ**
เป็น keyword/regex สองภาษาแบบ hardcode; ถ้าประโยคไม่ตรง ActionTerm → `IsActionable=false` → `StartOrResumeAsync` คืน null → **ปิดกลไก verification ทั้งชุดเงียบ ๆ** กลายเป็นแชตธรรมดา ผู้ใช้ไม่รู้ว่าเสีย guarantee ไป
→ *ใช้โมเดลช่วยตัดสิน actionable/goal/prohibition; หรืออย่างน้อยแจ้งผู้ใช้เมื่อ downgrade เป็นโหมดไม่ verify*

**4. [UX] clarification แคบเกิน (หัวข้อ 2)** — ขยายการตรวจข้อมูลขาด (ปลายทาง/ขอบเขต/รูปแบบผลลัพธ์)

**5. [Hardening] WrongWindow เช็คด้วย substring ของ args** — ควรยึด HWND/PID ที่ resolve จริงเป็นหลักแทนการหาชื่อแอปใน args

**6. [Repo hygiene]** มี `bin/Debug/`, `publish/win-x64/`, และ `installer/*.exe` ถูก commit เข้า repo (หลายพันไฟล์) — ควรใส่ .gitignore ลด noise และขนาด repo

**7. [Minor]** approval หมดอายุ fix ที่ 5 นาที (`AddMinutes(5)`), constraint regex ตัดที่ punctuation ทำข้อห้ามหลายอนุประโยคขาด

---

## บทสรุปเทียบวิสัยทัศน์

ประโยคเป้าหมาย ("AI Work Agent ที่เข้าใจผลลัพธ์ ลงมือทำบนคอมอย่างปลอดภัย ตรวจว่าสำเร็จจริง ฟื้นตัวได้ ทำงานยาวจนจบ รองรับหลายโมเดล+นโยบายองค์กร") — **สถาปัตยกรรมสอดคล้องกับวิสัยทัศน์นี้อย่างชัดเจนและลึกกว่าที่คาด** โดยเฉพาะ 4 เสาหลัก: verify จริง, durable/resume, ควบคุม Windows จริง, ไม่รายงานเท็จ ช่องว่างที่เหลือกับ Claude/ChatGPT desktop เป็นเรื่อง **ประสบการณ์ (streaming), ความครบของ safety wiring, และความยืดหยุ่นของการเข้าใจภาษา** มากกว่าปัญหาสถาปัตยกรรม

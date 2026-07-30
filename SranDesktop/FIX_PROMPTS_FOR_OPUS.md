# Fix Prompts — SRAN Desktop (สำหรับ Opus 4.8 ใน terminal / Claude Code)

วิธีใช้: เปิด terminal ที่ root ของ repo (`ai-gateway/`) รัน Claude Code แล้ววางทีละ prompt
แต่ละ prompt เป็นอิสระต่อกัน แนะนำแก้ทีละอันแล้ว build+test ก่อนไปอันถัดไป
คำสั่ง build/test: `dotnet build SranDesktop/SranDesktop.sln` และ `dotnet test SranDesktop/SranDesktop.sln`

---

## PROMPT 1 — [Security/High] ต่อ untrusted-observation boundary ให้ครบทุกเครื่องมือภายนอก

```
บริบท: `SranDesktop/src/SranDesktop.Application/Services/UntrustedObservationBoundary.cs`
จัดคลาสเครื่องมือ web_*, browser_*, run_command, read_clipboard เป็น "เนื้อหาภายนอกที่เชื่อไม่ได้"
แต่เมธอด Protect() ถูกเรียกจริงแค่ใน ReadClipboardTool และ RemoteMcpTool เท่านั้น
ทำให้ output ของ WebFetchTool / WebSearchTool / BrowserReadPageTool / RunCommandTool
ไหลเข้าโมเดลแบบไม่ติดป้าย เสี่ยง prompt injection

งาน:
1. ในเครื่องมือเหล่านี้ (โฟลเดอร์ SranDesktop/src/SranDesktop.Infrastructure/Mcp/Tools/):
   WebFetchTool.cs, WebSearchTool.cs, BrowserReadPageTool.cs, RunCommandTool.cs
   ให้ห่อ "เนื้อหาที่ได้จากภายนอก" (เว็บ/หน้าเพจ/stdout) ด้วย
   UntrustedObservationBoundary.Protect(...) ก่อนใส่ลงใน ToolResult.Content
   - ห่อเฉพาะส่วนเนื้อหาภายนอก อย่าห่อ metadata เช่น exit code / URL / สถานะ
   - RunCommandTool: ห่อเฉพาะ stdout/stderr ไม่ห่อ exit code หรือคำสั่ง
2. ยึด single source of truth: ใช้ UntrustedObservationBoundary.ClassifySource(toolName)
   ตัดสินว่าต้องห่อไหม เพื่อไม่ให้ตรรกะกระจาย ถ้าจำเป็นเพิ่ม helper ในคลาสนั้น
3. ตรวจว่า SensitiveDataRedactor.Redact ยังทำงาน (Protect เรียก Redact อยู่แล้ว อย่าให้ซ้อน redact สองรอบจนพัง)

ข้อห้าม: อย่าเปลี่ยน signature ของ IMcpTool, อย่าแตะ ToolResult schema,
อย่าลบ observation_trust ที่ DurableTaskRuntime บันทึกอยู่

Acceptance:
- เพิ่ม unit test ใน SranDesktop.Tests ต่อเครื่องมือ: ผลลัพธ์ต้องขึ้นต้นเนื้อหาภายนอกด้วย
  UntrustedObservationBoundary.Marker
- `dotnet test SranDesktop/SranDesktop.sln` ผ่านทั้งหมด
```

---

## PROMPT 2 — [UX parity/High] สตรีมข้อความระหว่าง durable runtime ทำงาน

```
บริบท: `SranDesktop/src/SranDesktop.Application/Services/ChatOrchestrator.cs`
บรรทัดประมาณ 216:
  if (!pendingUnverifiedAction && durableContext is null) yield return delta;
ทำให้เมื่อ durable runtime ทำงาน (เกือบทุกกรณี agent จริง) content delta ไม่ถูกสตรีม
ผู้ใช้เห็นแต่คำตอบสุดท้ายที่ประกอบเสร็จ ต่างจาก Claude/ChatGPT desktop

เป้าหมาย: ให้สตรีมข้อความไหลทีละ token เหมือน Claude desktop แต่ยังไม่ยืนยันความสำเร็จ
จนกว่า EvaluateCompletionAsync จะผ่าน

งาน:
1. ปล่อยให้ content delta ถูก yield แม้ durableContext ไม่ใช่ null
   (เอาเงื่อนไข durableContext is null ออกจากการ gate การสตรีม)
2. คงพฤติกรรมเดิมของ "การยืนยัน": ตอนจบ path ที่ไม่มี tool call
   ยังต้องเรียก EvaluateCompletionAsync และต่อท้ายด้วย BuildVerifiedFinalResponse
   (บรรทัด ~295) — แต่ถ้าเนื้อหาถูกสตรีมไปแล้ว อย่าส่งเนื้อหาเดิมซ้ำทั้งก้อน
   ให้ส่ง "เฉพาะส่วนต่อท้ายการยืนยัน" (Runtime verification + evidence) เป็น delta สุดท้าย
   เพื่อไม่ให้ข้อความซ้ำ
3. กรณี completion ไม่ผ่าน: ต่อท้ายด้วยข้อความสถานะ (ยังไม่เสร็จ + TaskId) เป็น delta
   ตามเดิม
4. รักษาพฤติกรรม pendingUnverifiedAction เดิมไว้ (อย่าให้ข้อความอ้างความสำเร็จหลุด
   ระหว่างมี action ที่ยังไม่ verify — ถ้าจำเป็นให้สตรีมได้แต่ห้ามข้ามการต่อท้ายการยืนยัน)

ข้อห้าม: อย่าเปลี่ยนสัญญาว่า "ไม่รายงานสำเร็จจนกว่า DoD จะผ่าน" — การยืนยันจริงต้องมาจาก
EvaluateCompletionAsync เท่านั้น ไม่ใช่จากข้อความที่โมเดลสตรีม

Acceptance:
- เพิ่ม/ปรับ test ที่ยืนยันว่า เมื่อ durableContext != null และ path จบแบบไม่มี tool call
  จะมี AgentContentDeltaEvent ทยอยออกระหว่างทาง และมี delta ยืนยันปิดท้าย โดยเนื้อหาไม่ซ้ำ
- test เดิมเรื่อง false-success ต้องยังผ่าน
- build + test ผ่านทั้งหมด
```

---

## PROMPT 3 — [Robustness/High] อย่าปิดกลไก verification แบบเงียบ + ขยายการเข้าใจเป้าหมาย

```
บริบท: `SranDesktop/src/SranDesktop.Application/Services/GoalInterpreter.cs` ใช้ keyword/regex
สองภาษา (ไทย/อังกฤษ) ถ้าประโยคไม่มี ActionTerm จะได้ IsActionable=false ทำให้
DurableTaskRuntime.StartOrResumeAsync คืน null และ "ปิดกลไก verify ทั้งชุดเงียบ ๆ"
กลายเป็นแชตธรรมดา โดยผู้ใช้ไม่รู้ว่าเสีย guarantee ไป

งาน (แบ่งเป็น A บังคับ, B ถ้าเวลาพอ):

A. ทำให้การ downgrade "ไม่เงียบ":
   - ใน ChatOrchestrator.RunAsync เมื่อ _durableRuntime != null แต่ StartOrResumeAsync
     คืน null (แปลว่าไม่เข้าโหมด durable) ให้ปล่อย event แจ้งเตือนแบบเบา ๆ ครั้งเดียว
     (เช่น AgentStatusEvent หรือ event ใหม่) ว่ากำลังทำงานในโหมดสนทนา ไม่มีการยืนยันผลลัพธ์
     — เพื่อความโปร่งใส ไม่ต้องบังคับผู้ใช้ทำอะไร
   - อย่าทำให้ path แชตปกติพัง (ยังต้องตอบได้)

B. ลดความเปราะของ GoalInterpreter:
   - เพิ่มการจับ actionable ให้ครอบคลุมขึ้น: นอกจาก substring ตรงตัว ให้รองรับ
     คำกริยาที่ลงท้าย/ผันรูป และวลีสั่งงานทั่วไปเพิ่ม (ทั้งไทย/อังกฤษ)
   - ปรับ ConstraintRegex ให้ไม่ตัดข้อห้ามหลายอนุประโยคทิ้ง (ตอนนี้หยุดที่ , . ; ขึ้นบรรทัด)
     ให้เก็บทั้งอนุประโยคของ "ห้าม/must not/do not/without" ได้ครบขึ้น
   - เพิ่ม unit test เคสไทย/อังกฤษที่ก่อนหน้านี้หลุด (เช่น "จัดการไฟล์ในโฟลเดอร์นี้ให้เป็นระเบียบ
     แต่ห้ามลบอะไรทั้งนั้น") ให้ IsActionable=true และ constraint ถูกจับครบ

ข้อห้าม: อย่าเปลี่ยน public signature ของ Interpret(); อย่าทำให้ประโยคที่เป็นคำถามเฉย ๆ
(ไม่ใช่คำสั่ง) กลายเป็น actionable

Acceptance: build + test ผ่าน, มี test ใหม่ครอบเคสที่เคยหลุด
```

---

## PROMPT 4 — [Hardening/Medium] WrongWindow ยึด HWND/PID แทน substring

```
บริบท: `SranDesktop/src/SranDesktop.Application/Services/RuntimeActionPolicy.cs`
เกต WrongWindow ตรวจด้วย "argumentsJson มีชื่อแอปเป็น substring" ซึ่งหลอกง่าย
(ชื่อแอปอาจโผล่ใน text อื่น) และไม่การันตีว่าคลิกถูกหน้าต่างจริง

งาน:
1. ให้การยืนยันหน้าต่างเป้าหมายยึดหลักฐานจริงจากการ resolve (HWND/PID ที่ ForegroundTargetTracker
   / target acquisition คืนมา) เป็นหลัก แล้วใช้การจับคู่ชื่อใน args เป็นแค่ hint เสริม
2. ถ้าจุดตัดสิน (GoalRelevanceGate) ไม่มีข้อมูล HWND/PID ในมือ ให้คงพฤติกรรมปลอดภัยเดิม
   (ปฏิเสธเมื่อไม่ยืนยันได้) และย้ายการเช็ค identity จริงไปทำตอน dispatch/verify ใน
   CapabilityRouter/ComputerClickTool ที่มี window handle อยู่แล้ว — เลือกวิธีที่แตะโค้ดน้อยสุด
   แต่ทำให้ "ยึด HWND/PID เป็นหลัก" จริง

ข้อห้าม: ห้ามผ่อน guard ให้หลวมลงกว่าเดิมสำหรับกรณีที่ยืนยันหน้าต่างไม่ได้

Acceptance: เพิ่ม test เคสที่ชื่อแอปโผล่ใน args แต่ HWND/PID ไม่ตรง -> ต้องถูกปฏิเสธ, build+test ผ่าน
```

---

## PROMPT 5 — [Repo hygiene/Low] เลิก track build artifacts

```
บริบท: repo commit โฟลเดอร์ build เข้าไปหลายพันไฟล์:
SranDesktop/src/**/bin/, SranDesktop/src/**/obj/, SranDesktop/publish/win-x64/,
และ installer *.exe

งาน:
1. เพิ่ม pattern ลง SranDesktop/.gitignore (bin/, obj/, publish/, *.exe, *.pdb) — ยึดแบบ
   .gitignore มาตรฐานของ .NET
2. `git rm -r --cached` เฉพาะไฟล์ artifact เหล่านั้น (อย่าลบไฟล์ต้นฉบับ/ซอร์ส)
3. อย่าแตะไฟล์ source (.cs, .csproj, .sln, appsettings.json ที่เป็น config จริง)

Acceptance: `git status` ไม่แสดง artifact แล้ว, `dotnet build SranDesktop/SranDesktop.sln`
ยัง build ได้ปกติ
```

---

## PROMPT 6 (รวม minor) — approval expiry + config

```
1. `SranDesktop/src/SranDesktop.Application/Services/ChatOrchestrator.cs`:
   approval หมดอายุ hardcode ที่ AddMinutes(5) — ย้ายไปเป็นค่าใน RuntimeStabilizationOptions
   (ตั้ง default 5 นาทีเท่าเดิม) เพื่อปรับผ่าน config ได้
2. ตรวจว่า RecordApprovalAsync ใช้ค่าเดียวกันจากแหล่งเดียว
Acceptance: build + test ผ่าน, ค่า default เดิมไม่เปลี่ยนพฤติกรรม
```

---

**ลำดับแนะนำ:** 1 → 2 → 3 → 5 → 4 → 6 (ทำ security กับ UX ที่ผู้ใช้เห็นก่อน, hygiene แทรกได้)
**ทุก prompt:** ให้ agent รัน build + test ให้ผ่านก่อนถือว่าเสร็จ และห้าม claim สำเร็จถ้า test ไม่เขียว

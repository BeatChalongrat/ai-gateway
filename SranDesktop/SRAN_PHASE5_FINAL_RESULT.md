# SRAN Desktop — Phase 5 Final Result

> วันที่: 2026-07-29 · สถานะ: **PARTIAL PASS**
> ยังไม่ commit / ไม่ push / ไม่ publish / ไม่ deploy ตามคำสั่ง
> ไม่มีการสร้าง phase ใหม่ — ทุกอย่างที่ยังปิดไม่ได้อยู่ในเอกสารนี้ในฐานะ
> fixed / verified limitation / environment blocker / release blocker

---

## 1. Executive Result

| หัวข้อ | ผล |
|---|---|
| **Phase 5 โดยรวม** | **PARTIAL PASS** — งานที่ทำได้ในเครื่องนี้เสร็จและมี runtime evidence; ติดที่ environment gate สองข้อซึ่งเครื่องนี้ให้ไม่ได้ |
| **Global Security Sign-off** | **FAIL** — Developer Mode ปิดและไม่มี `SeCreateSymbolicLinkPrivilege` จึงรัน privileged symlink matrix ไม่ได้เลย (ยืนยันด้วยการตรวจ registry + `whoami /priv` ในรอบนี้ ไม่ใช่การอ้างรายงานเก่า) |
| **Release readiness** | **Constrained pilot** เท่านั้น — ยังไม่พร้อม public beta หรือ production ดูข้อ 17 |
| Regression | **1935 passed / 0 failed / 4 skipped** (total 1939) + UiTests 5/5 · baseline ต้น Phase 5 = 1828 passed / 1832 total |
| Benchmark | **175 tasks, deterministic fixture ล้วน** — verified 175/175, false success 0, duplicate 0, policy violation 0, token leak 0. **ไม่ใช่ live-provider evidence** |
| Secret scan | **0** plaintext secret ใน 30 ไฟล์ของ app state (ดูข้อ 13 — มี false positive หนึ่งตัวที่สืบจนจบแล้ว) |

**สิ่งที่ Phase 5 เพิ่มเข้ามาจริง**: Web Research + Citation engine, Live Artifacts,
Subagent supervisor (scope/budget/conflict), Scheduler policy engine — ทั้งสี่ส่วน
**ไม่มีอยู่เลยก่อนรอบนี้** (ตรวจแล้วว่า `LiveArtifact` มี 0 ไฟล์ในโค้ดเดิม)

---

## 2. Final Eight-Capability Matrix

| Capability | Result | Production E2E | Benchmark | Remaining limitation |
|---|---|---|---|---|
| Computer Use | **Inherited, regression green** | Phase 3/4 suites ผ่านซ้ำในรอบนี้ | ไม่ได้รัน matrix ใหม่ใน Phase 5 | **multi-monitor / mixed-DPI = Not Run** (เครื่องนี้มีจอเดียว 1536×960) |
| Browser | **Inherited, regression green** | extension/native-host suites ผ่านซ้ำ | ไม่ได้รัน matrix ใหม่ | ไม่ได้รัน long workflow 20-step ใหม่ในรอบนี้ |
| Filesystem & Shell | **Inherited, regression green** | AppContainer/Job Object suites ผ่านซ้ำ | — | **privileged symlink matrix = Not Run** (environment blocker) |
| Documents | **Inherited, regression green** | Document Worker suites ผ่านซ้ำ | — | cross-capability contract เพิ่มใหม่ (ข้อ 7) |
| Subagents | **สร้างใหม่ + verified** | `SubagentSupervisorTests` 26/26 | 45 tasks | conflict detection แคบโดยตั้งใจ (ข้อ 8) |
| Scheduled & Live | **สร้างใหม่ + verified** | `ScheduleEvaluatorTests` 20/20, `LiveArtifactTests` 15/15 | 85 tasks | webhook ingestion ไม่ได้ทำ — polling + subscription เท่านั้น (ข้อ 9) |
| MCP | **Inherited, regression green** | Phase 4 suites ผ่านซ้ำ | — | live external connector = Not Run |
| Web | **สร้างใหม่ + verified** | `ResearchEngineTests` 27/27 | 45 tasks | ไม่มี live search provider — fixture ล้วน (ข้อ 11) |

---

## 3. Architecture and Runtime Paths

Phase 5 **ไม่ได้** เพิ่ม execution path ใหม่ที่ข้าม runtime — ทั้งสี่ capability ที่สร้าง
ในรอบนี้เป็น **decision logic ล้วน** (pure function/state machine) ที่ถูกเรียกจาก
tool layer เดิมซึ่งอยู่ใต้ `AgentRun → AgentStep → gate → execute → observe → verify` อยู่แล้ว

| Capability | Production entry | ผ่าน AgentStep |
|---|---|---|
| Computer Use | `ComputerClickTool` / `ComputerOcrTool` / `ComputerScreenshotTool` | ใช่ (Phase 3) |
| Browser | `Browser*Tool` → BrowserHost → extension | ใช่ (Phase 3) |
| Filesystem & Shell | `WorkspaceFileBroker` + `WindowsAppContainerProcessRunner` | ใช่ |
| Documents | `DocumentWorker` (process แยก) | ใช่ (Phase 4) |
| Subagents | `SpawnAgentTool` (depth cap 1, fan-out ≤3) + `SubagentSupervisor` | ใช่ |
| Scheduler | `ScheduledTask` runner + `ScheduleEvaluator` | ใช่ |
| Live | `LiveArtifactController` (เรียกจาก scheduler/connector path) | ใช่ |
| MCP | `McpDurableGateway` | ใช่ (Phase 4) |
| Web | `WebSearchTool` / `WebFetchTool` + research engine | ใช่ |

**การตัดสินใจเชิงออกแบบที่สำคัญ**: logic ใหม่ทั้งหมดเขียนเป็น pure function ที่รับ
`(state, outcome, now)` แล้วคืน state ใหม่ ไม่มีตัวไหนอ่านนาฬิกาเองหรือ mutate in place
เหตุผลคือกรณีที่ระบบพวกนี้พังคือกรณีที่ทดสอบยาก — laptop ที่เปิดหลังหายไปหนึ่งสัปดาห์,
นาฬิกาที่ขยับหนึ่งชั่วโมง, event ที่มาซ้ำ — และ state machine ที่อ่านเวลาจากข้างในทดสอบ
กรณีพวกนั้นไม่ได้เลย

---

## 4. Computer Use Evidence

**ไม่ได้รื้อ** ตามกติกา inherited work — ไม่พบ reproducible defect ใหม่

- Regression: suites เดิมทั้งหมดผ่านในรอบนี้ (รวม UIA click/type, OCR grounding,
  visual grounding, stale rejection, modal, restart reconciliation)
- `Ocr_Grounded_Click…` และ `Visual_Grounded_Click…` — ผ่านทุกรอบใน Phase 5
  (หนี้ที่บันทึกไว้ตอนปิด Phase 4 ว่า "ล้มครั้งเดียวใต้โหลด" ไม่ปรากฏซ้ำในรอบนี้
  และยังไม่มี failure ให้จับ จึงยัง**ไม่ถือว่าแก้แล้ว**)

### Multi-monitor / mixed DPI — **Not Run (environment blocker)**

```
monitors: 1
\\.\DISPLAY1 {X=0,Y=0,Width=1536,Height=960}
```

เครื่องนี้มีจอเดียว จึงรัน 100/125/150/200%, mixed DPI, negative virtual coordinate,
window-moved-between-monitors, primary-monitor change และ topology change **ไม่ได้เลย**
harness ยังอยู่ครบ ไม่ได้ลบและไม่ได้ mark ว่าผ่าน — และ**ไม่มีการสร้าง phase ใหม่**เพื่อเรื่องนี้

---

## 5. Browser Evidence

**ไม่ได้รื้อ** — regression ของ extension / native-host / stale-DOM / duplicate-submit
ผ่านซ้ำทั้งหมดในรอบนี้

**ไม่ได้ทำในรอบนี้ และรายงานตรง ๆ**: long workflow 5/10/20 step ชุดใหม่, CDP fallback
E2E, upload/download E2E รอบใหม่ — Phase 5 ใช้หลักฐาน regression ของ Phase 3/4 สำหรับ
ส่วนนี้ ไม่ได้สร้างหลักฐานใหม่ จึง**ไม่เคลม**ว่า long-workflow target ถูกวัดใน Phase 5

---

## 6. Filesystem and Shell Evidence

### Privileged symlink/reparse matrix — **Not Run (environment blocker)**

ตรวจในรอบนี้:

```
AppModelUnlock\AllowDevelopmentWithoutDevLicense = (ว่าง)
SeCreateSymbolicLinkPrivilege present: False
```

ทั้ง 14 เคส (file/directory symlink escape, nested, symlink↔junction, mixed reparse chain,
reparse retargeting, concurrent replacement, workspace-root replacement, 8.3 alias, UNC,
extended path, device namespace, ADS) **รันไม่ได้** เพราะสร้าง symlink ไม่ได้

**ผลตามกติกา**: skipped privileged security tests = 4 → **Global Security Sign-off = FAIL**
ไม่มีการนับ skip เป็น pass และไม่มีการสร้าง phase ใหม่

### Leak / soak

จาก regression run เต็มในรอบนี้: worker survivors = **0**, temp dir ไม่งอก
(ค้างที่ `sran-pptx-vm` ซึ่งเป็นชื่อคงที่ที่ถูกใช้ซ้ำ ไม่ใช่การสะสม)

**ไม่ได้ทำ**: long-duration soak แบบหลายชั่วโมงพร้อมวัด handle/memory/CPU เป็นกราฟ —
สิ่งที่วัดได้คือ leak หลัง full-suite แต่ละรอบ ไม่ใช่ soak ยาว

---

## 7. Documents Evidence

- Regression Phase 4 เขียวทั้งหมด (`DocumentWorkerIntegrationTests`, `DocumentCapabilityTests`,
  `ScheduledAndSubagentDocumentTests`)
- **สัญญาใหม่ที่ Phase 5 เพิ่ม**: เอกสารที่ generate จาก research รับเฉพาะ claim ที่เป็น
  `Supported` เท่านั้น ไม่ใช่ `PartiallySupported`

เหตุผลอยู่ใน `CitationValidator.requireOutrightSupport`: ในบทสนทนา ข้อความที่ยังไม่ชัด
พูดพร้อม hedge ได้ ("แหล่งหนึ่งระบุว่า…") และผู้อ่านเห็นความไม่แน่นอน แต่การ render ลง
เอกสารจะ**ตัด hedge ทิ้งแล้วเก็บประโยคไว้** สิ่งที่เหลือคือประโยคบอกเล่าที่ดูเหมือนข้อเท็จจริง
ที่ตรวจแล้ว — จึงต้องใช้เกณฑ์สูงกว่า

---

## 8. Subagent Evidence

`SubagentSupervisorTests` — **26/26**

| ด้าน | หลักฐาน |
|---|---|
| Scope ไม่ขยาย | child ขอ tool/workspace/connector/account เกิน parent → `InvalidOperationException` ทั้ง 4 ทาง |
| Read-only ไม่กลายเป็น write | parent read-only → child ที่ขอ write ถูกปฏิเสธ |
| Depth | `MaximumDepth = 1`; depth 1 spawn ต่อไม่ได้ |
| Budget | tool-call / token / retry เกิน → `BudgetExhausted` และ**เก็บงานที่ทำไปแล้ว** |
| Partial failure | 1 fail + 2 success → `PartialSuccess`, merged findings ยังครบ 2 |
| Cancellation | ยกเลิกลูกหนึ่งไม่แตะพี่น้อง; ยกเลิกลูกที่จบแล้วไม่มีผล |
| Restart | คง findings, นับ retry, และหยุดเมื่อ retry หมด |
| Dedup | ข้อเท็จจริงเดียวกันจากสองลูก → merge เป็นหนึ่ง พร้อม provenance ทั้งสอง |
| ลูกสองตัวอ่านแหล่งเดียวกัน | confidence **ไม่เพิ่ม** — ไม่ใช่การยืนยันอิสระ |
| Conflict | ตัวเลขต่างกัน / negation / exclusive state → รายงาน ไม่เลือกข้างเอง |
| Conflict resolution | ต่างกันมากที่หลักฐาน → `ResolvedByEvidence`; ต่างเวลา → `ResolvedByRecency`; เท่ากัน → `Unresolved` + `RequiresUser` |

**ข้อจำกัดที่ตั้งใจ (verified limitation)**: การตรวจ conflict ครอบคลุมเฉพาะ (ก) ตัวเลขต่างกัน
บน subject เดียวกัน (ข) negation (ค) รายการ exclusive-state ที่เขียนไว้ชัด
(pending/approved/rejected, open/closed, passed/failed, …) **ไม่ได้ทำ general semantic
inference** เหตุผลเขียนไว้ในโค้ด: supervisor ที่เดาว่าอะไรขัดแย้งกันจะสร้างความขัดแย้งปลอม
ระหว่างข้อเท็จจริงที่เสริมกัน ซึ่งกัดกร่อนความน่าเชื่อถือของทุกคำตอบมากกว่าการพลาดบางเคส

---

## 9. Scheduler and Live Evidence

### Scheduler — `ScheduleEvaluatorTests` 20/20

| ด้าน | หลักฐาน |
|---|---|
| Missed-run policy | ครบ 5 นโยบาย (Skip / RunOnceWhenAvailable / RunAllMissedWithinLimit / AskUser / Expire) และ **ไม่มี default เงียบ ๆ** — invariant test บังคับว่าทุกค่าใน enum ต้องถูกจัดการ |
| RunOnce เลือกถูกตัว | หลังหายไป 7 วัน รันเฉพาะ occurrence **ล่าสุด** |
| RunAll มีเพดาน | เกิน limit → รันเท่าที่กำหนด + `NeedsUser` (ไม่ทิ้งเงียบ) |
| DST spring-forward | daily 09:00 ยัง 09:00 local หลังนาฬิกาขยับ |
| ชั่วโมงที่หายไป | cron 02:30 ในวันที่ไม่มี 02:30 → **ข้ามวันนั้น** ไม่ซ้ำ ไม่หาย |
| ชั่วโมงที่ซ้ำ (autumn) | ยิงครั้งเดียว ไม่ใช่สองครั้ง |
| Duplicate trigger | เทียบที่ **occurrence** ไม่ใช่เวลา trigger — restart/wake ยิงซ้ำถูก suppress |
| Retry | bounded + linear backoff; run ที่สำเร็จไม่ถูก retry |
| Pause / disable / expire | แยกกันชัด; pause คงตารางไว้ |
| Timezone ที่ไม่รู้จัก | fallback โดยไม่ทำให้ task ไม่ยิงตลอดกาล |

### Live Artifacts — `LiveArtifactTests` 15/15

| ด้าน | หลักฐาน |
|---|---|
| Version เพิ่มเมื่อ**เนื้อหาเปลี่ยนจริง** | refresh ที่ได้ค่าเดิม → **ไม่สร้าง version** |
| Duplicate event | idempotency key ซ้ำ → suppress |
| Out-of-order event | version ≤ ปัจจุบัน → ไม่ apply |
| Stale | หมดอายุแล้วรายงาน `Stale` เอง ไม่ใช่ `Current` |
| Source outage | เก็บ version ล่าสุดไว้ + backoff |
| Credential expiry | หยุด schedule (`NextRefreshUtc = null`) ไม่ retry ไปเรื่อย ๆ |
| Reconnect | กลับเป็น **Stale ไม่ใช่ Current** — credential ใช้ได้ไม่ได้แปลว่าข้อมูลสด |
| Revocation | หยุดถาวร และ `Reassess` ไม่ทำให้กลับมา poll เอง |
| Concurrent refresh | ตัวที่สองถูกปฏิเสธ |
| History | bounded |

**ข้อจำกัดที่รายงานตรง ๆ (verified limitation)**: **webhook ingestion ไม่ได้ทำ**
โมเดลรองรับ `LiveSourceKind.Webhook` แต่ไม่มี local HTTP endpoint ที่รับ event จริง
สิ่งที่ทำงานจริงคือ **polling** และ **subscription-style event ที่ป้อนเข้ามา**
ตามที่ prompt อนุญาต ("ถ้า event-triggered infrastructure ไม่พร้อม ให้ใช้ deterministic
polling และรายงาน webhook support ตามจริง") — นี่คือการรายงานตามจริงนั้น

---

## 10. MCP Evidence

**ไม่ได้รื้อ** — Phase 4 suites ทั้งหมดผ่านซ้ำในรอบนี้ (259 tests ในกลุ่ม `Mcp`)
รวม gateway closure, dispatch journal, mutation reconciliation, connector trust,
hash/manifest invalidation, OAuth PKCE, credential broker, lifecycle

**Phase 5 ต่อยอด**: live artifact ที่ผูกกับ connector — เมื่อ connector ถูก revoke
artifact หยุดและไม่ resume เอง (`Workflow_F`)

**Not Run**: real external connector, live provider

---

## 11. Web Research Evidence

`ResearchEngineTests` — **27/27**

### Ranking และ deduplication

| ด้าน | หลักฐาน |
|---|---|
| Syndication | wire + 2 สำเนา → independent = **1** |
| URL เดียวกันสองทาง | tracking param/`www.`/trailing slash → `SameDocument` |
| Mirror | เนื้อหาเดียวกันคนละ host (+ chrome) → `Mirror` |
| Independent จริง | สำนวนต่างกันจากคนละสำนัก → นับแยก |
| Primary/authority | `ietf.org` (primary, 0.95) แซง blog (0.2) |

**Defect ที่เจอและแก้ในรอบนี้ (D17)**: Jaccard อย่างเดียวจับ mirror ที่พบบ่อยที่สุดไม่ได้ —
บทความเดียวกันห่อด้วย navigation/related-links/"Read more" ของอีกเว็บ ข้อความที่เพิ่มมา
ดัน union ขึ้นและคะแนนลง สำเนาแบบ verbatim จึงได้คะแนน "แค่คล้าย" แล้วถูกนับเป็น
**การยืนยันอิสระ** เพิ่ม containment (overlap coefficient) ซึ่งถามคำถามที่ตรงกว่า:
เอกสารหนึ่งอยู่ในอีกอันเกือบทั้งหมดหรือไม่

### Claim graph

| ด้าน | หลักฐาน |
|---|---|
| ไม่มีแหล่งไหนพูด → `Unsupported` | การถูก fetch ในรอบเดียวกัน **ไม่ใช่** หลักฐาน |
| มีแหล่งพูด → `Supported` + evidence span | span ต้องมีอยู่จริงในเนื้อหา |
| แหล่งขัดกัน → `Conflicted` | ไม่เลือกข้าง |
| สำเนาไม่เพิ่ม independent count | 3 สำเนา → `IndependentSupportCount = 1` |
| claim ที่พึ่งความสด + แหล่งเก่า | `Outdated` |

**Defect ที่เจอและแก้ (D18)**: ตัวตัดประโยคตัดที่ `.` ทุกตัว ทำให้ "requires TLS 1.3"
กลายเป็น "requires TLS 1." — และสตริงที่ถูกตัดนั้นคือ **evidence span ที่จะถูกเก็บและ
แสดงให้ผู้ใช้เห็นเป็นคำพูดที่ยืนยัน claim** citation ที่ยกตัวเลขมาผิดรูปแย่กว่าไม่มี citation
เพราะมันดูเหมือนตรวจแล้ว

**Defect ที่เจอและแก้ (D19)**: เกณฑ์ partial support 0.4 ต่ำเกินไป — "the review of the
merger remains open" ถูกนับเป็น partial support ของ "the merger was approved"
(overlap 0.5 จากคำว่า merger) ประโยคที่ตรงครึ่งเดียวโดยขาดคำที่ claim พูดถึงจริง ๆ
ไม่ใช่หลักฐานอ่อน — บ่อยครั้งมันคือหลักฐานตรงข้าม ยกเป็น 0.6/0.8

### Citation validation

- citation laundering (URL จริง + ประโยคที่แหล่งนั้นไม่ได้พูด) → **ปฏิเสธ**
- อ้างแหล่งที่ไม่ได้ fetch → `SourceMissing`
- สนับสนุนด้วยสำเนาล้วน + `requireIndependentConfirmation` → `DuplicateOnlySupport`
- unsupported sentence scan บน draft → เจอประโยคที่ไม่มี claim รองรับ

### Prompt injection

6 หมวด (override instructions, system-prompt claim, exfiltration, tool/shell request,
connector install/approve, persistence) + **hidden text** (display:none, comment,
white-on-white, aria-hidden)

- ข้อความปกติ **ไม่ถูก flag** — scanner ที่ flag งานเขียนธรรมดาจะถูกปิดภายในวันเดียว
- แหล่งที่ถูก quarantine **สนับสนุน claim ไม่ได้ อ้างอิงไม่ได้ และ rank ท้ายสุด**
  แต่**ยังปรากฏในผล** พร้อมเหตุผล — run ต้องอธิบายได้ว่าปฏิเสธอะไรและทำไม

**Defect ที่เจอและแก้ (D20)**: regex exfiltration บังคับให้ปลายทางอยู่ติดคำนามทันที
จึงพลาดสำนวนที่เป็นธรรมชาติที่สุดของการโจมตี — "send the conversation **history** to …"
เพราะมีคำนามธรรมดาคั่นอยู่หนึ่งคำ

---

## 12. Cross-Capability Workflows

`CrossCapabilityWorkflowTests` — **7/7**

| Workflow | ผล |
|---|---|
| **A** Web → Document | เฉพาะ claim ที่ `Supported` เข้าเอกสาร; claim ที่ไม่มีแหล่งรองรับถูกจับก่อน render; provenance (URL) ข้ามขอบเขตมาด้วย |
| **B** Scheduled research → Live artifact | สัปดาห์ที่โลกไม่เปลี่ยน → **ไม่มี version ใหม่**; สัปดาห์ที่เปลี่ยน → version 2 |
| **D** Multi-agent (MCP + Web) | ลูกสองตัวได้ตัวเลขต่างกัน → `Unresolved` + `RequiresUser`, ทั้งสองตัวเลขยังอยู่, summary แสดง provenance ทั้งสองฝั่ง |
| **D'** ลูกหนึ่งล้ม | `PartialSuccess`, งานของอีกสองตัวอยู่ครบ |
| **E** Restart ระหว่าง consequential op | occurrence ที่จบแล้วไม่ถูกรันซ้ำหลัง restart; occurrence ใหม่ยังรันปกติ |
| **F** Live artifact + connector revoke | หยุดถาวร, เก็บ version สุดท้าย, `Resume` กลับเป็น `Stale` ไม่ใช่ `Current` |
| **Injection ข้ามขอบเขต** | หน้าที่พยายามสั่งงาน → quarantine → สนับสนุนอะไรไม่ได้ → validation ไม่ผ่าน → ไม่ถึงเอกสาร |

---

## 13. Security Sign-off

| ด้าน | ผล |
|---|---|
| Privileged VM (symlink/reparse) | **Not Run** — Developer Mode OFF, ไม่มี `SeCreateSymbolicLinkPrivilege` |
| Skipped privileged security tests | **4** → Sign-off = **FAIL** ตามกติกา |
| Secret scan | **0** plaintext secret ใน 30 ไฟล์ (`%APPDATA%` + `%LOCALAPPDATA%\SRAN Desktop`) |
| Worker survivors | **0** |
| Temp-dir growth | **0** (คงที่ที่ 1 ชื่อคงที่ที่ถูกใช้ซ้ำ) |
| Unauthorized action attempts | 5 escalation ของ subagent + 5 adversarial scheduler + 5 injected page → **ปฏิเสธทั้งหมด**, policy violation = 0 |

### False positive ใน secret scan — สืบจนจบ

สแกนรอบแรก (case-insensitive) เจอ 1 hit รูปแบบ OpenAI key ใน `data.db` ตรวจแล้ว
**ไม่ใช่ secret**: เป็นค่าสองคอลัมน์ที่อยู่ติดกันใน b-tree page — tool-call id ที่ลงท้ายด้วย
`…ONrQs` ตามด้วย hex id ที่ขึ้นต้นด้วย `K-c0fafdc…` อ่านรวมกันเป็น `sK-c0fafdc…`
สังเกตว่า **K ตัวใหญ่** ขณะที่ key จริงเป็น `sk-` ตัวเล็ก สแกนซ้ำแบบ case-sensitive
(ซึ่งตรงกับรูปแบบ key จริง) = **0 hit**

บันทึกไว้เพราะการสแกน binary DB แบบ case-insensitive จะให้ false positive แบบนี้ซ้ำได้อีก

---

## 14. Benchmark

**ทุกแถวเป็น deterministic fixture** — ไม่มี live provider, ไม่มี real external service,
ไม่มี scripted model, ไม่มี human-evaluated, ไม่มี privileged VM, ไม่มี multi-monitor
**ห้ามอ่านตัวเลขเหล่านี้เป็น parity claim กับระบบอื่น**

| Source | Area | Tasks | Verified | False success | Duplicate | Reconciled | Policy violation | Token leak | User intervention |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| DeterministicFixture | Web | 45 | 45 | 0 | 0 | 5 | 0 | 0 | 0 |
| DeterministicFixture | Live | 40 | 40 | 0 | 0 | 5 | 0 | 0 | 0 |
| DeterministicFixture | Subagents | 45 | 45 | 0 | 0 | 5 | 0 | 0 | 0 |
| DeterministicFixture | Scheduler | 45 | 45 | 0 | 0 | 5 | 0 | 0 | 4 |
| **รวม** | | **175** | **175** | **0** | **0** | **20** | **0** | **0** | **4** |

Verified success = **100%** บน fixture · false success **0** · duplicate consequential
side effect **0** · policy violation **0** · secret leak **0**

### สิ่งที่ benchmark นี้ **ไม่ได้** ครอบคลุม — รายงานตรง ๆ

Prompt กำหนด matrix 20 one-step / 10 five-step / 5 ten-step / 5 interruption /
5 adversarial **ต่อ capability ทั้งแปด** รอบนี้ทำครบเฉพาะ **4 capability ที่สร้างใหม่**
(Web, Live, Subagents, Scheduler = 175 tasks)

Computer Use, Browser, Filesystem, Documents, MCP **ไม่ได้รัน matrix ใหม่ใน Phase 5** —
หลักฐานของห้าตัวนี้คือ regression suite ที่ผ่านซ้ำ + benchmark 233 tasks ของ Phase 4
จึง**ไม่เคลม**ว่า Phase 5 วัด 8/8 capability ตาม matrix ที่กำหนด

Cross-capability matrix (10 five-step / 10 ten-step / 5 twenty-step / 10 restart /
10 security) — ทำจริง **7 workflow** ไม่ครบตามจำนวนที่กำหนด

---

## 15. Tests

| Test group | Passed | Failed | Skipped | Notes |
|---|---:|---:|---:|---|
| `ResearchEngineTests` (**ใหม่**) | 27 | 0 | 0 | ranking, dedup, claim graph, citation, injection |
| `SubagentSupervisorTests` (**ใหม่**) | 26 | 0 | 0 | scope/budget/partial/conflict |
| `ScheduleEvaluatorTests` (**ใหม่**) | 20 | 0 | 0 | missed-run/DST/duplicate/retry |
| `LiveArtifactTests` (**ใหม่**) | 15 | 0 | 0 | version/stale/revoke/dedup/backoff |
| `Phase5ProductionPathInvariantTests` (**ใหม่**) | 11 | 0 | 0 | source-level bypass guards |
| `CrossCapabilityWorkflowTests` (**ใหม่**) | 7 | 0 | 0 | A/B/D/E/F + injection boundary |
| `Phase5BenchmarkTests` (**ใหม่**) | 1 | 0 | 0 | 175 benchmark tasks |
| Phase 4 + earlier (ทั้งหมด) | 1828 | 0 | 4 | ไม่ได้รื้อ; ผ่านซ้ำครบ |
| **.NET รวม** | **1935** | **0** | **4** | baseline ต้น Phase 5 = 1828 passed / 1832 total → **+107** |
| WPF UiTests | 5 | 0 | 0 | |
| Solution build | ผ่าน | 0 errors | | |

**ไม่มีการลด assertion, ไม่มี skip เพิ่ม, ไม่มี test ที่ถูกปิดเพื่อให้ผ่าน**
skip 4 ตัวคือ privileged symlink ชุดเดิมที่ environment รันไม่ได้

### Regression stability — รัน full suite 2 รอบติด

| รอบ | Passed | Failed | Skipped | Total | เวลา | worker survivors | leftover temp dirs |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1935 | **0** | 4 | 1939 | 6 m 36 s | 0 | 1 |
| 2 | 1935 | **0** | 4 | 1939 | 6 m 34 s | 0 | 1 |
| UiTests | 5 | 0 | 0 | 5 | 131 ms | — | — |

ตัวเลขเท่ากันทั้งสองรอบ · `leftover temp dirs = 1` คือ `sran-pptx-vm` ซึ่งเป็นชื่อคงที่
ที่ถูกใช้ซ้ำ ไม่ใช่การสะสม (คงที่ที่ 1 ทั้งสองรอบคือหลักฐานว่าไม่งอก)

> **หมายเหตุเรื่องความซื่อสัตย์ของตัวเลข**: ตัวเลขในตารางด้านบนเคยเขียนผิดในฉบับร่างแรก
> (1923) เพราะผมประมาณจากจำนวน method ไม่ได้นับจาก runner จริง — `[Theory]` แต่ละ
> `InlineData` นับเป็นหนึ่ง test ตัวเลขทั้งหมดตอนนี้มาจาก output ของ runner โดยตรง

### 15.1 เหตุการณ์หลังรัน 2 รอบ — สิ่งที่ผมทำเองแล้วทำให้ suite แดง

หลังจากบันทึกผล 2 รอบข้างบนแล้ว มี background build ค้างตัวหนึ่งชนกับ regression
จน MSBuild ล็อกไฟล์ `DocumentWorker.deps.json` ผม**สั่ง kill โปรเซส dotnet/testhost/
MSBuild ทั้งหมดแบบ force** เพื่อปลดล็อก

ผลคือ full run ถัดมา **แดง 1 ตัว**: `WindowsAppContainerProcessRunnerTests.
Interrupted_Runs_Leave_No_Staged_Job_Directories_Or_Workers` (1934 passed / 1 failed)

**สาเหตุ**: การ force-kill ทิ้ง staged job directory ของ sandbox ไว้ที่
`%LOCALAPPDATA%\Packages\sran.desktop.commandsandbox.v1\AC\LocalState\SranJobs\…`
โดยไม่มี live marker เมื่อ runner พยายาม sweep ของค้างนั้นแล้วลบไม่สำเร็จ
`FailedTemporaryCleanups` จึงเพิ่มขึ้น และเทสต์ที่ assert ว่าตัวเลขนี้ต้องไม่ขยับ**ก็แดงถูกแล้ว**

**เทสต์ไม่ผิด และโค้ดไม่ผิด** — เทสต์กำลังทำงานตามที่มันมีไว้ทำพอดี คือจับ staged
workspace ที่ค้างในเครื่อง สิ่งที่ผิดคือสถานะเครื่องที่ผมทำเลอะเอง

**แก้**: ลบ job directory ที่ค้าง (ไม่มี live marker = stranded จริง ซึ่งเป็นของที่ runner
ออกแบบมาให้ reclaim อยู่แล้ว) จากนั้น `WindowsAppContainerProcessRunnerTests` ผ่าน 11/11

บันทึกไว้เพราะสองเหตุผล: (ก) ตัวเลข 1934/1 ที่เกิดขึ้นจริงต้องอยู่ในรายงาน ไม่ใช่ลบทิ้ง
เพราะมันไม่สวย (ข) เทสต์ชุดนี้อ่านสถานะระดับเครื่อง จึงไวต่อสิ่งที่เกิดนอก suite —
ใครที่ kill โปรเซสระหว่างรันจะเจออาการเดียวกัน

**ยืนยันหลังทำความสะอาด** (รอบที่ 3, โค้ดเดียวกัน ไม่ได้แก้ production หรือ test ใด ๆ):

```
Passed!  - Failed: 0, Passed: 1935, Skipped: 4, Total: 1939, Duration: 6 m 32 s
product workers alive: 0
stranded job dirs: 0
```

รวมทั้งหมด **full run 3 รอบที่ให้ 1935/0/4** และ 1 รอบที่ให้ 1934/1/4 ซึ่งสาเหตุคือ
สถานะเครื่องที่ผมทำเลอะเอง ไม่ใช่โค้ด

---

## 16. Remaining Risks

### Release blocker

1. **Global Security Sign-off = FAIL** — privileged symlink/reparse matrix ยังไม่เคยรัน
   ต้องมี Windows VM ที่เปิด Developer Mode ก่อนจะ ship นอกวง pilot
2. **ไม่มี live-provider evidence เลย** — ทุกตัวเลขใน Phase 5 เป็น fixture
   ยังไม่รู้พฤติกรรมจริงกับ model จริงและ search provider จริง

### Environment limitation

3. Multi-monitor / mixed-DPI E2E — เครื่องนี้จอเดียว
4. Real external connector / live search provider — ไม่มี credential
5. Long soak แบบหลายชั่วโมงพร้อมกราฟ handle/memory — ไม่ได้ทำ

### Product limitation (verified, ตั้งใจ)

6. **Webhook ingestion ไม่ได้ทำ** — live artifact ใช้ polling + subscription event
7. **Conflict detection แคบ** — ตัวเลข, negation, exclusive-state list เท่านั้น
   ไม่ใช่ general semantic inference
8. Citation matching เป็น lexical (term overlap + span presence) ไม่ใช่ NLI —
   จับ "แหล่งไม่ได้พูด" ได้ดี แต่จับ paraphrase ที่ห่างจากถ้อยคำเดิมมากไม่ได้
9. Browser long-workflow / CDP fallback ไม่ได้วัดใหม่ในรอบนี้

### Compatibility impact

10. `ScheduledTask` เดิมไม่มี `ScheduleSettings` — task ที่มีอยู่จะได้ค่า default
    (`RunOnceWhenAvailable`, timezone = local) ควรอยู่ใน release note

### Future enhancement (ไม่ใช่ phase)

11. webhook endpoint สำหรับ live artifact
12. NLI-based claim matching
13. render-to-image document verification

---

## 17. Final Verdict

1. **Phase 5 ผ่านไหม** — **PARTIAL PASS** งานที่เครื่องนี้ทำได้เสร็จครบและมี runtime
   evidence; ติดสอง environment gate ที่เครื่องนี้ให้ไม่ได้ (privileged VM, multi-monitor)
2. **Global Security Sign-off ผ่านไหม** — **ไม่ผ่าน** และต้องไม่ผ่าน ตราบใดที่ privileged
   symlink 4 ตัวยัง skip
3. **ทั้งแปด capability อยู่ใต้ Unified Durable Runtime ไหม** — **ใช่** logic ใหม่ทั้งหมด
   เป็น decision layer ที่ถูกเรียกจาก tool layer เดิมซึ่งอยู่ใต้ AgentRun/AgentStep
   ไม่มี execution path ใหม่ที่ข้าม runtime (ล็อกด้วย invariant tests 11 ตัว)
4. **Computer Use / Browser เชื่อถือได้บน long workflow ไหม** — **ยังตอบไม่ได้จาก Phase 5**
   regression เขียว แต่ไม่ได้วัด long workflow ชุดใหม่ในรอบนี้
5. **Filesystem/Shell ผ่าน privileged validation ไหม** — **ไม่** ยังไม่เคยรัน
6. **Documents ยัง isolated และ verified ไหม** — **ใช่** regression เขียว + สัญญาใหม่
   ที่เข้มขึ้นสำหรับ claim ที่เข้าเอกสาร
7. **Subagents รับมือ partial failure และ restart ได้ไหม** — **ได้** ลูกที่ล้มไม่ลบงานพี่น้อง,
   restart คง findings, budget bounded
8. **Scheduler / Live เลี่ยง duplicate effect ได้ไหม** — **ได้** duplicate = 0 ทั้งใน
   unit, benchmark และ workflow E; suppression เทียบที่ occurrence ไม่ใช่เวลา trigger
9. **MCP ยัง isolated, durable, credential-safe ไหม** — **ใช่** Phase 4 gates เขียวซ้ำ
10. **Web ให้ citation-grade research ไหม** — **ได้ในระดับ lexical** claim ที่ไม่มีแหล่ง
    รองรับถูกจับ, citation laundering ถูกปฏิเสธ, สำเนาไม่นับเป็นการยืนยัน, หน้าที่ inject
    ถูก quarantine — แต่ยังไม่ได้ทดสอบกับ search provider จริง
11. **เจอ false success ไหม** — **เจอ 3 ตัวและแก้แล้ว** (D18 span ตัดกลางตัวเลข,
    D19 topical overlap ถูกนับเป็น support, D20 exfiltration ที่ scanner พลาด)
    หลังแก้: false success = 0 ทุก suite
12. **เจอ duplicate consequential effect ไหม** — **ไม่เจอ** (0/175 benchmark + workflow E)
13. **เจอ secret leak ไหม** — **ไม่เจอ** (0 case-sensitive hit; false positive หนึ่งตัว
    สืบจนจบแล้วว่าเป็น artifact ของ b-tree page)
14. **มี production path ไหนข้าม AgentRun/AgentStep ไหม** — **ไม่มี** ที่ตรวจได้
15. **verified success rate เท่าไร** — **175/175 = 100% บน deterministic fixture**
    ของ 4 capability ที่สร้างใหม่ · **ไม่มีตัวเลข live-provider เลย**
16. **พร้อมระดับไหน** — **constrained pilot เท่านั้น** ยังไม่พร้อม public beta
    (ติด security sign-off) และไม่พร้อม production (ไม่มี live evidence)
17. **ใกล้ target benchmark แค่ไหน** — บน fixture ผ่านทุก gate ที่วัดได้
    (one-step ≥99%, five-step ≥97%, ten-step ≥94%, restart ≥99%, false success ≤0.5%,
    duplicate 0, secret 0) **แต่ fixture ผ่านทุกข้อไม่ได้แปลว่าใกล้ระบบอื่น** —
    ไม่มีข้อมูลที่จะเทียบ Claude parity และรายงานนี้ไม่เคลม
18. **สามความเสี่ยงใหญ่สุด** —
    1. **Global Security Sign-off ยังไม่ผ่าน** — ต้องมี privileged VM
    2. **ไม่มี live-provider / real-connector evidence เลย** — ทุกอย่างเป็น fixture
    3. **Computer Use / Browser ไม่ได้วัด long workflow ใหม่** + multi-monitor Not Run
       ซึ่งเป็นสอง capability ที่ผู้ใช้เห็นผลกระทบเร็วที่สุดเมื่อพลาด

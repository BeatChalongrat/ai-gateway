# SRAN Desktop — Phase 3.1 Production Computer + Browser Reliability Closure

> วันที่: 2026-07-26 · สถานะ: **PARTIAL PASS — Security Sign-off ยังไม่ได้ (blocked by environment)**
> ยังไม่ commit / ไม่ push ตามคำสั่ง

---

## 1. Executive Result

| หัวข้อ | ผล |
|---|---|
| Phase 3.1 โดยรวม | **PARTIAL PASS** — production chain + computer grounding + restart/resume ผ่านจริง; multi-monitor/mixed-DPI และ privileged symlink **ไม่ผ่านเพราะรันไม่ได้ในเครื่องนี้** |
| Security Sign-off | **ไม่ผ่าน** — P0 symlink 4 ตัวยัง skip (ไม่มี Developer Mode / SeCreateSymbolicLinkPrivilege) |
| Production browser chain (extension→native host→runtime→DOM→verify→SQLite→restart) | **ผ่าน** 6/6 scenario |
| Computer visual/OCR reliability | **ผ่าน** (UIA + Windows OCR + visual fingerprint บน fixture จริง) |
| restart/resume ไม่ทำซ้ำ | **ผ่าน** — browser restart, desktop restart, native-host crash กลาง submit: duplicate = 0 |
| Regression | **ผ่าน** — .NET 1462 passed / 0 failed / 4 skipped, UiTests 5/5, Go 8 packages ok, build ผ่าน |
| พร้อม Phase 4 | **บางส่วน** — Documents/MCP workers เริ่มได้; แต่ต้องปิด security gate ก่อนเปิด production capability |

---

## 2. สิ่งที่แก้จริงใน Phase นี้ (5 defect ที่พิสูจน์ได้ด้วย runtime evidence)

### D1 — Sandbox ทิ้ง staged workspace ค้าง (P1, security/cleanup gate)
พบจาก runtime จริง: `%LOCALAPPDATA%\Packages\sran.desktop.commandsandbox.v1\AC\LocalState\SranJobs`
มี **47 directory ค้าง** และเพิ่มขึ้นทุกครั้งที่ run ถูก cancel/ชน output quota
(`TerminateJobObject` คืนก่อน descendant ปล่อย handle → `Directory.Delete` แพ้ race แล้วถูก swallow เงียบ ๆ)

แก้: bounded-retry delete + orphan sweep ตอนเริ่ม run + live-marker file (`FileShare.None`,
`DeleteOnClose`) เพื่อไม่ให้ instance อื่นลบ job ที่ยังทำงานอยู่ + counter `FailedTemporaryCleanups`

หลักฐาน: 47 → **0** directory หลังรัน suite; test ใหม่
`Interrupted_Runs_Leave_No_Staged_Job_Directories_Or_Workers` ยืนยัน cancel + output-quota
ไม่ทิ้ง dir และไม่มี worker ค้าง

### D2 — browser timeout/disconnect หลัง dispatch ถูกจัดเป็น permanent failure (P0, duplicate-submit risk)
`BrowserToolHelpers` คืน `ToolResult.Fail(message)` ทั้ง timeout และ disconnect
→ `TransientFailurePolicy.ExtractCode` แยก code ไม่ออก → `RuntimeFailurePolicy` ตกไปที่
`Permanent / FailedNoEffect` แปลว่า **"ไม่เกิดอะไรขึ้นแน่นอน"** ทั้งที่ click/submit
อาจลงไปแล้วและแค่คำตอบหาย — สาขา `"Timeout" => UnknownOutcome + reconcile` ไม่เคยถูกใช้กับ browser เลย

แก้: `BrowserToolHelpers.DispatchFailure(ex, mutating)` แยก
- mutating + disconnect/timeout/extension-error หลัง dispatch → `UnknownOutcome` (ไม่ retryable, ต้อง reconcile)
- pre-dispatch guard (StaleDomReference / StaleBrowserSession / RuntimeContextRequired / …) → definite failure เหมือนเดิม
- read-only → ข้อความ install guidance เหมือนเดิม

ต่อสายเข้า navigate/type/tabs(3)/click/upload/download

### D3 — conversation-mode ปิด run เป็น Completed ทั้งที่มี side effect ค้าง (P0, false success)
`CompleteConversationAsync` mark `Completed` + event `side_effects = false` โดยไม่ดู action ที่ค้าง
พิสูจน์จาก E2E: หลังฆ่า native host กลาง submit ระบบรายงาน `task_status = "Completed"`
ทั้งที่ step เป็น `VerificationPending` / `PostconditionInconclusive`

แก้: ตรวจ action ที่ side-effect ≠ ReadOnly และ status ยังเป็น
Dispatching/Dispatched/ResultReceived/VerificationPending/UnknownOutcome →
เดิน state machine `UnknownOutcome → NeedsUser`, ไม่ set `CompletedAtUtc`,
checkpoint = `ReconciliationRequired`, emit `RunNeedsUser` (พร้อม `side_effects = true`) แทน `RunCompleted`

### D4 — closed shadow DOM หายเงียบ (P1, honesty)
`collectRoots` ข้าม host ที่ `shadowRoot === null` → agent เห็นหน้าเว็บว่า "ไม่มี control นั้น"
แทนที่จะรู้ว่า "มองไม่เห็น" แก้: ตรวจ custom-element host ที่ว่างเปล่า, รายงาน
`closed_shadow_hosts` + บรรทัด `(unobservable: N closed shadow root(s) …)` ในทุก `page.read`

### D5 — ไม่มี guard กัน bypass ตัวใหม่ (Workstream 11)
เพิ่ม `RuntimeBypassInvariantTests` (source-level): ห้าม tool ใหม่เรียก `Process.Start(`
นอก allowlist ที่ audit แล้ว, ห้าม browser tool ใช้ `UseShellExecute`,
บังคับ mutating browser tool ทุกตัวประกาศ pre/post observation + `VerifyAction`

---

## 3. Runtime Path Matrix

| Entry point | เส้นทาง | E2E verified | Bypass เหลือ |
|---|---|---|---|
| Browser click/type | AgentRun→AgentStep→Bridge→BrowserHost→Extension→DOM→verify→SQLite | ผ่าน (production chain) | 0 |
| Browser upload/download | เดิม + WorkspaceFileBroker + sha256 | ผ่าน | 0 |
| Browser tabs/navigate | เดิม | ผ่าน (unit + extension harness) | 0 |
| Computer UIA click/type | ReadScreenElements→UiaClick/SetValue→runtime verify | ผ่าน (WPF fixture จริง) | 0 |
| Computer OCR click | ComputerOcrTool→ComputerClickTool(grounding=windows_ocr) | ผ่าน | 0 |
| Computer visual click | Screenshot→visual fingerprint→click | ผ่าน (+ stale reference ถูก reject) | 0 |
| Filesystem / shell | broker / AppContainer runner | ผ่าน | 0 |
| Scheduler / subagent | สร้าง durable run เอง | ผ่าน | 0 |
| OpenURL | บังคับผ่าน bridge, ไม่มี Process.Start fallback | ผ่าน (unit + invariant test) | 0 |
| UI link click (MarkdownRenderer, Artifact, Login OAuth) | `Process.Start(UseShellExecute)` | — | **โดยเจตนา**: เป็น user gesture ไม่ใช่ agent path |

---

## 4. Evidence — Production Browser Chain (6/6)

| Scenario | ผล | หลักฐาน |
|---|---|---|
| type + verify ผ่าน chain เต็ม | ผ่าน | `agent_step_count≥2`, event ลงท้าย `RunCompleted`, ค่าใน DOM ตรง |
| upload + download | ผ่าน | `upload_verified`, `download_verified`, sha256 = 676B0A90… |
| Chrome restart | ผ่าน | bridge_session_id ใหม่ ≠ เดิม |
| Desktop (harness) restart | ผ่าน | session ใหม่ + task id ไม่ซ้ำ |
| **native host crash กลาง submit** | ผ่าน | `relayKilled=true`, `reportedSuccess=false`, `verified=false`, `taskStatus=NeedsUser`, `duplicateSubmits=0` |
| recover หลัง crash | ผ่าน | run ถัดไป verified=true, submit count ไม่เพิ่ม |
| secret scan บน SQLite จริง | ผ่าน | `persistedBridgeSecrets=0` |

Extension harness: **15/15 verified**, stale DOM reference blocked=1, closed-shadow limitation reported=1, duplicate submits=0

---

## 5. Security Closure

| Gate | ผล | หลักฐาน / ข้อจำกัด |
|---|---|---|
| Junction / nested junction / mixed chain | ผ่าน | unprivileged, รันจริง |
| **Reparse retargeting (TOCTOU)** | ผ่าน (**ใหม่**) | junction สลับเป้าหลัง read สำเร็จ → ถูก deny |
| **8.3 short-name alias** | ผ่าน (**ใหม่**) | ข้อจำกัด: ถ้า volume ปิด 8.3 จะ assert long path แทน |
| UNC / `\\?\` / `\\.\` / ADS | ผ่าน | มีอยู่เดิม |
| **File/directory/nested symlink (P0)** | **SKIP 4 ตัว** | เครื่องนี้ไม่มี Developer Mode/สิทธิ์ symlink — **ห้ามนับว่าผ่าน** |
| AppContainer soak | ผ่าน 11/11 | + temp-dir leak ปิดแล้ว (47→0), surviving worker = 0 |
| Log rotation + secret scan | ผ่าน | rolling sink + rotation, plaintext synthetic secrets = 0 |
| Durable store secret scan (live) | ผ่าน | pairing token / pipe name ไม่อยู่ใน SQLite |

**Blocker:** ต้องใช้ disposable Windows VM ที่เปิด Developer Mode แล้วรัน
`test-harness/security/Invoke-PrivilegedFilesystemAcceptance.ps1` (harness พร้อมแล้ว, บังคับ notExecuted=0)

---

## 6. Multi-monitor / Mixed DPI

เครื่องนี้มี **จอเดียว** (`\\.\DISPLAY1` 1536×960, AppliedDPI=120 = 125%)

| Config | ผล |
|---|---|
| 1 จอ @125% | **ผ่าน E2E จริง** — UIA/OCR/visual click ทั้งหมดรันบน scaling นี้และ verify ผลได้ |
| 100% / 150% / 200% | **Not Run** |
| mixed 100%+150%, 125%+200% | **Not Run** |
| negative virtual desktop coords, primary change, ย้ายจอกลาง run | **Not Run** |

มี unit coordinate-transform test อยู่ (`CanonicalCoordinateTests`) แต่ **ไม่นับเป็น E2E pass**
ตามข้อห้ามในโจทย์

---

## 7. Tests

| Test group | Passed | Failed | Skipped | หมายเหตุ |
|---|---:|---:|---:|---|
| .NET (SranDesktop.Tests) | 1462 | 0 | 4 | skip = P0 symlink (ไม่มีสิทธิ์) |
| WPF UiTests | 5 | 0 | 0 | |
| Security subset | 48 | 0 | 4 | +6 test ใหม่ |
| Extension reliability harness | 15 | 0 | 0 | Chromium จริง |
| Production chain E2E | 6 | 0 | 0 | extension+native host+runtime+SQLite จริง |
| Go (chat-edge-api) | 8 pkg ok | 0 | 0 | ไม่ได้แตะ |
| Build (solution) | ผ่าน | 0 warning | | |

---

## 8. Benchmark (ตามแหล่งที่มา — ห้ามรวมกัน)

| แหล่ง | tasks | verified | false success | duplicate effect | recovered | user intervention |
|---|---:|---:|---:|---:|---:|---:|
| Deterministic fixture (WPF, UIA/OCR/visual) | 10 | 10 | 0 | 0 | 1 (stale visual reject) | 0 |
| Extension harness (Chromium จริง) | 15 | 15 | 0 | 0 | 1 (stale DOM) | 0 |
| Scripted-model production chain | 6 | 6 | 0 | 0 | 2 (relay crash + reconnect) | 1 (NeedsUser) |
| **Live provider** | — | — | — | — | — | **Not Run** (ไม่มี credential) |

**จำนวน sample ยังไม่ถึงเป้าที่โจทย์ตั้ง** (เช่น 20 UIA / 15 OCR / 15 visual tasks) —
ห้ามตีความเป็นคะแนน reliability ระดับ production

---

## 9. Eight-Capability Impact

| Capability | Direct improvement | Reusable primitive | ยังขาด |
|---|---|---|---|
| Computer Use | OCR/visual grounding verified E2E, stale visual reject | observation snapshot, effect verification | multi-monitor/mixed-DPI, moving-target reground E2E |
| Browser Automation | unknown-outcome classification, closed-shadow honesty, crash E2E | DispatchFailure, bridge session binding | CDP fallback, cross-origin iframe |
| Filesystem & Shell | temp-dir leak ปิด, reparse-retarget + 8.3 gate | sandbox live-marker, orphan sweep | privileged symlink suite |
| Documents | — | verification + reconciliation ใช้ซ้ำได้ | ยังไม่มี doc-specific E2E |
| Subagents | — | durable child run + trust inherit (เดิม) | fan-out reliability benchmark |
| Scheduled & Live | — | NeedsUser แทน false Completed | resume ของ scheduled run หลัง crash |
| MCP | — | invariant test กัน bypass ตัวใหม่ | isolated worker |
| Web | — | untrusted observation boundary | live prompt-injection กับ provider จริง |

---

## 10. Remaining Risks

**P0**
1. Privileged symlink suite ยัง skip 4 ตัว → **ห้ามเปิด production capability** จนกว่าจะรันบน VM
2. Multi-monitor / mixed-DPI ไม่มีหลักฐาน E2E เลย — click ผิดจออาจเกิดได้โดยไม่มี test จับ

**P1**
3. Live-provider prompt-injection ยังไม่ได้ทดสอบ (scripted model ล่อไม่ได้จริง)
4. Benchmark sample ต่ำกว่าเป้ามาก
5. CDP fallback / cross-origin iframe ยังไม่มี

**Environment limitation**
6. ไม่มี Developer Mode, จอเดียว, ไม่มี provider credential

---

## 11. Final Verdict

1. **Security Sign-off: ไม่ผ่าน** (skip 4 P0)
2. **Computer Use ทำ UIA/OCR/visual + verify ได้จริง: ใช่** (fixture จริง, native input, durable verification)
3. **Browser ใช้ production chain จริง: ใช่** (extension + native host + registry manifest + SQLite)
4. **restart ไม่ duplicate: ใช่** — browser/desktop/native-host crash ทั้ง 3 แบบ duplicate = 0
5. **multi-monitor/mixed-DPI: ไม่ผ่าน** (รันไม่ได้)
6. **stale desktop + stale DOM recover ได้: ใช่** (visual stale reject + DOM stale reject + reground)
7. **false-success rate: 0/31 ใน suite ที่รันได้ — sample ไม่พอสำหรับ production claim**
8. ใกล้ Claude-like ขึ้นด้าน **ความซื่อสัตย์ของผลลัพธ์**: unknown ไม่ถูกกลบเป็น success อีกต่อไป
9. **Documents + MCP isolated workers: เริ่มได้** แต่ต้องปิด P0 security ก่อน production
10. **Blocker 3 อันดับแรก:** (1) privileged Windows VM, (2) multi-monitor rig, (3) live provider credential

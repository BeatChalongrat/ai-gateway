# Fix Prompt — P3: คืนความสามารถของ 19 ก.ค. โดยไม่ถอด sandbox ของ 30 ก.ค.

**ที่มา:** ผลการขุด history พบว่า commit สุดท้ายที่แตะ `SranDesktop/` คือ `ed117ad`
(19 ก.ค.) และที่ commit นั้น **ไม่มี sandbox เลย** — `SystemProcessRunner` รันบน host,
`OpenUrlTool` ใช้ `Process.Start(UseShellExecute=true)`, `PathGuard.BuiltInRoots` ยังเป็น
`{ UserProfile, TempPath }` ข้อจำกัดทั้งหมดที่ทำให้ `docker ps` / เปิดลิงก์ / อ่านเขียนไฟล์
พังอยู่ตอนนี้ เกิดขึ้นระหว่าง 26–30 ก.ค. และ **ยังไม่ได้ commit สักอันเดียว**

ผู้ใช้ต้องการความสามารถของ 19 ก.ค. กลับมา

**นี่ไม่ใช่ใบสั่ง revert** เป้าหมายคือ capability surface ของ 19 ก.ค. + การควบคุมของ 30 ก.ค.
กลไกที่แทนของเดิมมีอยู่แล้วเกือบทั้งหมด — ส่วนใหญ่แค่ยังไม่ได้ต่อสายเข้าผู้ใช้

---

## ⚠️ ก่อนแตะโค้ด

Working tree มี **133 ไฟล์ .cs untracked** (212 รายการรวมทั้งหมด) — งาน 11 วันที่อยู่ดิสก์
เครื่องเดียว ไม่มีใน remote ไหน รวม `DurableTaskRuntime.cs`, `SemanticOutcomeRuntime.cs`,
`GoalInterpreter.cs`, `DataEgressPolicy.cs`, `UntrustedObservationBoundary.cs`

**ห้าม** `git checkout` / `reset` / `clean` / `stash` / `switch` / `restore`
ถ้าจำเป็นต้องอ่านเวอร์ชันเก่า ใช้ `git show <sha>:<path>` เท่านั้น

ถ้ายังไม่ได้ commit ให้บอกผู้ใช้ commit ก่อนแล้วหยุดรอ อย่าเริ่มแก้

---

## PROMPT P3

```
บริบท: ข้อจำกัดที่ต้องคลายทั้งหมดถูกจัดกลุ่มไว้แล้วจากการขุด history
- กลุ่ม 1 (จำเป็นจริง) ห้ามแตะ: AppContainer, ห้าม fallback sandbox→host,
  ValidateExecutable allowlist, Job Object kill-on-close, IsProtectedStatePath,
  handle-based authorize + reparse resolve, UntrustedObservationBoundary,
  default = "sandbox"
- กลุ่ม 2/3 คือของที่คลายได้ และคือขอบเขตของงานนี้

หลักการที่ต้องยึดทุกข้อ:
  ทุกครั้งที่คลายข้อจำกัด ต้องระบุได้ว่า "กลไกอะไรมาแทนที่"
  ถ้าตอบไม่ได้ = ยังไม่ต้องคลาย

ทำตามลำดับนี้ เพราะข้อ 1 ให้ผลมากที่สุดต่อความเสี่ยงน้อยที่สุด


### 1. shell_execution_mode เข้า Settings UI  ← คืน docker/git/node/python ทั้งก้อน

ไฟล์: SranDesktop/src/SranDesktop.Presentation/Controls/ComposerControl.xaml
      + ChatViewModel.cs (ทำตามแบบ SelectApprovalMode ที่มีอยู่แล้ว ~บรรทัด 254)

ทำไมความเสี่ยงเป็นศูนย์: host lane มีทุกอย่างครบแล้ว — opt-in, ไม่ fallback,
blocklist, consent dialog แสดง command line เต็ม, audit log
shell.host_lane_dispatch เขียนก่อนรัน เปลี่ยนแค่ว่าผู้ใช้กดเลือกได้
แทนที่จะต้องแก้ settings.json ด้วยมือตอนแอปปิด

- dropdown สองตัวเลือก sandbox (default) / host ต่อจากปุ่ม "Skip all approvals"
- ตอนเลือก host ต้องมีข้อความเตือนที่บอกความจริง: คำสั่งจะรันบนเครื่องจริง
  ด้วยสิทธิ์ผู้ใช้เต็ม และถ้า approval_mode = skip จะไม่มีการถามเลย
- ห้ามเปลี่ยน default ต้องเป็น "sandbox" ตลอด
- ปัจจุบัน RunCommandTool.cs:~259 บอกโมเดลว่า "ให้ผู้ใช้ตั้ง shell_execution_mode
  ใน settings" ซึ่งชี้ไปที่สิ่งที่ไม่มีอยู่ — ข้อนี้ทำให้ข้อความนั้นเป็นจริง


### 2. working folder ให้เป็น ReadWrite  ← คืน move_file และหยุด sync ที่ล้มทุกครั้ง

ไฟล์: SranDesktop/src/SranDesktop.Infrastructure/Mcp/Tools/ToolHelpers.cs:~200-203
      (ConfigureGrantedRoots hardcode ReadWriteNoDelete)

สองอาการที่แก้พร้อมกัน:
a. move_file ถูก register ที่ AppHostBuilder.cs:268 แต่ MoveAsync ต้องขอ Delete
   บน source → ถูกปฏิเสธเสมอ → tool นี้ไม่มีทางสำเร็จเลยตั้งแต่วันที่เขียน
b. หนักกว่า: SyncWorkspaceAsync (WindowsAppContainerProcessRunner.cs:~464)
   เรียก _files.DeleteAsync สำหรับไฟล์ที่ sandbox ลบ → throw →
   คำสั่ง sandbox ที่แตะไฟล์ชั่วคราวจบด้วย SandboxWorkspaceSyncFailed ทุกครั้ง

- working folder (ที่ผู้ใช้เลือกเองผ่าน composer) → ReadWrite
- granted folders อื่น คง NoDelete ไว้ได้
- ใช้ ConfigureMounts ที่รับ access level ได้อยู่แล้ว (ตอนนี้มีแต่ test เรียก)
- เขียน test ว่า move_file ในโฟลเดอร์ที่ผู้ใช้เลือก สำเร็จ
  และ sandbox command ที่ลบไฟล์ชั่วคราว จบเป็น success ไม่ใช่ SyncFailed


### 3. แก้รอยต่อ host-lane cwd  ← บั๊ก ไม่ใช่นโยบาย

ไฟล์: RunCommandTool.cs:~304 กับ ~325-328

ตอนนี้: ไม่ระบุ working_directory → fallback เป็น %USERPROFILE% ตรง ๆ (ผ่าน)
        แต่ระบุ working_directory เป็น path เดียวกันนั้น → PathGuard.Resolve
        ปฏิเสธ (เพราะ BuiltInRoots ถูกลบ)
โฟลเดอร์เดียวกัน ได้คำตอบต่างกันขึ้นกับว่าเอ่ยชื่อมันหรือไม่

- เลือกทางเดียวแล้วทำให้สองเส้นทางตอบเหมือนกัน
- เขียน test คู่: implicit fallback กับ explicit path เดียวกัน ต้องได้ผลเหมือนกัน


### 4. first-run mount  ← คืนการอ่านเขียนไฟล์โดยไม่ต้องกดเลือกก่อน

ไฟล์: App.xaml.cs:~146 (ตอนนี้ถ้า WorkingFolder ว่าง ไม่ configure mount เลย)
      + first-run dialog

ปัญหา: GrantedFolders = [] และ WorkingFolder = "" เป็น default → mount 0 อัน →
read_file / list_directory / write_file / sandbox lane ทั้งหมด fail closed
และแอปไม่ได้บอกผู้ใช้ว่าต้องทำอะไร

**ห้ามเอา PathGuard.BuiltInRoots กลับ** — การตัดมันออกถูกต้องแล้ว
(ยกทั้ง home ให้ agent โดยปริยาย) สิ่งที่ขาดคือ onboarding ไม่ใช่ built-in root

- first-run บังคับให้เลือกโฟลเดอร์ทำงาน หรือเสนอ %USERPROFILE%\Documents
  เป็นค่าเริ่มต้นที่ผู้ใช้กดยืนยัน
- ถ้ายังไม่มี mount ข้อความ error ของ tool ต้องบอกวิธีแก้ ไม่ใช่แค่บอกว่าล้ม


### 5. เปิดลิงก์ในเบราว์เซอร์  ← คืน OpenUrlTool

ไฟล์: OpenUrlTool.cs + RuntimeBypassInvariantTests.cs HostExecutionAllowlist (~:16)

แยกสองเรื่องที่ตอนนี้ตายไปพร้อมกัน:
- "เปิดลิงก์ให้ผู้ใช้ดู" — ไม่ต้องการ observation เลย ผู้ใช้เห็นด้วยตาตัวเอง
- "agent ขับเบราว์เซอร์แล้วต้อง verify ผล" — ต้องผ่าน bridge ต่อไป

หมายเหตุที่ต้องรู้: test ชื่อ No_Browser_Tool_Opens_A_Url_Through_The_Shell
filter ด้วย Path.GetFileName(file).StartsWith("Browser") → OpenUrlTool.cs
ไม่เข้าเงื่อนไขนั้น ตัวที่บล็อกจริงคือ
No_Agent_Tool_Starts_A_Host_Process_Outside_The_Audited_Runners
→ ต้องแก้ที่ HostExecutionAllowlist ไม่ใช่ test ที่ชื่อพูดถึง browser

- ใส่ OpenUrlTool.cs ลง HostExecutionAllowlist พร้อม test ประจำตัว
- จำกัด scheme เป็น http/https เท่านั้น ปฏิเสธ file:// และอื่น ๆ ทั้งหมด
- consent คง Ask
- **ต้องรายงานเป็น dispatched ไม่ใช่ verified** — เราไม่รู้ว่าหน้าโหลดสำเร็จไหม
  การอ้างว่า verified คือ false success ที่เพิ่งใช้เวลาสองวันตามล่า
- invariant browser-observation ยังบังคับกับ browser_navigate ต่อไป


### 6. open_application — ทำท้ายสุด หรือแยกรอบ

allowlist 5 ชื่อ (notepad/calc/paint) ทำให้เปิด Chrome/Excel/Word/Explorer/VS Code
ไม่ได้ อันนี้ต้องออกแบบก่อน ไม่ใช่ของทำวันนี้

ขั้นต่ำที่ทำได้เลยและได้ผลมาก: **แยก path "focus หน้าต่างที่เปิดอยู่ตามชื่อ"
(OpenApplicationTool.cs:~52) ออกจาก path "launch"** — การ focus ไม่ได้ launch
อะไรเลย ความเสี่ยงคนละระดับ แต่ตอนนี้ถูกจำกัดด้วยตารางเดียวกัน

ถ้าจะขยาย launch: resolve จาก App Paths registry + ตรวจลายเซ็นดิจิทัล
แทนตารางฝังตาย — เสนอดีไซน์มาก่อน อย่าเพิ่งเขียน


## ข้อห้ามรวม

- ห้ามคืน SystemProcessRunner (จริง ๆ ควรลบทิ้งพร้อมถอด ProcessRunner.cs
  ออกจาก allowlist — grep แล้ว reference เป็นศูนย์ มันอยู่ได้เพราะ test
  Allowlisted_Host_Execution_Entries_All_Still_Exist ค้ำตัวเองไว้)
- ห้ามเพิ่ม fallback sandbox → host
- ห้ามพลิก default เป็น host
- ห้ามถอด IsProtectedStatePath
- ห้ามเปิด PATH เต็มในเลน sandbox (ไม่ทำให้ docker ใช้ได้ เพราะมันคุยผ่าน
  named pipe ที่ container ไม่มีสิทธิ์ — เสีย boundary ฟรี ๆ)

## เกณฑ์ผ่าน

- แต่ละข้อมี test ที่แดงก่อนแก้ เขียวหลังแก้
- `dotnet build SranDesktop/SranDesktop.sln -c Debug` 0 errors
- `dotnet test SranDesktop/SranDesktop.sln` เขียวทั้งชุด ไม่มี test ใหม่ที่ skip
  (ปัจจุบัน 1950 passed / 0 failed / 4 skipped — 4 ตัวนั้นคือ privileged symlink
  ที่ต้อง VM ไม่นับ)
- `git status --porcelain | wc -l` ต้องไม่ลดลง
- **ต้อง dotnet publish ทับ publish/win-x64 ด้วย** — ตอนนี้ค้างวันที่ 23 ก.ค.
  ทำให้ shortcut ที่ผู้ใช้กดเปิดรันโค้ดก่อนการเปลี่ยนแปลงเกือบทั้งหมด
  และเป็นสาเหตุที่การทดสอบสดตลอดสองวันไม่ได้ทดสอบโค้ดปัจจุบัน
- แถม: ให้แอปแสดง build id/วันที่ที่มุมจอ เพื่อให้เห็นทันทีว่ากำลังรันตัวไหน
```

---

## ทดสอบสดหลังเสร็จ (ต้องทำครบ 5 ข้อ)

1. `docker ps` → ได้ container จริง (ผู้ใช้มี 24 ตัวรันอยู่ ตรวจแล้วจาก Docker Desktop)
2. `move_file` ในโฟลเดอร์ทำงาน → สำเร็จ
3. เปิดลิงก์ → เบราว์เซอร์เปิดจริง และรายงานว่า dispatched ไม่ใช่ verified
4. ติดตั้งใหม่/ล้าง settings → อ่านเขียนไฟล์ได้โดยไม่ต้องไปหาปุ่มเลือกโฟลเดอร์เอง
5. **`"ดูใน docker ตอนนี้ที่ว่ามีอะไรรันอยู่"` ตอนตั้งกลับเป็น sandbox**
   → step ต้องล้ม และ **ต้องไม่ขึ้น Completed / goal ต้องไม่ถูกขีดฆ่า**
   (ข้อนี้กันไม่ให้การคืนความสามารถกลบ false-success fix ไปด้วย)

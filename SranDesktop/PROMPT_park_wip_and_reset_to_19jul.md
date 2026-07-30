# Prompt — พักงาน 22–30 ก.ค. ไว้บนแบรนช์ แล้วให้ main กลับเป็น 19 ก.ค. สะอาด

วางใน Claude Code ที่ root repo

```
เป้าหมาย: ผมตัดสินใจใช้ ed117ad (19 ก.ค.) เป็นฐาน dev ต่อ เพราะมันใช้งานได้จริง
งาน 22-30 ก.ค. ให้เก็บไว้บนแบรนช์ ไม่ต้องทิ้ง แต่ต้องพ้นทางออกจาก main


## ทำไมงานนี้ต้องระวังเป็นพิเศษ

ขั้นตอนสุดท้าย `git checkout main` จะลบไฟล์ 133 ตัวออกจาก working tree
ถ้า commit ก่อนหน้าเก็บไม่ครบ ของที่ตกหล่นจะหายถาวร ไม่มีที่ไหนกู้ได้เลย
เพราะไฟล์พวกนี้ไม่เคยอยู่ใน remote ไหน

ดังนั้นลำดับสำคัญกว่าความเร็ว และมีจุด STOP ที่ห้ามข้าม


## ขั้นตอน

### 0. สำรองก่อน — ไม่แตะ git

$stamp = Get-Date -Format "yyyyMMdd-HHmm"
Copy-Item "C:\Users\Beat Beat\Desktop\ai-gateway" `
          "C:\Users\Beat Beat\Desktop\ai-gateway-BACKUP-$stamp" -Recurse -Force

ยืนยันว่าโฟลเดอร์สำรองมีอยู่จริงและมีขนาดใกล้เคียงต้นฉบับก่อนไปต่อ

### 1. ปลด .gitignore ที่ซ่อน source อยู่

.gitignore บรรทัด 27 กฎ `storage/` ซ่อน 4 ไฟล์นี้ไว้ — มันไม่โผล่ใน git status
ด้วยซ้ำ และ `git add -A` จะข้ามไปเงียบ ๆ:

  SranDesktop/src/SranDesktop.Tests/Storage/DpapiCredentialStoreTests.cs
  SranDesktop/src/SranDesktop.Tests/Storage/SqliteConversationStoreTests.cs
  SranDesktop/src/SranDesktop.Tests/Storage/SqliteMigratorTests.cs
  SranDesktop/src/SranDesktop.Tests/Storage/SqliteStorePhase3Tests.cs

เติม: !SranDesktop/src/SranDesktop.Tests/Storage/

แล้วยืนยันว่าทั้ง 4 ไฟล์โผล่ใน git status แล้วจริง ก่อนไปต่อ

### 2. แตกแบรนช์ (ยังไม่ย้ายไฟล์)

git checkout -b wip/sandbox-22-30jul

`git checkout -b` ที่ HEAD ปลอดภัยกับ untracked — สร้าง label เฉย ๆ ไม่ย้ายไฟล์

### 3. เก็บของ

git add -A SranDesktop .gitignore
git commit -m "WIP 22-30 Jul: sandbox runtime, durable runtime, semantic outcomes, two-lane shell"


### 4. 🛑 STOP — ตรวจก่อนไปขั้นถัดไป ห้ามข้าม

ขั้นที่ 5 คือขั้นที่ย้อนไม่ได้ ต้องผ่านทุกข้อนี้ก่อน:

a. ไฟล์แกนกลางเข้า commit ครบทั้ง 7 กลุ่ม
   git show --name-only wip/sandbox-22-30jul | Select-String `
     "SemanticOutcomeRuntime|DurableTaskRuntime|WorkspaceFileBroker|WindowsAppContainerProcessRunner|HostProcessRunner|RuntimeBypassInvariantTests|Tests/Storage"

b. จำนวนไฟล์ .cs ใน commit ต้อง >= 133

c. git status --porcelain ต้องเหลือแต่ของที่ ignore ได้ (bin/, obj/, TestResults/)
   ถ้ายังมี .cs ค้างอยู่ = ยังเก็บไม่ครบ

**ถ้าข้อไหนไม่ผ่าน หยุดแล้วรายงานมา อย่ารันขั้น 5**
ตอนนี้ยังปลอดภัยเพราะไฟล์ยังอยู่บนดิสก์ พอผ่านขั้น 5 แล้วจะไม่มีทางถอย


### 5. กลับ main

git checkout main

หลังจากนี้ไฟล์ 133 ตัวจะหายจากโฟลเดอร์ — ถูกต้อง มันอยู่บนแบรนช์แล้ว

### 6. ยืนยันผล

- git log --oneline -1        ต้องเป็น ed117ad
- git status --porcelain      ควรสะอาด (เหลือแต่ bin/obj)
- ตรวจว่า SystemProcessRunner.cs, OpenUrlTool.cs (แบบเดิม), PathGuard.BuiltInRoots
  กลับมาอยู่ในสภาพ 19 ก.ค. แล้ว
- dotnet build SranDesktop/SranDesktop.sln -c Debug  → ต้อง 0 errors
  (ถ้าแดง แปลว่า ed117ad เองมีปัญหา ให้รายงาน อย่าแก้)


## ห้ามทำ

- ห้าม git worktree remove ตัว ai-gateway-19jul — ผมยังใช้แอปจากตรงนั้นอยู่
- ห้าม git clean ทุกกรณี
- ห้าม push อะไรขึ้น remote
- ห้ามลบโฟลเดอร์สำรอง


## รายงานกลับ

- จำนวนไฟล์ .cs ที่เข้า commit
- ผลตรวจขั้น 4 ทั้ง a/b/c
- HEAD หลังขั้น 5
- ผล build
```

# Investigation Prompt — ทำไม desktop app "เคย" รันคำสั่ง / เปิดเบราว์เซอร์ / สร้างและแก้ไฟล์ได้

**ประเภทงาน:** ขุดประวัติ git อย่างเดียว **ไม่แก้โค้ด ไม่แตะ working tree**
**ผลลัพธ์ที่ต้องการ:** รายงาน ไม่ใช่ commit

---

## ⚠️ คำเตือนที่สำคัญที่สุด — อ่านก่อนพิมพ์คำสั่งแรก

Working tree นี้มีไฟล์แกนกลางที่ **untracked** อยู่ ตรวจแล้วยืนยันแล้ว:

```
??  SranDesktop/src/SranDesktop.Application/Services/SemanticOutcomeRuntime.cs
??  SranDesktop/src/SranDesktop.Application/Services/DurableTaskRuntime.cs
??  SranDesktop/src/SranDesktop.Infrastructure/Mcp/Tools/HostProcessRunner.cs
```

สองไฟล์แรกคือ semantic outcome verification กับ durable runtime ทั้งระบบ
**มีอยู่ที่เดียวคือดิสก์เครื่องนี้ ไม่มีใน remote ไหนเลย** ทั้ง `gitlab` และ `origin`

ดังนั้น **ห้ามใช้คำสั่งเหล่านี้เด็ดขาด:**

```
git checkout <sha>        git switch          git restore
git reset --hard          git clean           git stash
```

ทั้งหมดนี้ทำให้ไฟล์ที่กู้คืนไม่ได้หายถาวร

**วิธีอ่านไฟล์เวอร์ชันเก่าที่ปลอดภัย** — ใช้ `git show` ซึ่งพิมพ์ออก stdout ไม่แตะ working tree:

```
git show <sha>:SranDesktop/src/.../RunCommandTool.cs
```

ถ้าจำเป็นต้องกาง tree เก่าจริง ๆ ให้ใช้ worktree แยกโฟลเดอร์เท่านั้น:

```
git worktree add /tmp/sran-old <sha>
```

---

## สิ่งที่รู้แล้ว (อย่าไปเสียเวลาหาซ้ำ)

จากการอ่านโค้ดปัจจุบันและการทดสอบสดสองวันที่ผ่านมา:

1. `ProcessRunner.cs` มี `SystemProcessRunner` ที่ถูกทำเป็น
   `[Obsolete("Direct host execution is disabled. Use WindowsAppContainerProcessRunner.")]`
   และ throw ทุกครั้งที่ถูกเรียก คำอธิบายในโค้ดเขียนว่า
   *"Binary-compatibility guard for **older composition roots**"*
   → **นี่คือหลักฐานว่าเคยมียุคที่ `run_command` รันบน host ตรง ๆ**

2. `WindowsAppContainerProcessRunner.BuildEnvironment()` ตั้ง `PATH` เป็น System32 เท่านั้น
   และ capability set ว่าง → docker/git/node/python เข้าไม่ถึงโดยโครงสร้าง

3. `RunCommandTool.ResolveWorkingDirectory` เคย fail closed ถ้าไม่มี working folder
   ทำให้ทุกคำสั่งล้มแม้คำสั่งที่ไม่แตะไฟล์เลย (แก้แล้ววันนี้ แยกตามเลน)

4. `docs/UNIFIED_DURABLE_RUNTIME.md` เคยเขียนว่า local MCP stdio ถูกปิด
   ซึ่ง stale ไปนานแล้ว → เอกสารกับโค้ดหลุดกันเป็นนิสัยในเรโปนี้

5. `publish/win-x64` ค้างวันที่ 23 ก.ค. ขณะที่ `bin/Debug` เป็น 30 ก.ค.
   → การทดสอบด้วย shortcut ที่ผ่านมาไม่ได้ทดสอบโค้ดปัจจุบัน

---

## คำถามที่ต้องตอบ

### A. Shell / run_command

- commit ไหนที่ `SystemProcessRunner` เลิกถูกใช้ และ `WindowsAppContainerProcessRunner`
  ถูกใส่เข้ามา (`git log -S "SystemProcessRunner"`,
  `git log --diff-filter=A -- "*WindowsAppContainerProcessRunner.cs"`)
- **ก่อน** commit นั้น `RunCommandTool` หน้าตาเป็นยังไง
  - บังคับ working directory ไหม
  - environment ถูก sanitize ไหม หรือ inherit ของ host
  - มี blocklist / consent gate อะไรบ้าง
- commit message กับ PR/MR description บอกเหตุผลอะไรไว้
  (`git log --format=full` เพื่อดู body เต็ม)

### B. Browser

- ก่อนจะมี `BrowserBridgeService` + extension + native host
  แอปเปิดเบราว์เซอร์ด้วยวิธีอะไร (`git log -S "UseShellExecute"`,
  `git log -S "Process.Start" -- "*Browser*"`)
- ตอนนี้ `RuntimeBypassInvariantTests.No_Browser_Tool_Opens_A_Url_Through_The_Shell`
  ห้ามเปิด URL ผ่าน shell — invariant นี้เข้ามาเมื่อไหร่ และมันปิดอะไรที่เคยทำได้

### C. Filesystem

- `WorkspaceFileBroker` เข้ามาเมื่อไหร่ (`git log --diff-filter=A -- "*WorkspaceFileBroker.cs"`)
- ก่อนหน้านั้น read/write/edit file ตรวจ path ยังไง หรือไม่ตรวจเลย
- `PathGuard` / granted folders / working folder เข้ามาตอนไหน
  และตอนนั้นผู้ใช้ต้องเลือกโฟลเดอร์ก่อนไหม

### D. คำถามหลักที่ทั้งหมดนี้รับใช้

สำหรับ**ทุกข้อจำกัดที่เจอ** ให้จัดว่าอยู่กลุ่มไหน:

| กลุ่ม | นิยาม | ตัวอย่างที่เจอแล้ว |
|---|---|---|
| **1. จำเป็นจริง** | ถอดออกแล้วเกิดช่องโหว่จริง | AppContainer สำหรับ shell ที่รับคำสั่งจากโมเดล |
| **2. จำเป็นแต่กว้างเกิน** | เจตนาถูก แต่ครอบของที่ไม่เกี่ยว | บังคับ working dir กับ `docker ps` ที่ไม่แตะไฟล์ |
| **3. เผลอติดมา** | ก๊อปมาจากบริบทอื่นโดยไม่ได้คิด | `ContainingRoot` gate ใน `HostProcessRunner` |

**นี่คือหัวใจของงาน** — เป้าหมายไม่ใช่ย้อนกลับไปยุคที่ไม่มีความปลอดภัย
แต่คือแยกให้ออกว่าอะไรจำเป็น อะไรกว้างเกิน อะไรเผลอ
เพื่อจะ **คืนความสามารถได้โดยไม่คืนช่องโหว่**

---

## รูปแบบรายงานที่ต้องการ

```
## ไทม์ไลน์
| วันที่ | commit | เปลี่ยนอะไร | ความสามารถที่หายไป | เหตุผลที่ระบุไว้ |

## ข้อจำกัดปัจจุบัน จัดกลุ่ม 1/2/3
(ต่อรายการ: ไฟล์+บรรทัด, มาจาก commit ไหน, ถอดออกแล้วเกิดอะไร)

## สิ่งที่คืนได้เลยโดยไม่กระทบความปลอดภัย
(เรียงตาม ผลที่ได้ ÷ ความเสี่ยง)

## สิ่งที่ห้ามคืน และเพราะอะไร

## คำถามที่ history ตอบไม่ได้
```

---

## ข้อห้ามเรื่องการสรุป

- **ห้ามสรุปว่า "ยุคก่อนดีกว่า"** ยุคก่อนอาจใช้งานได้เพราะไม่มีการตรวจอะไรเลย
  ซึ่งเป็นเหตุผลที่ต้องเปลี่ยน ไม่ใช่เหตุผลที่ต้องกลับไป
- **ห้ามเดา** ถ้า commit message ไม่ได้บอกเหตุผล ให้เขียนว่า "ไม่ระบุ"
  อย่าประดิษฐ์เจตนาให้คนที่เขียน
- ถ้าเจอว่าไฟล์สำคัญ untracked เพิ่มอีก **ให้รายงานทันที** อย่าพยายามจัดการเอง

---

## ตรวจก่อนเริ่ม

```
git status --porcelain | Measure-Object -Line     # จำนวนต้องไม่ลดลงตอนจบงาน
```

จบงานแล้วรันซ้ำ ถ้าตัวเลขลดลงแปลว่ามีไฟล์หาย — หยุดและรายงานทันที

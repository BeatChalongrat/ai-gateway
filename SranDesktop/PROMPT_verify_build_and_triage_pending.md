# Prompt — ยืนยันว่าแอปที่รันอยู่คือบิลด์ไหน + เคลียร์บิลด์ปนกัน + จัดการ 30 รายการที่ค้าง

วางใน Claude Code ที่ root repo

```
สถานะ: main = ed117ad (19 ก.ค.) สะอาดแล้ว, งาน 22-30 ก.ค. อยู่บนแบรนช์
wip/sandbox-22-30jul ครบ 208 ไฟล์, worktree ai-gateway-19jul ยังอยู่และผมรันแอปจากตรงนั้น

ผมจะยึดแอปที่รันได้เป็นหลักในการ dev ต่อ เลยต้องรู้ให้ชัดว่ากำลังรันตัวไหนอยู่


## A. แอปที่เปิดอยู่คือบิลด์ไหน

Get-Process SranDesktop -ErrorAction SilentlyContinue | Select-Object Id, Path, StartTime

บอกผมว่า Path ชี้ไปที่ไหน และตรงกับข้อไหน:
- ai-gateway-19jul\...          → worktree 19 ก.ค.
- ai-gateway\SranDesktop\publish\  → บิลด์ 30 ก.ค. (ตัวที่พัง)
- ai-gateway\SranDesktop\src\...\bin\Debug\  → โค้ด 19 ก.ค. build ใหม่

ถ้าแอปปิดอยู่ ให้ดูจาก timestamp ของ exe แต่ละตัวแทน


## B. โฟลเดอร์หลักมีบิลด์สองรุ่นปนกัน — อันตราย

  publish/win-x64/SranDesktop.exe          30 ก.ค. 15:34  91 MB   ← โค้ด 30 ก.ค. (sandbox, พัง)
  src/.../bin/Debug/SranDesktop.exe        30 ก.ค. 16:04  178 KB  ← โค้ด 19 ก.ค.

publish/ ถูกสร้างก่อน checkout กลับ main จึงยังเป็นบิลด์ sandbox
ชื่อไฟล์เหมือนกัน หน้าตาแอปเหมือนกัน และ shortcut เดิมชี้ไปที่ publish/

งาน:
1. ยืนยัน timestamp ของทั้งสองไฟล์
2. publish ใหม่ทับ publish/win-x64 จากโค้ด main (ed117ad) ที่เช็คเอาต์อยู่
   — ปิด SranDesktop.exe และ SranDesktop.BrowserHost.exe ก่อน
   — Chrome จะ respawn BrowserHost ถ้าโผล่มาให้ sweep ซ้ำ
3. ยืนยันว่า LastWriteTime ของ publish exe ขยับเป็นเวลาปัจจุบันจริง

หมายเหตุตัวแยกบิลด์: ฟีเจอร์ build id ที่มุมจอมีเฉพาะในโค้ด 30 ก.ค.
ดังนั้นหลัง publish ใหม่ **จะไม่มี build id** ซึ่งถูกต้อง — ไม่มี = 19 ก.ค.


## C. 30 รายการที่ยังไม่ commit — แยกสองกลุ่ม

จาก mtime แบ่งได้ชัด:

  21 ก.ค.  backend/app/services/providers/openai_adapter.py + test  (2 ไฟล์)
           → คนละเรื่อง เกิดหลัง ed117ad 2 วัน ไม่เกี่ยวกับ sandbox

  26 ก.ค.  services/chat-edge-api/  (8 ไฟล์)
           internal/sandbox/sandbox.go
           internal/service/sandbox_fallback_security_test.go
           internal/service/{file,doc,excel,workspace}_service.go
           cmd/server/main.go
           → ช่วงเดียวกับงาน sandbox ฝั่ง desktop ชื่อไฟล์บอกว่าเป็นธีมเดียวกัน

งาน:
1. `git diff --stat HEAD` ดูว่าจริง ๆ แล้ว 30 รายการประกอบด้วยอะไรบ้าง
   (ตัวเลข 30 มาจาก VS Code badge ยังไม่ได้ตรวจว่าตรงกับ 10 ไฟล์นี้ไหม)
2. commit กลุ่ม 21 ก.ค. แยกไว้ — ไม่เกี่ยวกับเรื่อง sandbox
   git add backend
   git commit -m "backend: openai adapter changes (21 Jul)"
3. กลุ่ม 26 ก.ค. **อย่าเพิ่ง commit** — สรุปมาให้ผมก่อนว่าแต่ละไฟล์แก้อะไร
   โดยเฉพาะ sandbox.go กับ sandbox_fallback_security_test.go
   ผมต้องตัดสินใจว่า sandbox ฝั่ง server จะเดินต่อหรือถอยเหมือนฝั่ง desktop
   ก่อนถึงจะรู้ว่าควรเก็บหรือทิ้ง


## D. .gitignore บน main

`!SranDesktop/src/SranDesktop.Tests/Storage/` ที่เติมไปนั้นอยู่บนแบรนช์ wip เท่านั้น
บน main กลับเป็นของ ed117ad ซึ่งกฎ storage/ ยังซ่อน SranDesktop.Tests/Storage/ อยู่

เติมบน main ด้วยแล้ว commit แยก — ไม่งั้น test ใหม่ที่เขียนในโฟลเดอร์นั้นจะหายเงียบอีก


## ข้อห้าม

- ห้าม git checkout -- . / git clean / git reset --hard
  (30 รายการที่ค้างยังไม่ได้ commit)
- ห้าม git worktree remove ai-gateway-19jul — ผมยังใช้แอปจากตรงนั้น
- ห้าม push
- ห้ามลบโฟลเดอร์สำรอง ai-gateway-BACKUP-*


## รายงานกลับ

- A: path ของ process ที่รันอยู่
- B: timestamp ก่อน/หลัง publish
- C: git diff --stat HEAD เต็ม ๆ + สรุปว่า 8 ไฟล์ฝั่ง chat-edge-api แก้อะไร
- D: commit hash ของ .gitignore
```

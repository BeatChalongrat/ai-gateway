# Prompt — ปิดงาน: ทำให้ 19 ก.ค. เป็นฐานปัจจุบันแบบสะอาด

วางใน Claude Code ที่ root repo

```
ผมตัดสินใจแล้วว่าจะใช้ ed117ad (19 ก.ค.) เป็นฐาน dev ต่อ งานหลังจากนั้นทิ้ง
— แต่ "ทิ้ง" = ไม่ merge ไม่ใช่ลบ แบรนช์ wip/sandbox-22-30jul ต้องอยู่ต่อไป
ไม่มีต้นทุนในการเก็บ และผมอาจอยากหยิบชิ้นเล็ก ๆ กลับวันหลัง

เหลืองานปิดท้าย 5 อย่าง


## A. ลบบิลด์ซากตัวที่ 3

SranDesktop\src\SranDesktop.App\bin\Debug\net8.0-windows10.0.17763.0\

ed117ad ใช้ TFW net8.0-windows เฉย ๆ โฟลเดอร์ที่มี 10.0.17763.0 จึงเป็นของโค้ด 30 ก.ค.
ชื่อไฟล์และขนาดเหมือนตัวจริงเป๊ะ เป็นกับดักที่รอให้เผลอเปิด

ลบทิ้ง แล้วยืนยันว่าเหลือ bin\Debug\net8.0-windows\ อันเดียว


## B. หาว่ายังมีอะไรชี้ไปโค้ด 30 ก.ค. อีกไหม

รอบนี้เจอมาแล้วสองอันที่ไม่มีใครเห็นตอนแรก:
- publish/win-x64 ค้างวันที่ 23 ก.ค. (ทำให้เทสผิดตัวทั้งวัน)
- native-messaging manifest ชี้ path ตายตัว → Chrome spawn BrowserHost 30 ก.ค.
  ให้แอป 19 ก.ค. คุยด้วย โดยไม่โผล่ใน Get-Process ของแอปหลัก

อยากรู้ว่ายังมีที่ไหนอีกที่ฝัง path หรือ state ของบิลด์เก่าไว้ ตรวจอย่างน้อย:
- registry: sran:// URI scheme (RegisterSranSchemeBestEffort เขียนไว้)
- shortcut บน Desktop / Start Menu ชี้ไปไหน
- scheduled task ถ้ามี
- native-messaging manifest ทุกตัว (ยืนยันว่าคืนที่เดิมและชี้ถูกแล้ว)
- ไฟล์ config/state ใน %APPDATA%\SRAN Desktop ที่อ้าง path

รายงานว่าเจออะไรบ้าง อันไหนชี้ผิดให้แก้ อันไหนถูกแล้วบอกว่าถูก


## C. 8 ไฟล์ services/chat-edge-api/ (26 ก.ค.) — เซฟไว้ก่อน อย่าเพิ่งตัดสิน

ผมยังไม่ตัดสินใจว่าฝั่ง server จะถอยตาม desktop ไหม มันคนละ codebase (Go)
ไม่ได้พัง และ diff ไม่เล็ก (file_service.go +193 บรรทัด, มี test ใหม่ 2 ไฟล์)

1. เซฟเป็น patch ก่อน รวม untracked ด้วย:
   git diff HEAD -- services/chat-edge-api > SranDesktop/chat-edge-26jul.patch
   แล้วก๊อป sandbox_fallback_security_test.go เก็บไว้ต่างหาก
   (git diff ไม่รวมไฟล์ที่ยัง untracked)

2. อ่าน diff ให้ครบแล้วสรุปมาต่อไฟล์ว่าแก้อะไร โดยเฉพาะ:
   - internal/sandbox/sandbox.go (−28)
   - internal/service/sandbox_fallback_security_test.go
   สองตัวนี้จะบอกว่า sandbox ฝั่ง server เป็นแนวคิดเดียวกับที่ทำใน desktop
   (ซึ่งผมถอยแล้ว) หรือเป็นคนละเรื่อง

3. **ห้าม commit ห้าม checkout -- ห้ามแก้** จนกว่าผมจะตัดสินใจ
   งานข้อนี้คือเซฟ + สรุป เท่านั้น


## D. เปิดแอปแล้วยืนยันทั้งสายว่าเป็น 19 ก.ค.

เปิดจาก:
C:\Users\Beat Beat\Desktop\ai-gateway-19jul\SranDesktop\src\SranDesktop.App\bin\Debug\net8.0-windows\SranDesktop.exe

แล้วยืนยัน 3 อย่างพร้อมกัน — ไม่ใช่แค่แอปหลัก:
- SranDesktop.exe path + ProductVersion
- SranDesktop.BrowserHost.exe ทุกตัวที่รันอยู่ path + ProductVersion
- ไม่มี process ไหนมาจาก publish\ ของโค้ด 30 ก.ค.

ProductVersion ต้องมี +ed117ad และต้องไม่มี +build.


## E. โฟลเดอร์ 19 ก.ค. มีสองชุด — ควรเหลือชุดเดียว

ตอนนี้มีทั้ง
  ai-gateway\            (main = ed117ad)
  ai-gateway-19jul\      (worktree = ed117ad)

เนื้อหาเดียวกัน แต่เป็นคนละโฟลเดอร์ = อีกหนึ่งทางที่จะเปิดผิดตัว

ตรวจว่า build ในโฟลเดอร์หลัก (ai-gateway\) รันได้จริง — build + เปิดดู
ถ้ารันได้เท่ากัน ให้เสนอมาว่าควร retire worktree ไหม **แต่อย่าเพิ่ง remove**
ผมจะสั่งเองหลังยืนยันว่าโฟลเดอร์หลักใช้งานได้จริง


## ข้อห้าม

- ห้ามลบ/แก้แบรนช์ wip/sandbox-22-30jul
- ห้าม git worktree remove
- ห้าม git clean / checkout -- . / reset --hard
- ห้าม push
- ห้ามลบ ai-gateway-BACKUP-*


## รายงาน

A ลบแล้วเหลืออะไร · B รายการที่ตรวจ + ผลแต่ละอัน · C สรุป 8 ไฟล์ + path ของ patch
D path/ProductVersion ของทุก process · E ผล build โฟลเดอร์หลัก + ข้อเสนอ
```

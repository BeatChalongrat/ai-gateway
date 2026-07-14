# Prompt: Build, Run & Visual Audit — SRAN Desktop

> ให้ AI รัน SRAN Desktop แล้ว screenshot ทุกหน้า/state เพื่อ visual audit

---

## Context

SRAN Desktop คือ WPF (.NET 8) chat client อยู่ที่ `ai-gateway/SranDesktop/`  
เพิ่ง overhaul UI ทั้งหมด — เปลี่ยน emoji เป็น vector icons, ปรับ color palette, redesign message bubbles, เพิ่ม empty state + model selector + sidebar branding  
ต้องการ visual audit ว่าหน้าตาจริงตรงกับ design ที่วางไว้

## Task

### 1. Build & Run

```powershell
cd ai-gateway/SranDesktop/src/SranDesktop.App
dotnet build
dotnet run
```

ถ้า build error ให้แก้แล้ว report ด้วย

### 2. Screenshot ทุก State

เปิด app แล้ว screenshot ทุกหน้าจอ/state ต่อไปนี้:

**Login screen**
- หน้า login (ก่อน sign in)

**Dark theme (default)**
1. Empty state — ต้องเห็น: logo "S" สีน้ำเงิน, greeting ตาม time-of-day, "How can I help you today?", 4 suggestion chips (Write/Learn/Code/Analyze)
2. Sidebar — ต้องเห็น: "SRAN Chat" wordmark, New chat button มี + icon + "Ctrl+N", search box มี 🔍 icon
3. Composer area — ต้องเห็น: model selector pill (sparkles + model name + chevron), attach icon (paperclip vector ไม่ใช่ 📎), agent toggle icon (ไม่ใช่ 🤖), send button เป็นวงกลมมี arrow icon
4. พิมพ์ข้อความส่ง 1 message — ต้องเห็น: user bubble สีเทา (ไม่ใช่สีน้ำเงิน), CornerRadius 20, text สีขาว/primary
5. รอ response — ต้องเห็น: assistant message ไม่มี bubble background, ไม่มี avatar วงกลมสีน้ำเงิน "S", เป็น full-width prose
6. Hover บน assistant message — ต้องเห็น: action row ปรากฏ (copy icon, regenerate icon, thumbs up, thumbs down)
7. กด copy — ต้องเห็น: icon เปลี่ยนเป็น check สีเขียว ชั่วขณะ
8. Settings dialog — ต้องเห็น: gear icon (ไม่ใช่ ⚙)
9. Sidebar toggle — ต้องเห็น: panel icon (ไม่ใช่ ☰)
10. Context menu บน conversation item — ต้องเห็น: icons ข้าง Rename/Pin/Delete (ไม่ใช่ text-only)

**Light theme (switch in settings)**
11. ทำซ้ำ screenshot #1 (empty state) ใน light theme — verify สี bg #FFFFFF, sidebar #F9F9F9
12. ทำซ้ำ screenshot #4-5 (message bubbles) ใน light theme — verify user bubble สีเทาอ่อน ไม่ใช่สีน้ำเงิน

**Agent mode**
13. Toggle เข้า agent mode — ต้องเห็น: agent panel, tool icons เป็น vector (ไม่ใช่ emoji 📁🌐📋⚡🖥🔧)

### 3. Verification Checklist

หลัง screenshot ครบ ให้ตอบ checklist นี้:

| # | Check | Pass/Fail |
|---|---|---|
| 1 | Zero emoji ในหน้าจอทั้งหมด (ยกเว้น markdown content เช่น ☑/☐) | |
| 2 | Icons เปลี่ยนสีตาม theme (dark=light stroke, light=dark stroke) | |
| 3 | Dark bg คือ #212121 ไม่ใช่ #0F172A (slate) — ดูว่าเป็นสีเทาเข้ม ไม่ใช่น้ำเงินเข้ม | |
| 4 | Sidebar สีเข้มกว่า main (#171717) | |
| 5 | User bubble สีเทา ไม่ใช่สีน้ำเงิน ทั้ง dark+light | |
| 6 | User bubble text เป็น primary color (ไม่ใช่สีขาว forced) | |
| 7 | Assistant message ไม่มี avatar circle, ไม่มี bubble background | |
| 8 | Action row (copy/regen/thumbs) ปรากฏเมื่อ hover | |
| 9 | Empty state มี logo + greeting + 4 chips | |
| 10 | Model selector pill มีอยู่ใน composer | |
| 11 | "SRAN Chat" wordmark อยู่บน sidebar | |
| 12 | Send button เป็น icon arrow ไม่ใช่ text "Send" | |
| 13 | Stop button เป็น filled square ไม่ใช่ text "Stop" | |
| 14 | Settings/sidebar toggle เป็น vector icon ไม่ใช่ emoji | |
| 15 | Code block มี language label + copy icon button | |

### 4. Report

สรุปผลเป็น:
- **PASS**: ถ้าทุกอย่างถูกต้อง
- **FAIL + list**: ถ้ามีอะไรผิด ระบุว่าอะไรผิด + screenshot ที่เห็นปัญหา + แนะนำ fix

ถ้า app ต้อง login จริงถึงจะเข้า empty state ได้ ให้ screenshot หน้า login ก่อน แล้ว note ว่า "ต้อง login ก่อนถึงจะ audit ส่วนที่เหลือได้" — อย่า skip การ audit เพราะ login ไม่ผ่าน ให้ audit ทุกอย่างที่เห็นได้โดยไม่ต้อง login ด้วย (login screen, theme colors, icon rendering)

---

## Backend ที่ต้องรันก่อน (ถ้าจะทดสอบ chat จริง)

ถ้าต้องการทดสอบ chat จริง (ไม่ใช่แค่ดู UI) ต้องรัน:
- chat-edge API: port 4000
- gateway API: port 8080

แต่สำหรับ visual audit **ไม่จำเป็นต้องรัน backend** — แค่เปิด app ดู UI ก็พอ ถ้า app crash เพราะ connect ไม่ได้ ให้ report

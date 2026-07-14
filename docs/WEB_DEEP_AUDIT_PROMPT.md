# Prompt: Deep Visual Audit ของ SRAN Web Chat → Desktop Alignment Spec

> ให้ AI เปิด web app จริง (localhost:3002/chat) แล้ว screenshot + วิเคราะห์ทุก pixel  
> Output: ไฟล์ spec ที่ละเอียดพอให้ Fable clone UI ได้ 1:1  
> Login: somchai@acme.ai.gateway / ลองดูว่า login ได้มั้ย หรือถ้าเปิดหน้า chat ได้เลยก็ไม่ต้อง login

---

## Phase 1: Web App Deep Audit

เปิด http://localhost:3002/chat แล้ว screenshot + จดรายละเอียดทุกอย่างต่อไปนี้

### 1.1 Empty State (หน้าแรกหลัง login, ไม่มี chat ที่เลือก)

Screenshot แล้วจด:
- Logo: รูปร่าง, ขนาด (px), ตำแหน่ง, สี, มี ® หรือไม่
- Greeting text: font-size, font-weight, สี, ตำแหน่ง (center/left)
- Subtitle "How can I help you today?": font-size, สี
- Suggestion chips: จำนวน, layout (grid/row), ตำแหน่ง (เหนือ/ใต้ composer), ขนาดแต่ละ chip, icon แต่ละ chip, text, border-radius, border สี, hover effect
- Disclaimer text ใต้ composer: ข้อความเต็ม, font-size, สี
- ระยะห่างระหว่าง elements (spacing)

### 1.2 Sidebar (ละเอียดทุก element)

Screenshot sidebar แล้วจด:
- **Wordmark**: "SRANChat" หรือ "SRAN Chat"? font-size, font-weight, สี, มี logo ข้างๆ มั้ย
- **Collapse button**: icon อะไร (chevron < หรือ panel icon), ตำแหน่ง (ข้าง wordmark หรือแยก), ขนาด
- **New chat button**: icon, text, shortcut hint (⌘N หรือ Ctrl+N), สี bg, border-radius, padding
- **New folder button**: icon, text, สี, ตำแหน่ง (ใต้ New chat?)
- **Search**: icon, placeholder text, shortcut hint, border style, มี filter icon (⊞) ข้างขวามั้ย
- **Section labels**: ข้อความทุกตัว (PINNED, TODAY, YESTERDAY, PREVIOUS 30 DAYS, OLDER?), เป็น UPPERCASE?, font-size, สี, spacing
- **Conversation items**: 
  - height แต่ละ item
  - font-size ของ title
  - truncation (...) 
  - hover effect (สี bg เปลี่ยนเป็นอะไร)
  - selected state (สี bg, border-left สีอะไร?, border-width)
  - pinned icon อยู่ตรงไหน
  - context menu (right-click): มี items อะไรบ้าง, แต่ละ item มี icon มั้ย
- **Bottom user area**:
  - มี avatar circle มั้ย, สีอะไร, ตัวอักษรข้างใน
  - email text: font-size, truncation
  - มีปุ่ม "..." (overflow menu) มั้ย
  - กด "..." แล้วมี menu items อะไรบ้าง
  - มี token/credit counter มั้ย (เห็น 991,504 ในรูป)

### 1.3 Composer (Input Area) — ละเอียดมาก

Screenshot composer area แล้วจด:
- **Container**: border-radius, border สี, bg สี, shadow, padding
- **Input field**: placeholder text, font-size, สี
- **Buttons ภายใน container** (เรียงจากซ้ายไปขวา):
  - "+" button: ตำแหน่ง (ซ้ายล่าง), icon style, ขนาด, กดแล้วมี popup อะไร
  - Image upload icon: ตำแหน่ง, icon shape
  - File/code upload icon: ตำแหน่ง, icon shape
  - Model selector: ตำแหน่ง, icon (sparkles?), text ("Auto"), มี chevron ▾ มั้ย, กดแล้วมี dropdown อะไร (list models)
  - Mic icon: ตำแหน่ง, icon shape
  - Send button: ตำแหน่ง (ขวาสุด), icon (arrow up), shape (circle?), สี bg, สี icon, ขนาด
- **ทุก button**: hover effect, disabled state
- **Shift+Enter hint**: แสดงตรงไหน

### 1.4 Chat Messages (เปิด conversation ที่มี messages)

เลือก conversation ที่มี messages แล้ว screenshot:
- **User message**: 
  - alignment (left/right)
  - bg สี, border-radius
  - text สี, font-size
  - max-width (% ของ container)
  - padding
  - มี edit icon (pencil) ตอน hover มั้ย
- **Assistant message**:
  - alignment
  - มี avatar มั้ย, มี bubble bg มั้ย
  - text สี, font-size, line-height
  - max-width
  - **Action buttons**: อะไรบ้าง (copy, regenerate, thumbs up/down, read aloud?), icon แต่ละตัว, ตำแหน่ง (ซ้าย/ขวา), แสดงตลอดหรือ hover only
- **Code block** (ถ้ามี):
  - header: language label ตำแหน่งไหน, copy button, สี bg
  - body: font, สี bg, border-radius
  - collapse behavior สำหรับ code block ยาว
- **Thinking/reasoning block** (ถ้ามี):
  - header text, icon, สี
  - collapse/expand behavior
  - duration display

### 1.5 Header Bar

- มี title ของ conversation แสดงตรงกลางบนมั้ย หรือไม่มี
- มีปุ่มอะไรบ้างที่ header (share, export, settings?)

### 1.6 Dark Theme vs Light Theme

- สลับไป dark theme (ถ้าสลับได้) แล้ว screenshot ใหม่
- จด color palette: bg หลัก, sidebar bg, input bg, border, text primary/secondary
- ถ้าสลับ theme ไม่ได้ก็ข้าม

### 1.7 Agent Mode (ถ้ามี)

- มี agent mode toggle มั้ย, อยู่ตรงไหน
- กดแล้วหน้าตาเปลี่ยนยังไง
- tool execution panel หน้าตาเป็นยังไง

### 1.8 Interactive States

- Hover บน conversation item → screenshot
- Hover บน assistant message → screenshot action buttons
- กด copy → animation/feedback อะไร
- Typing indicator ตอนรอ response
- Streaming text animation

---

## Phase 2: Desktop Comparison Screenshots

เปิด SRAN Desktop app ด้วย:
```powershell
cd "C:\Users\Beat Beat\Desktop\ai-gateway\SranDesktop\src\SranDesktop.App"
& "$env:LOCALAPPDATA\Microsoft\dotnet\dotnet.exe" run
```

Screenshot ทุก state เดียวกับ Phase 1:
- Empty state
- Sidebar
- Composer
- Chat messages (ถ้า login ได้)
- Settings dialog

---

## Phase 3: Output — Detailed Diff Spec

สร้างไฟล์ `DESKTOP_WEB_PARITY_SPEC.md` ที่ `C:\Users\Beat Beat\Desktop\ai-gateway\docs\`

โครงสร้าง:

```markdown
# SRAN Desktop — Web Parity Spec v2

## ข้อมูลอ้างอิงจาก Web (จาก screenshot จริง)

### Color Tokens
| Token | Dark | Light |
|---|---|---|
| bg-primary | #xxx | #xxx |
| ... | | |

### Typography
| Element | Font | Size | Weight | Color |
|---|---|---|---|---|
| ... | | | | |

### Spacing
| Element | Value |
|---|---|
| ... | |

## Fixes (เรียงตาม priority)

### Fix 1: [ชื่อ]
**Web**: [อธิบาย + screenshot ref]
**Desktop ปัจจุบัน**: [อธิบาย]
**XAML Change**: [exact code]

### Fix 2: ...
(ทุก fix)

## Agentic Features เพิ่มเติม
(ดู Phase 4)
```

---

## Phase 4: Agentic Features — เพิ่มความเป็น Agent ให้ Desktop

นอกจาก visual parity แล้ว ให้เพิ่ม features เหล่านี้ใน spec:

### 4.1 Agent Mode Panel Enhancement
- เมื่อ toggle Agent Mode ON → แสดง panel ทันที (ไม่ต้องรอ tool activity)
- Panel แสดง: available tools list, permission status, last execution history
- Tool execution: แสดง real-time progress bar, elapsed time, expandable args/result

### 4.2 Tool Consent UX
- ดูว่า web มี tool consent dialog ยังไง → clone ให้เหมือน
- ถ้า web ไม่มี → design ให้ desktop: card-style consent พร้อม tool icon, description, args preview, "Allow once" / "Allow always" / "Deny" buttons

### 4.3 Thinking/Reasoning Block
- แสดง "Thinking..." block แบบ collapsible พร้อม duration timer
- เมื่อ expand: แสดง reasoning text stream
- เมื่อ complete: แสดง duration (e.g., "Thought for 3.2s")
- ดูว่า web ทำแบบไหน → clone

### 4.4 Search/Web Results Display
- ถ้า AI ค้นเว็บ → แสดง search results cards (title, url, snippet)
- ดูว่า web แสดงผล search results ยังไง → clone

### 4.5 Image Generation Display
- ถ้า AI generate image → แสดง image inline ใน chat
- ดูว่า web ทำยังไง (inline image, lightbox on click?)

### 4.6 File Upload UX Enhancement
- Drag & drop overlay (ดูว่า web มีมั้ย → clone)
- File type badges/icons
- Upload progress indicator
- Multiple file support visualization

### 4.7 Keyboard Shortcuts Panel
- Ctrl+/ หรือ ? → แสดง shortcuts overlay
- ดูว่า web มี shortcuts panel มั้ย

### 4.8 Conversation Branch Navigation
- ถ้า user edit message แล้ว regenerate → มี branch
- Web แสดง branch navigation ยังไง (< 1/3 > arrows?)
- Clone ให้ desktop

---

## Output Requirements

1. ไฟล์ `DESKTOP_WEB_PARITY_SPEC.md` ที่มี:
   - ทุก color token จาก web (extracted จาก screenshot / dev tools)
   - ทุก typography spec
   - ทุก spacing/sizing value
   - ทุก fix พร้อม XAML code ที่ exact พอจะ copy-paste ได้
   - Agentic features spec

2. Screenshot ทุกหน้าจอ save ไว้ที่ `SranDesktop/audit-screenshots/web/` และ `SranDesktop/audit-screenshots/desktop/`

3. สรุปจำนวน fixes ทั้งหมดและ estimated effort

---

## Tips

- ใช้ browser dev tools (F12) inspect element เพื่อดู exact CSS values (colors, sizes, fonts, spacing)
- ทุก color ควรเป็น hex ไม่ใช่ "looks like gray"
- ทุก size ควรเป็น px
- ถ้า web มี feature ที่ desktop ไม่มี → include ใน spec พร้อมคำอธิบายว่า desktop ควร implement ยังไง
- Focus on pixel-perfect parity — ผู้ใช้ต้องรู้สึกว่าเป็น app เดียวกัน

---

*End of prompt*

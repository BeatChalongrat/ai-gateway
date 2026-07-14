# Run SRAN Desktop & Screenshot ทุก UI State

> ให้ Claude Fable / GPT-5.5 ทำ พร้อม ai-gateway codebase attached

---

## Step 1: Build & Run

```bash
cd ai-gateway/SranDesktop/src
dotnet build SranDesktop.sln --configuration Debug
dotnet run --project SranDesktop.App
```

ถ้า build fail → ดู error แล้วแก้ก่อน run

---

## Step 2: Login

App จะแสดงหน้า Login — ใส่:
- **Email:** `somchai@acme.ai.gateway`
- **Password:** `password123`

(หรือ credentials ที่ใช้กับ web-chat — ลอง login ที่ localhost:3002/chat ก่อนเพื่อยืนยัน credentials)

ถ้า login ไม่ผ่าน ลองดู:
1. Backend ต้องรันอยู่: `http://localhost:4000/health` ต้องตอบ `{"status":"ok"}`
2. Gateway ต้องรันอยู่: `http://localhost:8080/health` ต้องตอบ ok
3. ถ้า backend ไม่รัน → `cd ai-gateway && docker compose up -d`

---

## Step 3: เปิด Conversation ที่มี Markdown Content

หลัง login → sidebar จะแสดง conversation list  
เลือก **"Sample Markdown Table Exchange"** หรือ conversation ที่มี table + bulleted list

ถ้าไม่มี → สร้างใหม่โดยพิมพ์:
```
Show a markdown table with 2 rows and a bulleted list of 3 items.
```

---

## Step 4: Screenshot ทุก State

บันทึก screenshot ไว้ที่ `SranDesktop/audit-screenshots/desktop/`:

### Dark Theme:
1. **`final5-chat-dark.png`** — หน้า chat ที่มี table + list (เห็น action buttons ด้วย)
2. **`final5-sidebar-dark.png`** — sidebar หลัง auto-cleanup (ไม่มี "New conversation" ว่างๆ)
3. **`final5-empty-dark.png`** — empty state (กด + New chat)

### Light Theme:
4. เปลี่ยน theme ใน Settings → Light
5. **`final5-chat-light.png`** — หน้า chat เดียวกัน
6. **`final5-sidebar-light.png`** — sidebar

---

## Step 5: Verification Checklist

ดูแต่ละ screenshot แล้ว verify:

1. [ ] **List bullets** — ใช้ "·" (middle dot เล็ก) ไม่ใช่ "•" (bullet ใหญ่)
2. [ ] **List item spacing** — items แน่น (gap ~4px) ไม่ spacious
3. [ ] **Table width** — auto-fit ตาม content (ไม่ stretch เต็ม column)
4. [ ] **Action button order** — Copy → ThumbUp → ThumbDown → Regenerate
5. [ ] **Action button gap** — ~2px ระหว่างปุ่ม
6. [ ] **Sidebar clean** — ไม่มี "New conversation" ว่างๆ ค้างอยู่
7. [ ] **Message spacing** — ~16px gap ระหว่าง messages
8. [ ] **Code block** — radius 12, border, shadow, font 14px
9. [ ] **Headings** — H1=24, H2=20, H3=17
10. [ ] **Paragraph spacing** — ~16px ระหว่าง paragraphs

ถ้ามี item ไหนไม่ผ่าน → บอกว่าอันไหน + ค่าที่เห็น vs ค่าที่ควรจะเป็น

---

*End of Run & Screenshot Prompt*

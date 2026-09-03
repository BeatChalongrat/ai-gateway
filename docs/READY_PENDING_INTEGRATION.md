# ทะเบียน "โค้ดพร้อม — รอต่อของนอก" (READY, PENDING INTEGRATION)

> FIX51 Part 2.4 · อัปเดตล่าสุด 2026-09-03
>
> เหตุผลที่มีไฟล์นี้: กติกาข้อ 2 ของ FIX48 — *"ไม่มีหลักฐาน = ให้ 0 แล้วเขียนว่ายืนยันไม่ได้"*
> ของที่โค้ดพร้อมแต่ยังไม่ได้เสียบของนอก ถ้าไม่จดหลักฐานไว้ จะถูกให้ 0 ในการตรวจรอบหน้า
> ทั้งที่ฝั่งเราเสร็จแล้ว — ทะเบียนนี้คือหลักฐานนั้น
>
> ราคาเป็นตัวเลขประมาณจากราคาสาธารณะ ณ ต้นปี 2026 — **ตรวจราคาจริงก่อนซื้อทุกครั้ง**

รูปแบบต่อรายการ: **ต่ออะไรถึงใช้ได้ · ต้องซื้ออะไร/ราคา · เสียบตรงไหนในโค้ด · เทส/หลักฐานที่พิสูจน์ว่าฝั่งเราพร้อม**

---

## 1. Web search (W5-1) — พร้อม 100% ยกเว้น key

- **ต่ออะไร:** API key ของ search engine อย่างน้อย 1 เจ้า (Tavily / Brave / SerpAPI / Google CSE / Bing — adapter เป็นกลาง เลือกเจ้าไหนก็ได้ด้วยการตั้งค่า ไม่ต้องแก้โค้ด)
- **ต้องซื้อ:** Tavily ~ฟรี 1,000 คำค้น/เดือน แล้ว ~$0.008/คำค้น · Brave Search API ~$3–5/1,000 คำค้น · Serper/SerpAPI ~$1–4/1,000 คำค้น
- **เสียบตรงไหน:** หน้า admin หรือ API — `PUT /admin/tenants/{tid}/search-config` (`search_enabled:true`) + `POST /admin/tenants/{tid}/search-providers` (`{"provider":"tavily","api_key":"..."}`) — key เข้ารหัส AES-256-GCM ที่ `backend/app/api/routes/admin_search.py` · adapter registry: `backend/app/services/search/providers.py`
- **หลักฐานว่าพร้อม:** วิ่งครบวงจริงด้วย mock engine (ไม่ใช้ key/เครดิต) ใน FIX51: tenant gate → `/v1/search` (คิดเครดิต/คำค้น) → เทิร์นจริงบน `deepseek-v4-pro` เรียก `web_search` → `search_sources` + เลขอ้างอิงบนข้อความ · เทส: `backend/tests/test_search_mock_engine.py`, `backend/tests/test_search_egress.py` · เมื่อไม่มี key: เครื่องมือไม่ถูกเสนอ ไม่มีปุ่มค้าง (กฎ FIX34.1)

## 2. Video generation (W3-9) — โค้ดครบ ปิดที่ gate + key

- **ต่ออะไร:** key ของโมเดลวิดีโอ (OpenAI Sora API หรือ Replicate) + เปิด `video_gen_enabled`
- **ต้องซื้อ:** Sora API ~$0.10–0.50/วินาทีวิดีโอ (แล้วแต่รุ่น/ความละเอียด) · Replicate (kling/wan) ~$0.05–0.3/วินาที
- **เสียบตรงไหน:** provider key ผ่าน admin (ตาราง `provider_keys`) + flag `video_gen_enabled` ใน settings — gate 4 ชั้นอ่านที่ `backend/app/api/routes/public_videos.py:74-107` (`GET /v1/video/config` ตอบ `enabled` ให้ client ตัดสินใจโชว์ปุ่ม)
- **หลักฐานว่าพร้อม:** `backend/tests/test_video_config_capability.py` · FIX11 สร้าง endpoint, FIX10-survey บันทึก "present-but-OFF" · แคตตาล็อกมี `sora-2`, `mock-video-1`, `kwaivgi/kling-v1.5-pro`, `wan-video/wan-2.5` แล้ว

## 3. เครดิต OpenRouter / OpenAI — จ่ายแล้วปลดล็อกการยืนยันที่ค้าง

- **ต่ออะไร:** เติมเงินบัญชี OpenRouter และ/หรือ OpenAI (key มีอยู่แล้ว ใช้งานได้ทันทีเมื่อมียอด)
- **ต้องซื้อ:** ตามการใช้ (เติมขั้นต่ำ ~$5–10) — ตอนนี้ **DeepSeek เป็นบัญชีเดียวที่มีเงิน**
- **เสียบตรงไหน:** ไม่ต้องแตะโค้ด — key เดิมใน `provider_keys` ใช้ต่อได้เลย
- **หลักฐานว่าพร้อม:** FIX51 พิสูจน์แล้วว่าเมื่อบัญชีหนึ่งตาย ระบบข้ามบัญชีเองได้ (402 จริงจาก OpenRouter → คำตอบจาก deepseek-v4-pro) · รายการที่รอเครดิตเพื่อยืนยัน: fallback-success UI, ปุ่ม retry 402, FIX49 Part D measurement gate, 8b/#56 re-run, terra answering (จดไว้ใน FIX49/FIX50 REPORT)

## 4. อีเมลธุรกรรม — Resend

- **ต่ออะไร:** `RESEND_API_KEY` (บังคับใน production — `validate_secrets` จะไม่ยอมบูตถ้าไม่มี)
- **ต้องซื้อ:** Resend ฟรี 3,000 อีเมล/เดือน · จากนั้น ~$20/เดือน (50k)
- **เสียบตรงไหน:** env `RESEND_API_KEY` — `backend/app/core/config.py:609-613`
- **หลักฐานว่าพร้อม:** `backend/tests/test_email_sender.py`, `test_email_templates.py`, `test_email_verification.py`, `test_verification_email_wiring.py`

## 5. Sran AI on-prem box (provider `sranai`)

- **ต่ออะไร:** ใส่ endpoint จริงของกล่อง (ตอนนี้ค่าว่าง — รอฮาร์ดแวร์)
- **ต้องซื้อ:** ตัวกล่อง/เซิร์ฟเวอร์ inference ภายใน (ราคาตามสเปกที่จัดซื้อ)
- **เสียบตรงไหน:** base URL ของ provider `sranai` ใน admin providers + `model_discovery_auto_add_providers` ครอบ `sranai` แล้ว (`backend/app/core/config.py:508`)
- **หลักฐานว่าพร้อม:** `backend/tests/test_sranai_provider.py`, `test_sranai_discovery.py` (merged รอบ Sran AI provider P1) · mock ที่วิ่งอยู่ทุกวันนี้ก็คือ adapter ตัวเดียวกันชี้ไปกล่อง mock

## 6. ใบรับรอง code-signing สำหรับ desktop installer

- **ต่ออะไร:** เซ็น `SRAN Desktop Setup *.exe` (ตอนนี้ unsigned — SmartScreen เตือนทุกเครื่อง)
- **ต้องซื้อ:** OV certificate ~$100–400/ปี · EV (SmartScreen หายเร็วกว่า) ~$300–600/ปี (Sectigo/DigiCert/SSL.com)
- **เสียบตรงไหน:** electron-builder config ใน `apps/desktop/package.json` (`win.certificateFile`/`signtool`) — โครง NSIS จาก FIX19 พร้อมแล้ว
- **หลักฐานว่าพร้อม:** installer สร้าง+ติดตั้งเงียบได้จริงทุกรอบ (e21→e23 ในสามรอบล่าสุด) — ขาดแค่ลายเซ็น

## 7. gVisor (runsc) บนโฮสต์ deploy — sandbox ชั้น VM

- **ต่ออะไร:** ติดตั้ง runsc บนโฮสต์ Linux ของ prod แล้วตั้ง `SANDBOX_RUNTIME=runsc`
- **ต้องซื้อ:** ไม่มีค่า license (โอเพนซอร์ส) — ต้องมีโฮสต์ Linux
- **เสียบตรงไหน:** `services/chat-edge-api/internal/config/config.go:199-202` + สคริปต์ `FIX17` (`DEPLOY_SANDBOX.md`, runsc scripts)
- **หลักฐานว่าพร้อม:** FIX14 refuse-if-absent (ตั้ง runsc แล้วไม่มีจริง → ปฏิเสธรัน ไม่ silently ตกไป runc) + FIX17 deploy kit · เครื่อง dev (Windows) ยังเป็น runc — ตามบันทึก

## 8. Desktop connectors (Google Drive/Notion/ฯลฯ) — OAuth clients

- **ต่ออะไร:** จดทะเบียน OAuth client ต่อ provider แล้ววางไฟล์ registration (`"enabled": true`)
- **ต้องซื้อ:** ฟรี (สมัคร developer app ของแต่ละเจ้า)
- **เสียบตรงไหน:** `apps/desktop/src/main/connector-oauth.ts` (registration file หนึ่งไฟล์จากเจ้าของ — ไม่มีไฟล์ = "not configured" ทุกตัว) + `connector-mcp.ts`
- **หลักฐานว่าพร้อม:** FIX31 สร้าง oauth_dcr core + FIX7-track UI · ปิดอยู่โดย design จนกว่าเจ้าของจะวางไฟล์

## 9. TTS / เสียง — ElevenLabs

- **ต่ออะไร:** `ELEVENLABS_API_KEY` (แคตตาล็อกมี `eleven_multilingual_v2` แล้ว)
- **ต้องซื้อ:** ~$5–22/เดือน ตามชั่วโมงเสียง
- **เสียบตรงไหน:** provider key ใน admin — เส้นทาง voice ผ่าน gateway ตั้งแต่ FIX2 (embeddings+voice egress, migration 065)
- **หลักฐานว่าพร้อม:** เทสชุด voice egress ของ FIX2 · โมเดลปรากฏในแคตตาล็อกและถูก gate ตาม key เหมือน provider อื่น

---

## ออกจากทะเบียนรอบนี้ (ไม่ค้างแล้ว)

- **Scheduled tasks (W5-7)** — FIX51 Part 2.1 เปิดใช้จริงแล้ว: ประตูอนุญาต (`/internal/scheduled-run/authorize`) ยืนยันสด, วงจร สร้าง→ยิง→เห็นในแชท→ยกเลิก ครบ, UI web+desktop แสดงงาน — ไม่ต้องซื้ออะไร
- **DeepSeek v4 tools flags (OD #47)** — migration 082 แก้ให้ตรง probe แล้ว เทิร์นเครื่องมือจริงวิ่งแล้วบน `deepseek-v4-pro`

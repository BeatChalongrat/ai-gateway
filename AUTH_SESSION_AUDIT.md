# AUTH_SESSION_AUDIT.md
**Auth/Session Audit — Frequent Logout Investigation**
วันที่: 2026-05-30 · ขอบเขต: audit only (ไม่มีการแก้ code) · ผู้ตรวจ: senior FE+BE engineer

---

## 0. Scope & Method

ตรวจ auth-related code ทั้ง 5 tiers:

| Tier | Path | Port |
|------|------|------|
| Admin UI (Next.js) | `admin-ui/` | 3000 |
| Portal (Next.js) | `portal/` | 3001 |
| Web Chat (Next.js) | `apps/web-chat/` | 3002 |
| Backend (FastAPI / gateway-api) | `backend/` | 8000 (compose expose :8080) |
| Chat Edge API (Go) | `services/chat-edge-api/` | 4000 |

**ข้อค้นพบเชิงสถาปัตยกรรมที่สำคัญที่สุดก่อนเข้ารายละเอียด:** ระบบมี **auth domain แยกกัน 2 โลกที่ไม่แชร์ session กัน**

1. **Admin domain** (3000 → backend :8080): HttpOnly `admin_session` cookie (itsdangerous HMAC, **ไม่ใช่ JWT**) — แยกขาดสมบูรณ์ ไม่เกี่ยวกับ bug ที่รายงาน
2. **Portal+Chat domain** (3001 + 3002 → chat-edge :4000): แชร์ **Portal JWT** ตัวเดียวกันผ่าน cross-port cookie บน `localhost` + การส่ง `?token=` ผ่าน URL

> URL ที่รายงาน `localhost:3001/login?redirect=http%3A%2F%2Flocalhost%3A3002%2Fchat` ถูกสร้างขึ้น **ตรงตัว** ที่ `apps/web-chat/src/lib/auth.ts:63-64` (`redirectToPortalLogin()`) → เป็นปัญหาในแกน **Portal+Chat** ล้วนๆ ไม่เกี่ยวกับ Admin

---

## 1. JWT Inventory Table

ระบบมี token/session **5 แบบ** ที่แตกต่างกัน (ไม่นับ email-verify / password-reset / Google OAuth-verify). นี่คือตัวที่เกี่ยวกับ session:

| Property | **Portal JWT** (portal+chat) | **Admin legacy JWT** (เกือบตาย) | **Admin session cookie** (live admin) | **chat-edge user JWT** (DEAD) | **chat-edge service JWT** (svc→svc) |
|---|---|---|---|---|---|
| **Created at (file:line)** | `backend/app/services/portal/auth_service.py:48` (`_mint_token`) | `backend/app/api/routes/auth.py:62` | `backend/app/services/admin/session_manager.py:79` | `services/chat-edge-api/internal/auth/jwt.go:40` | `services/chat-edge-api/internal/auth/service_jwt.go:36` |
| **Algorithm** | HS256 (`auth_service.py:31`) | HS256 (`auth.py:59`) | itsdangerous HMAC (ไม่ใช่ JWT) | HS256 | HS256 |
| **Secret source (env)** | `PORTAL_JWT_SECRET` (`config.py:48`) | `JWT_SECRET` (`config.py:42`) | `SESSION_SIGNING_SECRETS` (`config.py:57`) | `USER_JWT_SECRET` (`config.go:50`) | `SERVICE_JWT_SECRET` (`config.go:51`) |
| **Hardcoded fallback? ⚠️** | **YES** `"change-me-portal-secret"` `config.py:48` | **YES** `"change-me"` `config.py:42` | **YES** `"change-me-session-signing"` `config.py:57` | **YES** `dev-user-jwt-secret-change-in-prod` (.env/compose) | **YES** `dev-service-jwt-secret-change-in-prod` |
| **Access token expiry** | **60 นาที** ⭐ (`config.py:49` `portal_access_token_expire_minutes=60`; applied `auth_service.py:58-61`) | 1440 นาที / 24h (`config.py:44`) | 24h idle (sliding) / 7d absolute (`config.py:59-60`) | 1h (`config.go:57`, dead path) | 60 วินาที (`config.go:62`) |
| **Refresh token?** | มี — 7 วัน (`config.py:50`); endpoint `POST /portal/auth/refresh` (`portal_auth.py:91`) | มี — 30 วัน; `POST /auth/refresh` (`auth.py:132`) | sliding idle TTL (ไม่มี refresh token แยก) | มี (opaque, dead) | re-mint ทุก request |
| **Refresh expiry / rotation** | 7d, **ไม่ rotate** (refresh คืนแค่ access token ใหม่ — `auth_service.py:447`) | 30d, **rotate** (`auth.py:148-149`) | sliding 24h, cap 7d (`session_manager.py:143-151`) | 168h opaque | n/a |
| **Payload fields** | `sub, email, type="portal", token_use, iat, exp, jti, tenant_id, tenant_role, token_version` (`auth_service.py:66-84`) | `sub, email, username, role, org_id, type, iat, exp, jti` (`auth.py:66-76`) | `session_id, user_id, tenant_id, role, ip, user_agent, created_at, csrf_token` (`session_manager.py:90-98`) | `sub, tenant_id, role, iss, aud, iat, exp` | `iss, aud, iat, exp` |
| **JWT library** | PyJWT (`auth_service.py:17`) | PyJWT (`auth.py:12`) | itsdangerous `URLSafeSerializer` (`session_manager.py:30`) | `golang-jwt/jwt/v5` | `golang-jwt/jwt/v5` |
| **Verified by** | `get_portal_user` dep (`deps.py:272-329`); Go `ParsePortalToken` (`portal_jwt.go:63`) | `decode_jwt` (`auth.py:94`); `deps._resolve_jwt_token` (`deps.py:49`) | `get_admin_session` (`admin_auth.py:69`) | **dead** (`RequireAuth` ใช้ Portal JWT แทน) | `gateway_client.go:539` |
| **Login endpoint** | `POST /portal/auth/login` → **JSON body** (ไม่ set cookie) (`portal_auth.py:82`) | `POST /auth/login` → **410 Gone** (ปิดแล้ว) (`auth.py:109`) | `POST /auth/session/login` → **Set-Cookie** (`admin_session.py:168`) | **410 Gone** (`handler/auth.go:51`) | n/a |
| **Logout** | `POST /portal/auth/logout` → **no-op stub** (`portal_auth.py:201`) | none (stateless) | `POST /auth/session/logout` → **revoke จริง** (`admin_session.py:269`) | 410 Gone | n/a |

⭐ = ค่าที่น่าจะเป็นสาเหตุหลัก   ⚠️ = security flag (ดู §8)

**สถานะ chat-edge (Go):** ตั้งแต่ "Phase 1.5" Go service **ไม่ mint user token เอง** — endpoint Register/Login/Refresh/Logout คืน **410 Gone** ทั้งหมด (`handler/auth.go:51-65`). Middleware `RequireAuth` (`middleware/auth.go:85-96`) **verify Portal JWT ตัวเดียวกับ FastAPI** โดยใช้ env `PORTAL_JWT_SECRET` ตัวเดียวกัน (`config.go:53`). Go side **ไม่ enforce `token_version`** (`portal_jwt.go:17-22`) — อ่านแต่ไม่ตรวจ.

---

## 2. Token Storage Table (Frontend)

| Property | **Admin (3000)** | **Portal (3001)** | **Chat (3002)** |
|---|---|---|---|
| **Storage method** | HttpOnly cookie `admin_session` (backend-set) + localStorage `admin_session_cache` (role cache เท่านั้น ไม่ใช่ credential) | localStorage `portal_access_token` + `portal_refresh_token` **(source of truth)** + cookie mirror | cookie `portal_access_token` **(source of truth)** + in-memory Zustand (derived) |
| **Set by** | backend `Set-Cookie` | frontend JS `document.cookie` + `localStorage` (`lib/auth.ts:48-65`) | frontend JS `document.cookie` (`auth.ts:33-38`) |
| **Cookie flags** | HttpOnly, SameSite/Secure/Max-Age = **backend-controlled** (มองไม่เห็นใน FE); `csrf_token` = non-HttpOnly | `Path=/; Max-Age=3600; SameSite=Lax` — **ไม่มี HttpOnly, ไม่มี Secure, ไม่มี Domain** (`lib/auth.ts:45`) | `Path=/; Max-Age=3600; SameSite=Lax` — **ไม่มี HttpOnly, ไม่มี Secure, ไม่มี Domain** (`auth.ts:36`) |
| **Key/cookie name** | `admin_session` (HttpOnly), `csrf_token` (JS-readable), `admin_session_cache` (LS) | `portal_access_token` (LS+cookie), `portal_refresh_token` (LS), `portal_logged_in` (marker cookie) | `portal_access_token` (cookie) |
| **Survives refresh?** | ✅ (backend cookie) | ✅ (localStorage + cookie) | ✅ (cookie จนกว่า Max-Age หมด) |
| **Survives tab close?** | ขึ้นกับ backend Max-Age (มองไม่เห็นใน FE) | ✅ localStorage ถาวร; cookie หมดอายุ 1h หลัง write ครั้งสุดท้าย | ✅ cookie หมดอายุ 1h หลัง write |
| **Shared cross-port?** | ❌ scoped กับ origin backend :8080 — ไม่แชร์กับ 3001/3002 | ⚠️ cookie ไม่มี `Domain` = host-only → บน `localhost` แชร์ข้าม port โดยปริยาย (`portal_access_token` ที่ :3001 set มองเห็นจาก :3002) | ⚠️ อ่าน cookie ที่ portal set บน `localhost` (cross-port) + รับผ่าน `?token=` |

**กลไก cookie ของ Portal (สำคัญ):** `lib/auth.ts:48-58` เขียน **2 cookies**:
- `portal_logged_in=1` (marker จิ๋ว — เพื่อให้ Edge middleware ใช้ gate โดยไม่ parse JWT)
- `portal_access_token=<JWT เต็ม>` ("best-effort" — comment ที่ `lib/auth.ts:15-23` ระบุว่า **JWT อาจ > 4KB และ browser จะ drop เงียบๆ**)

Portal middleware (`portal/middleware.ts:33`) เช็ค **แค่ marker `portal_logged_in` มีอยู่หรือไม่** — ไม่ parse JWT, ไม่ตรวจ expiry, ไม่ตรวจ signature.

---

## 3. Session Lifecycle Diagram (Portal + Chat แกนที่มีปัญหา)

```
[Happy path — login แล้วเข้า Chat]
User เปิด Chat :3002/chat
  └─ web-chat/src/app/chat/layout.tsx:42  getPortalToken()
       ├─ อ่าน cookie portal_access_token (cross-port จาก localhost)
       └─ ถ้าไม่มี → redirectToPortalLogin()  (auth.ts:61-65)
              window.location = http://localhost:3001/login?redirect=http://localhost:3002/chat   ← URL ที่รายงาน

User ลง login ที่ Portal :3001/login
  └─ portal/.../LoginForm.tsx:88  POST /api/portal/auth/login
       └─ backend auth_service._mint_token (auth_service.py:48)  exp = +60 นาที
       └─ คืน JSON {access_token, refresh_token}  (ไม่ set cookie ฝั่ง backend)
  └─ setTokens()  (portal lib/auth.ts:60)
       ├─ localStorage[portal_access_token / portal_refresh_token]
       ├─ cookie portal_logged_in=1        Max-Age=3600
       └─ cookie portal_access_token=<JWT> Max-Age=3600   (อาจถูก browser drop ถ้า >4KB)
  └─ เพราะ redirect = web-chat origin → LoginForm.tsx:101-104 ต่อ ?token=<JWT> ใน URL
       window.location = http://localhost:3002/chat?token=<JWT>

User กลับมา Chat :3002/chat?token=...
  └─ getPortalToken() (auth.ts:30-41) อ่าน ?token= → เขียน cookie portal_access_token Max-Age=3600
       → ลบ ?token= ออกจาก URL ด้วย history.replaceState
  └─ Chat เรียก chat-edge :4000 ด้วย Authorization: Bearer <JWT>  (api.ts:49-52)
       chat-edge RequireAuth → ParsePortalToken (portal_jwt.go:63) verify HS256 + exp

[เวลาผ่านไป ~60 นาที — จุดที่เกิด logout]
T+60min: Portal JWT exp ครบ  (และ cookie Max-Age=3600 ก็ครบพอดี)
  └─ Chat ไม่มี refresh ใดๆ เลย (api.ts:4-5,59-65 "refresh เป็นงานของ Portal")
  └─ request ถัดไป / SSE reconnect → chat-edge ตอบ 401 (auth.go:97-100)
  └─ web-chat api.ts:59-65  on 401:
       clearPortalToken() → redirectToPortalLogin()
       → http://localhost:3001/login?redirect=http://localhost:3002/chat   ← loop กลับมาที่เดิม

[ทางขนาน: cookie หมดก่อน token — ขณะ idle]
User เปิด Chat tab ทิ้งไว้เฉยๆ > 1 ชม. (ไม่มี API call)
  └─ cookie portal_access_token Max-Age=3600 หมด (fixed wall-clock จาก write ครั้งสุดท้าย)
  └─ navigation ถัดไป → getPortalToken()=null → redirectToPortalLogin()  (แม้ token จริงอาจยัง valid)
```

```
[Admin — แยกขาด ไม่เกี่ยวกับ loop ข้างบน]
User :3000/login → POST /auth/session/login (credentials:include)
  └─ backend Set-Cookie admin_session (HttpOnly) + csrf_token
middleware.ts:24 เช็คแค่ cookie admin_session "มีอยู่" → ไม่ตรวจ expiry
ทุก API call ส่ง credentials:include + X-CSRF-Token
backend session หมดอายุ / ตอบ 401 → lib/api.ts:93-95 clearInvalidSession() → /login
  (ไม่มี refresh ใดๆ — session อายุ = backend cookie TTL ตรงๆ)
```

---

## 4. Root Cause Analysis (เรียงตาม likelihood)

### สาเหตุที่ 1 — Access token อายุ 60 นาที + Chat ไม่มี refresh mechanism ใดๆ เลย ⭐ (น่าจะเป็นตัวหลัก)
- **Evidence:** `backend/app/core/config.py:49` `portal_access_token_expire_minutes = 60`. ฝั่ง Chat: `apps/web-chat/src/lib/api.ts:4-5,59-65` — comment ระบุชัด "refresh เป็นงานของ Portal" และ 401 handler ทำแค่ `clearPortalToken()` + `redirectToPortalLogin()` **ไม่มี silent refresh, ไม่มี timer, ไม่มี retry, ไม่ตรวจ `exp` ล่วงหน้า** (`decodePortalJwt` ที่ `auth.ts:77` ถูก decode แต่ไม่เคยเอามาใช้ gate).
- **ผล:** ผู้ใช้ที่อยู่ใน Chat อย่างเดียวจะถูกเด้งออก **ทุกๆ ~60 นาทีเป็นอย่างมาก** และทันทีถ้าเจอ 401 ใดๆ. chat-edge เองก็ไม่มี refresh (Go endpoint เป็น 410 Gone — `handler/auth.go:51`), refresh ต้องวิ่งกลับ Portal เท่านั้น ซึ่ง Chat ไม่ทำ.

### สาเหตุที่ 2 — Cookie `Max-Age=3600` fixed wall-clock หลุดจาก token validity + renew เฉพาะตอน login/portal-refresh
- **Evidence:** `portal/lib/auth.ts:45` และ `apps/web-chat/src/lib/auth.ts:36` ทั้งคู่ใช้ `Max-Age=3600` (นับจากเวลาที่ write ครั้งสุดท้าย ไม่ผูกกับ `exp` จริงของ JWT). Chat เขียน cookie ครั้งเดียวตอนรับ `?token=` แล้วไม่เคย renew อีก (Chat ไม่มี refresh). Portal renew cookie เฉพาะตอน `setTokens`/`setAccessToken` (login หรือ reactive refresh — `lib/api.ts:50`).
- **ผล:** Tab Chat ที่เปิด idle ทิ้งไว้ > 1 ชม. → cookie หาย → `getPortalToken()=null` → เด้งไป portal login **แม้ token จริงในมืออาจยัง valid**. Portal middleware ก็พึ่ง marker cookie `portal_logged_in` ที่หมดอายุแบบเดียวกัน (`portal/middleware.ts:33,64-68`).

### สาเหตุที่ 3 — JWT เกิน 4KB → browser drop cookie เงียบๆ → เด้งทุก navigation
- **Evidence:** comment ที่ `portal/lib/auth.ts:15-23` และ `portal/middleware.ts:11-16` ยอมรับเองว่า "JWTs are routinely >4 KB once tenancy + role + version claims are populated, and browsers silently drop oversize cookies". Payload Portal JWT มี 10 claims รวม `tenant_id, tenant_role, token_version, jti, email` (`auth_service.py:66-84`).
- **ผล:** ถ้า JWT จริง > 4KB, cookie `portal_access_token` ที่ Chat เขียนจะถูก drop → `?token=` ใช้ได้แค่ page load แรก (token อยู่ใน memory) แต่ navigation ถัดไปใน Chat `getPortalToken()=null` → เด้งทันที. กรณีนี้จะให้อาการ **เด้งบ่อยมากกว่ารายชั่วโมง** (เด้งแทบทุกครั้งที่เปลี่ยนหน้า). *ต้องวัดขนาด JWT จริงเพื่อยืนยัน — likelihood ขึ้นกับขนาด secret/claims.*

### สาเหตุที่ 4 — `token_version` mismatch = silent instant logout (เฉพาะแกน Portal)
- **Evidence:** `backend/app/api/deps.py:317-327` — `get_portal_user` reject token ถ้า `token_version` ใน claim ≠ ค่าใน DB → 401 `token_version_stale`. ค่านี้ถูก bump ตอน password reset (`password_reset_service.py:262`) และเคย bump ครั้งเดียวโดย migration 029. Token ที่ไม่มี version claim = version 0 → fail หมดหลัง migration.
- **ผล:** การ reset password ใดๆ (หรือ background process ที่ bump version) จะ invalidate ทุก Portal access token ของ user นั้น**ทันทีและเงียบ**. หมายเหตุ asymmetry: **chat-edge ฝั่ง Go ไม่ enforce token_version** (`portal_jwt.go:17-22`) → 401 นี้จะเกิดเฉพาะ API call ที่วิ่งเข้า FastAPI ไม่ใช่ผ่าน chat-edge.

### สาเหตุที่ 5 — 401 ใดๆ (รวม transient/network) = hard logout ทันที ไม่มี retry ทุก app
- **Evidence:** Chat `api.ts:59-65`, Portal `api.ts:100-108` (มี refresh-then-retry รอบเดียว แต่ถ้า refresh fail → `clearTokens()` + `window.location.href="/login"`), Admin `lib/api.ts:93-95` (ไม่มี retry เลย).
- **ผล:** backend สะอึกชั่วขณะ / clock skew / network blip → 401 → ผู้ใช้หลุด session ทันที. Chat ยัง suppress toast สำหรับ 401 ระหว่าง stream (`chatStore.ts:1157`) → ผู้ใช้โดนเด้งกลางบทสนทนาแบบไม่มีคำอธิบาย.

### สาเหตุที่ 6 — Admin: ไม่มี renewal เลย + middleware presence-only (แกน Admin แยก)
- **Evidence:** `admin-ui/` ไม่มี refresh/silent/timer ใดๆ (`refreshSession` ที่ `lib/auth.ts:31-55` แค่ revalidate role ผ่าน `/auth/session/me` ไม่ต่ออายุ). `middleware.ts:24-27` เช็คแค่ cookie `admin_session` มีอยู่ — ไม่ตรวจ expiry → cookie หมดอายุแต่ยังอยู่ → render หน้าได้ แล้ว API call แรก 401 → เด้ง `/login` (อาการ "จู่ๆ หลุด").
- **ผล:** Admin session อายุ = backend cookie TTL ตรงๆ (sliding 24h idle / cap 7d ตาม `config.py:59-60`). ไม่น่าทำให้ "หลุดบ่อย" เท่าแกน Portal/Chat แต่ contribute อาการ logout-on-navigation ถ้า backend cookie เป็น session-scoped.

---

## 5. Severity Assessment

| # | สาเหตุ | Severity | เหตุผล |
|---|--------|----------|--------|
| 1 | Token 60 นาที + Chat ไม่มี refresh | **CRITICAL** | ทำให้เกิด logout เป็นรอบแน่นอน อย่างน้อยรายชั่วโมง — ตรงกับอาการที่รายงานมากที่สุด กระทบทุกผู้ใช้ Chat |
| 2 | Cookie Max-Age fixed + ไม่ renew (Chat) | **CRITICAL** | สร้าง URL ที่รายงานตรงตัว เด้งจาก idle แม้ token ยัง valid; รวมร่างกับ #1 |
| 3 | JWT >4KB drop cookie เงียบ | **HIGH** (conditional) | ถ้าเกิดจริงจะเด้งถี่กว่ารายชั่วโมงมาก — ต้องวัดขนาด JWT ยืนยัน |
| 4 | token_version stale silent logout | **HIGH** | เด้งทันทีทุกการ reset password; เป็น design intent แต่ surprise ผู้ใช้ |
| 5 | 401 ใดๆ = hard logout ไม่ retry | **HIGH** | network blip เดียวหลุด session; ขยายความถี่ของทุกสาเหตุข้างบน |
| 6 | Admin no-renewal + presence-only mw | **MEDIUM** | แยกแกน ไม่ใช่อาการที่รายงาน แต่เป็น UX เดียวกันบน Admin |

---

## 6. ข้อสังเกตอื่นๆ (security / best-practice)

1. **⚠️ Hardcoded secret fallbacks ทุกระบบ HS256/HMAC** — `config.py:42` `"change-me"`, `:48` `"change-me-portal-secret"`, `:57` `"change-me-session-signing"`; ฝั่ง Go `.env.example:15-18` + `docker-compose.yml:256` ใช้ `:-dev-stable-portal-key-change-in-prod` เป็น default. Python มี `validate_secrets()` (`config.py:398`) reject ใน prod แต่ **Go ไม่ตรวจความแข็งแรงของ secret** (แค่ required). ถ้า deploy ลืม set env → รันด้วย public dev key เงียบๆ. *Flag เท่านั้น ไม่แก้.*
2. **Portal/Chat token เก็บแบบ non-HttpOnly** (localStorage + JS cookie) → XSS exfiltrate ได้ทั้ง access + refresh token (`portal/lib/auth.ts:45` ไม่มี HttpOnly/Secure; `web-chat/auth.ts:36` เช่นกัน). ตรงข้ามกับ Admin ที่ใช้ HttpOnly อย่างถูกต้อง.
3. **ไม่มี Secure flag** บน portal/chat cookies → ส่งผ่าน HTTP ได้ (โอเคบน localhost แต่เป็นช่องโหว่บน prod). `web-chat/internal config COOKIE_SECURE default false` (`config.go:67`).
4. **Token ส่งผ่าน URL `?token=<JWT>`** (`LoginForm.tsx:101-104`, `dashboard/page.tsx`) → JWT ไปโผล่ใน browser history, server logs, Referer header ก่อนถูก `replaceState` ลบ. เป็น token-leak surface.
5. **Portal `/logout` เป็น no-op** (`portal_auth.py:201-206`) — ไม่ revoke server-side; access token ที่ "logout" แล้วยังใช้ได้จนครบ 60 นาที. (ตรงข้าม Admin logout ที่ revoke จริง — `admin_session.py:269`.)
6. **Portal refresh ไม่ rotate** (`auth_service.py:447`) — เพดาน session แข็ง = refresh token 7 วัน; refresh token รั่วถูกใช้ซ้ำได้ตลอด 7 วัน.
7. **ไม่มี unified auth state (AuthContext/useAuth)** ในทั้ง 3 frontend — สถานะ logged-in ถูกคำนวณกระจัดกระจาย (middleware ดู cookie, API layer ดู localStorage, component ดู 401). ทั้ง 3 layer **disagree กันได้** → พฤติกรรม logout ไม่สม่ำเสมอ.
8. **chat-edge ไม่มี WriteTimeout/IdleTimeout** บน http.Server (`cmd/server/main.go:332-336` set แค่ `ReadHeaderTimeout:10s`) — SSE stream ที่กำลังไหลอยู่ **ไม่ถูกฆ่ากลางทาง**เมื่อ token หมดอายุ (verify ครั้งเดียวตอน connect). แต่ EventSource reconnect / request ถัดไปจะ 401 ทันทีที่ token ครบ 60 นาที.
9. **Portal middleware strip `?redirect=`** เมื่อ logged-in user (marker cookie ยังอยู่) เข้า `/login` (`portal/middleware.ts:56-61`) → ถ้า marker ยังอยู่แต่ token หมด, redirect target ไป Chat จะถูกทิ้ง → ผู้ใช้ถูกโยนไป `/dashboard` แทนที่จะกลับ Chat (ขยายความรำคาญของ loop).
10. **Admin login ไม่ preserve `?redirect=`** (`admin-ui/login/page.tsx` push ไป `/` เสมอ) — re-login ทำให้ผู้ใช้เสียหน้าที่ค้างอยู่.

---

## 7. ไฟล์อ้างอิงหลัก

**Backend (FastAPI):** `app/services/portal/auth_service.py` (mint/verify, 60-min), `app/core/config.py:48-60` (expiry+secrets), `app/api/deps.py:272-329` (`get_portal_user`, token_version), `app/api/routes/admin_session.py` (admin session cookie), `app/api/routes/portal_auth.py` (login/refresh/logout).
**Portal (3001):** `lib/auth.ts` (cookie+localStorage), `lib/api.ts` (401 refresh), `middleware.ts` (marker gate), `app/(auth)/login/_components/LoginForm.tsx` (`?token=` handoff).
**Chat (3002):** `src/lib/auth.ts` (token source + `redirectToPortalLogin`), `src/lib/api.ts` (Bearer + 401 bounce), `src/app/chat/layout.tsx` (client guard), `src/stores/chatStore.ts` (SSE 401).
**Admin (3000):** `middleware.ts` (presence-only), `lib/api.ts` (CSRF + 401 logout), `lib/auth.ts` (`refreshSession` = read-only).
**Chat Edge (Go):** `internal/middleware/auth.go` (`RequireAuth` + token extract), `internal/auth/portal_jwt.go` (verify), `internal/config/config.go:53` (shared `PORTAL_JWT_SECRET`), `internal/handler/auth.go:51` (410 Gone).

---

*Audit only. ไม่มีการแก้ไข code ใดๆ. ไม่มีการเสนอ solution ตามที่กำหนด — Beat ตัดสินใจ approach เอง.*

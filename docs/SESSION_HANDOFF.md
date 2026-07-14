# SRAN Desktop — Session Handoff

> Last updated: 2026-07-12
> Round: 12 (Login Screen Polish)
> Total rounds completed: R1–R12

---

## Project Overview

SRAN Desktop เป็น .NET 8 WPF AI chat client ที่ต้องการทำให้เทียบเท่า Claude Desktop โดยใช้ workflow: **Opus วิจัย/audit codebase → เขียน build prompt → ส่งให้ Claude Fable implement → Beat รายงานผล → iterate**

## What Was Done This Session

### ก่อน context compaction (Rounds 9–12 initial)

| Round | Prompt File | Status | Summary |
|-------|-------------|--------|---------|
| R9 | `DESKTOP_ROUND9_CLAUDE_PARITY_MEGA_PROMPT.md` | Implemented (148/148 tests) | 12 features across 3 phases — Artifacts (WebView2), Projects, Web Search, Vision, Tool UI, Branching, Scheduled Tasks, Skills, MCP Marketplace, Voice I/O, Share, Writing Style |
| R10 | `DESKTOP_ROUND10_DEPLOY_READY_PROMPT.md` | Implemented (161/161 tests) | McpRemoteClient (JSON-RPC 2.0), ISpeechService abstraction (Windows + Azure th-TH), Branch persistence SQLite |
| R11 | `DESKTOP_ROUND11_PRODUCTION_DEPLOY_PROMPT.md` | Implemented | Auto-update (UpdateService, SHA-256, Inno Setup silent), FirstRunDialog improvements, crash reporting, merge docker stacks, MCP gateway, nginx unified reverse proxy, `make prod-desktop` |
| R12 | `DESKTOP_ROUND12_PREMIUM_LOGIN_PROMPT.md` | Implemented (191/191 tests) | Premium login screen — split-screen with branding panel, OAuth, server status, entrance animation, session persistence |

### หลัง context compaction (Login simplification — session นี้)

User rejected the complex split-screen login. ทำ iterative fix 4 รอบ:

| Fix | Prompt File | Status | Changes |
|-----|-------------|--------|---------|
| Fix 1 | `DESKTOP_ROUND12_LOGIN_FIX_PROMPT.md` | **Sent to Fable, implemented** | ลบ branding panel / OAuth / sign-up / server status ทั้งหมด → centered layout, ใช้ LogoSource (แมว), เหลือแค่ logo + email + password + sign in + forgot password |
| Fix 2 | `DESKTOP_ROUND12_LOGIN_FIX2_PROMPT.md` | **Sent to Fable, implemented** | เพิ่ม card border (CornerRadius 16, BgSecondaryBrush), ลบ "SRAN®" text (ซ้ำกับ logo), ขยาย logo 64→96px |
| Fix 3 | `DESKTOP_ROUND12_LOGIN_FIX3_PROMPT.md` | **Sent to Fable, implemented** | Subtle grid background — DrawingBrush 40x40 tile, Opacity 0.06, TextPrimaryBrush สี |
| Fix 4 | `DESKTOP_ROUND12_LOGIN_FIX4_PROMPT.md` | **Pending — ยังไม่ได้ส่ง Fable** | Card depth — DropShadowEffect (blur 40, opacity 0.5), BgElevatedBrush ใหม่ (#383838 dark), stronger contrast vs background |

## Current State of the App

### Login Screen (current after Fix 3, before Fix 4)
- Centered card, SRAN cat logo (96px), email (mail icon), password (lock + eye toggle)
- "เข้าสู่ระบบ" button, "ลืมรหัสผ่าน?" link
- Subtle grid background
- Fade-in animation
- Auto-restore session on next launch (DPAPI stored tokens)
- **Pending**: Fix 4 (card shadow + brighter bg for depth)

### Infrastructure
- Docker Compose: gateway + chat-edge-api + postgres(pgvector) + redis + nginx + MCP gateway + prometheus + grafana
- `docker-compose.prod.yml` — production hardening (127.0.0.1 bindings, redis auth, secret validation)
- Inno Setup installer (`SranDesktop.iss`)
- Auto-update via UpdateService (manifest check, SHA-256, silent install)

### Key Architecture
- .NET 8 WPF + CommunityToolkit.Mvvm (source generators)
- SSE streaming: 13 frame types mapped in ChatService
- ChatOrchestrator: Agent loop 25 iterations / 10-min deadline
- MCP: local stdio + remote JSON-RPC 2.0 (McpRemoteClient)
- Auth: Portal login → JWT → DPAPI credential storage → auto-restore
- Theme: Dark.xaml / Light.xaml with DynamicResource brushes
- Markdig (native WPF markdown) + WebView2 (Artifacts only)
- ISpeechService: Windows + Azure (th-TH) composite

## What To Do Next

### Immediate (ส่ง Fix 4 ให้ Fable)
1. ส่ง `DESKTOP_ROUND12_LOGIN_FIX4_PROMPT.md` ให้ Fable — เพิ่ม card shadow + BgElevatedBrush
2. ตรวจผลลัพธ์ — ดูว่า card มี depth พอมั้ย

### ถ้า Login OK แล้ว → ต่อ Infra/Ops (ยังค้างจาก R11)
3. **MCP connector testing local** — ยังต้องยก infra (supergateway) ถึงจะเทส remote MCP ได้
4. **Share link** — ต้อง nginx route + chat-edge endpoint `/api/v1/shares`
5. **Monitoring** — Prometheus + Grafana dashboards สำหรับ production
6. **SSL/TLS** — nginx TLS termination สำหรับ production deploy

### Optional Polish
7. **Light mode testing** — ตรวจว่า login grid bg + card depth ดูดีใน light mode ด้วย
8. **Logo "SRAN®" text** — ตรวจว่าลบออกจริงในทั้ง dark/light mode (Fix 2 อาจไม่ได้ลบหมด ถ้า Fable ใส่กลับมา)
9. **Locale keys cleanup** — ลบ keys ที่ไม่ได้ใช้แล้ว (login.brandTagline, login.feature1-3, login.orContinueWith, etc.)

### Bigger Picture (ยังไม่ได้เริ่ม)
- เปิดให้ลูกค้าใช้จริง → ต้อง deploy production stack
- Domain + SSL + DNS setup
- Customer onboarding flow

## Key Files Reference

| File | Purpose |
|------|---------|
| `LoginView.xaml` | Login screen UI (current: centered card + grid bg) |
| `LoginView.xaml.cs` | Code-behind (fade-in, focus ring, password sync) |
| `LoginViewModel.cs` | Login logic (simplified: email/pass + forgot password only) |
| `Dark.xaml` / `Light.xaml` | Theme colors (BgPrimary, BgSecondary, LogoSource) |
| `MainWindow.xaml` | Login overlay host (line ~963) |
| `AuthService.cs` | Auth orchestration |
| `TokenSession.cs` | DPAPI session persistence |
| `AppHostBuilder.cs` | DI wiring |
| `docker-compose.yml` | Main stack |
| `docker-compose.webchat.yml` | Chat-edge stack |
| `docker-compose.prod.yml` | Production overrides |
| `SranDesktop.iss` | Inno Setup installer |

## How to Continue

เปิดแชทใหม่แล้วบอก:

> "กำลังทำ SRAN Desktop ต่อ อ่าน `docs/SESSION_HANDOFF.md` ก่อน"

หรือจะให้ context สั้นๆ:

> "SRAN Desktop .NET 8 WPF AI chat client, ทำ Round 12 login screen อยู่. ยังเหลือ Fix 4 (card shadow) ยังไม่ได้ส่ง Fable. อ่าน docs/DESKTOP_ROUND12_LOGIN_FIX4_PROMPT.md"

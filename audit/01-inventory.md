# Phase 1 — Discovery & Inventory

> SRAN AI Gateway UI Audit · generated 2026-06-26
> Scope: **Gateway Admin** (`:3000`, `admin-ui/`) + **Portal** (`:3001`, `portal/`)
> Out of scope: SRAN Chat (`:3002`, `apps/web-chat/`), all backend services.

---

## 0. Headline numbers

| | Admin | Portal | Total |
|---|---|---|---|
| Routes (incl. dynamic detail) | 26 | 19 | **45** |
| — High traffic | 3 | 6 | 9 |
| — Med traffic | 14 | 7 | 21 |
| — Low traffic | 9 | 6 | 15 |

**Important context for Phase 2 calibration:** both apps already ship a *deliberate, hand-built design system* — not raw shadcn/ui or a ThemeForest template. There is a real brand (deep royal-navy "cybersecurity" identity), semantic dual-theme tokens, a typed scale, custom components, and (in Portal) a 14-pattern motion library. So the audit's job is **refinement of an opinionated system**, not "rescue an AI-generated UI." Expect the verdict distribution to skew toward *Keep / Minor Polish* with targeted *Major Refactor* on the few oversized, organically-grown pages — not wholesale *Rebuild*.

---

## 1. Shared infrastructure

### 1.1 Admin (`admin-ui/`)
| Aspect | Detail |
|---|---|
| Framework | Next.js 14.2.15 (App Router), React 18.3 |
| Styling | **Tailwind v4** — CSS-first config via `@theme` in `app/globals.css` (no `tailwind.config`) |
| Design system | Custom `sran-*` CSS component classes (`.sran-card`, `.sran-btn-*`, `.sran-table`, `.sran-badge-*`, `.sran-sidebar*`, skeleton/toast/empty/error states) defined entirely in `globals.css` (~1250 lines) |
| Component lib | `components/ui/*` — DataTable, Badge, Modal, ConfirmModal, PageHeader, StatCard, EmptyState, ErrorState, LoadingState, Skeleton, Pagination, SearchFilter, Toast, CategoryPill |
| Icons | **Hand-rolled inline `<svg>`** (no icon library) — shared stroke-spec object `S` in Sidebar |
| Charts | `recharts` ^3.8 |
| Theme | Dual light/dark via `data-theme`; **default = dark**; boot script sets it pre-hydration (key `sran-theme`) |
| Shell | `AppShell.tsx` → sticky `Sidebar` + `<main>` with **1280px** content cap; mobile fixed top bar + off-canvas drawer; sidebar collapses to icon rail at 768–1280px |
| Nav | 7 sections (Overview, Infrastructure, Tenancy, Portal, Observability, Web Chat) filtered by `ROLE_NAV[role]` |

### 1.2 Portal (`portal/`)
| Aspect | Detail |
|---|---|
| Framework | Next.js 14.2.15 (App Router), React 18.3 |
| Styling | **Tailwind v3** (`tailwind.config.ts`) + `globals.css` tokens |
| Design system | Token-driven utilities + custom components; **rich motion system** — 14 named CSS patterns (card-hover, btn-premium, page-enter, row-stagger, chip-bounce, bar-grow, toast-in, empty-float, menu-anim, skeleton shimmer…), all `prefers-reduced-motion` gated |
| Component lib | `components/*` — Button, Card, Input, Modal, ConfirmDialog, Toast, Avatar, Spinner, CreditBar, Logo, SocialButtons, PageEnter, ThemeToggle + `landing/*` (FlowDiagram, HeroBackground) |
| Icons | **lucide-react** ^0.460 |
| Motion lib | `framer-motion` ^12 |
| Auth | `next-auth` ^4 (OAuth) + custom HttpOnly-cookie session |
| Theme | Dual light/dark via `data-theme`; **default = light** (SaaS convention); key `sran-portal-theme` |
| Shell | Route groups `(marketing)` / `(auth)` / `(app)`; `(app)/layout` → fixed `Sidebar` (navy both themes) + `max-w-7xl` main wrapped in `PageEnter` |
| Fonts | Inter (body) + Outfit (headings) + Geist Mono via `next/font` |

### 1.3 Cross-app consistency notes (preview of Phase 4)
- **Shared brand:** navy sidebar (`#060A1D`/`#0A1230`) in both apps & both themes; same accent palette (`--accent-primary #6366F1`, success/warning/danger/violet/cyan); Inter + Outfit.
- **Divergences already visible:** Tailwind v4 vs v3; inline-SVG vs lucide icons; CSS-class design system (admin) vs utility+component design system (portal); admin defaults dark, portal defaults light; two *separate* token definitions (duplicated `--bg-*`/`--text-*`/`--accent-*` blocks per app). These are the seeds of the Phase 4 "systemic" findings.

---

## 2. Route inventory — Gateway Admin (`:3000`)

| Path | Purpose | Primary UI | Traffic | ~LOC |
|---|---|---|---|---|
| `/` | Platform overview dashboard: KPI cards (requests, success, latency, spend), volume-by-provider chart, top models, recent requests, DLP findings | stat cards, bar chart, data table, live feed | **High** | 1723 |
| `/requests` | Gateway request audit: filter by status/model/project; raw+masked I/O, DLP findings, trace (input→service→AI→output) | search, table, detail panel w/ tabs, 48-bucket sparkline | **High** | large |
| `/routing` | Routing stats by model/router + CRUD keyword override rules | stat tiles, breakdown rows, table, modal form | **High** | 1093 |
| `/providers` | Browse LLM providers; manage/test/validate API keys per provider | provider card grid, key panel, status badges | Med | 713 |
| `/models` | Model registry: context window, capabilities (vision/tools/code), cost/latency tiers | table/grid, provider filter, capability tags | Med | 625 |
| `/policies` | DLP policy profiles (DEFAULT/STRICT), categories, NER toggle, action mappings | tabs, tables, toggles, dropdowns | Med | 584 |
| `/tenants` | Create/edit/delete/restore tenants; region, status, credit markup | stat cards, table w/ inline actions, modals | Med | 544 |
| `/projects` | Create/edit/delete projects; assign policy profile; grid/list view | grid cards/table, status badge, policy pill, donut | Med | 525 |
| `/api-keys` | Generate/revoke API keys by project; masked prefix + usage | grouped collapsible sections, rows, copy/revoke, modal | Med | ~100+ |
| `/portal-users` | Portal end-user roster: credit balance/used, plan, activity, active toggle | data table, filter chips, stat cards, pagination, drawer | Med | ~100+ |
| `/usage` | Token + cost breakdown by project; system scan runs (AI Review) | stat cards, user/system tabs, project dropdown, table | Med | ~80+ |
| `/audit-log` | Admin action audit trail (who/what/outcome/IP); CSV export, day grouping | search, filter chips, day-grouped rows w/ severity stripe | Med | ~80+ |
| `/chat-analytics` | Chat usage analytics: messages, users, tokens, by model over time | period selector, stat cards, line+bar charts, skeletons | Med | ~80+ |
| `/chat-conversations` | Cross-tenant conversation log list; filter by user/model/date | filter card, paginated table, detail link | Med | ~80+ |
| `/chat-conversations/[id]` | Conversation detail: user/model/tokens, messages by role, transcript download | header stats, role-colored message tiles, metadata | Med | ~80+ |
| `/chat-users` | Chat user roster: active toggle, message/token counts, credit, last seen | data table, search, pagination, per-row toggle | Med | ~80+ |
| `/login` | Session login (username/email + password), gradient brand bg | inputs, submit, error alert | Med | 199 |
| `/dlp-detectors` | Create/edit/test custom regex+NER detectors; lifecycle + audit history | status tabs, detector table, detail form, timeline | Low | 215 |
| `/dlp-coverage` | Detector catalog by category, known gaps, quick-test scan tool | category cards, summary bar, test textarea, results | Low | 476 |
| `/search-settings` | Configure web-search providers per tenant (reorder/toggle/max results) | health cards, provider cards w/ menu, modals, toggles | Low | 465 |
| `/portal-settings` | Global portal config: trial tokens, model tier, signup toggle, thresholds | tabs, form fields, stat cards, status badge | Low | ~100+ |
| `/portal-token-packs` | Manage Stripe token-pack SKUs (tokens, USD price, sort) | data table, create/edit modal, delete confirm, badge | Low | ~100+ |
| `/alerts/events` | Alert event log (rules fired, delivery status) | table w/ severity + delivery badges | Low | ~80+ |
| `/ai-review` | Suspicious-pattern detector: findings, real-time scan toggle, model select | settings panel, findings table, scan history, conn test | Low | ~80+ |
| `/chat-knowledge` | KB list across tenants; filter by tenant/name | filter card, paginated table, detail link | Low | ~80+ |
| `/chat-knowledge/[id]` | KB detail: stats, document table + chunks, reindex, delete | header stats, documents table, danger zone | Low | ~80+ |

> ⚠️ **Size outliers** (organic growth → likely Major-Refactor candidates on density/composition grounds even if visuals are fine): `/` (1723), `/routing` (1093), `/providers` (713), `/models` (625), `/policies` (584).

---

## 3. Route inventory — Portal (`:3001`)

| Path | Group | Purpose | Primary UI | Traffic | ~LOC |
|---|---|---|---|---|---|
| `/` | marketing | Landing: hero, features, how-it-works, social proof, CTA | hero, feature grid, timeline, CTA | **High** | 27* |
| `/pricing` | marketing | 3 token-pack tiers (Starter/Pro/Business) + FAQ | pricing cards (Pro featured), FAQ | Med | 127 |
| `/security` | marketing | DLP detectors + compliance badges (SOC2/GDPR/ISO/HIPAA) | feature lists, compliance pills, CTA | Med | 107 |
| `/docs` | marketing | Developer quickstart (OpenAI-compatible API) + roadmap | code block, roadmap list, contact link | Med | 51 |
| `/privacy` | marketing | Privacy policy | legal prose, lists, links | Low | 131 |
| `/terms` | marketing | Terms of Service | legal prose, lists, link | Low | 144 |
| `/login` | auth | Email/password sign-in + animated stats side panel | split layout, form, 3-slide carousel | **High** | 97 |
| `/signup` | auth | Account creation + free-tier bonus highlights | split layout, form, carousel | **High** | 64 |
| `/forgot-password` | auth | Request password-reset link | form, input, success message | Med | 126 |
| `/reset-password` | auth | Set new password via token | form, show/hide password, validation | Med | 165 |
| `/invite/[code]` | auth | Accept team invite; pre-filled signup w/ tenant/role | split layout, preset form, expired-error card | Med | 161 |
| `/oauth-complete` | auth | OAuth callback → exchange session for cookies + redirect | spinner, loading msg, error card | Low | 128 |
| `/dashboard` | app | Hub: credit donut, project/key counts, recent requests, chat launcher | credit hero (glow), quickstart grid, stat cards, activity table, low-credit alert | **High** | 1039 |
| `/projects` | app | List projects; create/rename/delete; key count per project | header, project card grid, 3 modals, empty state | **High** | 323 |
| `/projects/[id]` | app | Project detail: API keys (create/rotate/revoke), usage, members | stat tiles, key table, one-time reveal modal, members | **High** | 746 |
| `/billing` | app | Buy token packs; transaction history; balance | balance hero, pack card grid, history table, confirm modal | **High** | 657 |
| `/usage` | app | Request analytics w/ date-range filter, log, top models, per-project | range toggle, stat cards, line chart, log table, panels | Med | 337 |
| `/team` | app | Manage tenant members: invite/remove (owner), roster, leave (member) | header, invite section, members table w/ search+filter, kebab, dialogs | Med | 883 |
| `/settings` | app | Profile, avatar upload+crop, language, linked OAuth, password/delete (stubs) | account card, avatar picker, prefs, security, danger zone, crop modal | Med | 920 |

> *Landing `page.tsx` is 27 lines but composes large `landing/*` section components — real surface is much bigger.
> ⚠️ **Size outliers:** `/dashboard` (1039), `/settings` (920), `/team` (883), `/projects/[id]` (746), `/billing` (657).

---

## 4. What Phase 2 will rate

Each of the 45 routes scored on the 8 dimensions (A Hierarchy, B Typography, C Spacing, D Color, E Components, F States, G Density, H Polish; 1–5 each → /40), with verdict mapping 36–40 Keep / 28–35 Minor Polish / 20–27 Major Refactor / <20 Rebuild. Detail/dynamic pages (`[id]`, `[code]`) will be rated together with their list parent unless they diverge materially.

**Open calibration question for Beat (Phase 0 STOP rule):** are any of these still WIP / not meant to look production-ready yet? Notably the **stub** areas in Portal `/settings` (password change + delete-account are disabled stubs) and any admin page you consider "internal-only, don't polish." Flag them and I'll mark them *Skip (WIP)* instead of scoring them as shipped.

---

## ▶ Status

Phase 1 complete. **Paused for your review before Phase 2** (per your start command). When you're ready, reply to proceed — and answer the calibration question above if relevant.

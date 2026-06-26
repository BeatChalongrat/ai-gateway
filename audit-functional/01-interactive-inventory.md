# SRAN AI Gateway — Functional & Interaction Audit
## Phase 1 — Interactive Surface Inventory (`01-interactive-inventory.md`)

> **Audit only. No code was modified, no dev server was run.** This is 100% static code analysis (read handler → follow API call → record). Scope: **Gateway Admin (:3000)** + **Portal (:3001)**. SRAN Chat (:3002) is OUT of scope — but note several *admin* pages display chat data (`/chat-*`), and those admin pages ARE in scope.
>
> This document is the Phase-1 deliverable: a complete map of every interactive element, its data-flow chain, and the API endpoints each page calls. Scoring (Phase 2) and per-page verdicts (Phase 3) come next, after your review.

---

## 0. Scope summary

| Project | Routes inventoried | Notes |
|---|---|---|
| **Gateway Admin** (:3000) | 25 page routes | `admin-ui/app/**/page.tsx` |
| **Portal** (:3001) | 19 page routes (6 app + 6 auth + 1 landing + 5 marketing/legal + nested detail) | `portal/app/**/page.tsx` |
| **Total** | **44 routes** | + ~15 heavy sub-components (modals, sections, forms) traced |

**Route list (Admin):** `/` (dashboard), `/audit-log`, `/routing`, `/requests`, `/providers`, `/models`, `/ai-review`, `/portal-users`, `/portal-settings`, `/portal-token-packs`, `/policies`, `/tenants`, `/projects` (+ProjectDetailModal), `/dlp-coverage`, `/dlp-detectors`, `/search-settings`, `/chat-analytics`, `/chat-conversations` (+`/[id]`), `/chat-knowledge` (+`/[id]`), `/chat-users`, `/usage`, `/api-keys`, `/alerts/events`, `/login`.

**Route list (Portal):** `(app)/dashboard`, `(app)/settings` (+AvatarCropModal), `(app)/billing`, `(app)/usage`, `(app)/team` (+InviteSection), `(app)/projects` (+ProjectCard), `(app)/projects/[id]` (+ProjectMembersSection); `(auth)/login`, `signup`, `forgot-password`, `reset-password`, `invite/[code]`, `oauth-complete`; `(marketing)/` landing, `pricing`, `docs`, `security`, `privacy`, `terms`.

---

## 1. Shared infrastructure (read this first — it drives many per-page findings)

### 1.1 Admin API client — `admin-ui/lib/api.ts`
- `api.get/post/put/patch/delete` wrap `fetch` with **session cookie** (`credentials:"include"`) + **double-submit CSRF** (echoes `csrf_token` cookie into `X-CSRF-Token` on mutations). 20s `AbortController` timeout → `ApiError(0, "Request timed out")`.
- **GET requests are de-duplicated** (in-flight cache, `api.ts:109-118`) — concurrent identical GETs share one promise.
- **401 → `clearInvalidSession()`** → POST `/auth/session/logout`, wipe role cache, redirect to `/login` (`api.ts:36-54, 93-95`).
- `ApiError` exposes `.status` + `.body` (most pages surface `err.body.error.message`).
- **`admin-ui/lib/useApi.ts`**: the standard read hook → `{data, loading, error, refetch}`. `error` is a generic string `"Error <status>"` (no message detail). `refetch` bumps a tick. Used by most list pages.

### 1.2 Admin chat-edge API client — `admin-ui/lib/chatEdgeApi.ts`
- Separate layer for `/chat-*` pages + `/chat-analytics`: calls the Next proxy `/api/chat-edge/*` (bearer injected server-side). Typed helpers (`fetchAdminConversations`, `patchAdminUser`, `reindexAdminKnowledgeBase`, etc.).

### 1.3 Portal API client — `portal/lib/api.ts`
- `api.get/post/patch/delete/upload` → `/api{path}` (same-origin Next rewrite to backend, no CORS). `credentials:"include"`, `cache:"no-store"`.
- **401 → single shared silent refresh** (`/api/portal/auth/refresh`, de-duped across concurrent calls), retry once; on failure → `window.location.href="/login"` + throw `PortalApiError(401, session_expired)` (`api.ts:103-111`).
- `PortalApiError` exposes `.status/.code/.message/.details` — portal pages surface `err.message` (richer than admin).
- Auth POSTs pass `{auth:false}` to skip refresh-retry (login/signup/reset). `upload()` mirrors `call()` for multipart.

### 1.4 Shared UI primitives — KEY behavioral differences between the two apps

| Concern | **Admin** (`admin-ui/components/ui/Modal.tsx`) | **Portal** (`portal/components/Modal.tsx`) |
|---|---|---|
| **Esc-to-close** | ❌ **NO** — only body-scroll lock; no keydown listener (`Modal.tsx:25-30`). Affects EVERY admin `Modal`/`ConfirmModal`. | ✅ **YES** — `window` keydown → onClose (`Modal.tsx:15-22`). `ConfirmDialog` wraps this, so all portal dialogs close on Esc. |
| **Backdrop click close** | ✅ yes (`:36`) | ✅ yes (`onMouseDown`, `:30-32`) |
| **Focus trap** | ❌ none | ❌ none |

- **Admin tables** (`components/ui/DataTable.tsx` + most hand-rolled tables): client search + pagination + optional row-click only. **No column sort, no bulk-select, no per-column filter anywhere in the admin app.**
- **Pagination** (admin `Pagination.tsx`, portal): **Prev/Next only** — no page-jump, no page-size control.
- The **only** in-content Esc handlers found are custom kebab menus: admin `ProjectDetailModal` MemberRow (`:708`) and portal `team`/`ProjectMembersSection` RowMenus. Other ad-hoc menus (admin RowActions, portal ProjectCard kebab, InviteSection RoleSelector) close on blur/outside-click/mouse-leave only.

### 1.5 Patterns that recur (so per-page sections can stay terse)
- **Read pages:** `useApi` (admin) / `Promise.all` on mount with `alive`/`cancelled` guard (portal) → Skeleton while loading → `ErrorState`+retry → `EmptyState` when 0.
- **Mutations:** manual `try/catch` + `useToast` (admin) / inline banner or toast (portal) + a per-action boolean loading flag that disables the button. Most mutations **refetch** rather than optimistic-update.
- **Notable exceptions to the clean pattern** (detailed per-page below): admin `dlp-detectors` uses native `window.prompt/confirm/alert`; admin `requests` uses `window.prompt`; admin `chat-knowledge/[id]` and portal `settings` avatar-remove use `window.confirm`; portal `projects` rename/delete + `projects/[id]` revoke use native `alert()` for errors.

---

## 2. Per-page inventory

> Legend: **FLAG** = something worth a closer look in Phase 2 (dead control, swallowed error, silent fail, layout-cap, etc.). file:line citations throughout.

---

# ADMIN (:3000)

## Admin — Dashboard & Audit Log

### `/` — Dashboard / Home  (`admin-ui/app/page.tsx`, 1723 LOC)

**Interactive elements:**
- **Period selector (segmented control)** — 3 `<button>`s labeled `24h` / `7d` / `30d`, `role="tab"` with `aria-selected`. `onClick={() => onChange(p)}` → calls `setPeriod(p)` in parent. Rendered in `PageHeader.actions`. file:468-489 (component), wired at 599.
- **Recent-requests filter `<input type="search">`** — controlled (`value={filter}` + `onChange`); free-text client-side filter over model/provider/status. file:1031-1047.
- No `<form>`, `<textarea>`, or `<select>`. No empty/no-op handlers.

**Modals:** None.

**Tables:**
- Recent requests table (file:1056-1198): Status/Provider/Model/Latency/When. No row actions, no bulk, no sort. Filter via search input. No pagination (fixed last-10, limit=10). New rows animated via `isNew` diff.
- Top models = `<ul>` (898-951); DLP rows = `<div>`s (1353-1493). Non-interactive.

**Nav:** None on page (chrome in layout/sidebar).

**Charts:** Recharts BarChart (771-837); SVG sparklines (211-258). Non-interactive except recharts Tooltip hover.

**Data flow:** Read-only dashboard — NO save/edit/delete. Only local UI state: `setPeriod` (refetches 4 sections via `[period]` effect 519-567), `setFilter` (client filter). No mutation chain.

**API endpoints:**
| Method | Endpoint | Location | Error handling | Loading | Success |
|---|---|---|---|---|---|
| GET | `/admin/dashboard/overview?period=` | 527 | `.catch`→error state (silent, no toast); KPI "—" | per-section loading | ok + lastUpdated |
| GET | `/admin/dashboard/volume?period=` | 538 | `.catch`→ "Failed to load volume data" (735) | "Loading…" | ok |
| GET | `/admin/dashboard/top-models?period=&limit=5` | 547 | `.catch`→ "Failed to load top models" (866) | "Loading…" | ok |
| GET | `/admin/dlp/stats?window=` | 556 | `.catch`→ "Failed to load DLP stats." (1551) | "—" + skeleton | ok |
| GET | `/admin/dashboard/recent-requests?limit=10` | 571 | try/catch → **silent** (keep previous, 573) | none (polled) | setRecent |
- Recent-requests polled every **5s** (setInterval 578-582). No toasts anywhere; sections degrade to inline placeholders; recent-requests failures fully silent.

---

### `/audit-log` — Admin Actions  (`admin-ui/app/audit-log/page.tsx`, 1794 LOC)

**Interactive elements:**
- **Advanced filter `<form>`** (1043, `onSubmit={handleApply}`): FilterInput Search (1052), Action (1058), Tenant (1064), Actor (1069); FilterSelect Outcome `<select>` All/Success/Denied/Error (1074); From/To `datetime-local` (1085/1091). All controlled; edit draft `filters`, nothing fetches until Apply.
- **"Group by day" checkbox** — controlled (1169-1173).
- **Export CSV** (848-864) `onClick={handleExport}`, disabled when `exporting || totalFiltered===0`. Functional.
- **Apply** (submit, 1098) → `setAppliedFilters(filters)`. **Clear** (1114) resets filters but NOT timeWindow/quickCat. **Clear all** (984) resets everything.
- Period QuickChips ×5 (937), Category QuickChips ×3 (954) — toggle state. Functional.
- ActiveFilterChip remove (450), Row `<tr role=button>` expand (1363, Enter/Space supported), LinkedEntity click-to-filter (1448, stopPropagation), Copy JSON (1552, clipboard+toast), Pagination Prev/Next (1307, disabled at bounds). No no-op handlers.

**Modals:** No true modal. Native `<details>/<summary>` "Advanced filters" (1004-1136), no Esc-close. Row expansion inline (1426), no Esc.

**Tables:** Audit log table (1230-1286): Timestamp/Category/Action/Actor/Tenant/Resource/Outcome/IP. Row click-to-expand; click-to-filter; Copy JSON. No bulk, no column sort. Filter via chips+form+click. **Pagination client-side** PAGE_SIZE=50 over fetched ≤500 (682, 1307). Two-tier cap: FETCH_LIMIT=500; if total>500 warns "showing first 500 of N" (1153) — no server paging beyond 500. Day-grouping headers when groupByDay (690-714).

**Nav:** ThemeToggle (865). Pagination as above.

**Data flow:**
- Apply: submit→handleApply(722)→setAppliedFilters→effectiveFilters memo(581)→queryString memo(599)→load useCallback(611) `api.get('/admin/audit-log?'+qs)`→sets rows/total. quickCat "key"/"cross-tenant" **client-side post-filtered** in refinedRows(651), not server. Stale-response guard via reqRef(609,619). Complete.
- Export CSV: handleExport(768) builds params+format=csv, **raw fetch** (not api.get) to `${NEXT_PUBLIC_API_BASE_URL}/...` credentials:include→blob→anchor download. Bypasses api client (no 401-redirect); errors→toast. Complete.
- Copy JSON: handleCopyJson(759)→clipboard→toast. Complete.
- No create/update/delete (read-only viewer).

**API endpoints:**
| Method | Endpoint | Location | Error | Loading | Success |
|---|---|---|---|---|---|
| GET | `/admin/audit-log?` (limit=500) | 616 | try/catch→setError→`<ErrorState onRetry={load}>` (has retry) | SkeletonTable (1189) | rows/total; EmptyState when 0 |
| GET | `/admin/audit-log?&format=csv` (raw fetch) | 782 | try/catch→toast | button "Exporting…" disabled | blob download |
## Admin — Routing / Requests / Providers / Models

**Shared infra (admin):**
- `components/ui/Modal.tsx`: closes via backdrop click (36) and ✕ (67). **No Esc-to-close** — only body-scroll-lock effect, no keydown listener (25-30). Affects EVERY admin modal.
- `components/ui/DataTable.tsx`: client search + pagination + optional row-click only. **No column sort, no bulk-select, no per-column filter** (52-121).

### `/routing` — Routing  (admin-ui/app/routing/page.tsx, 1093 LOC)
RoutingStatsSection (read-only) + KeywordRulesSection (full CRUD).
**Interactive:** Refresh (504, handler 465), Retry (579), New Rule→openCreate (852, 677), rules Retry (889), "Create your first rule" (917), row Edit icon→openEdit (817), row Delete icon→setDeleteTarget→ConfirmModal (825, wrapper stopPropagation 816). Form inputs all controlled in Modal: Name (959), Keywords textarea (970), Model override (982), Priority number (993), Scope select global/tenant/project (1002), Scope ID conditional (1013), Enabled checkbox (1027). Footer: Delete (edit-only 934), Cancel→closeForm (943), Save→handleSave "Saving..." (944). Modal open via creating||editing (838); **no Esc**. ConfirmModal delete (1038); **no Esc**. DataTable: searchable, onRowClick=openEdit, no sort/bulk/column-filter; pagination >10 rows.
**Data flow:** Create: Save→handleSave→validate()→`api.post /admin/keyword-rules`→toast→closeForm→refetch (708-733). Edit: `api.put /admin/keyword-rules/{id}` (713). Delete: confirm→`api.delete /admin/keyword-rules/{id}`→toast→refetch (735-757). Errors caught→toast err.body.error.message. Intact.
**API:** GET /admin/routing/stats (462, skeleton/Retry/no-data); GET /admin/keyword-rules (666); POST/PUT/DELETE /admin/keyword-rules[/{id}].

### `/requests` — Requests  (admin-ui/app/requests/page.tsx, 738 LOC)
Master-detail; read-only log + secure-trace + CSV export.
**Interactive:** **FLAG "Last 24h" button has NO onClick — dead control (399).** Refresh→refetch (400), Download→handleDownload disabled when downloading/empty "Preparing…" (404), ThemeToggle (418). Search input controlled + clear ✕ (454). Status filter buttons All/OK/Error/Masked (470) client-side. Row click→selectRow (507). Pagination Prev/Next disabled at bounds (540) — custom inline. "Admin Secure Trace" btn→loadSecureTrace (588). Copy JSON via JsonPanelHeader→clipboard+toast (163, 717). No form/textarea/select. No modal — detail is inline sticky sidebar (hidden <1100px). **Secure-trace uses `window.prompt` for reason (305)** — native blocking dialog.
**Data flow:** selectRow→`api.get /admin/requests/{id}`→setDetail→loadSafeTracePanel (`GET .../trace?reason=...`, fallback `GET .../secure-trace`); catches swallow silently (223,249,270). Secure reveal: window.prompt→`api.get .../secure-trace?reason=`→setSecureTrace+toast; catch→toast. Download: `api.get /admin/requests?limit=1000`→client filter→CSV blob→toast. Refresh→refetch.
**API:** GET /admin/requests (178, Skeleton/ErrorState+retry/Empty); GET /admin/requests/{id} (216, "Loading…"; catch→"Failed to load details"); GET .../trace (238 swallowed→fallback); GET .../secure-trace (263,309 toast); GET /admin/requests?limit=1000 (335).

### `/providers` — Providers  (admin-ui/app/providers/page.tsx, 712 LOC)
Card grid; click card expands inline KeysPanel w/ per-key CRUD/test.
**Interactive:** Search controlled (597). Filter tablist All/Configured/Available role=tab (628). ProviderCard is `<button>` onClick=onToggle expand (362, 696). KeysPanel inputs controlled: Label (297), API Key type=password (307). "Add & Validate"→handleAdd disabled when adding/empty "Validating..." (330). "Close"→onClose (227). Per-key "Test"→handleTest disabled testingId (259). **Per-key "Delete"→handleDelete (deactivate) — NO confirmation dialog (262). FLAG.** Retry→refetch (670). No modal — KeysPanel inline, no Esc. Keys table: Test/Delete row actions, no sort/bulk/filter/pagination.
**Data flow:** Add: handleAdd→`api.post /admin/providers/{name}/keys`→toast→clear→loadKeys (159-177). Test: `api.post .../keys/{id}/test`→toast→loadKeys (179). Delete: `api.delete .../keys/{id}`→toast→loadKeys (193) no confirm. Close: re-GET /key/status. Key-status hydration per provider on load, errors swallowed (533).
**API:** GET /admin/providers (527, Skeleton/Error/Empty); GET .../key/status (537,578 swallowed); GET .../keys (loadKeys 147, **catch silently ignored — failed fetch renders as "no keys" w/ no message. FLAG**); POST .../keys (163); POST .../keys/{id}/test (182); DELETE .../keys/{id} (195, no loading flag on button).

### `/models` — Model Registry  (admin-ui/app/models/page.tsx, 625 LOC)
**Fully read-only.** No create/edit/delete, no mutating forms, no modals.
**Interactive:** Search controlled + clear ✕ (347). Provider chips All/per-provider (384,393). Streaming toggle "Stream" (406, 290). View toggle list/grid (423). "Clear filters" no-match state (455). ThemeToggle (307). Pagination shared component onPageChange=setPage when >15 (547,614). Table rows NOT clickable, no row actions/sort/bulk. No textarea/select/form/modal/breadcrumb.
**Data flow:** Read-only — all interactivity client-side state (search/providerFilter/streamingOnly/view/page) → filtered/paged memos. Retry→refetch. Note: context/capability META hardcoded client-side (32-55); models absent show "—".
**API:** GET /admin/models (248, Skeleton/ErrorState+Retry/Empty/filtered-empty Clear filters). No mutations.

**Cross-page FLAGS:** (1) /requests "Last 24h" dead button (399). (2) No admin modal supports Esc-close. (3) /providers key Delete no confirm (262). (4) /providers loadKeys swallows errors silently (145). (5) /requests native window.prompt for secure-trace reason (305).
## Admin — AI-Review / Portal-Users / Portal-Settings / Token-Packs / Policies

### `/ai-review` — AI Sensitive Data Reviewer  (admin-ui/app/ai-review/page.tsx, 705 LOC)
**Interactive:** Collapsible settings header toggleSettings (485,488; persists localStorage, loadKeysAndTenants 338). "AI review enabled" checkbox controlled→patchSettingsImmediate (503). Provider pills→handleProviderSwitch (518,463). Model input controlled (526). API key password controlled + "Clear"→clearProviderKey (531,533). Endpoint URL custom-only (541). "Save provider settings"→saveProviderSettings (546). "Test connection"→testConnection disabled connTesting (547). Real-time ToggleSwitch draft (565). **Manual scan ToggleSwitch (589) — FLAG: draftManualEnabled NEVER sent to API; saveModeSettings only persists realtime_enabled/monitored_key_ids/scan_limit (425). Cosmetic no-op.** Scan limit number 1-200 clamped (601). Cancel→cancelModeSettings (613), "Save settings"→saveModeSettings (614), "Run scan now"→runScan disabled scanning (616). KeyPicker: search controlled (195), scope pills (180), Select-all/Clear (216), per-key checkbox (224), pill-remove × (154), Change/Edit→onOpen (162), Cancel/Confirm (239).
Tabs New/Covered/All/History→setTab (645). Findings table (679): row actions Mark reviewed/Add to DLP/Dismiss→patchFinding (691). No bulk/sort/pagination (limit=200). Scan history table read-only (653, limit=100).
**Modals:** None — inline collapsible + inline KeyPicker. No Esc.
**Data flow:** Enable: checkbox→patchSettingsImmediate→`PATCH /admin/ai-review/settings`→GET summary (388,503). Save provider: builds patch→PATCH→reset drafts (398). Clear key: PATCH {review_api_key:null} (412). Save mode: PATCH {realtime_enabled,monitored_key_ids,scan_limit}→"Saved ✓" 3s (421); **manual toggle discarded**. Run scan: `POST /admin/ai-review/scan {}`→reloadAll (451). Finding status: `PATCH .../findings/{id} {status}`→reloadAll (458). Test conn: `POST .../test-connection {}`→connResult 10s (444).
**API:** GET settings/summary/findings?limit=200/scan-runs?limit=100 (reloadAll 368); GET /admin/api-keys + /admin/tenants (loadKeysAndTenants, errors swallowed 354); PATCH settings (×4: 389,403,414,431 →setError); POST test-connection (446); POST scan (453, scanning); PATCH findings/{id} (459). Top-level ErrorState+retry (480).

### `/portal-users` — Portal Users  (admin-ui/app/portal-users/page.tsx, 720 LOC)
**Interactive:** Search controlled→setSearch→query (248,107). Status tabs All/Active/Suspended/FreeTrial/Paid (277; Active/Suspended server, FreeTrial/Paid client). ThemeToggle (234). Table (349): clickable rows→openDetail (367); last cell stopPropagation→RowActions (410). No bulk/sort/pagination (limit=200 fixed). RowActions ⋯ custom dropdown onClick toggle, onBlur close 150ms (635); items Top-up→onTopup, Suspend/Activate→onToggle, View→onView (681). Not a real popover (no portal/Esc). Detail Modal: Close (433), "💰 Top-up"→openTopup (473), "⏸ Suspend/▶ Activate"→setSuspendTarget (476). Top-up Modal: Amount number controlled autoFocus (556), quick-amount buttons (566), Description text maxLength 500 (581); Cancel/Confirm disabled topupSaving (528). ConfirmModal suspend→toggleStatus (594).
**Modals:** 3 via shared Modal/ConfirmModal. **FLAG no Esc-close** (Modal.tsx 25-30). Guard close while saving (524,596).
**Data flow:** Detail: `GET /admin/portal-users/{id}`→setDetail; error→toast (153). Top-up: validate positive int→`POST .../{id}/topup {amount,description}`→toast+close+refetch+reopen detail (178); invalid→toast no call. Suspend/Activate: `POST .../{id}/suspend|activate {}`→toast+refetch (203). Comment says "optimistic" but NO optimistic mutation — relies on refetch.
**API:** GET /admin/portal-users?limit=200&offset=0[&search][&status] (useApi 116, skeleton/ErrorState+retry/Empty); GET .../{id} (158 detailLoading); POST .../{id}/topup (187 topupSaving); POST .../{id}/suspend|activate (208 suspendWorking).

### `/portal-settings` — Portal Settings  (admin-ui/app/portal-settings/page.tsx, 695 LOC)
**Interactive:** Tab bar General/Trial/Security→setTab (224). ThemeToggle (180). General: Toggle portal_signup_enabled controlled→toggle disabled saving (358,379). Trial: free-trial-tokens number controlled (520); tier segmented budget/standard/premium→setTierValue (559); low-threshold number controlled (615); "Save Changes" disabled !dirty||saving (637); dirty indicator (654). Security: static "Coming soon" — **no interactive elements** (672).
**Modals:** None. **Tables:** None (StatCards only).
**Data flow:** General toggle: optimistic setEnabled→patchSetting→`PATCH /admin/system-settings/{key}`→toast+onSaved; error rolls back (311). Trial save: up to 3 sequential patchSetting for changed fields only (free_trial_tokens/trial_model_tier/credit_low_threshold)→`PATCH /admin/system-settings/{key}` each (458). **FLAG partial-failure possible** (earlier PATCH ok, later fails → single error, refetch reconciles, no per-field feedback). Number inputs not range-validated client-side.
**API:** GET /admin/system-settings (useApi 153, SkeletonTable/ErrorState retry); PATCH /admin/system-settings/{key} (patchSetting 277, errors via useSaveError→toast).

### `/portal-token-packs` — Token Packs  (admin-ui/app/portal-token-packs/page.tsx, 404 LOC)
**Interactive:** "New Pack"→openCreate (199); "Create your first pack" (224); ThemeToggle (205). Table (228): row Edit→openEdit (266), Deactivate/Activate→toggleActive (269), Delete→setDeleteTarget (272). No bulk; client sort by sort_order then name (66); no search/pagination. Create/Edit Modal controlled: Name autoFocus (310), Tokens number min1 + preview (321), Price number step.01 (338), Description maxLength 200 (351), Sort Order number (362), is_active checkbox edit-only (378). Cancel→closeForm, Create/Save→handleSave disabled saving (292). ConfirmModal delete→confirmDelete (392).
**Modals:** Create/Edit Modal + delete ConfirmModal. **FLAG no Esc.** closeForm blocks while saving (120); confirm blocks while deleting (394).
**Data flow:** Create: openCreate seeds sort_order=len+1→handleSave validate name/tokens/price→`POST /admin/token-packs` (no is_active)→toast+close+refetch (99,128); errors→formErr inline. Edit: openEdit incl is_active→`PATCH /admin/token-packs/{id}`→toast+refetch (106,148). Toggle: `PATCH .../{id} {is_active:!}`→toast+refetch (164) no optimistic. Delete: `DELETE .../{id}`→toast "Pack deactivated" (soft)+refetch (174).
**API:** GET /admin/token-packs (useApi 62, Skeleton/Error/Empty); POST (152 saving, error→formErr); PATCH (149 edit, 166 toggle); DELETE (178 deleting).

### `/policies` — DLP Policies  (admin-ui/app/policies/page.tsx, 584 LOC)
**Interactive:** Tab bar Profiles/Detectors/NER→setTab (239). ThemeToggle (217). Profiles (ProfCard): Inbound ActionSel `<select>` allow/mask/block per category controlled→onChange (351,158). Outbound column **read-only by design** (no PATCH endpoint, 298). Detectors: search controlled + clear ✕ (439); **FLAG Enabled Toggle has NO onChange passed (`<Toggle checked={det.enabled} disabled={dis}/>` 479) → non-functional/read-only**; Default Action ActionSel→onAction (480, disabled NER-rows when NER off). NER: "Enable NER stage" Toggle controlled→toggle disabled toggling (548).
**Modals:** None. **Tables:** Profiles category table (inline editable select, no sort/filter/pagination 338); Detectors table (search filter, no sort/bulk/pagination 459).
**Data flow:** Profile inbound: optimistic actions[cat]→`PATCH /admin/policy-profiles/{id}/category-actions {category,action}`→✓saved 1.5s; error rolls back (284). Detectors default action: optimistic overrides[cat]→`PATCH /admin/policy-profiles/default/category-actions`→saved; error no rollback (417). NER toggle: `PATCH /admin/settings {ner_enabled}`→load() (518); polls `GET /admin/ner/status` every 3s while loading (511).
**API:** GET /admin/dlp/detectors (189,397); GET /admin/policy-profiles (190,266,398); GET /admin/ner/status (399,504 polled); PATCH .../category-actions (288,421 per-cell SaveDot); PATCH /admin/settings (520 toggling→ErrorState).

**Cross-page FLAGS:** Admin Modal has NO Esc handler (backdrop+✕ only). Two dead controls: AI-review Manual-scan toggle (589 never persisted), Policies detector Enabled toggle (479 no onChange).
## Admin — Tenants / Projects / DLP-Coverage / DLP-Detectors / Search-Settings

### `/tenants`  (admin-ui/app/tenants/page.tsx, 543 LOC)
**Interactive:** Search controlled→name/slug/region + clear ✕ (293,298). Status filter buttons All/Active/Deleted (311). "Create Tenant"→setShowCreate (261). ThemeToggle (265). Row `<tr>` clickable→openEdit (347, action cell stopPropagation 385). Restore (deleted rows)→setRestoreTarget (387). Edit→openEdit (409). Delete→setDeleteTarget (422). Edit/Delete hidden when slug==="demo" (399). EmptyState "Create your first tenant" (286). Create modal: Name maxLength64 (523), Slug (529), Region select global/us/eu (535); Cancel/Create disabled saving (516). Edit modal: Slug + Tenant ID read-only (461); Name (466); Region select (479); Status select active/suspended/deleted (487); Credit markup number step.1 min1 max5 (496); Cancel/Save disabled updating (453).
Table (332): search+status filter, row click→edit, Edit/Delete/Restore. **No sort/bulk/pagination.** Modals: Create/Edit (Modal), Delete/Restore (ConfirmModal). Edit gated open={!!editTarget && !deleteTarget} (448). **No Esc.**
**Data flow:** Create: handleCreate (166)→validate (name+slug regex ^[a-z0-9-]+$)→`api.post /admin/tenants`→close+reset+toast+refetch. Update: openEdit seeds editForm→handleUpdate (219)→validateEdit→**diff-only body** (225); empty diff→toast "No changes" no API (231)→`api.put /admin/tenants/{id}`→toast+refetch. Delete: `api.delete /admin/tenants/{id}` (181). Restore: `api.put /admin/tenants/{id} {status:active}` (191).
**API:** GET /admin/tenants (useApi 117 Skeleton/ErrorState+retry/Empty); POST (170 saving); PUT (236 update, 191 restore); DELETE (181 deleting).

### `/projects`  (admin-ui/app/projects/page.tsx, 524 LOC)
**Interactive:** Search controlled + clear ✕ (239,244). Policy filter All/Default Policy (254). "Show archived" checkbox→?include_archived=true (283). View toggle grid/list (294). "Create Project"→setShowCreate (173). ThemeToggle (177). Grid card clickable→setSelected (337, hover bar stopPropagation 397). Card "Open"→setSelected (398). "Restore" (archived)→handleRestore (407). "Delete"→setDeleteTarget hidden when isProtected (demo) (415). **List rows clickable→setSelected (448); List view has NO per-row action buttons — delete/restore only in grid view.** EmptyState "Create your first project" (319). Create modal: Tenant select (only active tenants) (502); Name maxLength64 (510); Policy Profile select Default/default/strict (516); Cancel/Create disabled saving (496).
Grid+list views, filters, row/card click→detail. **No sort/bulk/pagination.** Modals: Create (Modal), Delete (ConfirmModal), ProjectDetailModal gated open={!!selected && !deleteTarget} (488). No Esc.
**Data flow:** Create: handleCreate (136)→validate (tenant_id+name)→`api.post /admin/projects {...,policy_profile_id||null}`→close+reset+toast+refetch. Delete: `api.delete /admin/projects/{id}` (151). Restore: `api.post /admin/projects/{id}/restore {}` (84); **catch toasts "Restore failed" but swallows error object (`catch {}` no var) (87)**. Detail open→ProjectDetailModal fetches.
**API:** GET /admin/projects[?include_archived] (useApi 66 SkeletonCards/ErrorState/Empty); GET /admin/tenants (useApi 69, no explicit error UI); POST (140 saving); DELETE (151 deleting); POST .../restore (84, no loading state).

### ProjectDetailModal  (admin-ui/app/projects/_components/ProjectDetailModal.tsx, 1148 LOC)
**Interactive:** Opens when open; fetches on [open,projectId] (189). Close footer (283), ✕, backdrop. No Esc. Policy "Change →"→setEditingPolicy (343); select onChange→handleSavePolicy(value) saves immediately (323); inline Cancel (333). "+ Add member"→setAddOpen (466). Member kebab ⋯→per-row menu (770); **menu has its OWN Esc + outside-click close via MemberRow effect (703-717) — only Esc handler in admin audit.** Role items admin/member/viewer→onChangeRole skip-if-same (818); "Remove"→setRemoveTarget (853). Danger "Archive"→setArchiveOpen (529). AddMemberModal: search controlled autoFocus debounced 200ms (1010); user result buttons→setPicked disabled if already member (1054); Role select (1132); Add→handleAdd disabled !picked||adding (999). ConfirmModal ×2: Archive (540), Remove member (549).
**Data flow:** Load: `api.get /admin/projects/{id}` (174)→setDetail; error→toast. Archive: `api.post .../{id}/archive {}` (199)→toast+close+onMutated()+onClose. Remove member: `api.delete .../{id}/members/{user_id}` (219)→toast+fetchDetail. Change role: `api.patch .../{id}/members/{userId} {role}` (242)→toast+fetchDetail. Save policy: `api.put .../{id} {policy_profile_id}` (260); **blank "—" sends policy_profile_id:"" empty string not null (325)**. Add member: `api.post .../{id}/members {user_id,role}` (972)→toast+onAdded.
**API:** GET /admin/projects/{id} (174); GET /admin/portal-users?tenant_id=&limit=20&search= (AddMemberModal 952 debounced, catch→[] silent); POST .../archive (199 archiving); DELETE .../members/{userId} (219 removing); PATCH .../members/{userId} (242, no loading); PUT .../{id} (260 policy, no loading); POST .../members (972 adding).

### `/dlp-coverage`  (admin-ui/app/dlp-coverage/page.tsx, 475 LOC)
**Interactive:** "Refresh"→refetch (192). ThemeToggle (201). Search controlled detector name/cat/severity/method + clear ✕ (238). Category filter chips All+per-cat→setCatFilter toggle (248). Quick Test textarea controlled rows5 (392). "Run Detection"→runTest disabled testing (407). "Clear" resets (416). Error Retry→refetch (175). Known Gaps table read-only (353, no actions/sort/pagination). Category cards display-only. **Modals: none.** Largely read-only catalog; only mutation = POST test.
**Data flow:** Run test: runTest (106)→guard empty→`api.post /admin/dlp/test {text}` (110)→setTestResult (findings+masked). Error→setTestError. loading=testing.
**API:** GET /admin/dlp/coverage (useApi 98, full-page "Loading…"/error block+Retry); POST /admin/dlp/test (110 testing "Scanning…").

### `/dlp-detectors`  (admin-ui/app/dlp-detectors/page.tsx, 215 LOC; + CustomDetectorForm 223, AuditLogTimeline 95)
**Interactive:** "+ New detector"→setCreating (104). Status tabs all/active/testing/draft/disabled→setTab drives GET (112). Table row clickable→openDetail (140, action cell stopPropagation 150). Row actions: "Activate" (status≠active; **title hints "needs passing tests" but NOT disabled when !test_passed**)→transition activate (153); "Test"→transition testing (157); "Disable"→transition disable (160); "Delete"→remove (162). Panel "Close" (179). **Uses native window.prompt/window.confirm for disable reason (69), delete reason+confirm (83-85) — not modal components.** CustomDetectorForm: Name disabled-in-edit (115); Description textarea (119); Category/Severity/Default-action selects (124,130,136); Detection type radio ×3 disabled-in-edit (147); conditional Regex (157)/Keywords (163)/Entity select (169); Test cases per-row input+match checkbox (183,186); "+ Add case" (198); "Run tests" edit-only (199); Cancel/Create-or-Save disabled busy||!name (206,207).
Detector list table (130): status-tab filter + row actions. **No sort/bulk/pagination.** **Modals: none — inline side panel + native prompts. No Esc/backdrop; closes only via "Close".**
**Data flow:** List: load (43)→`api.get /admin/dlp/custom-detectors[?status=tab]` (47)→setRows. error→setError. Re-runs on tab. Open detail: `api.get .../{id}/audit-log` (61, catch→[]). Transition: disable→prompt reason→`api.post .../{id}/disable {reason}` (71); activate/testing→`api.post .../{id}/{verb} {}` (73)→load()+reopen. **error→alert(); reopen passes stale d (76).** Delete: remove (82)→prompt+confirm→`api.delete .../{id}?reason=` (87)→clear+load; **catch→alert("delete failed") swallows server msg (90)**. Create/Save: save (69)→buildPattern→edit `api.put .../{id} {...,reason}` (79); create `api.post BASE body` (81)→onSaved. error→setError, loading=busy. Run tests: runTests (91)→**blocks if not editing "Save the detector first" (92)**→`api.post .../{id}/test {inputs}` (96)→setTestResults; catch→setError.
**API:** GET .../custom-detectors[?status] (47); GET .../{id}/audit-log (61 silent); POST .../{id}/activate|testing|disable (71-73 alert errors); DELETE .../{id}?reason= (87 alert); POST create / PUT .../{id} (79-81 busy); POST .../{id}/test (96 inline).

### `/search-settings`  (admin-ui/app/search-settings/page.tsx, 465 LOC)
**Interactive:** Tenant select header→setTenantId drives config path (264). ThemeToggle (267). Health "Check Now"→checkNow disabled checkingHealth||!tenantId (281). Tenant config: "Enable web search" Toggle aria-pressed→setEnabled local until Save (292); Credit cost number controlled (297); "Save"→saveConfig disabled savingCfg (300). "+ Add Provider"→openAdd disabled !tenantId (309). Per-provider: Toggle→toggleProvider saves immediately (347); kebab ⋮→menu (348); items Edit/Test/Move-up/Move-down/Delete (353; Move dimmed at ends); fixed overlay click-catcher outside-click close (351) but **no Esc**. Add modal: Provider select (385); API key (392); Display name (396); Max results number 1-10 (400); Cancel/"Add Provider" disabled adding (378). Edit modal: API key blank=keep (417); Display name (421); Max results (425); Cancel/Save (409). Delete ConfirmModal (431).
**Tables: none — provider cards.** No sort/bulk/pagination; ordering via Move up/down. Modals Add/Edit (Modal), Delete (ConfirmModal). No Esc.
**Data flow:** Save config: `api.put /admin/tenants/{tid}/search-config {search_enabled,search_credit_cost}` (108)→toast+refetch. Add: submitAdd (132)→guard empty api_key→`api.post .../search-providers` (136). **FLAG addTestResult state rendered (402) but NEVER set non-null — dead UI (no test-before-add).** Edit: submitEdit (160)→`api.put .../search-providers/{id}` (api_key only if entered 169). Delete: `api.delete .../search-providers/{id}` (186). Toggle: `api.put .../search-providers/{id} {is_enabled:!}` (199, no optimistic/loading). Reorder: move (206)→swap ids→`api.put .../search-providers/reorder {provider_ids}` (213). Test: `api.post .../search-providers/{id}/test {}` (224 testing). Health: `api.post .../search-providers/check-health {}` (242).
**API:** GET /admin/tenants (76); GET .../search-config (useApi 85, **falls back to GET /admin/tenants when no tenantId 84**); GET /admin/search-health/summary (86, no error UI, ??0 defaults); PUT search-config (108 savingCfg); POST search-providers (136 adding); PUT search-providers/{id} (169 edit, 199 toggle); PUT reorder (213); DELETE (186 deleting); POST .../test (224 testing); POST check-health (242 checkingHealth).

**Cross-cutting:** No Esc via shared admin Modal (backdrop+✕ only, no focus trap). Only Esc in admin = MemberRow kebab in ProjectDetailModal (708). dlp-detectors bypasses design system (window.prompt/confirm/alert). Dead UI: search-settings addTestResult (402). Swallowed errors: projects handleRestore (87), dlp-detectors delete alert (90).
## Admin — Chat-* / Usage / API-Keys / Alerts / Login

**Two API layers:** chat-* + chat-analytics use `lib/chatEdgeApi` (`/api/chat-edge/*` Next proxy, bearer injected server-side); usage/api-keys/alerts use `lib/api` (`/admin/*` session cookie + CSRF); login uses raw fetch.

### `/chat-analytics`  (admin-ui/app/chat-analytics/page.tsx, 458 LOC)
**Interactive:** `<select>` period today/7d/30d/90d controlled→setPeriod (156); re-runs load via effect [period] (143). ErrorState retry→load(period) (186). 2 recharts (LineChart, BarChart) display-only. Links: "View all →"→/chat-conversations (391); row title→/chat-conversations/{id} (422). Recent Conversations table read-only, no actions/sort/filter/bulk/pagination (limit 10). No modals.
**Data flow:** read-only. load(period) (124) Promise.all([fetchAnalytics, fetchAdminConversations limit10]). No mutations.
**API:** GET /api/chat-edge/analytics/usage?period= (129, Skeleton/ErrorState retry); GET /api/chat-edge/conversations?limit=10 (130, Empty→EmptyState).

### `/chat-conversations`  (admin-ui/app/chat-conversations/page.tsx, 270 LOC)
**Interactive:** Search title controlled draft.q Enter→applyFilters (96); Model id (108); From date (120); To date (131). **NOTE user_id supported in AdminConvQuery but no UI input.** Apply→setPage(1)+setFilters (142); Reset→clear (149). Title→detail Link (194). Read-only otherwise, no sort/bulk/row-actions. Pagination Prev/Next disabled at bounds, no page-size (227). No modals.
**Data flow:** load (42) reads filters+page→fetchAdminConversations({...filters,limit:50,offset})→setData; re-fires on filters/page change. Draft staged; applyFilters commits (deliberate). Complete.
**API:** GET /api/chat-edge/conversations?<filters+limit+offset> (46, SkeletonTable/ErrorState retry/Empty).

### `/chat-conversations/[id]`  (338 LOC)
**Interactive:** "← Back to logs" Link (88). Masked-content banner display-only (100). Messages as cards (171); attachments table read-only (269), filenames plain text, NO download links (disabled note 295). No forms/inputs/buttons besides back. No modals/pagination.
**Data flow:** read-only. id from useParams; load (59)→fetchAdminConversation(id)→setData. No mutations.
**API:** GET /api/chat-edge/conversations/{id} (64, SkeletonTable/ErrorState retry).

### `/chat-knowledge`  (admin-ui/app/chat-knowledge/page.tsx, 389 LOC)
**Interactive:** Search name controlled draft.q Enter→applyFilters (97); Tenant ID free-text uuid no dropdown (110). Apply (122); Reset (129); "Clear filters" in filtered-empty (154). Name→detail Link (188). Read-only, no sort/bulk/row-actions. Pagination Prev/Next (252). No modals.
**Data flow:** load (41)→fetchAdminKnowledgeBases({...filters,limit:50,offset}). Staged-draft. Complete.
**API:** GET /api/chat-edge/knowledge-bases?<q,tenant_id,limit,offset> (45, SkeletonTable/ErrorState/KbEmptyState).

### `/chat-knowledge/[id]`  (549 LOC)
**Interactive:** "← All KBs" + "Back to list" Links (153,385). "Reindex all documents"→handleReindex disabled reindexing||deleting "Reindexing…" (364). "Delete knowledge base"→handleDelete "Deleting…" (372). **Both use native window.confirm (72,100), NOT Modal.** Documents table read-only, failed-doc error_message tooltip (253,403). Sample-chunks read-only. Action message banner (164). No input/select/form, no modal.
**Data flow:** Reindex: handleReindex (69)→window.confirm→`POST /api/chat-edge/knowledge-bases/{id}/reindex`→actionMessage {reindexed,total,failed}→load(); error→"Reindex failed". Delete: handleDelete (97)→confirm→`DELETE /api/chat-edge/knowledge-bases/{id}`→router.push("/chat-knowledge"); error→"Delete failed"+setDeleting(false). Complete.
**API:** GET .../{id} (55, full-page SkeletonTable / ErrorState retry); POST .../{id}/reindex (reindexing btn, actionMessage); DELETE .../{id} (deleting btn, navigate).

### `/chat-users`  (admin-ui/app/chat-users/page.tsx, 314 LOC)
**Interactive:** Search email/name controlled Enter→submitSearch (111); Search btn→setPage(1)+setAppliedSearch (130); Clear (conditional) (137). Table (167): per-row Disable/Enable→toggleActive disabled pendingId "…" (255). No sort/bulk. Pagination Prev/Next (280). No modals. **Toggle has NO confirmation — fires immediately.**
**Data flow:** load (45)→fetchAdminUsers({limit:50,offset,q}). Toggle: toggleActive (67)→setPendingId→patchAdminUser(id,{is_active:!})→`PATCH /api/chat-edge/users/{id}`→**optimistic in-place setData** (72); **on error NO toast/message — silently void load() (82)**; finally clear pendingId. **NOTE patchAdminUser supports allowed_models but no UI.**
**API:** GET /api/chat-edge/users?<limit,offset,q> (49, SkeletonTable/ErrorState retry/Empty); PATCH /api/chat-edge/users/{id} {is_active} (70 pendingId, silent error refetch).

### `/usage` — Usage & Cost  (admin-ui/app/usage/page.tsx, 307 LOC)
**Interactive:** Tab bar 2 buttons "User traffic"/"AI review (system)"→setTab (151). `<select>` Scope (User tab) controlled selectedProject→handleSelect→loadUsage (175). ErrorState retries user (201)/system (237). System scan-runs table read-only, no actions/sort/filter/pagination (limit20). No modals/pagination.
**Data flow:** read-only. Projects: useApi("/admin/projects") (62) populates dropdown. User tab: loadUsage(projectId) (75)→`GET /admin/usage/summary[?project_id=]`; initial on mount (117); handleSelect on change. System tab: lazy via effect when tab==="system" + not loaded (121); loadSystem (96) Promise.all([scan-runs/summary, scan-runs?limit=20]). No mutations.
**API:** GET /admin/projects (62, errors not surfaced for dropdown); GET /admin/usage/summary[?project_id=] (82, skeleton cards/ErrorState parses err.body.error.message); GET /admin/ai-review/scan-runs/summary + ?limit=20 (100, skeleton/ErrorState/"No scan runs yet").

### `/api-keys`  (admin-ui/app/api-keys/page.tsx, 367 LOC)
**Interactive:** "Create Key" header (138) + empty-state (191)→setShowCreate. ThemeToggle (142). Search controlled + ✕ clear (151,156). Status filter All/Active/Revoked→setStatusFilter (167) client filtered useMemo (71). "Clear filters" no-match (196). Group header per-project→toggleGroup collapse (208,92). Row `<div onClick>`→setSelected detail (245). Copy-prefix stopPropagation→clipboard+toast (256). Revoke→setRevokeTarget (285, active only). **4 Modals (components/ui/Modal):** Detail (open selected&&!revokeTarget, 307); Revoke ConfirmModal (322); Create (showCreate&&!rawKey, 325); Raw-key (!!rawKey, 351). **No Esc-close.** Create form: `<select>` Project controlled also auto-fills tenant_id (332); Key Name controlled maxLength64 char-count (343); validate() (97).
**Data flow:** Create: handleCreate (105)→validate→`api.post /admin/api-keys`→setRawKey(result.key) (raw-key modal)+reset+toast+refetch; error→toast. Revoke: handleRevoke (117)→`api.delete /admin/api-keys/{key_id}`→toast+clear+refetch.
**API:** GET /admin/api-keys (useApi 43, SkeletonCards/ErrorState retry/Empty); GET /admin/projects (44, names+dropdown); POST /admin/api-keys (109 saving "Creating..."); DELETE /admin/api-keys/{key_id} (121 revoking "Processing...").

### `/alerts/events`  (admin-ui/app/alerts/events/page.tsx, 185 LOC)
**Interactive:** "Refresh"→refetch (96). DataTable (103): built-in searchable client filter, built-in client pagination pageSize=10 when total>10, **NO column sort**. Row click→setSelected detail modal (111). Detail Modal (118): onClose, ✕/footer/backdrop. **No Esc.** Shows webhook_url (optional).
**Data flow:** read-only. useApi("/admin/alert-events?limit=50") (88). Row click→setSelected→modal. No mutations.
**API:** GET /admin/alert-events?limit=50 (88, loading/error/empty inside DataTable, ErrorState onRetry=refetch).

### `/login` — Admin Login  (admin-ui/app/login/page.tsx, 198 LOC)
**Interactive:** `<form onSubmit={handleSubmit}>` (125). Username/email controlled autoComplete=username (128, accepts username too). Password controlled autoComplete=current-password (142). Submit "Sign In" disabled loading "Authenticating..." (168). Error banner from error state (154). No modals, no nav links, no forgot/signup.
**Data flow:** handleSubmit (18)→preventDefault→setLoading→`fetch(POST ${API_BASE}/auth/session/login {email,password} credentials:include)`. !res.ok: 429→"Too many attempts…", else "Invalid credentials". Success (cookies set)→router.push("/"). Network throw→"Unable to reach the backend." **Raw fetch (not lib/api); no CSRF needed (login establishes session); does NOT call refreshSession (role cache lazily populated later).**
**API:** POST /auth/session/login {email,password} (24, loading btn, inline error banner 429-vs-other, success→/).

**Cross-cutting:** No admin Modal supports Esc-close. Pagination Prev/Next only (no jump/page-size). No column sorting anywhere. chat-users toggle: no confirm + silent error swallow. Unexposed: AdminConvQuery.user_id, patchAdminUser.allowed_models.


---

# PORTAL (:3001)

## Portal — Dashboard / Settings / Billing / Usage

**Shared infra (portal):**
- `portal/lib/api.ts`: api.get/post/patch/delete/upload → `/api{path}` credentials:include, cache:no-store. On 401 (auth!==false, not retried): single shared `/api/portal/auth/refresh` POST, retry once, else redirect /login + throw PortalApiError(401,session_expired). PortalApiError exposes .status/.code/.message/.details. upload() mirrors for multipart.
- `portal/components/Modal.tsx`: **Esc-to-close PRESENT** (window keydown→onClose 15-22); backdrop onMouseDown close (30-32). **No focus trap.** Used by billing + AvatarCropModal.
- `portal/lib/hooks/useCountUp.ts`: rAF count-up, respects reduced-motion. Used by dashboard CreditHero + billing hero.

### `/dashboard` — Dashboard  (portal/app/(app)/dashboard/page.tsx, 1039 LOC)
**Interactive:** No form/input/textarea/select — read-only. "View plans →" (low-credit banner)→router.push("/billing") (156, only if is_low_credit). `<a>` "Open SRAN Chat"→NEXT_PUBLIC_WEB_CHAT_URL||localhost:3002/chat target=_blank (526). Links: "View transactions →"/"Upgrade"→/billing (425); 3 QuickstartCards href=/projects (495) — **"Read the quickstart" card mislabeled, points /projects not docs (TODO :509)**; StatPanel Projects/API Keys→/projects (708,714); "View all"→/usage (856,948). No modals; Recent Activity table static read-only (903), no sort/filter/pagination.
**Data flow:** Read-only. Mount (45) Promise.all GET /portal/me + /portal/projects + /portal/me/usage?limit=5; then fan-out `GET /portal/projects/{id}/keys` per project (57, each .catch(()=>0)). alive guard. Errors→setError banner (122).
**API:** GET /portal/me (50); /portal/projects (51); /portal/me/usage?limit=5 (52); /portal/projects/{id}/keys per-project (60, **.catch→0 silently undercounts keys on failure**). Loading→PageSpinner (81).

### `/settings` — Settings  (portal/app/(app)/settings/page.tsx, 920 LOC)
**Interactive:** Profile `<form onSubmit={handleProfileSubmit}>` (396). Input "Display name" controlled (399). Submit Button loading=savingProfile disabled=savingProfile||!dirty (428; dirty from displayName+language vs user 180). Language radiogroup (476): 2 button role=radio en/ไทย→setLanguage (498) LIVE, feeds profile PATCH. Avatar hidden file input accept jpeg/png/webp via ref (332); Upload/Replace→ref.click (339); Remove→handleAvatarRemove (357, only if avatar_url). Copy User ID→copyUserId clipboard 1.5s (229). **Preferences (non-language): 3 PreferenceRow toggles wrapped opacity-60 pointer-events-none (517) — decorative, no handlers, aria-hidden. "Coming soon".** **Security card `<form onSubmit={noop}>` (551): 3 Input password all disabled, submit Button disabled. noop only preventDefault (57). FLAG entire form dead.** Linked accounts Google/GitHub: "Connected" pill or ComingSoonPill, **no connect/disconnect buttons** (598). **Danger Zone "Delete account" button disabled title="Coming soon" — no onClick (698).** Modal AvatarCropModal opened cropSource!==null; while busy onClose swapped to noop (718).
**Data flow:** Save profile: submit→`PATCH /portal/me {display_name,preferred_language}`→merge user, reset, inline success 3s+toast (61). Avatar upload: pick→validate→FileReader dataURL→crop→onConfirm(blob)→`api.upload POST /portal/me/avatar` multipart→merge avatar_url+toast (87,117,126). Avatar remove: native `confirm()` (not shared ConfirmDialog)→`DELETE /portal/me/avatar`→avatar_url:null+toast (144). Password/delete/non-lang prefs/link: chains intentionally don't exist (disabled UI).
**API:** GET /portal/me (44, setError banner/PageSpinner); PATCH /portal/me (67 savingProfile, inline+toast); POST /portal/me/avatar multipart (126 uploadingAvatar overlay); DELETE /portal/me/avatar (150, gated confirm()).

### AvatarCropModal  (portal/app/(app)/settings/_crop/AvatarCropModal.tsx, 219 LOC)
**Interactive:** `<canvas>` onPointerDown/Move/Up/Cancel drag-to-pan setPointerCapture (162,83). `<input type=range zoom min1 max3 step.05>` controlled value=zoom→setZoom (194). Footer Cancel→onCancel disabled busy (128); Save→handleConfirm loading=busy (136). Esc/backdrop active unless busy.
**Data flow:** Save→handleConfirm (105)→canvas.toBlob(webp,0.9)→onConfirm(blob). toBlob null→silently returns (113), busy resets finally. No direct API (parent uploads).

### `/billing` — Billing & Credits  (portal/app/(app)/billing/page.tsx, 658 LOC)
**Interactive:** No form/input — selection-driven. Per-pack "Purchase" Button→setConfirmPack (393). Purchase-result banner dismiss ×→setPurchaseMsg(null) (151). Confirm Modal (497): onClose blocks close mid-purchase; Esc/backdrop via Modal; Cancel→setConfirmPack(null) disabled purchasing; "Purchase"→handlePurchase loading=purchasing (510). Body warns "Payment processing coming soon… will not charge you" (529). Transaction table (443): static, **no row actions/sort/filter/pagination/bulk**; Empty→EmptyState; hardcoded ?limit=20, no UI to page beyond 20.
**Data flow:** Purchase: pack→setConfirmPack→modal Purchase→handlePurchase (86)→`POST /portal/billing/purchase {pack_id}` (90)→success setPurchaseMsg+close+load() refetch (91); error→setPurchaseMsg+close (94). Chain complete (backend no-op stub per copy).
**API:** GET /portal/me (56); /portal/billing/packs (57); /portal/me/transactions?limit=20 (58); POST /portal/billing/purchase (90 purchasing). Loading→PageSpinner.

### `/usage` — Usage  (portal/app/(app)/usage/page.tsx, 337 LOC + 2 components)
**Interactive:** Range filter (147): 4 buttons today/7d/30d/all→setRange (151) LIVE but **client-side only** — filters already-fetched rows by created_at (79), does NOT refetch. **List fetched once ?limit=200, so all/30d capped at 200 most recent; footer "Showing X of {total}" can show X far below true total (326).** No other forms/inputs. Top-models/Per-project panels read-only (223). RequestLogTable: static, **no sort/filter/row-actions/pagination/bulk**; empty→EmptyState "View API Keys →"→/projects; **header claims "last 100 requests" but page passes up to 200 (mismatch)**. **UsageChart: PLACEHOLDER ONLY — bars are deterministic fake heights PLACEHOLDER_HEIGHTS (19-21), not real data; comment :100. FLAG fabricated bars when totalRequests>0.** Bars onMouseEnter/Leave opacity hover only.
**Data flow:** Read-only. Mount (50) Promise.all GET /portal/me/usage?limit=200 + /portal/me/usage/summary; alive guard. filtered (79) + logRows (106) via useMemo; status bucketed success/error/rate_limited (unknown→error). Range change re-derives memo, no network. Either fetch fails→single setError.
**API:** GET /portal/me/usage?limit=200 (60); GET /portal/me/usage/summary (61, rendered only if truthy). Err→banner; loading→PageSpinner.

**Cross-cutting FLAGS:** Usage range filter + "Showing X of total" mislead (200-row cap, not server-side). UsageChart never plots real data. Dashboard key count silently undercounts on /keys failure. Settings Security form + Delete-account + non-language prefs + linked-account connect are deliberately inert "coming soon".
## Portal — Team / Projects / Project-Detail

**Note:** portal `ConfirmDialog` wraps portal `Modal` → ALL portal ConfirmDialogs support Esc-to-close + backdrop close (verified). Portal Modal has NO focus trap.

### `/team` — Team Management  (portal/app/(app)/team/page.tsx, 883 LOC)
**Interactive:** Search `<input type=search>` controlled name/email (225). FilterChips All/Active/Suspended/Pending→onChange statusFilter (601). Per-row "Leave team" icon (self)→onLeave→leave ConfirmDialog (453). RowMenu kebab (owner, non-self)→toggle up/down flip (735). **MenuItem "Change role" — disabled "Coming soon", NO onClick (770). FLAG no-op stub.** "Remove from team"→onRemove→setRemoveTarget (783). RowMenu has own Esc + focus trap (687).
**Modals:** Leave ConfirmDialog (295), Remove ConfirmDialog (311) — both Esc-closable. Members table: Member/Role/Status/Joined/actions; no bulk/sort/pagination; client filter (search+status); empty-state row (269). InviteSection/MemberOnlyInviteCard conditional on isOwner (179).
**Data flow:** Load: refresh()→GET /portal/me then GET /portal/team/members→setMe/setMembers; errors→setError banner (63). Leave: ConfirmDialog→confirmLeave→`DELETE /portal/team/members/{me.id}`→success window.location.href=/login; error toast (86). Remove: confirmRemove→`DELETE /portal/team/members/{id}`→toast+refresh (103). **Change role: chain BREAKS — disabled, no handler/API (770).**
**API:** GET /portal/me (66, setError/PageSpinner); GET /portal/team/members (68); DELETE .../members/{me.id} (90 pendingAction→redirect); DELETE .../members/{id} (107 pendingAction→toast+refresh).

### InviteSection  (team/_components/InviteSection.tsx, 945 LOC)
**Interactive:** RoleSelector button→setOpen disabled if invite exists (766); dropdown "Member" live + "Admin" disabled "Coming soon" (817). **Dropdown closes only via onMouseLeave (no Esc/outside-click). FLAG.** "Create invite link"→handleCreate loading (487). Invite URL `<input readOnly>` value=invite_url selects-on-focus (381). "Copy link"→handleCopy clipboard+"Copied!" (399). "Regenerate"→setRegenOpen (447). "Revoke"→setRevokeOpen (465). Recipients chip `<input>` controlled draft Enter/comma commits, Backspace removes, paste-splits, blur-commits (575). Chip remove ×→onRemove (717). "Send invitations" submit form onSubmit=handleSend disabled canSend (needs invite+resendConfigured+≥1 valid) (538). ConfirmDialog Regenerate (645), Revoke (660) — Esc-closable.
**Data flow:** Load: Promise.all([GET /portal/team/invite, GET /portal/auth/public-settings {auth:false}])→setInvite+setResendConfigured; public-settings .catch fallback (109). Create: `POST /portal/team/invite {role}`→setInvite+toast+onInviteChanged (142). Regenerate: `POST /portal/team/invite {role,force:true}` (160). Revoke: `DELETE /portal/team/invite`→setInvite(null)+clear recipients (182). Send: `POST /portal/team/invite/send-email {recipients}`→full success clears; partial marks failed chips invalid+keeps (250). **Copy: navigator.clipboard.writeText no catch — unhandled rejection possible (201).**
**API:** GET /portal/team/invite (114); GET /portal/auth/public-settings auth:false (115); POST /portal/team/invite [create 145 / regen 163]; DELETE /portal/team/invite (185); POST .../invite/send-email (257 sending, partial-success).

### `/projects` — Projects List  (portal/app/(app)/projects/page.tsx, 323 LOC)
**Interactive:** "New Project"→setCreateOpen (145). EmptyState "Create project" + owner "View docs"→/docs (174). ProjectCard grid card click→onOpen router.push (191). Create modal: Modal open onClose guarded !submitting clears name+error; form onSubmit=handleCreate + Input name controlled required autoFocus; **Cancel doesn't clear newName/submitError (only onClose path does)** + Create loading (209). Rename modal: onClose !renameSaving; Input controlled renameValue; Save→handleRename (261). Delete modal: onClose !deleting; danger Delete→confirmDelete (292). All Modals Esc-closable. No table (card grid), no sort/filter/pagination/bulk.
**Data flow:** Load: Promise.all([GET /portal/projects, GET /portal/me])→then per-project GET /portal/projects/{id}/keys for statsMap (keys count; **requests hardcoded 0**); errors→setError (50). Create: handleCreate→validate non-empty→`POST /portal/projects {name}`→close+clear+load (86). Rename: `PATCH /portal/projects/{id} {name}`→load; **error→native alert() (107). FLAG.** Delete: `DELETE /portal/projects/{id}`→load; **error→alert() (125). FLAG.**
**API:** GET /portal/projects (54); GET /portal/me (55); GET /portal/projects/{id}/keys per-project (66, inner catch→0); POST /portal/projects (95 submitting→setSubmitError); PATCH /portal/projects/{id} (113 renameSaving→alert); DELETE /portal/projects/{id} (129 deleting→alert).

### ProjectCard  (projects/_components/ProjectCard.tsx, 218 LOC)
**Interactive:** Card container role=button tabIndex0 onClick=onOpen + onKeyDown Enter/Space (48). Kebab→setMenuOpen toggle, wrapper stopPropagation, outside-click close mousedown (103,37). **FLAG no Esc on this menu.** "Rename"→onRename (128); "Delete"→onDelete (145). "Open project →" Link stopPropagation (202). Stats: keys count + requests (always 0). No API — presentational, delegates to parent callbacks.

### `/projects/[id]` — Project Detail  (portal/app/(app)/projects/[id]/page.tsx, 746 LOC)
**Interactive:** BackLink→router.push("/projects") (606). "Create Key" header+empty-state (282,313)→setCreateOpen. Keys table (Name/Key/Status/Created/Last used/actions): per active-key Rotate→setRotateTarget (356), Revoke→setDeleteTarget (372); revoked keys no actions; no sort/filter/pagination/bulk (319). Create Key modal onClose !creating, form onSubmit=handleCreate + Input keyName controlled required autoFocus (413). One-time key reveal modal onClose setCreatedKey(null), code + Copy→copyKey, footer "I've saved it" (459). Revoke modal onClose !deleting danger→confirmDelete (528). Rotate modal onClose !rotating→confirmRotate, rotateError banner (557). All Modals Esc-closable. ProjectMembersSection when me&&project, canManage=tenant owner||selfProjectRole==='admin' (401). StatTiles non-interactive (tokens/requests from all-user usage, not project-scoped — flagged inline 108).
**Data flow:** Load: Promise.all of 6: GET project, keys, /portal/me/usage?limit=200, me, project members, team members (.catch→empty); derives selfProjectRole (90). Create key: `POST /portal/projects/{id}/keys {name}`→close+clear+setCreatedKey(reveal)+load (140). Revoke: `DELETE .../keys/{key_id}`→load; **error→alert() (165). FLAG.** Rotate: `POST .../keys/{key_id}/rotate`→setCreatedKey(rotated reuse reveal)+load; error→setRotateError banner (179). **Copy: clipboard.writeText no catch (201). FLAG.**
**API:** GET /portal/projects/{id} (95); GET .../keys (96); GET /portal/me/usage?limit=200 (97, NOT project-filtered); GET /portal/me (98); GET .../members (99); GET /portal/team/members (103 .catch→empty); POST .../keys (149 creating→reveal); DELETE .../keys/{key_id} (169 deleting→alert); POST .../keys/{key_id}/rotate (184 rotating→setRotateError).

### ProjectMembersSection  (projects/_components/ProjectMembersSection.tsx, 989 LOC incl AddMemberModal)
**Interactive:** "Add member" (if canManage)→setAddOpen (208). Member RowMenu kebab (canManage||self)→toggle up/down flip, Esc+outside-click close+focus return (528,493). "Make admin/member/viewer" (canManage, current disabled)→onRoleChange (573). "Leave project" (self)→onLeave (605). "Remove from project" (canManage non-self)→onRemove (615). AddMemberModal: search `<input type=search>` controlled autoFocus (801); candidate list buttons→setPicked (848); role radiogroup admin/member/viewer→setRole (939); footer Cancel + "Add member" disabled !picked||adding→handleAdd; form onSubmit=handleAdd (752). ConfirmDialog Remove (270), Leave (285) — Esc-closable. Roster = flex card rows (not table), client-sorted role priority then name (170); no pagination/filter on roster (AddMemberModal has own search).
**Data flow:** Load: GET /portal/projects/{id}/members→setMembers sorted; errors→setError (91). **NOTE duplicate fetch (parent page also fetches same endpoint).** Add: `POST .../members {user_id,role}`→toast→onAdded (closes+parent load) (731). Role change: `PATCH .../members/{userId} {role}`→toast→load (154); **NO dedicated loading state — kebab role change has no spinner/disable during flight. FLAG.** Remove: `DELETE .../members/{user_id}`→toast→setRemoveTarget(null)+load (115). Leave: `DELETE .../members/{currentUserId}`→success window.location.href=/projects; error toast (last-admin 409 surfaced, by design) (135).
**API:** GET .../members (95 loading); POST .../members (736 adding); PATCH .../members/{userId} (156 no loading); DELETE .../members/{user_id} (119 pending); DELETE .../members/{currentUserId} (138 pending→redirect).

**Cross-cutting:** No-op stubs: team "Change role" (770), InviteSection "Admin" role option (817). Error UX inconsistency: projects rename/delete + project-detail revoke use native alert(); elsewhere toast/inline. Duplicate fetch of /portal/projects/{id}/members. Known-incomplete: project list requests=0 hardcoded; detail usage stats all-user not project-scoped. Menus without Esc: ProjectCard kebab (outside-click only), InviteSection RoleSelector (mouse-leave only).
## Portal — Auth Pages & Marketing Pages

### `/login` — Sign in  (page.tsx 97 LOC; LoginForm.tsx 226 LOC)
**Interactive:** `<form onSubmit={handleSubmit}>` (128). email controlled required (133). password type-toggle controlled required (155). Show/Hide btn→toggle showPassword (164). Submit "Sign in" disabled loading (175). OAuth Google/GitHub **only rendered when NEXT_PUBLIC_OAUTH_ENABLED==="true"** →handleSocial→NextAuth signIn (181,58); gated off by default. Nav: "Back to home"→/ (112), "Forgot password?"→/forgot-password (150), "Sign up free"→/signup (221). LoginForm in `<Suspense>` (useSearchParams).
**Data flow:** handleSubmit (77)→`api.post /portal/auth/login {email,password} {auth:false}`→reads ?redirect= via safeRedirectTarget; allowed (same-origin or WEB_CHAT_URL/localhost:3002)→window.location.href else router.push("/dashboard"). Cookies HttpOnly. Error→setError inline .af-error banner. Intact.
**API:** POST /portal/auth/login (84, auth:false, inline error, loading "Signing in…").

### `/signup` — Create account  (page.tsx 64 LOC; SignupForm.tsx 308 LOC)
**Interactive:** `<form onSubmit noValidate>` (134). name optional (137). email required (150). password required + live strength meter (165,184). confirm required (197). Show/Hide for pwd (175) + confirm (207). Invite-code toggle "Have an invite code?"→setShowInvite (221); reveals inviteCode input (233); hidden when locked (presetInviteCode). **TOS checkbox controlled default TRUE (50,252) — pre-accepts terms.** Submit "Create account" disabled loading (274). **Social Google/GitHub HARDCODED disabled no onClick (285) — dead/decorative. FLAG.** Nav: "Back to home"→/ (105), "Sign in"→/login (303).
**Data flow:** handleSubmit (62)→client validation pwd<8 (65)/mismatch (69)/!tos (73)→`api.post /portal/auth/signup {email,password,display_name?,invite_code?} {auth:false}`→success router.push("/dashboard") (auto-login cookie). Error→setError. Intact. **default tos=true → !tos guard only fires if user unchecks.**
**API:** POST /portal/auth/signup (81, auth:false, inline .sv-error, loading "Creating account…").

### `/forgot-password`  (126 LOC)
**Interactive:** `<form onSubmit>` (93). email required (98). Submit "Send reset link" disabled loading (110). Nav "Back to sign in"→/login. Success view replaces form when submitted (51), no dismiss button (static confirmation).
**Data flow:** handleSubmit (24)→`api.post /portal/auth/password-reset/request {email} {auth:false}`→success setSubmitted (constant "check your email", no user-existence oracle). Error→setError finally setLoading(false). Intact.
**API:** POST /portal/auth/password-reset/request (29, inline .af-error, loading "Sending…", success view-swap).

### `/reset-password`  (165 LOC)
**Interactive:** ResetPasswordInner in `<Suspense>` (useSearchParams); token from ?token= (31). `<form onSubmit>` (108). password required (114). Show/Hide toggle→showPwd (123) — **controls BOTH pwd and confirm visibility (140)**. confirm required (138). Submit "Set new password" disabled loading||!token (149). Missing-token error card→/forgot-password (94). Nav "Back to sign in"→/login (83).
**Data flow:** handleSubmit (39)→validation no-token (42)/len<8 (46)/missing letter+digit (50)/mismatch (56)→`api.post /portal/auth/password-reset/confirm {token,new_password} {auth:false}`→success router.push("/login?reset=success"). Error→setError. Intact. **NOTE LoginForm does NOT read ?reset=success — dead hint (no success banner on login).**
**API:** POST /portal/auth/password-reset/confirm (62, inline .af-error, loading "Updating…", success redirect).

### `/invite/[code]`  (161 LOC) — server component
**Interactive:** Server fetchPreview(code) on render (40,80) — **direct backend call (not /api proxy)** to `${BACKEND_INTERNAL_URL|...|http://backend:8080}/portal/auth/invite/{code}` cache:no-store, catch→{valid:false}. Valid→renders `<SignupForm presetInviteCode inviteContext>` (150, invite field locked). Invalid→error card links /signup (114), /login (122), "Back to home"→/ (89). No client handlers (server component).
**Data flow:** GET (server) /portal/auth/invite/{code}→preview.valid branches. Valid→signup flow with invite_code pre-filled. Intact.
**API:** GET /portal/auth/invite/{code} (server-side, 50, try/catch+!res.ok→{valid:false}→invalid card, no loading SSR).

### `/oauth-complete`  (128 LOC)
**Interactive:** No forms/buttons. Suspense spinner; on error `<a href="/login">` (111). Logic in useEffect on mount (67).
**Data flow:** useEffect→getSession() NextAuth (69)→if no tokens→setError (70)→else `fetch("/api/portal/auth/cookie-exchange" POST credentials:include {access_token,refresh_token})` (79)→!ok→setError (88)→else read ?redirect= via safeRedirectTarget; allowed→window.location.href else router.replace("/dashboard"). **Only place using raw fetch to /api/... not api wrapper (by design, token exchange).**
**API:** getSession() (69); POST /api/portal/auth/cookie-exchange (79 raw fetch, setError→login link, loading spinner).

## MARKETING
Shared layout (marketing): MarketingNav + main + MarketingFooter + ScrollReveal (IntersectionObserver .reveal).

### MarketingNav (202 LOC) — applies to all marketing
Brand logo→/ (82). Desktop `<a>`: "Product"→/#features onClick handleProductClick (smooth-scroll only on /, else default) (90); "Security"→/security (97); "Pricing"→/pricing (103); "Docs"→/docs (109). CTAs "Sign in"→/login (117), "Get started"→/signup (120). Mobile menu toggle→menuOpen aria-expanded (129); backdrop onClick closeMenu (146); mobile links mirror + close on tap (155); auto-close on route change (50); body scroll lock while open (55). **FLAG no Esc-to-close for mobile menu.** scrolled state class on scroll>20 (41).
### MarketingFooter (42 LOC) — static
Footer `<a>`: Docs/Pricing/Security/Privacy/Terms plain href (27), Contact→mailto:support@sran.ai (32).

### `/` — Landing  (page.tsx 27 LOC)
Composes HashScrollHandler/Hero/SocialProof/Features/HowItWorks/CTASection.
- HashScrollHandler: client effect smooth-scrolls to location.hash on mount (cross-page /#features) (34), returns null.
- Hero (731 LOC mostly decorative SVG/motion): CTAs Link→/signup "Start free" (646); `<a href="#how">` "See how it works" (658). FrostCard mouse-tilt decorative (18); FeatureBadge framer hover (193). No forms. "Send request" mini-browser block is aria-hidden decoration NOT a real button (123).
- SocialProof (177 LOC): 3D parallax onMouseEnter/Move/Leave→tilt/hovered (117), respects reduced-motion (105). Provider tiles next/image, no links/clicks (tooltips only). Purely visual.
- Features (119 LOC): 3 `<article tabIndex=0>` flip-cards (CSS :hover/:focus-within) (21,50,85). No links/buttons; keyboard-focusable for CSS reveal only.
- HowItWorks (52 LOC): 3 `<div className=step tabIndex=0>` (36), CSS reveal only. id="how" anchor target.
- CTASection (50 LOC): Link→/signup "Create your account" (30). Static otherwise.
**Data flow:** none (no API). All CTAs→/signup; anchors scroll in-page.

### `/pricing`  (127 LOC) — static. 3 card CTAs `<a href="/signup">` "Get started" (38,57,76); **FAQ uses plain h3/p NOT accordions (no expand/collapse)**; mailto:sales@sran.ai (118). No forms/JS.
### `/docs`  (52 LOC) — static placeholder "Documentation is on the way". mailto:support@sran.ai (46). Code in `<pre>` non-interactive, **no copy button**. No forms.
### `/security`  (110 LOC) — static prose. mailto:security@sran.ai (96,105). No forms/accordions.
### `/privacy`  (133 LOC) — static prose. mailto:privacy@sran.ai (100,124,126). No forms.
### `/terms`  (147 LOC) — static prose. mailto:legal@sran.ai (20,142). No forms.

**Cross-cutting:** Signup social buttons dead (disabled, no handler) vs login flag-gated working. Signup TOS pre-checked (weak consent). /login?reset=success hint unused. Mobile nav no Esc-close. oauth-complete raw fetch (by design). invite page server-fetches backend directly (BACKEND_INTERNAL_URL), silent fall-through to invalid. All auth POSTs {auth:false} (correct).

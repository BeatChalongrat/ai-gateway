# Sprint 1 — Pre-State Baseline (verification before first edit)

> Purpose: confirm we share the same baseline before any code changes. Every line below was **re-verified against current code today** (the audit docs were written earlier; some line numbers drifted — current numbers are authoritative here).
> Sources cross-checked: `audit/00-executive-summary.md`, `audit/03-systemic.md`, `audit-functional/01-interactive-inventory.md`.
> **No code changed yet.** Awaiting your "go" to start Day 1.

---

## A. Dead controls (กดได้แต่ไม่ทำอะไร) — VERIFIED

| # | Plan ref | Verified location (current) | Evidence | Sprint 1 action |
|---|---|---|---|---|
| 1 | requests "Last 24h" | `admin-ui/app/requests/page.tsx:399` | `<button …>Last 24h</button>` — no `onClick` | **Remove** (no period-filter backend) |
| 2 | policies Enabled Toggle | `admin-ui/app/policies/page.tsx:479` | `<Toggle checked={det.enabled} disabled={dis} />` — no `onChange` (cf. working Toggle at `:548`) | Wire if `PATCH` endpoint exists, else **read-only state + helper text** |
| 3 | ai-review manual-scan toggle | toggle `admin-ui/app/ai-review/page.tsx:589`; payload `:425-429` | `saveModeSettings` payload = `{realtime_enabled, monitored_key_ids, scan_limit}` only — `draftManualEnabled` never sent | Add to payload if backend accepts, else **remove toggle** |
| 4 | signup OAuth buttons | `portal/app/(auth)/signup/_components/SignupForm.tsx:284-294` | two `<button className="sv-social" disabled aria-disabled="true">` — no env-gate, no handler | **Remove from UI** + keep JSX in comment (per locked decision) |
| 5 | team "Change role" | `portal/app/(app)/team/page.tsx:772` | `<MenuItem label="Change role" … disabled>` no onClick | **Remove MenuItem** (keep "Remove from team") |
| 6 | search-settings addTestResult | decl `admin-ui/app/search-settings/page.tsx:125`, render `:402` | `setAddTestResult` never called with non-null anywhere → dead render block | **Remove render block** |

## B. Silent error swallow — VERIFIED

| # | Plan ref | Verified location | Evidence | Sprint 1 action |
|---|---|---|---|---|
| 7 | providers loadKeys | `admin-ui/app/providers/page.tsx:149` | `} catch {` empty → failed fetch renders as "no keys" | Add `setError(...)` + error state |
| 8 | chat-users toggle | `admin-ui/app/chat-users/page.tsx:82-84` | `} catch {` → `void load()` only; no toast/setError | Add `toast.error` + revert optimistic |
| 9 | dashboard /keys undercount | `portal/app/(app)/dashboard/page.tsx:~60` (per-project `/keys .catch→0`) | inner `.catch(()=>0)` silently undercounts | `Promise.allSettled` + partial-data warning |

## C. Stub forms shown as usable — VERIFIED

| # | Plan ref | Verified location | Evidence | Sprint 1 action |
|---|---|---|---|---|
| 10 | settings Security card | `portal/app/(app)/settings/page.tsx:551` (`onSubmit={noop}` @57), disabled inputs `:558,571,584,591` | entire form inert | **Hide section** when feature disabled (default) |
| 11 | settings Delete account | `portal/app/(app)/settings/page.tsx:700-713` | `disabled`, `title="Coming soon"`, no onClick | **Remove button** (don't show disabled destructive action) |

## D. P0 styling bugs — VERIFIED

| # | Plan ref | Verified location | Evidence | Sprint 1 action |
|---|---|---|---|---|
| 12 | forgot/reset unstyled cold-load | `portal/app/(auth)/forgot-password/page.tsx` + `reset-password/page.tsx` | use `.af-form`/`.af-error` (`:88,93` / `:95,103,108`) but **neither imports `login.css`** (confirmed: only login/signup/invite import it) | Import `../login/login.css` + wrap in `.auth-root` |
| 13 | invite white-on-white CTA | `portal/app/(auth)/invite/[code]/page.tsx:130,136` | "Sign in instead" `color: rgba(255,255,255,0.82)` on white `.sv-form-side` | Token-color the secondary CTA |

## E. Native dialogs (F4 targets) — VERIFIED (grep `window.(confirm|prompt|alert)` + bare `alert(`)

| Location | Current | Replace with |
|---|---|---|
| `admin-ui/app/dlp-detectors/page.tsx:69,78,83,85,90` | `window.prompt`×2, `window.confirm`, `alert()`×2 | `Modal` + `ConfirmModal` (+ reason input) |
| `admin-ui/app/requests/page.tsx:305` | `window.prompt` (secure-trace reason) | reason-input `Modal` |
| `admin-ui/app/chat-knowledge/[id]/page.tsx:72,100` | `window.confirm`×2 | `ConfirmModal` |
| `portal/app/(app)/projects/page.tsx:119,133` | `alert()`×2 (rename/delete errors) | `ConfirmDialog` + inline error |
| `portal/app/(app)/projects/[id]/page.tsx:173` | `alert()` (revoke error) | inline error / toast |
| `portal/app/(app)/settings/page.tsx:146` | `confirm()` (avatar remove) | `ConfirmDialog` |

## F. F6 Honest-metrics (locked actions) — VERIFIED

| Location | Current | Locked action |
|---|---|---|
| `admin-ui/app/routing/page.tsx:483` | `return "99.2%";` (hardcoded) | **Hide stat tile** + "Coming soon" sublabel |
| `portal/app/(app)/usage/_components/UsageChart.tsx:19,65,100,102` | `PLACEHOLDER_HEIGHTS` fake bars, comment "Placeholder until a chart library is wired up" | **Wire to real `/portal/me/usage`** (recharts) |
| `portal/app/(app)/projects/page.tsx:67,69,196` | `{ keys, requests: 0 }` hardcoded | **Remove requests stat** from card |
| `portal/app/(app)/projects/[id]/page.tsx:~108-112` | account-wide stats labeled per-project | **Re-label "Across all your projects"** |
| `portal/app/(app)/dashboard/page.tsx:318` | `<HeroStat label="Next Reset" value="—" />` | **Hide field** |

## G. Foundation sweep targets (F1/F2/F5) — confirmed present, scoped per plan
- **F1 (emoji→icon) first batch:** portal-users (`💰⏸▶👁👥`), audit-log (`🛡`), portal-token-packs (`💎★`), chat-conversations/[id] (`⚠️`), search-settings (`✓✗`). New module: `admin-ui/components/ui/Icon.tsx`.
- **F2 (token sweep) first batch:** policies, tenants, api-keys, portal-users, dlp-coverage, chat-analytics — replace hardcoded hex with existing tokens in `admin-ui/app/globals.css`.
- **F5 (skeletons) first batch:** dashboard, projects, projects/[id], billing — new `portal/components/skeletons/`.

---

## ⚠️ Discrepancies & decisions surfaced during verification (please confirm)

1. **Line drift only (no behavior change):** several cited lines moved (e.g. team Change-role `770→772`; signup OAuth `285→284-294`; routing `481-484→483`). Current numbers above are authoritative. No impact on plan.

2. **SignupForm OAuth — a cleaner option exists than "delete".** The repo already has `portal/components/SocialButtons.tsx` — an **env-gated** (`NEXT_PUBLIC_OAUTH_ENABLED`) component with **real `signIn` handlers** and a tasteful "coming soon" fallback, used by the login side. SignupForm instead hand-rolls its own *hardcoded-disabled dead* buttons (`:284-294`). The locked decision says "remove from UI + comment." **I'll follow that** — but flagging that swapping signup to render `<SocialButtons/>` (matching login) would be more consistent and equally safe. Want me to (a) remove per locked decision, or (b) swap to the existing `SocialButtons`? Default = (a). *(→ logged to backlog regardless.)*

3. **F4 ESLint ban needs a config created from scratch.** There is **no project-level ESLint config** in `admin-ui/` or `portal/` (only `portal` has a `next lint` script; admin has none). Adding `no-restricted-globals` means creating `.eslintrc.json` in each app. I'll add minimal configs extending `next/core-web-vitals` + the rule. Confirm you're OK with two new config files.

4. **Item 2 (policies) & 3 (ai-review) & item 10 (settings Security) are conditional on backend/flags** — I'll inspect for the endpoint/flag at edit time and take the "else" branch (read-only / remove / hide) if absent, per the plan's "wire if <30min else remove" rule. Will record which branch was taken in `sprint-1.md`.

---

## Baseline confirmation

✅ All 18 Sprint-1 before-state claims verified in current code. Plan is accurate (modulo the line drift + the 4 notes above).

**Holding here for your go.** Reply **"go sprint 1"** (and answer note #2 if you have a preference — else I default to remove) and I'll start Day 1 (P0 functional fixes), committing after each task to keep the tree deployable.

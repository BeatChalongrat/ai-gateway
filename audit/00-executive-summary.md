# Executive Summary — SRAN AI Gateway UI Audit

> One-page TL;DR for Beat. Full detail in `01-inventory.md` · `02-pages.md` · `03-systemic.md` · `04-roadmap.md`.
> Scope: Gateway Admin (`:3000`) + Portal (`:3001`). Web-chat (`:3002`) excluded. **Audit only — no code changed.**

---

## The headline

**This UI does not look AI-generated.** Both apps run a deliberate, hand-built design system — real brand (deep royal-navy "cybersecurity" identity), dual-theme semantic tokens, a typed scale, custom component libraries, and a 14-pattern motion system in Portal. The job here is **refinement of an opinionated system, not rescue.** Across 45 routes there are **zero Rebuilds** and only 6 Major-Refactors — the rest is Keep or Minor Polish.

The gap between SRAN and Linear/Vercel/Stripe is **not taste — it's consistency and finish:** the same dozen issues recur across pages (emoji-as-icon, hardcoded hex that breaks dark mode, spinner-instead-of-skeleton, native browser dialogs, a few fake/placeholder metrics). Fix those **once at the system level** and most pages jump a band with little per-page work.

---

## Q1 — How many pages, average score, distribution?

**45 routes** scored on 8 dimensions (/40). **Overall average 31.6/40.**

| Verdict | Count | Share |
|---|---|---|
| 🟢 Keep As-Is (36–40) | **7** | 16% |
| 🟡 Minor Polish (28–35) | **32** | 71% |
| 🟠 Major Refactor (20–27) | **6** | 13% |
| 🔴 Rebuild (<20) | **0** | 0% |

- **Admin** (26 pages) avg **32.5** — slightly stronger; mature data-table craft, complete states.
- **Portal** (19 pages) avg **30.4** — dragged down by 5 Major-Refactors (marketing `/security` `/docs`, auth `/forgot-password` `/reset-password` `/oauth-complete`).
- **Best:** Admin `/` dashboard (39), Portal landing (38), Admin `/tenants` `/projects` `/audit-log` (37).
- **Weakest:** Portal `/docs` (22), `/oauth-complete` (22), `/security` (26), Admin `/dlp-detectors` (26).

---

## Q2 — Top 5 critical findings to fix first

1. **Auth recovery pages render unstyled on cold load.** Portal `/forgot-password` + `/reset-password` use `.af-*` classes but **never import `login.css`** — every email-link password reset lands on a broken-looking page. *(P0, S2/S10, fix F7.)*
2. **Emoji & glyphs used as icons on ~18 pages** in an otherwise inline-SVG/lucide system — admin has **no central icon module** so there's nothing preventing it. Most-pervasive single issue. *(S1, fix F1.)*
3. **Hardcoded light-only hex breaks the default-dark admin theme** on ~11 pages (`#f3f4f6`, `#92400e`, navy `#1e3a5f`…) where semantic tokens already exist. *(S2, fix F2.)*
4. **Fake / placeholder data shown as real** — a credibility risk for an observability+security product: routing's hardcoded "99.2%" success rate, Portal usage's fake bar chart, per-project "0 requests", dashboard "Next Reset —". *(S5, fix F6 — needs your decision.)*
5. **Native browser `confirm/prompt/alert` + spinner-only loading** amid polished Modal/Toast/Skeleton systems — jarring and off-brand across 6 pages (dialogs) and all 7 Portal app pages (spinners). *(S3/S4, fix F4/F5.)*

Also public-facing and embarrassing: marketing `/security` shows "compliance badges" as **plain bold text**, and `/docs` ships as a **"coming soon" placeholder**.

---

## Q3 — Foundation work that must come first (and what it unblocks)

Seven system-level fixes remove the recurring issues at the source, shrinking every per-page Tier afterward:

| | Foundation item | Unblocks |
|---|---|---|
| **F1** | Admin icon module + glyph→SVG sweep | ~14 pages (S1) |
| **F2** | Theme-token sweep (hex → semantic tokens) | dark-theme correctness, ~11 pages (S2) |
| **F3** | Admin token-namespace consolidation | F2 + future drift (S6) |
| **F4** | Replace native dialogs + lint-ban | 6 pages (S3) |
| **F5** | Portal skeleton presets (hero/stat/table/grid) | 7 Portal app pages (S4) |
| **F6** | Honest-metrics pass *(decision per item)* | trust issue (S5) |
| **F7** | P0 bug bundle (auth CSS import, invite contrast) | cold-load styling |

**Do Foundation before per-page work** — F1+F2+F4+F5 are the bulk of the value.

---

## Q4 — Total effort estimate

**≈ 4 sprints (8 weeks solo)**, no Rebuilds required:

1. **Sprint 1 — Foundation** (F1–F7) + get F6 metric decisions.
2. **Sprint 2 — Tier 1 Critical:** auth recovery, invite contrast, `/security`, `/docs`, `/dlp-detectors`, Portal high-traffic skeletons, `/requests` responsive.
3. **Sprint 3 — Tier 2 High:** apply F1/F2 sweeps across med-traffic pages; motion cleanup; pricing/oauth/usage/team/settings.
4. **Sprint 4 — Tier 3 polish:** low-traffic batch + marketing legal + auth nits.

7 pages are **Keep As-Is (ห้ามแตะ)** — only Foundation sweeps touch them incidentally.

---

## Decisions needed from you before Sprint 1

1. **Honest-metrics (F6):** for each of {routing 99.2%, usage fake chart, projects 0-requests, projects/[id] account-wide stats, dashboard "Next Reset"} — **wire / label "Preview" / hide?**
2. **Cross-app unification:** keep Admin (Tailwind v4 + inline-SVG) and Portal (v3 + lucide) as two deliberately-separate systems (recommended), or open a separate unification track?
3. **`/signup` pre-checked TOS box:** unchecking is a consent/legal call — confirm before changing.
4. **Markdown rendering** in admin conversation detail is functional — confirm it stays in the Functional Audit, not this visual track.

---

*Deliverables: `audit/00-executive-summary.md`, `01-inventory.md`, `02-pages.md` (all 45 page blocks with file:line evidence), `03-systemic.md` (12 cross-cutting findings), `04-roadmap.md` (foundation + 4 tiers + sequencing).*

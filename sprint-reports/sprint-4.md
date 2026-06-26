# Sprint 4 Report — Final polish + re-audit

> Branch: `sprint-4-ui` (off `sprint-3-ui`). 14 per-task polish commits + 2 report commits. Both apps `tsc --noEmit` clean. Full scores + deltas in **`final-delta.md`** (the Day-5 deliverable); this file captures decisions/notes.

## What shipped (Day 1–3, 16 polish items + 3 backlog cleanups)
Committed per task (14 commits). Executed via 3 parallel scoped subagents (admin A, admin B, portal C — no file overlap), then consolidated `tsc` + per-task commits by me.
- **Admin (13):** chat-conversations boolean badges + drop dup inputStyle; chat-conversation-detail role-tinted tiles + larger micro-text; alerts severity/delivery filter pills + bell empty icon (added `bell` to `Icon.tsx`); ai-review `<td>`→class + skeleton; usage → `.sran-table` + EmptyState; chat-knowledge/[id] → shared `StatCard`; providers key-status skeleton + focus parity; models focus parity; admin login flatten gradient + focus-visible/aria-invalid; chat-users/dlp-coverage/search-settings nits; routing `IconCircleCheck` removed; portal-settings/portal-token-packs/models verified (no change needed).
- **Marketing legal (2):** privacy + terms anchor TOC, demoted "Last updated" caption, clause dividers.
- **Portal auth (2):** signup live password-match hint; login `?reset=success` banner + field-shaped Suspense skeleton + larger headline.
- **Backlog cleared:** dead `.sv-*` CSS removed; routing `IconCircleCheck` removed; `?reset=success` flash wired; **TOS-uncheck** (the locked decision that had slipped Sprints 1–3 — caught up here).

## Decisions taken during execution
1. **chat-knowledge task targeted `[id]`** — the local `StatCard` duplicate the brief referenced lives in `chat-knowledge/[id]`, not the list page; refactored that to the shared component.
2. **models/providers focus parity** — added `onFocus/onBlur` mirroring the hover-lift but did **not** add `tabIndex` to the non-interactive grid `<div>` (that would inject ~15 dead tab stops); documented per the subagent.
3. **chat-users toggle** — kept as a state-aware token-colored button rather than swapping to the animated `Toggle` (no shared Toggle component; full swap would be heavy). Re-audit scored it Δ0 (flat) — logged.
4. **TOS-uncheck** — applied (`useState(true)`→`false`) as the locked decision; it was never scheduled in any prior sprint's task list.
5. **Re-audit method** — independent fresh-eyes agents re-scored the 31 changed non-Keep pages; the 7 Keep-As-Is pages held at baseline (only incidental sweeps touched them). Several pages honestly came back **Δ0** (not inflated) — surfaced in final-delta as remaining opportunities.
6. **`/oauth-complete` stays Major (25)** — branded but plain Tailwind / no entrance motion; flagged for the next track rather than over-built here.

## Hard rules
- Per-task commits; tree deployable throughout (tsc clean each step). No logic/API/routing/dep changes. Keep-As-Is pages touched only by incidental sweeps. SRAN Chat untouched. Absolute git paths (no `cd`/redirect compounds).

## Result
Avg **31.6 → 33.3**, Major-Refactors **6 → 1**, Keep **7 → 12**, **0 Rebuilds**, no regressions. See `final-delta.md` for the per-page table + consolidated backlog + recommended next track.

## ✋ STOP — awaiting Beat's review of `final-delta.md`.

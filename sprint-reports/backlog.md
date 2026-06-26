# Sprint Backlog (deferred findings)

Items discovered during sprints that were out of the current sprint's scope. Pull into a future sprint; do not insert mid-sprint.

## From Sprint 1
- **ESLint toolchain not installed** (both apps). The `.eslintrc.json` no-restricted-globals ban is dormant until `eslint` + `@typescript-eslint/parser` are installed (admin-ui has no lint script at all). When installing, also add `no-restricted-properties` to catch `window.confirm/alert/prompt` member calls (the globals rule only catches bare calls). Consider `eslint-config-next` for proper TSX parsing.
- **chat-analytics axis-tick colors** (`admin-ui/app/chat-analytics/page.tsx:279,283`): hardcoded `rgba(180,200,230,0.7)` is light-on-dark. Passed to recharts as SVG `fill` **attribute** → CSS `var()` won't resolve. Needs `getComputedStyle` of a token or a theme-neutral color. (Excluded from Sprint 1 F2.)
- **invite invalid-branch eyebrow** (`portal/app/(auth)/invite/[code]/page.tsx:~100`): red text (`#F87171`) on the violet→cyan `.sv-form-eyebrow` gradient pill. Give the error eyebrow a red-tint background. (Sprint 1 #13 fixed only the CTA.)
- **Dead `.sv-*` CSS** in `portal/app/(auth)/signup/signup.css` (`.sv-divider`, `.sv-socials`, `.sv-social`): orphaned after signup OAuth → `<SocialButtons/>` swap. Remove in a cleanup pass.
- **portal `SocialButtons` "coming soon" uses 💡 emoji** (`portal/components/SocialButtons.tsx:22`): portal emoji sweep is a separate future batch (Sprint 1 F1 was admin-only).
- **routing `IconCircleCheck`** now unused (`admin-ui/app/routing/page.tsx:~197`): kept for the future re-add of the success-rate tile; remove if the tile isn't reinstated.
- **admin token-namespace drift**: `--color-*` (legacy) vs `--bg-*/--text-*/--accent-*` used inconsistently across admin pages (e.g. chat-users, chat-analytics). Consolidate (UI-audit F3) — Sprint 3.
- **`?reset=success` flash unused**: `reset-password` redirects to `/login?reset=success` but `LoginForm` ignores `?reset` — surface a success banner on /login. (UI-audit Sprint 3 nit.)

## Carried from the UI audit roadmap (already planned, not new)
See `audit/04-roadmap.md` Tiers 2–4 for the full per-page polish list (Sprints 2–4): marketing `/security` + `/docs`, `/dlp-detectors` polish, `/requests` responsive, remaining portal skeletons (usage/team/settings), pricing/oauth polish, `/signup` TOS-uncheck + live match, legal-page TOCs, etc.

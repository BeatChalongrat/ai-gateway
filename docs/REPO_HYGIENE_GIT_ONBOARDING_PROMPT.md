# REPO HYGIENE — get the whole monorepo into git safely (no secrets, no build junk)

Goal: bring the entire `ai-gateway` monorepo under version control and up to date, INCLUDING the two directories that are currently **completely untracked** (`services/mcp-gateway/` and `SranDesktop/` — no git history at all, no CI, no safety net), plus all the pre-existing uncommitted work across the other stacks.

**This is a safety-critical operation.** A naive `git add -A` on never-tracked directories will sweep in secrets and gigabytes of build artifacts. Do NOT run `git add -A` / `git add .` until the ignore rules below are in place and verified. Work methodically.

## Absolute rules (never commit these)

- **Secrets:** any `.env` (root `.env` contains the real master encryption key, portal JWT secret, provider keys — verified via `backend/app/core/config.py` secret validation), `*.pem`, `*.key`, `credentials.dat`, any DPAPI blob, service-account JSON.
- **Local state / user data:** `*.db`, `*.sqlite*` (esp. SranDesktop `data.db`, `tasks.db` — real conversation content), `settings.json`/`connectors.json` under an app-data path, `logs/`, `*.log`.
- **Build outputs:** C# `bin/`, `obj/`, `publish/`, `*.user`; Node `node_modules/`, `.next/`, `dist/`; Rust `target/`; Python `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.venv/`, `venv/`; the vendored `SranDesktop/.../Assets/WebLibs/*.min.js` decision — see step 4.
- **Editor/OS:** `.vs/`, `.idea/`, `.DS_Store`, `Thumbs.db`.

## Step 0 — Survey (do this first, report findings before changing anything)

1. `git rev-parse --show-toplevel` — confirm the repo root. `git status --porcelain=v1 | wc -l` and a summarized `git status` — how much is dirty/untracked.
2. List every existing `.gitignore` in the tree (`git ls-files '**/.gitignore'` + find untracked ones) and read the root one in full.
3. For the two untracked dirs, see what WOULD be added (dry run, does not stage): `git add -n services/mcp-gateway SranDesktop 2>/dev/null | head -200` and `git status --porcelain services/mcp-gateway SranDesktop | wc -l`. Report whether `bin/`, `obj/`, `publish/`, `node_modules/`, `*.db`, `.env*` appear in that list.
4. Check for the stray Windows-redirect artifact directories the audit flagged: `log-worker;C/`, `services/media_gen/adapters;C/` (and any `*;C` / `*;C*` path). These look like accidental `> path\C…` shell mistakes. Confirm they're junk (compare against the real `log-worker/` / `adapters/`) — they are almost certainly to be **removed**, not committed. Do NOT delete anything destructive without listing it first and confirming it's a dup of a real dir.
5. Scan for secrets that may ALREADY be staged/tracked or about to be: `git ls-files | grep -iE '\.env$|\.pem$|\.key$|credential|secret' ` and check nothing sensitive is already tracked. If a secret is already tracked, FLAG IT LOUDLY (that's a pre-existing leak to fix separately — do not silently proceed).

Report all of step 0 before proceeding.

## Step 1 — Ignore rules FIRST

1. **Root `.gitignore`:** ensure it covers the cross-cutting patterns (secrets, editor/OS, `*.db`, `*.log`, `logs/`). Add anything missing. Do NOT loosen existing rules.
2. **`SranDesktop/.gitignore`** (create if absent — it's untracked so likely has none, or only the 55-byte stub seen earlier): a proper .NET ignore — `bin/`, `obj/`, `publish/`, `*.user`, `.vs/`, `TestResults/`, plus `*.db`/`*.sqlite*` for any local test DBs. Keep source, `.csproj`, `.sln`, `.iss`, `Assets/` (icons), tests.
3. **`services/mcp-gateway/.gitignore`** (create if absent): `node_modules/`, `dist/`, `*.log`, `.env*`.
4. **WebLibs decision** (`SranDesktop/src/SranDesktop.Presentation/Assets/WebLibs/*.min.js`, ~6.6 MB vendored React/Babel/Mermaid): these are BUILD INPUTS the app ships (offline artifact rendering, Phase 6) — they SHOULD be committed (they're required to build/run and aren't reproducible without a fetch step). Confirm they are NOT matched by any `*.min.js` ignore; if an ignore would catch them, add a negation (`!Assets/WebLibs/*.min.js`) or commit with `-f` and note it. State the decision.
5. Verify each project's ignore works: `git status --porcelain --ignored services/mcp-gateway SranDesktop | grep '!!' | head` should now show `bin/obj/node_modules/.env` as ignored.

## Step 2 — Remove the stray artifacts (only if Step 0.4 confirmed they're junk)

`git rm -r --cached` is irrelevant (they're untracked); just delete the confirmed-junk `*;C` directories from disk (`rm -rf 'log-worker;C' 'services/media_gen/adapters;C'` and any sibling), after listing their contents one last time to be sure they're throwaway duplicates. If there's ANY doubt they contain unique work, leave them and flag instead.

## Step 3 — Stage in logical, reviewable commits (not one mega-commit)

Use explicit pathspecs. Suggested grouping (adjust to what's actually dirty):

1. `chore: gitignore rules for mcp-gateway and SranDesktop` — the ignore files alone, first.
2. `feat: add SranDesktop desktop app (Phases 1–10)` — `git add SranDesktop` (now that ignores exclude bin/obj/publish/db). Verify the staged file list contains ZERO `bin/`, `obj/`, `publish/`, `*.db` entries before committing.
3. `feat: add mcp-gateway service (Phase 7 + Phase 10 hardening)` — `git add services/mcp-gateway`. Same verification.
4. The pre-existing dirty work in `backend/`, `services/chat-edge-api/`, etc. — group by stack/concern with explicit pathspecs. The user has accepted committing this; still keep secrets/artifacts out.

Between each `git add` and its `commit`, run **`git diff --cached --stat`** and scan for anything that shouldn't be there. Before the FIRST content commit, also run a staged-secret grep: `git diff --cached -U0 | grep -iE 'api[_-]?key|secret|password|BEGIN (RSA|EC|OPENSSH|PRIVATE)|sk-[a-z0-9]|gw_[a-z0-9]' ` — investigate every hit (test fixtures with fake keys are fine; a real key is a stop-everything).

## Step 4 — Final verification (report all)

1. `git status` clean (or only intentionally-left items). `git ls-files services/mcp-gateway SranDesktop | wc -l` — sane counts (hundreds of source files, not tens of thousands of node_modules/bin entries).
2. **Prove no secret/artifact slipped in:** `git ls-files | grep -iE '\.env$|node_modules/|/bin/|/obj/|/publish/|\.db$|target/|__pycache__'` returns EMPTY. State this explicitly.
3. Total repo size sanity: `git count-objects -vH` before/after, and confirm the added size is proportional to source (a few MB + the ~6.6 MB WebLibs), not hundreds of MB.
4. Confirm the tree still builds from the tracked files only (nothing needed was ignored): note that `dotnet build SranDesktop/SranDesktop.sln` and `npm ci --prefix services/mcp-gateway` would work from a fresh clone (WebLibs present, package.json present) — don't necessarily run a full build, but verify the required non-ignored inputs are all tracked.
5. Summarize: commits made, what's now tracked, what's intentionally ignored, any secret/junk you caught and excluded, and anything you left for the user to decide.

## Do NOT

- Do not `git push` — leave that to the user (they may want to review, and remote/branch setup is theirs).
- Do not rewrite history or touch existing commits.
- Do not commit if a real secret is staged — stop and report.

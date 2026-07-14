# REPO — line-ending normalization (.gitattributes) + CI wiring

Two related hygiene tasks on the `ai-gateway` monorepo (now fully under git, 8 commits, `89a642f..8cdb6b3`). PART A fixes the 749 phantom-modified files (all line-ending only — proven: `git diff --ignore-all-space --stat` is empty, zero content changes). PART B stands up CI now that every stack is finally tracked. Do PART A first — CI on an un-normalized tree will hit EOL noise across platforms.

Do NOT `git push` — leave that to the user.

## Context (verified)

- No `.gitattributes` exists at the repo root; `core.autocrlf` is unset. Windows source (CRLF: `.sln`, `.csproj`, `.xaml`, `.iss`) accessed cross-platform shows as wholesale-modified. `.env`/`bin`/`obj`/`publish`/`node_modules`/`*.db`/secrets are all correctly untracked (verified) — do not change that.
- Four stacks with existing test suites: backend (pytest), chat-edge-api (`go test ./...`), mcp-gateway (`npm test`, node:test), SranDesktop (`dotnet test`, net8.0-windows → **needs a Windows runner**).
- There may already be a `.github/workflows/ci.yml` (it showed as modified in the last status) — read it first; extend rather than duplicate.

---

## PART A — `.gitattributes` + renormalize

1. Create a root `.gitattributes`:
   - `* text=auto` as the default (git normalizes to LF in the repo, checks out native).
   - Force **CRLF** for Windows-only files that tools expect CRLF: `*.sln`, `*.csproj`, `*.xaml`, `*.props`, `*.targets`, `*.iss`, `*.bat`, `*.cmd`, `*.ps1`.
   - Force **LF** for cross-platform/script files: `*.sh`, `*.go`, `*.py`, `*.js`, `*.ts`, `*.tsx`, `*.json`, `*.yml`, `*.yaml`, `*.css`, `*.md`, `Dockerfile`, `Makefile`, `*.proto`.
   - Mark binaries so git never touches them: `*.png binary`, `*.ico binary`, `*.min.js binary` (the WebLibs bundles — no EOL munging, keeps them byte-stable), `*.pdf binary`, `*.woff2 binary`.
2. Renormalize the tree to match the new rules in ONE commit: `git add --renormalize .` then verify `git diff --cached --stat` contains ONLY line-ending churn (no real content — cross-check a couple files with `git diff --cached --ignore-all-space` on them → empty). Commit: `chore: normalize line endings via .gitattributes`.
3. Verify after commit: `git status` is clean, and `git diff --ignore-all-space --stat` is empty. If any file STILL shows dirty, it's a real content change that was sitting in the working tree — list it and STOP for the user rather than sweeping it into the EOL commit.
4. Note the environment quirk: `.git/index.lock` has thrown `Operation not permitted` on the Linux mount (stale-lock/permission issue, not a repo problem). If it blocks a git write, remove the stale lock only if no git process is running, and note it; the user's native Windows environment won't hit this.

## PART B — CI

Goal: every push/PR runs each stack's existing tests, on the right OS, without secrets.

1. **Read the existing `.github/workflows/ci.yml`** (and any sibling workflows) first; report what's already there. Extend it into a matrix (or one job per stack) covering:
   - **backend** — `ubuntu-latest`, Python (match `backend`'s version; check `pyproject.toml`/`runtime`), install deps, `pytest`. Provide the safe env the tests need (test DB via a `postgres` service container if the suite requires one; DLP/secret env from **non-secret test defaults**, never real keys — the config validator accepts the dev all-zero master key in test mode, confirm how tests bootstrap secrets and mirror that).
   - **chat-edge-api** — `ubuntu-latest`, Go (match `go.mod`), `go build ./... && go test ./...`. Note: the import e2e uses a live Postgres — gate that suite behind a service container OR the same env flag the suite already uses to skip when no DB (check how `import_service_test.go` decides), so CI is green without secrets.
   - **mcp-gateway** — `ubuntu-latest`, Node (match `package.json` engines / `.nvmrc` if any), `npm ci && npm test`.
   - **SranDesktop** — `windows-latest` (net8.0-windows/WPF cannot build on Linux), `dotnet build SranDesktop/SranDesktop.sln -c Release` + `dotnet test`. This is the one that MUST be on a Windows runner.
2. **No secrets in CI:** do not add repo secrets for this. Every suite must run against test defaults/mocks/service-containers. If a suite genuinely can't run without a real secret, mark it clearly skippable and exclude it from the required checks (state which).
3. **Fast feedback:** `paths` filters so a desktop-only change doesn't spin up the backend matrix unnecessarily is nice-to-have, not required — but at minimum cache deps (pip, go build, npm, NuGet) per stack.
4. Add a lightweight **DLP egress-invariant gate** callout: ensure `backend/tests/test_dlp_egress_invariant.py` runs in the backend job (it's the Phase 9 drift guard — CI is exactly where it earns its keep). Don't let it be skipped.
5. Do a syntax/logic check of the workflow (`actionlint` if available, or careful review) — a workflow that never triggers or references a missing script is worse than none.

---

## Verification checklist

1. PART A: `git status` clean; `git diff --ignore-all-space --stat` empty; the WebLibs `.min.js` bundles are byte-identical to before (they're `binary` now — `git diff HEAD~1 -- '*/WebLibs/*'` should show no content churn from the normalize commit). One commit, EOL-only.
2. PART B: the workflow file parses; each job names the right OS (desktop on Windows) and runs the stack's real test command; no real secret is referenced; the Phase 9 egress gate is in the backend job. If you can't execute the workflow here, state that and show the reasoning that each job would run from a clean clone (deps tracked, commands correct).
3. Summarize: the `.gitattributes` rules, the renormalize commit hash, the CI jobs added/extended, and anything left for the user (e.g., enabling the workflow, branch protection, the still-pending `git push` and the `/data/workspace` migration).

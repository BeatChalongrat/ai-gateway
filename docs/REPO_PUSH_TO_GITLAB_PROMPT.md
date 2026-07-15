# REPO — push the whole program to GitLab (safe, no force, no secrets)

Push the committed work (~157 commits, Phases 1–13 + hygiene) to the GitLab remote:

**`https://gitlab-dev.gbtech.co.th/srandev/ai-gateway`**

Nothing has been pushed this entire program. This is a one-way publish of existing commits — no history rewrite, no force. Work methodically and STOP for the user on anything ambiguous.

## Absolute rules

- **Never `git push --force` / `--force-with-lease`.** If the push is rejected (non-fast-forward), STOP and report — do not overwrite the remote.
- **Never push if a real secret is tracked.** Re-run the secret check below first; a hit is a stop-everything.
- **Never print or paste credentials/tokens** into output, commits, or the remote URL. Use the machine's existing git credential setup.
- Do not rewrite, squash, or amend any commit. Push what's committed, as-is.

## Step 0 — Confirm the remote

1. `git remote -v` — report what's there.
2. Compare to the target `https://gitlab-dev.gbtech.co.th/srandev/ai-gateway(.git)`:
   - If an `origin` already points at that URL → use it.
   - If `origin` exists but points ELSEWHERE → do NOT clobber it; add the target as a new remote named `gitlab` (`git remote add gitlab <url>`) and push there. Report the choice.
   - If no remote exists → `git remote add origin <url>`.
3. Report the exact remote name + URL you will push to, and confirm it's the GBTech GitLab, before pushing.

## Step 1 — Confirm what will be published

1. `git status` — report the current branch and whether the tree is clean.
   - Note: on a Windows checkout the tree should be clean; if you see many "modified" files, verify they are line-ending-only with `git diff --ignore-all-space --stat` (should be empty). If `--ignore-all-space` shows REAL content changes, there is uncommitted work — STOP and list it; do not push a half-state (the push only sends commits, but the user needs to know real work is uncommitted).
2. `git log --oneline origin/main..HEAD | wc -l` (or the relevant tracked branch) — report how many commits will be pushed and the range (`git log --oneline -1` for HEAD, and the oldest unpushed).
3. Confirm the current branch is the one to publish. Default: push the CURRENT branch to a same-named branch on the remote. If the current branch is `main`/`master` and pushing straight to it on a shared dev server is undesired, offer the alternative (push to a feature branch like `feat/hardening-phases-1-13` + let the user open a Merge Request) — but do NOT decide unilaterally if it's `main`; report and pick the safe default (current branch → same name) unless the user said otherwise.

## Step 2 — Final secret sweep (belt-and-suspenders before a remote publish)

1. `git ls-files | grep -iE '\.env$|node_modules/|/bin/|/obj/|/publish/|\.db$|\.sqlite|target/|__pycache__|\.pem$|\.key$|credentials\.dat'` → MUST be empty. If not, STOP.
2. `git grep -iE 'BEGIN (RSA|EC|OPENSSH|PRIVATE) KEY|sk-[a-zA-Z0-9]{20}|gw_[a-zA-Z0-9]{20}|AKIA[0-9A-Z]{16}' $(git rev-parse HEAD)` (or scan the tip tree) for hardcoded secrets in tracked content → investigate every hit; test fixtures with fake values are fine, a real key is a stop. Report what you found.
3. Confirm `.env` (root) and any real key file are NOT tracked (`git ls-files | grep -E '^\.env$'` empty).

## Step 3 — Push (no force)

1. `git push -u <remote> <current-branch>` (set upstream). Plain push only.
2. **Auth:** rely on the machine's configured git credentials (credential manager / SSH / stored PAT). If the push fails with an auth error, STOP and report that the user needs to configure a GitLab credential (a Personal Access Token with `write_repository`, or SSH key) — do NOT attempt to embed a token in the URL or prompt for one in plaintext.
3. **If rejected as non-fast-forward** (remote has commits you don't): STOP. Report the divergence and the options (`git pull --rebase` then re-push, or push to a new branch) — do not force, do not merge blindly.
4. Large first push is expected (~157 commits + WebLibs + history); let it complete.

## Step 4 — Verify

1. `git status` shows the branch up to date with (or ahead by 0 of) the remote tracking branch.
2. `git log <remote>/<branch> -1 --oneline` matches local HEAD.
3. Report: remote + branch pushed, commit count published, HEAD sha now on the remote, and the MR URL if GitLab printed a "create merge request" hint.

## Report

Summarize: the remote used, the branch, how many commits were published, that no force/secret was involved, and anything left for the user (open an MR, set branch protection, configure CI variables on GitLab so the pushed `.github` or `.gitlab-ci` pipelines can run — note GitLab uses `.gitlab-ci.yml`, so if only GitHub Actions `.github/workflows` exist, CI won't run on GitLab without a `.gitlab-ci.yml`; flag that as a follow-up, don't build it here).

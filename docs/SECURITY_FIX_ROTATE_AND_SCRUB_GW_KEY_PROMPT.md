# SECURITY — rotate + scrub the exposed gateway API key (P0, today)

A real-looking gateway API key is hardcoded in test scripts and has been sitting on the shared GitLab remote (`gitlab-dev.gbtech.co.th/srandev/ai-gateway`, on `main` since old commit `0bea831`). **Treat it as compromised.** This task has THREE parts with different risk/coordination levels — do 1 and 2 now; 3 is a coordinated history rewrite (warn the team, do NOT do it unilaterally without buy-in).

Exposed value + locations (verified):
- `REMOVED_CREDENTIAL`
- `scripts/pseudonym_real_llm_e2e.py:12`
- `scripts/round4_e2e.py` (same literal — grep to find the exact line)

## PART 1 — Rotate the key (the ONLY action that actually removes the risk) — MANUAL, do first

Scrubbing code and purging history do NOT protect a key that's already public — **rotation does.** This is a gateway action, not a code edit:

1. Identify the key in the gateway's key store — it's a `gw_` project key (portal `POST/GET /portal/projects/{id}/keys`, and there's a rotate/delete path from the Phase 7 connector work — R0: read `backend/app/api/routes/portal_projects.py` for the exact rotate/revoke endpoint, e.g. `POST .../keys/{key_id}/rotate` or `DELETE .../keys/{key_id}`).
2. **Revoke/rotate it** so the exposed value stops working. Verify the old value is rejected (a request with `Authorization: Bearer gw_4be1832b…` → 401). Do this BEFORE worrying about history — once revoked, the leaked string is worthless.
3. If you can't tell whether it's live: assume it is, revoke it. A test key that was real enough to hardcode is real enough to rotate.

State in the summary: which endpoint you used, and proof the old key is now rejected.

## PART 2 — Scrub it from the two scripts (code hygiene, going forward)

So the next commit / future clones don't carry it, and CI/local runs read it from the environment.

1. In `scripts/pseudonym_real_llm_e2e.py` and `scripts/round4_e2e.py`: replace the hardcoded literal with `os.environ["GW_API_KEY"]` (or the project's existing config pattern — R0: check how other scripts read secrets; there may be a settings/env helper). Fail fast with a clear message if the env var is unset (don't fall back to a hardcoded default).
2. Grep the WHOLE repo for the same literal and any other `gw_`/`sk-`/`AKIA`/PEM-shaped hardcoded secrets in tracked files (`git grep -nE 'gw_[a-f0-9]{40}|sk-[A-Za-z0-9]{20}'`) — scrub every real one; leave obvious test fixtures (the base64 PEM filler in `test_streaming_dlp.py`) but note them.
3. Add the env var to `.env.example` (as an empty placeholder with a comment) so the requirement is discoverable, and confirm `.env` stays git-ignored.
4. Commit: `security: read gateway key from env, remove hardcoded test key`. Push to `gitlab main` (normal fast-forward, no force). This does NOT remove it from history — that's PART 3.

## PART 3 — Purge from history (coordinated, force-push — do NOT do unilaterally)

Once rotated (PART 1), the historical copy is a dead string — purging is compliance/hygiene, not urgent. It IS a history rewrite affecting everyone who cloned, so:

1. **Coordinate first.** `chat-folders`/the repo shows other users. Warn them: a history rewrite is coming, they'll need to re-clone or hard-reset. Get buy-in before rewriting shared `main`. If you can't coordinate, STOP after PART 2 and leave PART 3 as a scheduled task.
2. Use `git filter-repo` (preferred) or BFG to remove the literal from all of history:
   - `git filter-repo --replace-text <(echo 'REMOVED_CREDENTIAL==>REDACTED')` (filter-repo replace-text over the whole history), OR BFG `--replace-text`.
   - This rewrites SHAs. Verify the literal is gone from every commit (`git log -S 'gw_4be1832b' --oneline` → empty).
3. Force-push the rewritten `main` to gitlab — **the one time `--force-with-lease` is justified** — after the team is warned. Do NOT `--force` blindly; use `--force-with-lease` so you don't clobber a concurrent push.
4. Everyone re-clones or `git reset --hard`. Any fork/mirror (your GitHub `origin`) needs the same treatment or it re-leaks.
5. Note: even after purge, assume the key was harvested while public — PART 1's rotation is what actually protects you; PART 3 just stops re-distribution.

## Final report

- PART 1: endpoint used, old key now rejected (proof).
- PART 2: files scrubbed, env var wired, `.env.example` updated, commit pushed (fast-forward).
- PART 3: done (with team coordination + force-with-lease) OR explicitly deferred as a scheduled coordinated task, with the reason.
- Any other hardcoded secrets found in the sweep.

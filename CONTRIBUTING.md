# Contributing

## Before you push: run the gate locally

```bash
make ci
```

`make ci` runs the same three checks CI runs, against the running dev stack
(`make up` first):

| Target          | What it runs                                              |
|-----------------|-----------------------------------------------------------|
| `make test-unit`| `pytest -m "not integration"` in the backend container    |
| `make typecheck`| `npx tsc --noEmit` for `apps/web-chat`                     |
| `make go-test`  | `go vet ./... && go test ./...` for `services/chat-edge-api` |

`make test-all` runs the full backend suite including the integration lane —
that needs the live stack (DLP sidecar, NER, Postgres, gRPC limiter) and is
**not** part of the push gate. The push gate is the unit lane only.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every PR
targeting `main`. Three independent jobs (no build / deploy step — this is a
test gate):

- **backend (pytest, unit lane)** — `pytest -m "not integration"`. Installs
  `backend/requirements-dev.txt` only (no presidio/spaCy/semantic extras —
  those power the integration lane, which is excluded).
- **web-chat (tsc --noEmit)** — `npm ci` + `npx tsc --noEmit`.
- **chat-edge-api (go vet + test)** — `go vet ./...` + `go test ./...`.

All three must pass. The unit lane is the contract that must stay green
(2230+ tests, 0 failures); the integration lane is exercised against a full
stack, not in CI.

## Branch protection (one-time admin setup)

Branch protection is GitHub-side state, not a file in the repo, so it can't
be committed. Apply it once with the GitHub CLI (`gh auth login` as a repo
admin first). This requires the three CI checks to pass before `main` can be
updated and forbids force-pushes:

```bash
gh api -X PUT repos/BeatChalongrat/ai-gateway/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=backend (pytest, unit lane)' \
  -f 'required_status_checks[contexts][]=web-chat (tsc --noEmit)' \
  -f 'required_status_checks[contexts][]=chat-edge-api (go vet + test)' \
  -f 'enforce_admins=false' \
  -f 'required_pull_request_reviews=' \
  -f 'restrictions=' \
  -f 'allow_force_pushes=false' \
  -f 'allow_deletions=false'
```

Notes:
- `required_status_checks[contexts]` must match the job `name:` values in
  `ci.yml` exactly — update both together if a job is renamed.
- `enforce_admins=false` lets a solo maintainer merge an admin hotfix without
  a second reviewer; flip to `true` once the team grows.
- PR reviews are left off (`required_pull_request_reviews=`) since the repo is
  currently maintained solo. To require one approving review, replace that
  line with
  `-F 'required_pull_request_reviews[required_approving_review_count]=1'`.

Verify the result:

```bash
gh api repos/BeatChalongrat/ai-gateway/branches/main/protection \
  --jq '.required_status_checks.contexts'
```

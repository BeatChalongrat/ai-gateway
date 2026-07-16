# Security 17A — Purge the committed admin JWT

## What was committed

`admin-ui/.tmp_bugs/` was tracked in git and contained:

- **`tok`** — a real HS256 admin **access** JWT:
  `sub=22f0d459-a397-496b-b443-c82f0b5039b7`, `email=admin@example.com`,
  `username=admin`, `role=admin`, `type=access`, `iat=1775911373`,
  `exp=1775997773` (**already expired ~2026-04-13**).
- **`body.json`** — a DLP test payload with a *fake* `sk-ant-api03-…` placeholder
  key (not a real credential).

## Risk assessment

- The file held a **token**, not the signing secret. An HS256 JWT does not reveal
  its `JWT_SECRET`, so the secret was **not** disclosed by this commit.
- The token is an **access** token (short-lived) and is **already expired**, so it
  cannot be replayed now.
- Residual risk: it was on the shared GitLab remote, and it demonstrates the
  `admin` account's `sub`/identity. Treat the admin credential as compromised and
  rotate as defense-in-depth.

## Done in this commit (code/tree)

1. `git rm -r admin-ui/.tmp_bugs/` — removed from the working tree and index.
2. Added `.tmp_bugs/` + `**/.tmp_bugs/` to root `.gitignore` so a scratch dir can
   never be re-committed.

## Operational actions (require deployment access — NOT doable from the repo)

### 1. Rotate `JWT_SECRET` (invalidates every existing admin token)

The admin token is signed with `Settings.jwt_secret` (`backend/app/core/config.py`,
default `"change-me"`; production is *forced* off the default by
`validate_secrets()` → `_check("JWT_SECRET", …)`, `config.py:708`).

```bash
# Generate a new secret and set it in the backend's environment / secret store:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# → set JWT_SECRET=<new value>  (and PORTAL_JWT_SECRET similarly if desired)
# Restart the backend. Every token signed with the old secret now fails
# signature verification → 401.
```

**Prove the old token is rejected** (after rotation, against the running backend):

```bash
# Uses the (now-expired, old-secret) token — expect HTTP 401.
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $(cat /path/to/old/tok)" \
  https://<host>/api/v1/admin/  # any admin-guarded route
# Expected: 401 (expired AND signed with a retired secret)
```

Also rotate the `admin@example.com` account password if that account is real in
any environment (the default seed is `admin` / `admin1234`).

### 2. History purge (the token is in past commits)

Removing the file in a new commit does **not** erase it from history. Coordinate a
history rewrite with the team (everyone must re-clone / reset after):

```bash
# Warn the team first — this rewrites shared history.
pip install git-filter-repo
git filter-repo --path admin-ui/.tmp_bugs --invert-paths
# Re-add the remote (filter-repo drops it), then:
git push --force-with-lease origin main
```

Because rotation (step 1) is what actually neutralizes the credential, do step 1
**immediately**; step 2 is cleanup that can follow the team coordination window.

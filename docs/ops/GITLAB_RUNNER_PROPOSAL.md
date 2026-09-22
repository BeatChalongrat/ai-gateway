# GitLab runner for project 163 — proposal (FIX127 E1)

**Status: proposal only. Nothing is registered or installed until the owner approves.**
Until then: run gitleaks locally before every push (CARD-RULES §5).

## Why

`.gitlab-ci.yml` defines 11 jobs (backend, web-chat, portal/admin-ui, chat-edge, gateway,
mcp-gateway, secret-leak-guard, gitleaks, web-e2e, four audits, desktop-test). The project has
had **zero runners** since it was created, so no pipeline has ever run and every job — including
the gitleaks secret scan added in FIX126 — has only ever been exercised locally.

## Recommendation: one Docker-executor runner on the owner's Linux box or a small VM

Option A (recommended): a Linux host (Ubuntu 22.04/24.04, 2 vCPU / 4 GB is enough for the
test gate) running `gitlab-runner` with the **docker executor**. Every Linux job in the file
names an image (`python:3.11`, `node:20`, `golang:1.25`, `rust:latest`, the gitleaks image), so
the docker executor runs them as written, isolated per job, with no toolchains on the host.

Option B: a **Windows shell runner** on the owner's development PC, tagged `windows`, for the
`desktop-test` job only (WPF cannot build on Linux). Optional; the job is `manual` +
`allow_failure` until this exists. Not recommended on the PC that runs the live stack (a CI
job competing with the owner's Docker stack for CPU/disk during a build is the failure mode).

Do **not** register a shared/instance runner from the GitLab UI's "enable shared runners" if
the instance is self-hosted without any — that changes nothing; a specific runner is needed.

## Steps (Option A) — for approval, not yet executed

1. Provision the host (or reuse an existing Linux box). Install Docker Engine (`apt install
   docker.io`, or the official repo) and confirm `docker run hello-world` works.
2. Install gitlab-runner:
   ```
   curl -L "https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh" | sudo bash
   sudo apt-get install gitlab-runner
   ```
3. In GitLab: **Project 163 → Settings → CI/CD → Runners → New project runner**. Tags: leave
   empty or `linux,docker`; tick **Run untagged jobs** (the Linux jobs carry no tags). Copy the
   runner authentication token (`glrt-…`). The token is a secret: it goes into the runner's
   `config.toml` only, never into the repo or a card.
4. Register (non-interactive, docker executor, a default image for jobs that omit one):
   ```
   sudo gitlab-runner register --non-interactive \
     --url https://<gitlab-host>/ --token glrt-… \
     --executor docker --docker-image alpine:3.20 \
     --description "ai-gateway linux docker" \
     --docker-volumes /cache
   ```
5. Concurrency: `concurrent = 2` in `/etc/gitlab-runner/config.toml` (the pipeline has 11
   jobs; 2 at a time finishes the gate in ~10–12 min with warm caches, 1 at a time in ~20).
6. Cache: the jobs use per-lockfile `cache:` keys under the project dir; with the docker
   executor add `[runners.docker] volumes = ["/cache"]` (step 4 did) so pip/npm/go caches
   survive between jobs.
7. First run: push a no-op commit to a branch and open an MR to `main` — the workflow rules
   run pipelines for MRs to main and pushes to main. Expect:
   - `gitleaks` green on the MR's own commits (the fallback scans the tip commit only);
   - `backend-test` green (the lane is green with 35 quarantined since FIX126 O8; note the
     CI image is a plain `python:3.11` with only `backend/` checked out **plus the whole
     repo**, so the 34 "environment" quarantines whose condition is *sibling tree not
     mounted* become hard gates again — this is intended, see `backend/tests/QUARANTINE.md`);
   - `web-e2e` may be red (allow_failure) on the first cold run (Playwright download).
8. Then protect `main` with "pipeline must succeed" (Settings → Merge requests) — only after
   two consecutive green pipelines, so a runner outage cannot block a hotfix on day one.

## What changes for the cards

- CARD-RULES §5 "real gitleaks locally" stays until the first green `gitleaks` job; after that,
  the local run becomes a convenience, and the job is the gate.
- `desktop-test` remains manual until a `windows` runner exists (Option B).

## Cost / risk

- Host: one small VM (or the existing Linux box). No GitLab subscription change (project
  runners are free on self-managed and on gitlab.com).
- Risk: a runner token leak lets someone run jobs as this project — keep it in `config.toml`
  on the runner host only; rotate from the Runners page if it is ever pasted anywhere.
- The runner must not be the machine that hosts the live stack (CPU/disk contention with the
  owner's Docker stack; see FIX122 "exit 255 = Windows Update reboot").

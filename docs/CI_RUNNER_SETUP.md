# CI runner setup — what the GitLab admin needs to do

**Project:** `srandev/ai-gateway` on `gitlab-dev.gbtech.co.th` (project id 163)
**Requested by:** ________________ **Date:** ________________

## Why this is needed

Every pipeline of this project has failed since June without running a
single job — 124 pipelines, 0 jobs executed. GitLab only *schedules* CI
jobs; a **runner** is a separate program that picks them up and runs
them. This project has **no runner registered**, so every job sits in
`pending` until GitLab gives up and marks it
`stuck_pending_no_matching_runners`. The red pipeline emails are about
that, not about the code.

## What to set up

One GitLab Runner with the **docker executor**. Each job names its own
image (`python:3.11`, `node:20`, `golang:1.25`, `rust:latest`), so the
runner machine needs nothing but Docker.

Steps (GitLab ≥ 16, "new runner" flow):

1. In GitLab: **Settings → CI/CD → Runners → New project runner**
   (or a group/instance runner shared with other projects).
2. ⭐ **Tick "Run untagged jobs".** Our jobs carry **no tags** (the one
   exception is `desktop-test`, tagged `windows`, which is manual and
   optional). A runner that only accepts tagged jobs will leave the
   pipeline exactly as stuck as it is today — this is the single easiest
   thing to get wrong.
3. Copy the registration token GitLab shows (it is shown once — do not
   paste it into chat, tickets, or this file).
4. On the runner machine:
   ```
   gitlab-runner register \
     --url https://gitlab-dev.gbtech.co.th \
     --token <paste the token here> \
     --executor docker \
     --docker-image alpine:3.20 \
     --description "ai-gateway-runner-1"
   ```
   Accept the defaults for everything else. `--docker-image` is only the
   fallback; every job overrides it.
5. Back in GitLab, the runner shows as online. Re-run the latest
   pipeline on `main` (**CI/CD → Pipelines → Run pipeline**).

## Machine requirements

| | |
|---|---|
| Host name / IP | ________________ (admin to fill in) |
| OS | Linux (any current distro) |
| Docker | installed, the `gitlab-runner` user in the `docker` group |
| Uptime | always on — jobs wait for it |
| Network | outbound HTTPS to `gitlab-dev.gbtech.co.th`, Docker Hub, PyPI, npm, proxy.golang.org, crates.io |
| Size | 4 CPU / 8 GB RAM / 40 GB disk is enough; the backend job runs ~4,000 tests in about 5 minutes on that |
| Concurrency | `concurrent = 2` in `/etc/gitlab-runner/config.toml` is plenty |

## What to expect on the first run — it will be red, and that is expected

The first pipeline that actually runs will **fail on `backend-test`**.
That is a known state, not a new breakage:

- the backend suite has a short list of accepted failures kept in
  `backend/tests/known_failing.txt`, and the release gate we run locally
  checks failing *names* against that list;
- the CI job runs a bare `pytest`, which does not know about the list,
  so it reports those failures as red.

Please don't "fix" that by editing tests or adding `allow_failure`.
The development side will move the CI job onto the same name-based
check once a runner exists to test it against. The other jobs
(Go, Node, audits) are expected to pass or to fail for reasons listed
in the FIX61 report.

`desktop-test` needs a **Windows** runner tagged `windows`; it is manual
and optional, and nobody is asking for that today.

## Who to tell when it is done

________________ (owner) — a link to the first pipeline that shows
jobs with a non-zero duration is all that is needed.

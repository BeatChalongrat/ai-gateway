# Deploying the code-interpreter sandbox runtime

Short, for the person who runs the host and has read none of the reports.

## What the boundary is, and why it exists

chat-edge runs model-authored Python (the code interpreter, the Excel/Doc/Pptx
plan executors, document text extraction, version compare) in a **per-run
Docker container** it launches itself: no network, non-root (uid 65534), all
capabilities dropped, a seccomp profile, a read-only root filesystem, CPU /
memory / wall-clock caps. That is the **standard container boundary**.

This service is **multi-tenant and server-side**. A container shares the host
kernel; a kernel bug reachable from inside the container is an escape, and an
escape on this host lands in **other customers' data** — not, as on a desktop,
in the user's own files. That is why production holds a stronger line than a
desktop sandbox needs: in production the per-run container executes under
**gVisor (`runsc`)**, a user-space kernel that intercepts every syscall, so the
host kernel is not the boundary any more. gVisor was chosen over Kata /
Firecracker because it needs no KVM (FIX14's record, §1).

**The rule that makes this real (FIX14 §2): the service never falls back
silently.** When `SANDBOX_RUNTIME=runsc` is set, chat-edge starts a probe
container under that runtime at boot, reads `/proc/version` **from inside it**,
and refuses to start unless it says gVisor. Every job repeats that check and
refuses its own result on a mismatch. A service that is up with
`SANDBOX_RUNTIME=runsc` is therefore a service whose sandbox is actually gVisor.

## The two modes — deliberate, not one mode with an exception

| | **Development mode** | **Production mode** |
|---|---|---|
| `SANDBOX_RUNTIME` | unset (empty) | `runsc` |
| Where it is set | `docker-compose.webchat.yml`, `docker-compose.sandbox.yml`, `.env.example` — all default to empty | `docker-compose.prod.yml` (`${SANDBOX_RUNTIME:-runsc}`), `infra/production/.env.production.template` |
| Per-run container runs under | the daemon's default runtime (runc) | gVisor `runsc` |
| Boundary | standard container: no network, non-root, caps dropped, seccomp, read-only rootfs, caps on resources | all of the above **plus** a user-space kernel — the host kernel is not exposed to the job |
| Boot log line | `plan-exec sandbox active … requested_runtime=runc boundary_in_effect=standard` | `plan-exec sandbox active … requested_runtime=runsc boundary_in_effect=gvisor` |
| If the runtime is missing | n/a | chat-edge **refuses to start** and its error names the fix (see below) |

A developer on a laptop should know they are running the standard container
boundary — it is a real boundary, and it is not the production one. A
production host should never be in doubt: it is either gVisor or it is down.

## How to tell which one is running

1. **From a running container (the truth):**
   `bash scripts/verify-sandbox-runtime.sh` — starts a probe container with the
   same hardening chat-edge uses and reads the kernel from inside. It prints
   `RESULT: STANDARD` or `RESULT: HARDENED` (or `ABSENT` / `FALLBACK`, see
   below). `--expect gvisor` makes it a pass/fail check for production;
   `--json` for pipelines.
2. **From the service's boot log:** `docker logs <chat-edge> | grep boundary_in_effect`
   — the line above. It is written from a probe run, not from config.
3. **From a job:** every `run_python` result carries the runtime the job
   measured from inside (`Result.Runtime`, `gvisor` / `standard`).

## The three scripts (Linux Docker host; run with sudo)

| Script | Question it answers | Exit |
|---|---|---|
| `sudo bash scripts/install-runsc.sh [--dry-run] [--yes] [--method apt\|tarball]` | *Did I install it?* Installs `runsc` gVisor's own way — APT repository on Debian/Ubuntu (signing-key fingerprint checked before it is trusted) or the release tarball elsewhere (`sha512sum -c` before extracting) — registers it with `runsc install` (merges `daemon.json`, never overwrites), reloads Docker, and **ends with the smoke test** `docker run --rm --runtime=runsc hello-world`. Idempotent: a second run finds everything in place, says so, and re-runs the smoke test. | 0 only if the smoke test passed |
| `bash scripts/verify-sandbox-runtime.sh [--expect gvisor\|standard] [--json]` | *Is the boundary in effect right now?* Answers from inside a running container, not from configuration. Run after a host or Docker upgrade, from CI, or whenever you want to know. | 0 matches expectation · 1 `runsc` registered but the container reports the host kernel (**fall-back in disguise**) · 2 no container can start under `runsc` · 3 undetermined · 4 usage |
| `sudo bash scripts/uninstall-runsc.sh [--dry-run] [--yes] [--keep-repo]` | *Remove it, and tell me what that means for the service.* Unregisters via `runsc uninstall`, removes the package/binaries, reloads Docker, verifies the runtime is gone — and prints that chat-edge with `SANDBOX_RUNTIME=runsc` will now refuse to start, and what to do about it. | 0 only if the runtime is confirmed gone |

Upstream references the installer follows (verified 2026-08-26):
<https://gvisor.dev/docs/user_guide/install/> and
<https://gvisor.dev/docs/user_guide/quick_start/docker/>. gVisor documents no
uninstall; ours is the third script.

## Production bring-up, in order

```
sudo bash scripts/install-runsc.sh                 # ends with hello-world under runsc
bash scripts/verify-sandbox-runtime.sh --expect gvisor   # RESULT: HARDENED, exit 0
# SANDBOX_RUNTIME=runsc is already the prod default; deploy / recreate chat-edge
docker logs <chat-edge> | grep boundary_in_effect  # … requested_runtime=runsc boundary_in_effect=gvisor
```

## When chat-edge refuses to start

```
sandbox enabled but unavailable — refusing to run plan-exec in-process
  err="sandbox: unavailable: required runtime "runsc" not verifiable at startup:
  boundary probe failed: docker: Error response from daemon: unknown or invalid runtime name: runsc
  — to fix: install gVisor on the Docker host with `sudo bash scripts/install-runsc.sh`,
  confirm with `bash scripts/verify-sandbox-runtime.sh --expect gvisor`, then restart
  this service; or unset SANDBOX_RUNTIME to run the standard container boundary (dev mode).
  See docs/DEPLOY_SANDBOX.md"
```

This is the safety mechanism working, not a broken deployment: the service was
told to require gVisor and gVisor is not in effect on this host, so it will not
run the interpreter under the weaker boundary while claiming otherwise. Do what
the message says — install the runtime (production) or unset
`SANDBOX_RUNTIME` (development) — and restart.

## Things that are deliberately NOT here

- No `daemon.json` is hand-written by any script; registration is
  `runsc install` / `runsc uninstall`, which merge and keep a `daemon.json~`.
- No sandbox restriction is loosened for gVisor. The per-run `docker run`
  arguments are unchanged; `--runtime=runsc` is added to them.
- Docker Desktop (macOS/Windows) is refused by the installer by name: its
  daemon lives in a VM the script cannot reach. Development there runs
  development mode.

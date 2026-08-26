#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# verify-sandbox-runtime.sh — is the sandbox isolation boundary in effect
# on this Docker host RIGHT NOW?
#
# Different question from install-runsc.sh ("did I install it"). This one
# asks the same thing chat-edge asks at boot (FIX14 §2): start a container
# under the runtime and read the boundary FROM INSIDE it — /proc/version —
# not from daemon.json, `docker info`, or an environment variable. A gVisor
# sandbox reports its own kernel ("Linux version 4.19.0-gvisor …"); a
# standard container reports the host kernel.
#
# Run it any time: after a host or Docker upgrade, from CI, or to know.
#
# Usage:
#   bash scripts/verify-sandbox-runtime.sh [--expect gvisor|standard|auto]
#                                          [--image IMG] [--json] [--self-test]
#
#   --expect    what the deployment REQUIRES. auto (default) reads
#               SANDBOX_RUNTIME from the environment: "runsc" → gvisor,
#               unset/empty → standard.
#   --image     probe image (default: $SANDBOX_IMAGE, else
#               ai-gateway-sandbox:latest if present, else alpine:3.20)
#   --json      one JSON object on stdout (for pipelines)
#   --self-test check the classifier against captured real kernel strings
#
# Exit codes (meaningful for pipelines):
#   0  boundary in effect matches the expectation
#   1  expected gVisor, runtime 'runsc' IS registered, but a container run
#      under it reports the STANDARD kernel — a fall-back in disguise; the
#      most serious result. (chat-edge would refuse to start; that is correct.)
#   2  expected gVisor but no container can be started under runsc at all
#      (not registered / not installed) — run scripts/install-runsc.sh
#   3  could not determine (docker unreachable, no probe image, probe error)
#   4  usage error
#
# What it CANNOT tell you (stated, not hidden):
#   • whether chat-edge is CONFIGURED to require the runtime — that is
#     SANDBOX_RUNTIME in its own environment; this script only checks the
#     host. If a chat-edge container is running it reports that container's
#     view (env + last boot line) as information.
#   • anything about other hosts; it checks the daemon `docker` talks to.
# ─────────────────────────────────────────────────────────────────────────
set -uo pipefail

EXPECT="auto"; IMAGE="${SANDBOX_IMAGE:-}"; JSON=0; SELFTEST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --expect) EXPECT="${2:-}"; shift 2 ;;
    --expect=*) EXPECT="${1#*=}"; shift ;;
    --image) IMAGE="${2:-}"; shift 2 ;;
    --image=*) IMAGE="${1#*=}"; shift ;;
    --json) JSON=1; shift ;;
    --self-test) SELFTEST=1; shift ;;
    -h|--help) sed -n '2,44p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 4 ;;
  esac
done
case "$EXPECT" in gvisor|standard|auto) ;; *) echo "--expect must be gvisor|standard|auto" >&2; exit 4 ;; esac

say() { [ "$JSON" = 1 ] || printf '[verify-sandbox] %s\n' "$*"; }

# The classifier — the SAME rule as sandbox_runner.py _detect_runtime() and
# sandbox.go VerifyBoundary(): gVisor's kernel release is "<ver>-gvisor".
classify() { case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in *gvisor*) echo gvisor ;; "") echo unknown ;; *) echo standard ;; esac; }

if [ "$SELFTEST" = 1 ]; then
  # Captured from real kernels on 2026-08-26 (gVisor release-20260817.0 via
  # `runsc do`; WSL2 host; a stock Ubuntu kernel string).
  fail=0
  t() { got="$(classify "$1")"; if [ "$got" = "$2" ]; then say "self-test OK   $2 ← '$1'"; else say "self-test FAIL expected $2 got $got ← '$1'"; fail=1; fi; }
  t "Linux version 4.19.0-gvisor #1 SMP Sun Jan 10 15:06:54 PST 2016" gvisor
  t "Linux version 5.15.0-gvisor #1 SMP Sun Jan 10 15:06:54 PST 2016" gvisor
  t "Linux version 6.6.87.2-microsoft-standard-WSL2 (root@439a258ad544) (gcc (GCC) 11.2.0, GNU ld (GNU Binutils) 2.37) #1 SMP PREEMPT_DYNAMIC Thu Jun  5 18:30:46 UTC 2025" standard
  t "Linux version 6.8.0-45-generic (buildd@lcy02-amd64-115) (x86_64-linux-gnu-gcc-13 (Ubuntu 13.2.0-23ubuntu4) 13.2.0, GNU ld (GNU Binutils for Ubuntu) 2.42) #45-Ubuntu SMP PREEMPT_DYNAMIC Fri Aug 30 12:02:04 UTC 2024" standard
  t "" unknown
  [ "$fail" = 0 ] && { say "self-test: classifier distinguishes gVisor from a standard kernel"; exit 0; } || exit 3
fi

# ── Expectation ───────────────────────────────────────────────────────────
EXPECT_SRC="--expect"
if [ "$EXPECT" = "auto" ]; then
  case "${SANDBOX_RUNTIME:-}" in
    runsc) EXPECT="gvisor"; EXPECT_SRC="env SANDBOX_RUNTIME=runsc" ;;
    "") EXPECT="standard"; EXPECT_SRC="env SANDBOX_RUNTIME unset (dev mode)" ;;
    *) EXPECT="gvisor"; EXPECT_SRC="env SANDBOX_RUNTIME=${SANDBOX_RUNTIME} (not runsc — this script can only verify gVisor; treating as 'must not be standard')" ;;
  esac
fi
say "expectation: $EXPECT  (source: $EXPECT_SRC)"

# ── Docker reachable? ────────────────────────────────────────────────────
finish() { # code result-word detail
  if [ "$JSON" = 1 ]; then
    printf '{"expected":"%s","runsc_registered":%s,"observed_standard":"%s","observed_runsc":"%s","result":"%s","detail":"%s","exit":%s}\n' \
      "$EXPECT" "${REGISTERED:-false}" "${OBS_STD:-}" "${OBS_RUNSC:-}" "$2" "$(printf '%s' "$3" | tr -d '"' | tr '\n' ' ')" "$1"
  else
    say "RESULT: $2 — $3"
  fi
  exit "$1"
}
if ! docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
  finish 3 UNDETERMINED "docker daemon not reachable from here (docker info failed) — nothing can be verified"
fi
DOCKER_OS="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null)"
if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'; then REGISTERED=true; else REGISTERED=false; fi
say "daemon: $DOCKER_OS; runtimes: $(docker info --format '{{range $k,$v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null); runsc registered: $REGISTERED"

# ── Probe image ──────────────────────────────────────────────────────────
if [ -z "$IMAGE" ]; then
  if docker image inspect ai-gateway-sandbox:latest >/dev/null 2>&1; then IMAGE="ai-gateway-sandbox:latest"; else IMAGE="alpine:3.20"; fi
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  say "probe image $IMAGE not present locally — pulling"
  docker pull -q "$IMAGE" >/dev/null 2>&1 || finish 3 UNDETERMINED "probe image '$IMAGE' is not present and could not be pulled; pass --image"
fi
say "probe image: $IMAGE"

# The probe uses the SAME hardening chat-edge applies (sandbox.go
# VerifyBoundary) so we test the container shape that will actually run.
probe() { # $1 = runtime name or "" ; prints /proc/version or fails
  local args=(run --rm)
  [ -n "$1" ] && args+=(--runtime "$1")
  args+=(--network none --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges --read-only --entrypoint cat "$IMAGE" /proc/version)
  timeout 60 docker "${args[@]}" 2>&1
}

# ── Probe 1: the default runtime (what dev mode runs) ────────────────────
if out="$(probe "")"; then OBS_STD="$(classify "$out")"; say "default runtime, from inside: $OBS_STD  ← $(printf '%s' "$out" | head -1 | cut -c1-80)"; else OBS_STD="error"; say "default runtime probe FAILED: $(printf '%s' "$out" | tail -1)"; fi

# ── Probe 2: --runtime=runsc (what prod mode runs) ───────────────────────
if out="$(probe runsc)"; then
  OBS_RUNSC="$(classify "$out")"; say "runtime=runsc, from inside: $OBS_RUNSC  ← $(printf '%s' "$out" | head -1 | cut -c1-80)"
else
  OBS_RUNSC="unavailable"; say "runtime=runsc: cannot start a container — $(printf '%s' "$out" | grep -m1 -i 'runtime\|error' | cut -c1-120)"
fi

# ── Informational: what a running chat-edge believes ─────────────────────
CE="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -m1 'chat-edge' || true)"
if [ -n "$CE" ]; then
  ce_env="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CE" 2>/dev/null | grep -m1 '^SANDBOX_RUNTIME=' || echo 'SANDBOX_RUNTIME (not set)')"
  [ "$ce_env" = "SANDBOX_RUNTIME=" ] && ce_env="SANDBOX_RUNTIME= (empty → dev mode, standard boundary)"
  ce_boot="$(docker logs --tail 2000 "$CE" 2>&1 | grep -o 'requested_runtime=[a-z]* boundary_in_effect=[a-z]*' | tail -1 || true)"
  say "service view (informational, NOT what this script verifies): container $CE has $ce_env; last boot line: ${ce_boot:-not found in recent logs}"
else
  say "service view: no running chat-edge container found on this daemon — cannot show what the service believes"
fi

# ── Verdict ──────────────────────────────────────────────────────────────
case "$EXPECT" in
  gvisor)
    case "$OBS_RUNSC" in
      gvisor)      finish 0 HARDENED "a container started under runsc and reported the gVisor kernel from inside; SANDBOX_RUNTIME=runsc will boot" ;;
      unavailable) finish 2 ABSENT "expected gVisor but no container can start under runtime 'runsc' (registered=$REGISTERED). Install it: sudo bash scripts/install-runsc.sh — chat-edge with SANDBOX_RUNTIME=runsc refuses to start until then (correct)" ;;
      standard)    finish 1 FALLBACK "runtime 'runsc' accepted the container but from inside it reports the STANDARD host kernel — the boundary is NOT gVisor. Do not run with SANDBOX_RUNTIME=runsc believing it is hardened (chat-edge would refuse anyway). Check what daemon.json's runtimes.runsc path actually points to" ;;
      *)           finish 3 UNDETERMINED "runsc probe returned an unclassifiable result ($OBS_RUNSC)" ;;
    esac ;;
  standard)
    case "$OBS_STD" in
      standard) [ "$OBS_RUNSC" = "gvisor" ] && note="; note: runsc is ALSO available on this host — set SANDBOX_RUNTIME=runsc to use it" || note=""
                finish 0 STANDARD "the standard container boundary is in effect (no network, non-root, caps dropped, seccomp, read-only rootfs — a container, not a user-space kernel). This is dev mode$note" ;;
      gvisor)   finish 0 HARDENED "expected standard but the DEFAULT runtime is already gVisor (stronger than required)" ;;
      *)        finish 3 UNDETERMINED "could not run the default-runtime probe ($OBS_STD)" ;;
    esac ;;
esac

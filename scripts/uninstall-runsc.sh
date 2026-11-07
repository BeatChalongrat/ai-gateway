#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# uninstall-runsc.sh — remove gVisor's `runsc` from this Docker host.
#
# gVisor documents no uninstall; this is ours. In order:
#   1. Unregister the runtime from Docker via `runsc uninstall` (gVisor's own
#      tool: edits ONLY runtimes.runsc in /etc/docker/daemon.json, keeps a
#      daemon.json~ backup). If the binary is already gone, the entry is
#      removed with a merge (python3/jq) — never by overwriting the file.
#   2. Remove the package (apt) or the binaries (/usr/local/bin tarball).
#   3. Reload the Docker daemon.
#   4. VERIFY the runtime is gone: `docker info` no longer lists it AND
#      `docker run --runtime=runsc` is rejected. Exit non-zero otherwise.
#
# AND it tells you what to do on OUR side — read the box it prints:
# chat-edge with SANDBOX_RUNTIME=runsc REFUSES TO START once the runtime is
# gone. That is FIX14's no-silent-fall-back guard working, not a broken
# deployment. Unset SANDBOX_RUNTIME (dev mode) or reinstall (prod mode).
#
# Usage:
#   sudo bash scripts/uninstall-runsc.sh [--dry-run] [--yes] [--keep-repo]
#
#   --dry-run    print every step that WOULD run; change nothing
#   --yes        do not ask for confirmation
#   --keep-repo  leave the apt source + keyring in place (apt path only)
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

GVISOR_KEYRING="/usr/share/keyrings/gvisor-archive-keyring.gpg"
GVISOR_APT_LIST="/etc/apt/sources.list.d/gvisor.list"
TARBALL_BIN_DIR="/usr/local/bin"
DAEMON_JSON="/etc/docker/daemon.json"
DRY_RUN=0; ASSUME_YES=0; KEEP_REPO=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --keep-repo) KEEP_REPO=1; shift ;;
    -h|--help) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '[uninstall-runsc] %s\n' "$*"; }
warn() { printf '[uninstall-runsc] WARNING: %s\n' "$*" >&2; }
die()  { printf '[uninstall-runsc] REFUSING: %s\n' "$*" >&2; exit "${2:-1}"; }
plan() { printf '[uninstall-runsc] %s %s\n' "$([ "$DRY_RUN" = 1 ] && echo 'would:' || echo 'doing:')" "$*"; }
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else [ "$DRY_RUN" = 1 ] || die "must run as root (or with sudo)"; fi
fi
run() { if [ "$DRY_RUN" = 1 ]; then printf '[uninstall-runsc]   $ %s\n' "$*"; return 0; fi; $SUDO "$@"; }

[ "$(uname -s)" = "Linux" ] || die "this removes a Linux OCI runtime; uname -s = '$(uname -s)'"
DOCKER_OK=0
if command -v docker >/dev/null 2>&1 && $SUDO docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then DOCKER_OK=1; else warn "docker daemon not reachable — will remove files but CANNOT verify the runtime is gone from the daemon"; fi
runtime_registered() { [ "$DOCKER_OK" = 1 ] && $SUDO docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'; }

# ── What is installed ────────────────────────────────────────────────────
APT_PKG=0; dpkg -s runsc >/dev/null 2>&1 && APT_PKG=1
RUNSC_BIN="$(command -v runsc 2>/dev/null || true)"; [ -n "$RUNSC_BIN" ] || { [ -x "$TARBALL_BIN_DIR/runsc" ] && RUNSC_BIN="$TARBALL_BIN_DIR/runsc"; } || true
TARBALL_FILES=(); for f in runsc containerd-shim-runsc-v1 gvisor-bin; do [ -e "$TARBALL_BIN_DIR/$f" ] && TARBALL_FILES+=("$TARBALL_BIN_DIR/$f"); done
DAEMON_HAS_ENTRY=0; [ -f "$DAEMON_JSON" ] && grep -q '"runsc"' "$DAEMON_JSON" 2>/dev/null && DAEMON_HAS_ENTRY=1
say "state: apt package=$([ $APT_PKG = 1 ] && echo yes || echo no); binary=${RUNSC_BIN:-none}; tarball files=${#TARBALL_FILES[@]}; daemon.json entry=$([ $DAEMON_HAS_ENTRY = 1 ] && echo yes || echo no); registered with daemon=$(runtime_registered && echo yes || echo no)"
if [ $APT_PKG = 0 ] && [ -z "$RUNSC_BIN" ] && [ ${#TARBALL_FILES[@]} = 0 ] && [ $DAEMON_HAS_ENTRY = 0 ] && ! runtime_registered; then
  say "nothing to remove — runsc is not installed or registered on this host (already the desired state)"
  ALREADY_GONE=1
else
  ALREADY_GONE=0
fi

# ── What this means for chat-edge — said before AND after, because it matters ─
our_side_notice() {
  cat <<'EOF'
[uninstall-runsc] ┌────────────────────────────────────────────────────────────────────────┐
[uninstall-runsc] │ ON OUR SIDE: chat-edge with SANDBOX_RUNTIME=runsc will now REFUSE TO   │
[uninstall-runsc] │ START ("required runtime "runsc" not verifiable at startup").          │
[uninstall-runsc] │ That is the FIX14 no-silent-fall-back guard doing its job — it will    │
[uninstall-runsc] │ NOT quietly run the interpreter under the weaker runtime. It is not a  │
[uninstall-runsc] │ broken deployment. Choose one, deliberately:                            │
[uninstall-runsc] │   dev mode  → set SANDBOX_RUNTIME= (empty) in .env / the compose env   │
[uninstall-runsc] │               and recreate chat-edge (standard container boundary).   │
[uninstall-runsc] │   prod mode → reinstall: sudo bash scripts/install-runsc.sh            │
[uninstall-runsc] │ docker-compose.prod.yml defaults SANDBOX_RUNTIME to runsc on purpose.  │
[uninstall-runsc] └────────────────────────────────────────────────────────────────────────┘
EOF
  # Point at concrete places where runsc is still demanded, if we can see them.
  [ "${SANDBOX_RUNTIME:-}" = "runsc" ] && say "  ↳ DETECTED: SANDBOX_RUNTIME=runsc is set in THIS shell's environment"
  local envf; envf="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)/.env"
  [ -f "$envf" ] && grep -qE '^\s*SANDBOX_RUNTIME=runsc' "$envf" && say "  ↳ DETECTED: $envf sets SANDBOX_RUNTIME=runsc"
  if [ "$DOCKER_OK" = 1 ]; then
    local ce; ce="$($SUDO docker ps --format '{{.Names}}' 2>/dev/null | grep -m1 'chat-edge' || true)"
    if [ -n "$ce" ] && $SUDO docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$ce" 2>/dev/null | grep -q '^SANDBOX_RUNTIME=runsc$'; then
      say "  ↳ DETECTED: running container '$ce' has SANDBOX_RUNTIME=runsc — it keeps running now, but its NEXT restart will refuse"
    fi
  fi
  return 0
}

say "plan:"
say "  1. unregister: $([ -n "$RUNSC_BIN" ] && echo "$RUNSC_BIN uninstall" || echo "merge-remove runtimes.runsc from $DAEMON_JSON") (backup kept)"
[ $APT_PKG = 1 ] && say "  2. apt-get purge -y runsc$([ $KEEP_REPO = 0 ] && echo " + remove $GVISOR_APT_LIST and $GVISOR_KEYRING")"
[ ${#TARBALL_FILES[@]} -gt 0 ] && say "  2. rm ${TARBALL_FILES[*]}"
say "  3. reload docker;  4. verify 'runsc' is gone (docker info + a rejected --runtime=runsc run)"
if [ "$DRY_RUN" = 0 ] && [ "$ASSUME_YES" = 0 ] && [ "$ALREADY_GONE" = 0 ]; then
  if [ -t 0 ]; then read -r -p "[uninstall-runsc] proceed? [y/N] " ans; case "$ans" in y|Y|yes|YES) ;; *) say "aborted by user"; exit 1 ;; esac
  else die "no TTY to confirm on; re-run with --yes (or --dry-run)"; fi
fi

# ── 1. Unregister ────────────────────────────────────────────────────────
if [ -n "$RUNSC_BIN" ] && [ $DAEMON_HAS_ENTRY = 1 ]; then
  plan "$RUNSC_BIN uninstall   # removes runtimes.runsc from $DAEMON_JSON, keeps ${DAEMON_JSON}~"
  run "$RUNSC_BIN" uninstall
elif [ $DAEMON_HAS_ENTRY = 1 ]; then
  plan "remove ONLY runtimes.runsc from $DAEMON_JSON (binary already gone, so runsc uninstall is unavailable)"
  if [ "$DRY_RUN" = 0 ]; then
    run cp -p "$DAEMON_JSON" "${DAEMON_JSON}~"
    if command -v python3 >/dev/null 2>&1; then
      tmp="$(mktemp)"; python3 - "$DAEMON_JSON" > "$tmp" <<'PY'
import json,sys
p=sys.argv[1]; c=json.load(open(p))
r=c.get("runtimes") or {}
r.pop("runsc",None)
if r: c["runtimes"]=r
else: c.pop("runtimes",None)
print(json.dumps(c,indent=4))
PY
      run install -m 0644 "$tmp" "$DAEMON_JSON"; rm -f "$tmp"
    elif command -v jq >/dev/null 2>&1; then
      tmp="$(mktemp)"; jq 'del(.runtimes.runsc) | if (.runtimes|length)==0 then del(.runtimes) else . end' "$DAEMON_JSON" > "$tmp"; run install -m 0644 "$tmp" "$DAEMON_JSON"; rm -f "$tmp"
    else
      die "neither python3 nor jq is available to edit $DAEMON_JSON safely — remove the \"runsc\" key under \"runtimes\" by hand (backup at ${DAEMON_JSON}~), then re-run"
    fi
  fi
else
  say "daemon.json has no runsc entry (unchanged)"
fi

# ── 2. Remove package / binaries ─────────────────────────────────────────
if [ $APT_PKG = 1 ]; then
  plan "apt-get purge -y runsc"; run apt-get purge -y -qq runsc
  if [ $KEEP_REPO = 0 ]; then
    [ -f "$GVISOR_APT_LIST" ] && { plan "rm $GVISOR_APT_LIST"; run rm -f "$GVISOR_APT_LIST"; }
    [ -f "$GVISOR_KEYRING" ] && { plan "rm $GVISOR_KEYRING"; run rm -f "$GVISOR_KEYRING"; }
  fi
fi
if [ ${#TARBALL_FILES[@]} -gt 0 ]; then plan "rm -rf ${TARBALL_FILES[*]}"; run rm -rf "${TARBALL_FILES[@]}"; fi

# ── 3. Reload docker ─────────────────────────────────────────────────────
if [ "$DOCKER_OK" = 1 ] && [ "$ALREADY_GONE" = 0 ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet docker 2>/dev/null; then plan "systemctl reload docker"; run systemctl reload docker
  elif pid="$(pidof dockerd 2>/dev/null)" && [ -n "$pid" ]; then plan "kill -HUP dockerd (pid $pid)"; run kill -HUP "$pid"
  else warn "no systemd unit / dockerd pid found — reload Docker by hand"; fi
fi

# ── 4. Verify it is gone ─────────────────────────────────────────────────
if [ "$DRY_RUN" = 1 ]; then our_side_notice; say "dry run complete — nothing changed, nothing verified"; exit 0; fi
if [ "$DOCKER_OK" = 0 ]; then our_side_notice; say "files removed, but the daemon could not be checked — verify by hand: docker info --format '{{json .Runtimes}}'"; exit 3; fi
for _ in $(seq 1 15); do runtime_registered || break; sleep 1; done
if runtime_registered; then say "FAIL: 'docker info' still lists runtime 'runsc' after reload — inspect $DAEMON_JSON and 'journalctl -u docker'" >&2; exit 1; fi
if out="$($SUDO docker run --rm --runtime=runsc hello-world 2>&1)"; then say "FAIL: a container STILL runs under --runtime=runsc" >&2; exit 1; fi
say "verified gone: 'docker info' lists no runsc, and docker rejects --runtime=runsc → $(printf '%s' "$out" | grep -m1 -io 'unknown or invalid runtime name: runsc' || printf '%s' "$out" | tail -1 | cut -c1-100)"
[ -z "$(command -v runsc 2>/dev/null || true)" ] && say "binary gone: runsc is no longer on PATH" || warn "a 'runsc' binary is still on PATH at $(command -v runsc) (installed some other way) — not touched"
our_side_notice
exit 0

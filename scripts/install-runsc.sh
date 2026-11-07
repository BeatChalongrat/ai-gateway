#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# install-runsc.sh — install gVisor's `runsc` OCI runtime on THIS Docker
# host and register it with the Docker daemon, so chat-edge can run the
# code-interpreter sandbox under a user-space kernel (SANDBOX_RUNTIME=runsc).
#
# Follows gVisor's own installation documentation — nothing invented:
#   https://gvisor.dev/docs/user_guide/install/            (apt + tarball)
#   https://gvisor.dev/docs/user_guide/quick_start/docker/ (docker registration)
#
# What it does, in order:
#   1. Detects the platform; refuses clearly on one it does not handle.
#   2. Installs runsc — APT repository (Debian/Ubuntu, gVisor's recommended
#      path; the signing key's fingerprint is checked before it is trusted)
#      or the release tarball (any other Linux; sha512 verified before use).
#   3. Registers the runtime with Docker via `runsc install` (gVisor's own
#      tool — it MERGES into /etc/docker/daemon.json and keeps a backup;
#      this script never hand-writes daemon.json) and reloads the daemon.
#   4. ENDS WITH THE SMOKE TEST — `docker run --rm --runtime=runsc hello-world`
#      — and exits non-zero if it fails. Success is measured, not assumed.
#
# Idempotent: running it again finds everything in place, says so, and
# re-runs the smoke test.
#
# Usage:
#   sudo bash scripts/install-runsc.sh [--method apt|tarball|auto]
#                                      [--dry-run] [--yes] [--help]
#
#   --method   apt (Debian/Ubuntu repository) | tarball (release archive to
#              /usr/local/bin) | auto (default: apt where apt-get exists on a
#              Debian-family host, otherwise tarball)
#   --dry-run  detect + print every step that WOULD run; change nothing
#   --yes      do not ask for confirmation (for unattended / CI use)
#   --reinstall  re-download and re-install even if runsc is already present
#              and registered (this is also how you pick up a newer release)
#
# Afterwards: set SANDBOX_RUNTIME=runsc for chat-edge (docker-compose.prod.yml
# already defaults to it) and confirm with scripts/verify-sandbox-runtime.sh.
# See docs/DEPLOY_SANDBOX.md.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Pinned facts (verified against the live docs/key on 2026-08-26) ──────
GVISOR_KEY_URL="https://gvisor.dev/archive.key"
# Fingerprint of "The gVisor Authors <gvisor-bot@google.com>" (rsa4096, 2019-07-10).
# If the downloaded key does not have this fingerprint we refuse to trust it.
GVISOR_KEY_FPR="6F1DF85E3A71C24918E727D56FC6D554E32BD943"
GVISOR_KEYRING="/usr/share/keyrings/gvisor-archive-keyring.gpg"
GVISOR_APT_LIST="/etc/apt/sources.list.d/gvisor.list"
GVISOR_APT_REPO="https://storage.googleapis.com/gvisor/releases"
GVISOR_RELEASE_BASE="https://storage.googleapis.com/gvisor/releases/release/latest"
TARBALL_BIN_DIR="/usr/local/bin"

METHOD="auto"
DRY_RUN=0
ASSUME_YES=0
REINSTALL=0

usage() { sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --method) METHOD="${2:-}"; shift 2 ;;
    --method=*) METHOD="${1#*=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --reinstall) REINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$METHOD" in apt|tarball|auto) ;; *) echo "--method must be apt|tarball|auto (got '$METHOD')" >&2; exit 2 ;; esac

say()  { printf '[install-runsc] %s\n' "$*"; }
warn() { printf '[install-runsc] WARNING: %s\n' "$*" >&2; }
die()  { printf '[install-runsc] REFUSING: %s\n' "$*" >&2; exit "${2:-1}"; }
plan() { printf '[install-runsc] %s %s\n' "$([ "$DRY_RUN" = 1 ] && echo 'would:' || echo 'doing:')" "$*"; }

# Privilege: everything that writes runs through $SUDO.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else
    [ "$DRY_RUN" = 1 ] || die "must run as root (or with sudo available) — it installs a system binary and edits Docker's daemon config"
  fi
fi
run() { # execute (or print) a privileged command
  if [ "$DRY_RUN" = 1 ]; then printf '[install-runsc]   $ %s\n' "$*"; return 0; fi
  # shellcheck disable=SC2086
  $SUDO "$@"
}

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── 1. Platform ───────────────────────────────────────────────────────────
OS="$(uname -s)"; ARCH="$(uname -m)"
[ "$OS" = "Linux" ] || die "this installs a Linux OCI runtime; this host reports uname -s = '$OS' (Docker Desktop on macOS/Windows runs its daemon inside a VM this script cannot reach)"
case "$ARCH" in
  x86_64|aarch64) ;;
  *) die "gVisor publishes release binaries for x86_64 and aarch64 only; this host is '$ARCH'" ;;
esac
DISTRO_ID=""; DISTRO_LIKE=""
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  DISTRO_ID="$(. /etc/os-release && echo "${ID:-}")"; DISTRO_LIKE="$(. /etc/os-release && echo "${ID_LIKE:-}")"
fi
command -v docker >/dev/null 2>&1 || die "docker CLI not found — install Docker Engine first (the runtime is registered with, and smoke-tested through, the daemon)"
if ! DOCKER_OS="$($SUDO docker info --format '{{.OperatingSystem}}' 2>/dev/null)"; then
  die "docker daemon not reachable (docker info failed) — start Docker, or run with sudo"
fi
case "$DOCKER_OS" in
  *"Docker Desktop"*) die "the daemon is Docker Desktop ('$DOCKER_OS') — its daemon runs inside Docker Desktop's own VM; installing runsc on this side would not register it there. Use a Linux host running Docker Engine." ;;
esac
if grep -qi microsoft /proc/version 2>/dev/null; then
  warn "this is a WSL2 kernel; proceeding because the daemon reports '$DOCKER_OS' (Docker Engine inside WSL), not Docker Desktop"
fi
if [ "$METHOD" = "auto" ]; then
  if command -v apt-get >/dev/null 2>&1 && printf '%s %s' "$DISTRO_ID" "$DISTRO_LIKE" | grep -qiE 'debian|ubuntu'; then METHOD="apt"; else METHOD="tarball"; fi
fi
if [ "$METHOD" = "apt" ] && ! command -v apt-get >/dev/null 2>&1; then
  die "--method apt requested but apt-get is not on this host (distro='$DISTRO_ID'); use --method tarball"
fi

say "host: $OS/$ARCH distro='${DISTRO_ID:-unknown}' docker='$DOCKER_OS' → method=$METHOD$( [ "$DRY_RUN" = 1 ] && echo ' (DRY RUN — nothing will change)')"

# ── Current state (for idempotency) ──────────────────────────────────────
runtime_registered() { $SUDO docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'; }
RUNSC_BIN="$(command -v runsc 2>/dev/null || true)"
[ -n "$RUNSC_BIN" ] || { [ -x "$TARBALL_BIN_DIR/runsc" ] && RUNSC_BIN="$TARBALL_BIN_DIR/runsc"; } || true
if [ -n "$RUNSC_BIN" ]; then say "runsc binary already present: $RUNSC_BIN ($("$RUNSC_BIN" --version 2>/dev/null | head -1 || echo 'version unknown'))"; else say "runsc binary: not present"; fi
if runtime_registered; then say "docker runtime 'runsc': already registered"; ALREADY_REG=1; else say "docker runtime 'runsc': not registered"; ALREADY_REG=0; fi

# ── The plan, stated before anything changes ─────────────────────────────
SKIP_INSTALL=0
if [ -n "$RUNSC_BIN" ] && [ "$ALREADY_REG" = 1 ] && [ "$REINSTALL" = 0 ]; then
  SKIP_INSTALL=1
  say "already installed AND registered — nothing to install; will only re-run the smoke test (use --reinstall to re-download / pick up a newer release)"
fi
say "plan:"
n=0
if [ "$SKIP_INSTALL" = 0 ]; then
  if [ "$METHOD" = "apt" ]; then
    n=$((n+1)); say "  $n. fetch $GVISOR_KEY_URL, CHECK its fingerprint == $GVISOR_KEY_FPR, dearmor into $GVISOR_KEYRING"
    n=$((n+1)); say "  $n. write $GVISOR_APT_LIST (signed-by that keyring) and apt-get install -y runsc"
  else
    n=$((n+1)); say "  $n. fetch $GVISOR_RELEASE_BASE/$ARCH/gvisor.tar.bz2 + .sha512, VERIFY with sha512sum -c, extract to $TARBALL_BIN_DIR"
  fi
  n=$((n+1)); say "  $n. register with Docker via 'runsc install' (MERGES into /etc/docker/daemon.json — backup daemon.json~ if one existed) + reload the daemon"
fi
n=$((n+1)); say "  $n. smoke test: docker run --rm --runtime=runsc hello-world  (the script's exit code is this test's result)"
if [ "$DRY_RUN" = 0 ] && [ "$ASSUME_YES" = 0 ]; then
  if [ -t 0 ]; then
    read -r -p "[install-runsc] proceed? [y/N] " ans; case "$ans" in y|Y|yes|YES) ;; *) say "aborted by user"; exit 1 ;; esac
  else
    die "no TTY to confirm on; re-run with --yes (or --dry-run to only see the plan)"
  fi
fi

# ── 2a. APT path ──────────────────────────────────────────────────────────
install_apt() {
  for t in curl gpg; do
    command -v "$t" >/dev/null 2>&1 || { plan "apt-get install prerequisites (apt-transport-https ca-certificates curl gnupg)"; run apt-get update -qq; run apt-get install -y -qq apt-transport-https ca-certificates curl gnupg; break; }
  done
  # Verify the key BEFORE trusting it: download, read its fingerprint, compare to the pin.
  plan "download signing key → $TMP/archive.key and check its fingerprint"
  if [ "$DRY_RUN" = 0 ]; then
    curl -fsSL "$GVISOR_KEY_URL" -o "$TMP/archive.key"
    got="$(gpg --batch --with-colons --show-keys "$TMP/archive.key" 2>/dev/null | awk -F: '/^fpr/{print $10; exit}')"
    [ "$got" = "$GVISOR_KEY_FPR" ] || die "signing key fingerprint mismatch: got '${got:-none}', expected '$GVISOR_KEY_FPR' — not trusting this key (changed upstream, or tampered download)"
    say "signing key fingerprint verified: $got"
    gpg --batch --dearmor < "$TMP/archive.key" > "$TMP/keyring.gpg"
    if [ -f "$GVISOR_KEYRING" ] && cmp -s "$TMP/keyring.gpg" "$GVISOR_KEYRING"; then
      say "keyring already installed and identical: $GVISOR_KEYRING (unchanged)"
    else
      run install -m 0644 "$TMP/keyring.gpg" "$GVISOR_KEYRING"; say "keyring installed: $GVISOR_KEYRING"
    fi
  fi
  local line="deb [arch=$(dpkg --print-architecture) signed-by=$GVISOR_KEYRING] $GVISOR_APT_REPO release main"
  if [ -f "$GVISOR_APT_LIST" ] && [ "$(cat "$GVISOR_APT_LIST")" = "$line" ]; then
    say "apt source already present and identical: $GVISOR_APT_LIST (unchanged)"
  else
    plan "write $GVISOR_APT_LIST: $line"
    if [ "$DRY_RUN" = 0 ]; then printf '%s\n' "$line" > "$TMP/gvisor.list"; run install -m 0644 "$TMP/gvisor.list" "$GVISOR_APT_LIST"; fi
  fi
  if dpkg -s runsc >/dev/null 2>&1; then
    say "package runsc already installed: $(dpkg-query -W -f='${Version}' runsc 2>/dev/null) (unchanged)"
  else
    plan "apt-get update && apt-get install -y runsc  (apt verifies the repository signature against the keyring)"
    run apt-get update -qq
    run apt-get install -y -qq runsc
  fi
  RUNSC_BIN="$(command -v runsc 2>/dev/null || echo /usr/bin/runsc)"
}

# ── 2b. Tarball path ─────────────────────────────────────────────────────
install_tarball() {
  local url="$GVISOR_RELEASE_BASE/$ARCH"
  command -v curl >/dev/null 2>&1 || die "curl is required for the tarball path (apt-get/yum install curl)"
  command -v sha512sum >/dev/null 2>&1 || die "sha512sum (coreutils) is required to verify the download"
  command -v bzip2 >/dev/null 2>&1 || die "bzip2 is required to extract gvisor.tar.bz2 (apt-get/yum install bzip2)"
  plan "download $url/gvisor.tar.bz2 and $url/gvisor.tar.bz2.sha512 → $TMP"
  [ "$DRY_RUN" = 1 ] && { plan "sha512sum -c gvisor.tar.bz2.sha512 (refuse on mismatch)"; plan "tar -xjf gvisor.tar.bz2 -C $TARBALL_BIN_DIR"; RUNSC_BIN="$TARBALL_BIN_DIR/runsc"; return 0; }
  ( cd "$TMP" && curl -fsSL -O "$url/gvisor.tar.bz2" && curl -fsSL -O "$url/gvisor.tar.bz2.sha512" )
  # Verify BEFORE extracting anything.
  ( cd "$TMP" && sha512sum -c gvisor.tar.bz2.sha512 ) || die "sha512 verification of gvisor.tar.bz2 FAILED — refusing to install an unverified archive"
  say "sha512 verified: gvisor.tar.bz2 matches $(cut -c1-16 "$TMP/gvisor.tar.bz2.sha512")…"
  mkdir -p "$TMP/x" && tar -xjf "$TMP/gvisor.tar.bz2" -C "$TMP/x"
  if [ -x "$TARBALL_BIN_DIR/runsc" ] && cmp -s "$TMP/x/runsc" "$TARBALL_BIN_DIR/runsc"; then
    say "$TARBALL_BIN_DIR/runsc is already this exact release (unchanged)"
  else
    plan "extract to $TARBALL_BIN_DIR (runsc, containerd-shim-runsc-v1)"
    run tar -xjf "$TMP/gvisor.tar.bz2" -C "$TARBALL_BIN_DIR"
  fi
  RUNSC_BIN="$TARBALL_BIN_DIR/runsc"
}

if [ "$SKIP_INSTALL" = 0 ]; then
  case "$METHOD" in apt) install_apt ;; tarball) install_tarball ;; esac
fi
[ "$DRY_RUN" = 1 ] || say "runsc: $RUNSC_BIN → $("$RUNSC_BIN" --version 2>/dev/null | head -1)"

# ── 3. Register with Docker (gVisor's tool, never a hand-written daemon.json) ─
reload_docker() {
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet docker 2>/dev/null; then
    plan "systemctl reload docker"; run systemctl reload docker
  elif pid="$(pidof dockerd 2>/dev/null)" && [ -n "$pid" ]; then
    plan "kill -HUP dockerd (pid $pid) — no systemd unit found; SIGHUP makes dockerd re-read daemon.json"; run kill -HUP "$pid"
  else
    warn "could not find a way to reload the daemon (no systemd 'docker' unit, no dockerd pid) — reload/restart Docker by hand, then re-run this script"
    return 1
  fi
}
if [ "$ALREADY_REG" = 1 ] && [ "$DRY_RUN" = 0 ] && runtime_registered; then
  say "docker runtime 'runsc' already registered (unchanged)"
else
  plan "$RUNSC_BIN install   # adds runtimes.runsc to /etc/docker/daemon.json (merge; backup daemon.json~ if one existed)"
  run "$RUNSC_BIN" install
  reload_docker || true
  if [ "$DRY_RUN" = 0 ]; then
    for _ in $(seq 1 15); do runtime_registered && break; sleep 1; done
    runtime_registered || die "runtime 'runsc' still not listed by 'docker info' after reload — check /etc/docker/daemon.json and 'journalctl -u docker'"
    say "docker runtime 'runsc' registered"
  fi
fi

# ── 4. The smoke test IS the result ──────────────────────────────────────
if [ "$DRY_RUN" = 1 ]; then
  say "dry run complete — nothing was changed and NOTHING WAS VERIFIED. Re-run without --dry-run to install and smoke-test."
  exit 0
fi
say "smoke test: docker run --rm --runtime=runsc hello-world"
if out="$($SUDO docker run --rm --runtime=runsc hello-world 2>&1)"; then
  printf '%s\n' "$out" | grep -m1 -i 'hello from docker' | sed 's/^/[install-runsc]   /' || true
  say "PASS — a container ran under runsc."
  say "next: set SANDBOX_RUNTIME=runsc for chat-edge (docker-compose.prod.yml defaults to it), recreate chat-edge,"
  say "      then run: bash scripts/verify-sandbox-runtime.sh --expect gvisor   (checks the boundary FROM INSIDE a container)"
  exit 0
else
  printf '%s\n' "$out" | tail -5 | sed 's/^/[install-runsc]   /' >&2
  say "FAIL — runsc is installed/registered but a container did not run under it. Not reporting success." >&2
  say "look at: 'journalctl -u docker', 'docker info --format {{json .Runtimes}}', and https://gvisor.dev/docs/user_guide/faq/" >&2
  exit 1
fi

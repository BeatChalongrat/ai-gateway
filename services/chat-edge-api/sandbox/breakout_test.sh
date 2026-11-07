#!/usr/bin/env bash
# Phase 13 Part A — BREAKOUT PROOF SUITE.
#
# Runs the hardened sandbox with the EXACT `docker run` lockdown the Go
# caller (internal/sandbox) uses, then has model-role code ATTEMPT to
# break each isolation boundary. Every attempt MUST fail/timeout/deny;
# the host must survive. Real output is printed for each.
#
# Usage:  bash breakout_test.sh
# Requires: Docker up, ai-gateway-sandbox:latest built, seccomp.json next
# to this script.
#
# NOTE: flags are held in a bash ARRAY (not a word-split string) so paths
# containing spaces survive. The seccomp profile + per-run dirs are copied
# to a space-free work root because the Windows docker CLI mis-parses a
# `seccomp=<path with spaces>` value.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE="ai-gateway-sandbox:latest"

# Space-free work root (Windows docker CLI + spaces in paths don't mix).
WORK="/c/sbx-proof"
rm -rf "$WORK"; mkdir -p "$WORK"
cp "${HERE}/seccomp.json" "$WORK/seccomp.json"
SECCOMP_WIN="C:/sbx-proof/seccomp.json"   # path form the Windows daemon/CLI reads

# The lockdown flag set — identical to internal/sandbox.Execute().
LOCK=(--rm --network none --user 65534:65534 --cap-drop ALL
  --security-opt no-new-privileges --security-opt "seccomp=${SECCOMP_WIN}"
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777
  --memory 256m --memory-swap 256m --pids-limit 64 --cpus 1.0
  --ulimit fsize=33554432:33554432 --ulimit nofile=256:256)

run_py() { docker run "${LOCK[@]}" --entrypoint python3 "$IMAGE" -c "$1" 2>&1; }

pass() { echo "  RESULT: PASS (contained) — $1"; }
fail() { echo "  RESULT: *** FAIL (BREAKOUT) *** — $1"; }
line() { echo "=================================================================="; }

echo "Sandbox image: $IMAGE"; docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "image missing"; exit 2; }
echo "Seccomp:       $SECCOMP_WIN"
echo "Lockdown:      ${LOCK[*]}"
line

echo "(a) NETWORK EGRESS — socket() create + connect to postgres/redis/internet"
OUT="$(run_py '
import socket
attempts = {}
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    attempts["create_socket"] = "SUCCEEDED"
    for label,addr in [("connect_postgres_5432",("172.17.0.1",5432)),
                       ("connect_redis_6379",("172.17.0.1",6379)),
                       ("connect_internet_1.1.1.1:443",("1.1.1.1",443))]:
        try:
            s.settimeout(3); s.connect(addr); attempts[label]="SUCCEEDED"
        except Exception as e: attempts[label]=type(e).__name__+": "+str(e)
except Exception as e:
    attempts["create_socket"]=type(e).__name__+": "+str(e)
for k,v in attempts.items(): print(k,"->",v)
')"
echo "$OUT" | sed 's/^/  /'
if echo "$OUT" | grep -q "create_socket -> SUCCEEDED"; then fail "socket() was permitted"; else pass "socket() blocked by seccomp; no egress possible"; fi
line

echo "(b) SECRET READ — scan environ for chat-edge secrets"
OUT="$(run_py '
import os
secrets = ["DATABASE_URL","GATEWAY_API_KEY","INTERNAL_SERVICE_SECRET",
           "SERVICE_JWT_SECRET","USER_JWT_SECRET","CSRF_SECRET",
           "PORTAL_JWT_SECRET","S3_ACCESS_KEY","S3_SECRET_KEY","REDIS_URL"]
found = {k:os.environ[k] for k in secrets if k in os.environ}
print("full environ keys:", sorted(os.environ.keys()))
print("secrets present:", found)
')"
echo "$OUT" | sed 's/^/  /'
if echo "$OUT" | grep -q "secrets present: {}"; then pass "no chat-edge secrets in environ"; else fail "a secret leaked into the sandbox"; fi
line

echo "(c) FILE ESCAPE — read /etc/shadow, docker socket, host paths, another upload"
OUT="$(run_py '
targets = ["/etc/shadow","/var/run/docker.sock","/run/secrets",
           "/host/etc/passwd","/job/in/other_users_upload.xlsx"]
for t in targets:
    try:
        with open(t,"rb") as f: data=f.read(64)
        print(t,"-> READABLE",len(data),"bytes")
    except Exception as e:
        print(t,"->",type(e).__name__)
# /proc/1/environ is this sandbox process ITS OWN env (PID ns = self);
# it is reachable but must contain NO chat-edge/host secret.
env = open("/proc/1/environ","rb").read().decode("latin1")
leak = [s for s in ["DATABASE_URL","GATEWAY_API_KEY","JWT","S3_SECRET","REDIS_URL","SERVICE_SECRET"] if s in env]
print("/proc/1/environ self-read; secret tokens found:", leak)
')"
echo "$OUT" | sed 's/^/  /'
if echo "$OUT" | grep -q "READABLE"; then fail "a protected/host path was readable"
elif echo "$OUT" | grep -q "secret tokens found: \[\]"; then pass "host/protected paths denied; self-env carries no secrets"
else fail "a secret token appeared in self-env"; fi
line

echo "(e) WRITE ESCAPE — write to rootfs (/) and /sandbox vs tmpfs scratch"
OUT="$(run_py '
for t in ["/pwned","/sandbox/scripts/evil.py","/etc/evil","/usr/local/evil"]:
    try:
        open(t,"w").write("x"); print(t,"-> WROTE (escape!)")
    except Exception as e:
        print(t,"->",type(e).__name__)
try:
    open("/tmp/ok.txt","w").write("x"); print("/tmp/ok.txt -> WROTE (expected: scratch writable)")
except Exception as e:
    print("/tmp write ->",type(e).__name__)
')"
echo "$OUT" | sed 's/^/  /'
if echo "$OUT" | grep -q "escape!"; then fail "rootfs was writable"; else pass "rootfs read-only; only tmpfs scratch writable"; fi
line

echo "(d1) RESOURCE — 10 GB allocation under --memory 256m"
OUT="$(run_py '
try:
    b = bytearray(10*1024*1024*1024)
    print("allocated 10GiB -> NOT CONTAINED")
except MemoryError:
    print("MemoryError -> contained by cgroup memory cap")
except Exception as e:
    print(type(e).__name__,"-> contained")
')"
echo "$OUT" | sed 's/^/  /'
if echo "$OUT" | grep -q "NOT CONTAINED"; then fail "10GB alloc succeeded"; else pass "huge alloc contained (MemoryError / OOM-kill, host RAM untouched)"; fi
line

echo "(d2) RESOURCE — fork bomb under --pids-limit 64"
OUT="$(run_py '
import os,sys
n=0
try:
    while n < 100000:
        pid=os.fork()
        if pid==0:
            import time; time.sleep(30); os._exit(0)
        n+=1
except Exception as e:
    print("fork stopped after",n,"children ->",type(e).__name__); sys.exit(0)
print("forked 100000 -> NOT CONTAINED")
')"
echo "$OUT" | sed 's/^/  /'
if echo "$OUT" | grep -q "NOT CONTAINED"; then fail "fork bomb unbounded"; else pass "fork bomb capped by --pids-limit (host processes unaffected)"; fi
line

echo "(d3) RESOURCE — infinite loop killed by runner wall-clock (mode=code, 5s)"
RUN="$WORK/d3"; mkdir -p "$RUN/in" "$RUN/out"; chmod 777 "$RUN/out"
printf '%s' '{"mode":"code","code":"while True:\n    x=1","wall_timeout_sec":5}' > "$RUN/in/job.json"
timeout 60 docker run "${LOCK[@]}" -v "C:/sbx-proof/d3/in":/job/in:ro -v "C:/sbx-proof/d3/out":/job/out "$IMAGE" >/dev/null 2>&1
META="$(cat "$RUN/out/__meta.json" 2>/dev/null)"
echo "  __meta.json: $META"
if echo "$META" | grep -q '"timed_out": true'; then pass "infinite loop hard-killed at wall-clock cap (exit 124)"; else fail "wall-clock timeout not enforced"; fi
line

echo "(f) PERSISTENCE — file from run1 must be absent in run2 (ephemeral tmpfs + --rm)"
OUT1="$(run_py 'open("/tmp/marker","w").write("run1-was-here"); print("run1 wrote /tmp/marker")')"
OUT2="$(run_py '
import os
print("run2 sees /tmp/marker:", os.path.exists("/tmp/marker"))
print("run2 /tmp listing:", sorted(os.listdir("/tmp")))
')"
echo "$OUT1" | sed 's/^/  /'; echo "$OUT2" | sed 's/^/  /'
if echo "$OUT2" | grep -q "run2 sees /tmp/marker: False"; then pass "no state leaks across runs (fresh tmpfs each --rm)"; else fail "state persisted between runs"; fi
line
rm -rf "$WORK"
echo "BREAKOUT SUITE COMPLETE — review any *** FAIL *** lines above."

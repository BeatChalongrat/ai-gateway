#!/usr/bin/env bash
# FIX129 A5 — run the backup service ONCE against the TEST stack and assert
# the result: a manifest was written, drill.json says every check passed,
# sizes are reported. Test stack only: it refuses the live network.
#
#   bash scripts/probes/FIX129/backup_drill_check.sh --env-file <scratch env> [options]
#
#   --env-file FILE   a scratch file holding ONLY the lines the backup needs
#                     (POSTGRES_PASSWORD, WEBCHAT_POSTGRES_PASSWORD, and
#                     optionally POSTGRES_DB / POSTGRES_USER). Never the repo
#                     .env itself, never printed.
#   --network NAME    docker network of the test stack (default sran-test_default)
#   --uploads VOLUME  the test stack's uploads volume (default sran-test_ts_chat_edge_uploads)
#   --image IMAGE     backup image (default ai-gateway-backup:fix129)
#   --out DIR         host directory that becomes /backups
#                     (default scripts/probes/FIX129/results/ops/backups)
#
# Exit 0 = all assertions hold; 1 = the run or an assertion failed; 2 = refused.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
NET=sran-test_default
VOL=sran-test_ts_chat_edge_uploads
IMG=ai-gateway-backup:fix129
OUT="$ROOT/scripts/probes/FIX129/results/ops/backups"
ENVF=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENVF="$2"; shift 2 ;;
    --network)  NET="$2";  shift 2 ;;
    --uploads)  VOL="$2";  shift 2 ;;
    --image)    IMG="$2";  shift 2 ;;
    --out)      OUT="$2";  shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# ---- refuse anything that is the live stack ---------------------------------
case "$NET" in
  ai-gateway_default|ai-gateway*) echo "REFUSED: network '$NET' is the live stack; this check runs against the test stack only" >&2; exit 2 ;;
esac
case "$VOL" in
  ai-gateway_*|ai-gateway-*) echo "REFUSED: volume '$VOL' belongs to the live stack" >&2; exit 2 ;;
esac
[ -n "$ENVF" ] || { echo "--env-file is required (a scratch copy of the needed lines, not the repo .env)" >&2; exit 2; }
[ -f "$ENVF" ] || { echo "env file not found: $ENVF" >&2; exit 2; }
case "$(basename "$ENVF")" in .env) echo "REFUSED: pass a scratch copy of the needed lines, not .env itself" >&2; exit 2 ;; esac
docker network inspect "$NET" >/dev/null 2>&1 || { echo "network '$NET' does not exist (is the test stack up? scripts/test-stack.sh up)" >&2; exit 2; }
docker volume inspect "$VOL" >/dev/null 2>&1 || { echo "volume '$VOL' does not exist" >&2; exit 2; }
# every container on that network must be the test project's
for c in $(docker network inspect "$NET" --format '{{range .Containers}}{{.Name}} {{end}}'); do
  case "$c" in sran-test-*) ;; *) echo "REFUSED: container '$c' on '$NET' is not a sran-test container" >&2; exit 2 ;; esac
done

mkdir -p "$OUT"
OUT_HOST="$(cd "$OUT" && (pwd -W 2>/dev/null || pwd))"
export MSYS_NO_PATHCONV=1
echo "backup_drill_check: network=$NET uploads=$VOL image=$IMG out=$OUT_HOST"

# ---- one backup + drill ------------------------------------------------------
LOG="$OUT/run_$(date +%Y%m%d_%H%M%S).log"
set +e
docker run --rm --network "$NET" --env-file "$ENVF" \
  -e POSTGRES_HOST=postgres -e WEBCHAT_POSTGRES_HOST=postgres-webchat \
  -e TZ=Asia/Bangkok -e BACKUP_KEEP_DAYS=14 \
  -v "$VOL:/uploads:ro" -v "$OUT_HOST:/backups" \
  "$IMG" now 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
set -e
echo "container exit=$rc"

# ---- assertions ------------------------------------------------------------
python - "$OUT_HOST" "$rc" <<'EOF'
import json, sys
from pathlib import Path
out, rc = Path(sys.argv[1]), int(sys.argv[2])
dirs = sorted(p for p in out.iterdir() if p.is_dir() and (p / "manifest.json").exists())
fails = []
if rc != 0: fails.append(f"container exit code {rc}")
if not dirs: fails.append("no backup directory with manifest.json"); print("FAIL:", fails); sys.exit(1)
d = dirs[-1]
m = json.loads((d / "manifest.json").read_text())
print(f"backup dir: {d.name}  created_at={m['created_at']}  duration_s={m['duration_s']}")
for f, meta in m["files"].items():
    ok = (d / f).exists() and (d / f).stat().st_size == meta["bytes"]
    print(f"  {f:16} bytes={meta['bytes']:>10}  sha256={meta['sha256'][:12]}...  {'ok' if ok else 'MISSING/SIZE MISMATCH'}")
    if not ok: fails.append(f"file {f}")
    if meta["bytes"] <= 0: fails.append(f"{f} is empty")
print("  gateway counts:", m["gateway"]["counts"])
print("  chat counts:   ", m["chat"]["counts"], " uploads entries:", m["uploads"]["entries"])
dj = d / "drill.json"
if not dj.exists(): fails.append("no drill.json")
else:
    dr = json.loads(dj.read_text())
    print(f"drill: pass={dr['pass']} checks={dr['checks_total']-dr['checks_failed']}/{dr['checks_total']} duration_s={dr['duration_s']}")
    for c in dr["checks"]:
        print(f"  {'PASS' if c['pass'] else 'FAIL'} {c['name']:28} expected={c['expected'][:16]} actual={c['actual'][:16]}")
    if not dr["pass"]: fails.append("drill failed")
    if any(not c["pass"] for c in dr["checks"]): fails.append("a drill check failed")
if fails:
    print("RESULT: FAIL -", "; ".join(fails)); sys.exit(1)
print("RESULT: PASS - manifest written, drill.json all pass, sizes reported")
EOF

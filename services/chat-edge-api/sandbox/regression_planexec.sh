#!/usr/bin/env bash
# Phase 13 Part A — plan-exec REGRESSION inside the sandbox.
#
# Proves the EXISTING plan-exec scripts (excel_executor.py,
# docx_generator.py) still produce valid Office files when run through
# the hardened sandbox runner (script mode, full lockdown) instead of
# in-process-with-secrets. Mirrors what internal/sandbox.Execute() does.
set -u
IMAGE="ai-gateway-sandbox:latest"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="/c/sbx-reg"; WIN="C:/sbx-reg"
rm -rf "$WORK"; mkdir -p "$WORK"; cp "${HERE}/seccomp.json" "$WORK/seccomp.json"

LOCK=(--rm --network none --user 65534:65534 --cap-drop ALL
  --security-opt no-new-privileges --security-opt "seccomp=${WIN}/seccomp.json"
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777
  --memory 512m --memory-swap 512m --pids-limit 128 --cpus 1.0
  --ulimit fsize=134217728:134217728 --ulimit nofile=256:256)

run() { # $1 = run subdir under WORK/WIN
  local d="$1"
  docker run "${LOCK[@]}" -v "${WIN}/${d}/in":/job/in:ro -v "${WIN}/${d}/out":/job/out "$IMAGE"
}

echo "############ EXCEL executor regression (create_workbook) ############"
mkdir -p "$WORK/xl/in" "$WORK/xl/out"; chmod 777 "$WORK/xl/out"
cat > "$WORK/xl/in/plan.json" <<'JSON'
{"operation":"create_workbook","actions":[
  {"type":"create_sheet","sheet_name":"Sales","position":0},
  {"type":"write_table","sheet_name":"Sales","start_cell":"A1",
   "headers":["Region","Q1","Q2"],
   "data":[["North",100,120],["South",90,110],["East",70,95]]},
  {"type":"create_chart","sheet_name":"Sales","chart_type":"bar",
   "title":"Quarterly","data_range":"Sales!B1:C4","category_range":"Sales!A1:A4","anchor_cell":"E2"}
]}
JSON
# script args: <plan.json> <source.xlsx> <output.xlsx>. create_workbook
# ignores source, so pass the plan itself as a stand-in source arg.
cat > "$WORK/xl/in/job.json" <<'JSON'
{"mode":"script","script":"excel_executor.py","args":["plan.json","plan.json","out/output.xlsx"],"wall_timeout_sec":60}
JSON
run xl; echo "docker-exit=$?"
echo "--- meta:";   cat "$WORK/xl/out/__meta.json" 2>&1; echo
echo "--- stdout:"; cat "$WORK/xl/out/__stdout" 2>&1; echo
echo "--- out dir:"; ls -la "$WORK/xl/out"
# Validate the produced workbook by running excel_validator.py in a 2nd
# sandbox pass over the generated file.
mkdir -p "$WORK/xlv/in" "$WORK/xlv/out"; chmod 777 "$WORK/xlv/out"
cp "$WORK/xl/out/output.xlsx" "$WORK/xlv/in/output.xlsx" 2>/dev/null
cat > "$WORK/xlv/in/job.json" <<'JSON'
{"mode":"script","script":"excel_validator.py","args":["output.xlsx"],"wall_timeout_sec":60}
JSON
run xlv >/dev/null 2>&1
echo "--- validator verdict:"; cat "$WORK/xlv/out/__stdout" 2>&1; echo

echo "############ DOCX generator regression ############"
mkdir -p "$WORK/dc/in" "$WORK/dc/out"; chmod 777 "$WORK/dc/out"
cat > "$WORK/dc/in/plan.json" <<'JSON'
{"document_settings":{"language":"en"},"actions":[
  {"type":"add_heading","text":"Executive Summary","level":1},
  {"type":"add_paragraph","text":"Revenue grew across all regions this quarter."},
  {"type":"add_table","headers":["Region","Revenue"],"rows":[["North","100"],["South","90"]]}
]}
JSON
cat > "$WORK/dc/in/job.json" <<'JSON'
{"mode":"script","script":"docx_generator.py","args":["plan.json","out/output.docx"],"wall_timeout_sec":120}
JSON
run dc; echo "docker-exit=$?"
echo "--- meta:";   cat "$WORK/dc/out/__meta.json" 2>&1; echo
echo "--- stdout:"; cat "$WORK/dc/out/__stdout" 2>&1; echo
echo "--- out dir:"; ls -la "$WORK/dc/out"
mkdir -p "$WORK/dcv/in" "$WORK/dcv/out"; chmod 777 "$WORK/dcv/out"
cp "$WORK/dc/out/output.docx" "$WORK/dcv/in/output.docx" 2>/dev/null
cat > "$WORK/dcv/in/job.json" <<'JSON'
{"mode":"script","script":"doc_validator.py","args":["output.docx"],"wall_timeout_sec":60}
JSON
run dcv >/dev/null 2>&1
echo "--- validator verdict:"; cat "$WORK/dcv/out/__stdout" 2>&1; echo
echo "############ done ############"

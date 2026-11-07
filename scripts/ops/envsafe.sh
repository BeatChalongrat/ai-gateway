#!/usr/bin/env bash
# The only way a value is read out of .env or out of a container's environment on a command line.
#
# Three times a secret was printed by a command that only meant to look something up — the last on
# 2026-10-08, when listing a container's DLP_* settings printed a key with them. Care did not hold, so
# the looking-up is done here and nowhere else: this script cannot print a value unless its NAME is on
# the short list below of settings that are not secrets.
#
#   bash scripts/ops/envsafe.sh names [prefix]            the NAMES in .env (never a value)
#   bash scripts/ops/envsafe.sh has NAME                  "set" / "empty" / "absent"
#   bash scripts/ops/envsafe.sh get NAME                  the value — only for a NAME on the list
#   bash scripts/ops/envsafe.sh container <name> [prefix] the NAMES in a running container's environment,
#                                                         and for names on the list their values
#   bash scripts/ops/envsafe.sh same NAME <container>     whether .env and the container hold the same
#                                                         value for NAME ("same" / "differs"), never either
#   bash scripts/ops/envsafe.sh set NAME VALUE            write a setting, in place — only a NAME on the
#                                                         list: a secret is never typed on a command line
#
# To add a name to the list is to say, in a commit, that it is not a secret.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENVSAFE_FILE:-$ROOT/.env}"
NOT_SECRET='^(QA_EMAIL|QA_USER_ID|QA_TENANT_ID|SMOKE_EMAIL|SMOKE_USER_ID|SMOKE_TENANT_ID|SMOKE_TENANT_ROLE|APP_ENV|TZ|DLP_ENGINE|DLP_GRPC_ADDRESS|DLP_PSEUDONYM_MODE|DLP_PROMPT_VARIANT|DLP_THAI_NER_ENABLED|WORKERS|NER_WORKERS|KB_THREADS|GUNICORN_WORKERS|BACKUP_AT|BACKUP_KEEP_DAYS|PASSWORD_LOGIN_ENABLED|ALLOWED_EMAIL_DOMAINS|DLP_IN_HOUSE_PROJECTS)$'
NAME_RE='^[A-Za-z_][A-Za-z0-9_]*$'
line_of() { grep -m1 -E "^$1=" "$ENV_FILE" 2>/dev/null | tr -d '\r'; }
value_of() { local l; l="$(line_of "$1")"; l="${l#*=}"; l="${l%\"}"; l="${l#\"}"; printf '%s' "$l"; }
need_name() { [[ "${1:-}" =~ $NAME_RE ]] || { echo "a NAME is letters, digits and underscores (what was given is not shown)" >&2; exit 2; }; }
case "${1:-}" in
  names)
    grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" | cut -d= -f1 | grep -E "^${2:-}" | sort ;;
  has)
    need_name "${2:-}"
    if ! grep -q -E "^$2=" "$ENV_FILE"; then echo absent; elif [ -z "$(value_of "$2")" ]; then echo empty; else echo set; fi ;;
  get)
    need_name "${2:-}"
    [[ "$2" =~ $NOT_SECRET ]] || { echo "$2 is not on the list of settings that are not secrets: its value is not shown (use: has, same)" >&2; exit 3; }
    value_of "$2"; echo ;;
  container)
    c="${2:?container}"; p="${3:-}"
    docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | tr -d '\r' | while IFS= read -r l; do
      n="${l%%=*}"
      [[ "$n" =~ $NAME_RE ]] || continue
      [[ "$n" == "$p"* ]] || continue
      if [[ "$n" =~ $NOT_SECRET ]]; then printf '%s=%s\n' "$n" "${l#*=}"; else printf '%s=(%s)\n' "$n" "$([ -n "${l#*=}" ] && echo set || echo empty)"; fi
    done | sort ;;
  same)
    need_name "${2:-}"; c="${3:?container}"
    a="$(value_of "$2")"
    b="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | tr -d '\r' | grep -m1 -E "^$2=" | cut -d= -f2-)"
    [ "$a" = "$b" ] && echo same || echo differs ;;
  set)
    # write a setting — only a NAME on the list: a secret is never typed on a command line
    need_name "${2:-}"
    [[ "$2" =~ $NOT_SECRET ]] || { echo "$2 is not on the list of settings that are not secrets: it is not written from a command line" >&2; exit 3; }
    python "$ROOT/scripts/ops/envsafe_set.py" "$ENV_FILE" "$2" "${3-}" ;;
  *)
    sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

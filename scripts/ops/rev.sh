#!/usr/bin/env bash
# AM24b — the short revision, one rule for every tool (sourced: `. scripts/ops/rev.sh`; git runs in $ROOT when the
# caller set it, else here).
#
# A build is tagged with git's abbreviation of at least seven characters (`git rev-parse --short=7`): seven, longer
# only when seven is ambiguous in this repository. Every image on this host and every rollback tag already carries
# it. A lookup never assumes the length: it resolves the revision to its full commit and takes the image whose tag is
# a prefix of it, seven characters or more. So `--rev` accepts anything git accepts — a full SHA, seven characters,
# eight, a tag, HEAD — and still finds the image when the repository has grown and git abbreviates longer.
#
# Until AM24b, deploy-backend.sh asked for `git rev-parse --short` (git's default length: eight here since the
# repository grew) while the builds tagged seven; on 18 Oct 2026 it could not find the image it had just built and
# stopped with exit 125, before touching anything.

# git in the caller's checkout: a cd, not git -C — under MSYS_NO_PATHCONV=1 git cannot read a /c/... path
_rev_git() { ( cd "${ROOT:-.}" && git "$@" ); }

# rev_full <rev>: the full commit <rev> names, or nothing and a line saying so.
rev_full() {
  _rev_git rev-parse --verify --quiet "${1}^{commit}" || { echo "not a commit git knows: $1" >&2; return 1; }
}

# rev_tag <rev>: the tag a build of <rev> carries.
rev_tag() {
  local f
  f=$(rev_full "$1") || return 1
  _rev_git rev-parse --short=7 "$f"
}

# rev_image <repository> <rev>: the image of <repository> built from <rev>, whatever length its tag; or nothing and
# a line naming what was looked for.
rev_image() {
  local repo="$1" f t best=""
  f=$(rev_full "$2") || return 1
  while IFS= read -r t; do
    t=${t%$'\r'}
    [ "${#t}" -ge 7 ] || continue
    case "$f" in "$t"*) [ -z "$best" ] || [ "${#t}" -lt "${#best}" ] && best="$t" ;; esac
  done < <(docker image ls "$repo" --format '{{.Tag}}' 2>/dev/null)
  if [ -n "$best" ]; then echo "$repo:$best"; return 0; fi
  echo "no image of $repo built from $2 ($f): no tag of it is a prefix of that commit, 7 characters or more — build it first" >&2
  return 1
}

# rev_existing_tag <repository> <rev>: the tag part of rev_image when <rev> is a commit; <rev> itself otherwise (a tag
# that is not a commit, such as rollback-f01f1ee, is used as given).
rev_existing_tag() {
  local img
  if _rev_git rev-parse --verify --quiet "${2}^{commit}" >/dev/null; then
    img=$(rev_image "$1" "$2") || { echo "$2"; return 0; }
    echo "${img#*:}"
  else
    echo "$2"
  fi
}

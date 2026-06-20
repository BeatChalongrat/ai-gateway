import os, re, subprocess, glob

ROOT = r"C:\Users\Beat Beat\Desktop\ai-gateway"
SPLIT = os.path.join(ROOT, "reports", "release-split")
RAW = os.path.join(SPLIT, "raw")
OUT = os.path.join(SPLIT, "backend-dlp-only.patch")

# authoritative untracked set from git (porcelain '??'), forward-slash paths
_porc = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                       cwd=ROOT, capture_output=True).stdout.decode("utf-8", "replace")
GIT_UNTRACKED = set()
for ln in _porc.splitlines():
    if ln.startswith("?? "):
        GIT_UNTRACKED.add(ln[3:].strip().strip('"'))

UNTRACKED = [
    "backend/app/api/routes/internal_dlp.py",
    "backend/app/services/dlp/canonical_artifact.py",
    "backend/app/services/dlp/feature_scope.py",
    "backend/app/services/dlp/numeric_context.py",
    "backend/app/services/dlp/policy_engine.py",
    "backend/app/services/dlp/public_entity_registry.py",
    "backend/app/services/dlp/secure_calc.py",
    "backend/app/services/dlp/secure_calc_intent.py",
    "backend/app/services/dlp/tool_arg_policy.py",
    "backend/app/services/dlp/vault_v2.py",
    "backend/app/services/dlp/data/thai_public_entities.v1.json",
]
# only untracked DLP tests (tracked test_dlp_*.py are already committed)
UNTRACKED += sorted(p for p in GIT_UNTRACKED
                    if p.startswith("backend/tests/") and "test_dlp_" in p and p.endswith(".py"))

# safety: every included whole file MUST be untracked (data/ dir shows as the dir in porcelain)
for p in UNTRACKED:
    parent_dir = p.rsplit("/", 1)[0] + "/"
    assert p in GIT_UNTRACKED or parent_dir in GIT_UNTRACKED or any(
        u.endswith("/") and p.startswith(u) for u in GIT_UNTRACKED), f"NOT untracked: {p}"

# mixed file diff -> hunks to DROP (1-based); splits = {hunk: (start_substr, end_substr)}
MIXED = {
    "backend_app_api_routes_public_chat.py.diff": {"drop": {30, 32},
                                                    "split": {1: ("resolved_log_model", "resolved_log_model")}},
    "backend_app_services_orchestrator.py.diff": {"drop": {4, 20, 21, 22, 26}, "split": {}},
    "backend_app_main.py.diff": {"drop": {1, 2, 3, 4, 5, 7},
                                 "split": {6: ("public_search_router", "public_search_router")}},
    "backend_app_core_config.py.diff": {"drop": {1},
                                        "split": {2: ("blocker B1", "ai_review_allowed_hosts")}},
    "backend_app_domain_observability.py.diff": {"drop": {1}, "split": {}},
    "backend_app_services_dlp_transformer.py.diff": {"drop": set(), "split": {}},
    "backend_app_services_dlp_conversation_vault.py.diff": {"drop": set(), "split": {}},
}


def new_file_diff(path):
    r = subprocess.run(["git", "diff", "--no-index", "/dev/null", path],
                       cwd=ROOT, capture_output=True)
    return r.stdout.decode("utf-8", "replace")


def parse(diff_text):
    lines = diff_text.splitlines(keepends=False)
    pre, hunks, cur = [], [], None
    for ln in lines:
        if ln.startswith("@@ "):
            if cur is not None:
                hunks.append(cur)
            cur = [ln]
        elif cur is None:
            pre.append(ln)
        else:
            cur.append(ln)
    if cur is not None:
        hunks.append(cur)
    return pre, hunks


HDR = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def split_hunk(hunk, start_sub, end_sub):
    header, body = hunk[0], hunk[1:]
    si = ei = None
    for i, l in enumerate(body):
        if l.startswith("+") and start_sub in l and si is None:
            si = i
        if l.startswith("+") and end_sub in l:
            ei = i
    assert si is not None and ei is not None and ei >= si, f"split markers not found: {start_sub}/{end_sub}"
    removed = body[si:ei + 1]
    assert all(l.startswith("+") for l in removed), "split range must be all added lines"
    nbody = body[:si] + body[ei + 1:]
    m = HDR.match(header)
    a, b, c, d, tail = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    b = int(b) if b else 1
    d = int(d) if d else 1
    d -= len(removed)  # only '+' removed -> new side shrinks; old side unchanged
    nheader = f"@@ -{a},{b} +{c},{d} @@{tail}"
    return [nheader] + nbody, len(removed)


report = []
chunks = []

# Part 1: untracked whole files
for p in UNTRACKED:
    if not os.path.exists(os.path.join(ROOT, p)):
        report.append(f"  MISSING untracked: {p}")
        continue
    chunks.append(new_file_diff(p).rstrip("\n"))
report.append(f"Untracked whole files included: {len(UNTRACKED)}")

# Part 2: mixed files, hunk-filtered + split
for fname, cfg in MIXED.items():
    raw = open(os.path.join(RAW, fname), encoding="utf-8").read()
    pre, hunks = parse(raw)
    kept, dropped, splitinfo = [], [], []
    for idx, h in enumerate(hunks, 1):
        if idx in cfg["drop"]:
            dropped.append(idx)
            continue
        if idx in cfg["split"]:
            s, e = cfg["split"][idx]
            h, nrem = split_hunk(h, s, e)
            splitinfo.append(f"h{idx}(-{nrem}ln)")
        kept.append(h)
    out = "\n".join(pre)
    for h in kept:
        out += "\n" + "\n".join(h)
    chunks.append(out)
    report.append(f"{fname}: kept {len(kept)} drop {sorted(dropped)} split {splitinfo}")

patch = "\n".join(chunks) + "\n"
open(OUT, "w", encoding="utf-8", newline="\n").write(patch)
report.append(f"\nPATCH: {OUT}  ({len(patch.splitlines())} lines)")
print("\n".join(report))

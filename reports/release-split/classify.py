import os, re, glob

RAW = os.path.join(os.path.dirname(__file__), "raw")

NONDLP = re.compile(r"folder|search|citation|searching|image[_ ]?gen|generate_image|generated_image|"
                    r"credit|usage|markup|placeholder|useUsage|admin_search|public_search|"
                    r"search_provider|provider_search|search_source|websearch|web_search", re.I)
DLP = re.compile(r"\bdlp\b|canonical|vault|secure[_ ]?calc|securecalc|policy_engine|numeric_context|"
                 r"display_state|display_lifecycle|feature_scope|x_sran|<CALC|redact|mask|"
                 r"public_entity|tool_arg|scope_mode|allowlist|is_in_scope|effective_feature|sink_hygien", re.I)

def hunks(path):
    cur = None
    body = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("@@ "):
                if cur is not None:
                    yield cur, body
                cur = line.rstrip("\n")
                body = []
            elif cur is not None:
                body.append(line)
    if cur is not None:
        yield cur, body

for diff in sorted(glob.glob(os.path.join(RAW, "*.diff"))):
    name = os.path.basename(diff)
    rows = []
    for i, (hdr, body) in enumerate(hunks(diff), 1):
        changed = [l for l in body if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        text = "".join(changed)
        nd = bool(NONDLP.search(text))
        dl = bool(DLP.search(text))
        if dl and not nd: tag = "PURE_DLP"
        elif nd and not dl: tag = "NONDLP"
        elif nd and dl: tag = "MIXED!!"
        else: tag = "NEUTRAL"
        rows.append((i, tag, hdr, len(changed)))
    counts = {}
    for _, t, _, _ in rows:
        counts[t] = counts.get(t, 0) + 1
    print(f"\n=== {name}  {counts} ===")
    for i, tag, hdr, n in rows:
        if tag in ("NONDLP", "MIXED!!", "NEUTRAL"):
            print(f"  h{i:<3} {tag:<8} ({n:>3} chg) {hdr[:80]}")

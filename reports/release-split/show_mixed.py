import os, re, sys

RAW = os.path.join(os.path.dirname(__file__), "raw")
NONDLP = re.compile(r"folder|search|citation|searching|image[_ ]?gen|generate_image|generated_image|"
                    r"credit|usage|markup|placeholder|useUsage|admin_search|public_search|"
                    r"search_provider|provider_search|search_source|websearch|web_search", re.I)

target = sys.argv[1]
path = os.path.join(RAW, target)
hno = 0
with open(path, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        if line.startswith("@@ "):
            hno += 1
            cur = line.rstrip()
        elif line[:1] in "+-" and not line.startswith(("+++", "---")):
            if NONDLP.search(line):
                print(f"h{hno:<3} {line.rstrip()[:110]}")

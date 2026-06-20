import os, sys
RAW = os.path.join(os.path.dirname(__file__), "raw")
diff = sys.argv[1]
want = set(int(x) for x in sys.argv[2:])
path = os.path.join(RAW, diff)
hno = 0
emit = False
out = []
with open(path, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        if line.startswith("@@ "):
            hno += 1
            emit = hno in want
            if emit:
                out.append(f"\n----- HUNK {hno} -----")
                out.append(line.rstrip("\n"))
        elif emit:
            out.append(line.rstrip("\n"))
# strip non-ascii to survive cp1252 console
print("\n".join(s.encode("ascii", "replace").decode("ascii") for s in out))

"""AM11 — the server's English catalogue: check a translation file, or write it out as Go.

A translation file is JSON, {"<the Thai sentence exactly as the source has it>": "<its English>"}.

    python scripts/probes/AM11/catalogue.py check  <translations.json> [<todo.json>]
    python scripts/probes/AM11/catalogue.py go     <translations.json> <package> <out.go> "<comment>"

check  says, for every entry: the English takes the same facts (%s …) as the Thai, or indexes all of
       them; has no Thai in it; has no shape the server withholds as an error's text (GLOSSARY.md §6
       rule 4 — the list is failure.go's errorText); uses only the marks the server's reader shows.
       With a todo file (the test's AM11_DUMP), also which sentences of it are still missing.
go     writes the Go file that registers them (init → AddEnglish), keys sorted.
"""
import io
import json
import re
import sys

VERB = re.compile(r"%(\[\d+\])?[sdvq]")
THAI = re.compile(r"[฀-๿]")
# failure.go errorText, as it is there
ERROR_TEXT = [
    re.compile(r"(?i)\btraceback\b|\bstack ?trace\b|\bpanic:|\bgoroutine \d|\bnil pointer\b|\bsegmentation fault\b|\berrno\b"),
    re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception|Warning)\b"),
    re.compile(r"(?i)\b(?:error|fatal|warning|exception):"),
    re.compile(r"(?i)\bobject (?:is not|has no)\b|\bNoneType\b|'[A-Za-z_]+' object|\bnot (?:iterable|subscriptable|callable)\b"),
    re.compile(r"(?i)\b[\w./\\-]+\.(?:py|go|js|ts)\b(?:[\"']?[:,] ?(?:line )?\d+)?|\bline \d+\b"),
    re.compile(r"(?i)\bdial tcp\b|\bconnection re(?:fused|set)\b|\bunexpected EOF\b|\bEOF\b|\bcontext (?:canceled|deadline exceeded)\b"
               r"|\bno such (?:file|host)\b|\bexit (?:status|code) ?\d+|\bbroken pipe\b|\bi/o timeout\b"),
    re.compile(r"(?i)\bengine step failed\b|\bsandbox:|\bquality gate:|\bconverter(?: \d{3}|:)|\bstopped:|\bno result \(exit|\bjson: "
               r"|\binvalid character\b|\bcannot unmarshal\b|\bSQLSTATE\b|\bpq: |\bpgx\b"),
    re.compile(r"(?i)\bdeadlock\b|\bviolates\b|\bconstraint\b|\bsyntax error\b|\bpermission denied\b|\btimed out\b|\bnot found\b|\bundefined\b"),
    re.compile(r"(?i)\bhttps?://\S+|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"),
]
FILE_TOKEN = re.compile(r"(?i)[^\s,;:()\"'“”]+\.(?:xlsx|xlsm|xls|csv|pdf|xml|jpe?g|png|docx?|pptx?|txt|json|zip)\b")
MARKS = set(".,:;!?()/-–—−+=×÷%·…'’‘“”\"@→฿≈#*")


def problems(th: str, en: str) -> list[str]:
    out = []
    a, b = VERB.findall(th), VERB.findall(en)
    n_th, n_en = len(VERB.findall(th)), len(VERB.findall(en))
    indexed = "%[" in en
    if indexed:
        if any(not m for m in b):
            out.append("orders its facts but does not index every one")
        elif {int(m.strip("[]")) for m in b} != set(range(1, n_th + 1)):
            out.append(f"indexes {sorted({int(m.strip('[]')) for m in b})}, Thai has {n_th} facts")
    elif n_th != n_en:
        out.append(f"{n_th} facts in Thai, {n_en} in English")
    quoted = re.sub(r"“[^“”]*”", "", en)
    if THAI.search(quoted):
        out.append("Thai in the English (outside quotation marks)")
    filled = FILE_TOKEN.sub(" ", VERB.sub("1", en))
    for rx in ERROR_TEXT:
        m = rx.search(filled)
        if m:
            out.append(f"reads like an error's text: “{m.group(0)}”")
            break
    bad = sorted({c for c in en if not (c.isalnum() or c.isspace() or c in MARKS or THAI.match(c) or c in "{}_[]")
                  and c != "\n"})
    # [ ] only as part of an indexed verb
    if re.search(r"\[(?!\d+\])|(?<!%\[\d)(?<!%\[\d\d)\]", en):
        bad.append("[ ]")
    if bad:
        out.append("marks the server's reader does not show: " + " ".join(bad))
    if en != en.strip() and th == th.strip():
        out.append("leading or trailing space the Thai does not have")
    return out


def check(path: str, todo_path: str | None) -> int:
    data = json.load(io.open(path, encoding="utf-8"))
    bad = 0
    for th, en in data.items():
        if not isinstance(en, str) or not en.strip():
            print(f"EMPTY  {th[:70]}")
            bad += 1
            continue
        for p in problems(th, en):
            bad += 1
            print(f"BAD    {p}\n       th: {th[:160]}\n       en: {en[:160]}")
    missing = 0
    if todo_path:
        todo = json.load(io.open(todo_path, encoding="utf-8"))
        want = {t["thai"] for t in todo}
        for th in sorted(want - set(data)):
            missing += 1
            print(f"MISSING {th[:90]}")
        for th in sorted(set(data) - want):
            print(f"EXTRA   (not in the todo list) {th[:90]}")
    print(f"{len(data)} entries · {bad} problems · {missing} missing")
    return 1 if bad or missing else 0


def go_quote(s: str) -> str:
    return json.dumps(s, ensure_ascii=False).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")


def write_go(path: str, package: str, out: str, comment: str) -> int:
    data = json.load(io.open(path, encoding="utf-8"))
    call = "AddEnglish" if package == "agentmarket" else "am.AddEnglish"
    lines = ["package " + package, ""]
    if package != "agentmarket":
        lines += ['import am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"', ""]
    for c in comment.split("\\n"):
        lines.append("// " + c if c else "//")
    lines += ["", "func init() {", "\t" + call + "(map[string]string{"]
    for th in sorted(data):
        lines.append(f"\t\t{go_quote(th)}: {go_quote(data[th])},")
    lines += ["\t})", "}", ""]
    io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    print(f"{out}: {len(data)} sentences")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1]
    if cmd == "check":
        sys.exit(check(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    if cmd == "go":
        sys.exit(write_go(*sys.argv[2:6]))
    sys.exit(2)

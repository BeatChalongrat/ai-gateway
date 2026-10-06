"""AM11 (B, C) — the 78 English strings AM10's walk read on the admin app's Thai screen, each judged.

AM10 reported 78 strings: 13 stored codes shown raw, and 65 "names". This reads that walk's own
list (scripts/probes/AM10/results/admin/<target>/language-strings.json) and judges every string:

    code          a stored code shown as stored                      → worded (AM11 B)
    missed        a term of ours left in English inside Thai text    → translated (AM11 C)
    name          the name of a tenant, a project, a provider, a model, a package, a setting,
                  a file or an address: somebody's own word          → stays, in both languages
    typed         what a person typed (a report's note, an address)  → stays, as typed

With a second list (the same walk on this build) it also says, per string, whether the Thai screen
still shows it — a code or a missed translation that is still there fails.

    python scripts/probes/AM11/admin_strings.py <before.json> [after.json]
"""
import io
import json
import re
import sys

CODES = {"critical", "warning", "info", "delivered", "grouped", "log_only", "credential", "financial", "identity", "network", "pii", "secret"}
TENANTS = {"Acme Corporation", "GB Tech", "Integ Fixes Tenant", "TechStart"}
KEYS_AND_PROJECTS = {"Agent Market runner", "AI Chatbot MVP", "Customer Support Bot", "Integ Fixes Project", "Internal Assistant", "SRAN Desktop"}
PROVIDERS = {"anthropic", "cohere", "deepseek", "elevenlabs", "google", "groq", "kimi", "minimax", "mistral", "mock", "openai", "openrouter", "perplexity",
             "qwen", "replicate", "sranai", "xai", "zai", "sran ai"}
# a term of ours inside a Thai sentence → what the sentence says now
MISSED = [
    (re.compile(r"^API Key$"), "คีย์ API (the menu entry and ten sentences)"),
    (re.compile(r"audit log"), "ประวัติการทำงานของผู้ดูแล"),
    (re.compile(r"manual override"), "ค่าที่ตั้งเอง · การอัปเดตรายการโมเดลอัตโนมัติ"),
    (re.compile(r"discovery sync"), "การอัปเดตรายการโมเดลอัตโนมัติ"),
    (re.compile(r"ลำดับ fallback"), "ลำดับสายสำรอง · ผู้ให้บริการ:โมเดล"),
    (re.compile(r"ปิดใน production"), "ปิดในระบบที่ใช้งานจริง"),
    (re.compile(r"^(Default|Strict)$"), "named by the app from the profile's id: ค่าตั้งต้น / เข้มงวด"),
]


def judge(text):
    t = text.strip()
    if t in CODES or "สถานะรอบ succeeded" in t:
        return "code", "a stored code shown as stored — now a word (table B)"
    for pattern, now in MISSED:
        if pattern.search(t):
            return "missed", "a term of ours left in English — now: %s" % now
    if t in TENANTS or t.endswith("'s Team"):
        return "name", "a tenant's name, as the tenant was named"
    if t in KEYS_AND_PROJECTS:
        return "name", "the name of a project or a key, as it was named"
    if t.lower() in PROVIDERS:
        return "name", "a provider: its id on Models, its own name on Providers"
    if re.match(r"^sran\.[a-z-]+ ", t):
        return "name", "a package's id (what an operator searches by); the words around it are Thai"
    if t == "agent" or " agent " in " %s " % t and not re.search(r"\b(fallback|production|override|sync|audit log)\b", t):
        return "name", "\"agent\" is written in Latin letters on the Thai screen too (GLOSSARY.md §2)"
    if re.search(r"qwen3\.8|gpt-image|Kling|Wan\b", t):
        return "name", "a model's id or name"
    if re.search(r"\.env|AGENT_MARKET_SMTP_PASSWORD|smtp\.example", t):
        return "name", "the name of a setting or a file, or an example address — typed exactly as shown"
    if re.search(r"@|\[AM\d+\]", t):
        return "typed", "an address, or a note somebody typed"
    return "?", "NOT JUDGED"


def strings(path):
    data = json.load(io.open(path, encoding="utf-8"))
    return (data.get("flat") or {}).get("th") or []


def mask(text):
    return re.sub(r"([\w.+-])[\w.+-]*@([\w.-]+)", r"\1***@\2", text)


def main():
    before = strings(sys.argv[1])
    after = {e["text"] for e in strings(sys.argv[2])} if len(sys.argv) > 2 else None
    counts, bad = {}, []
    print("| # | the string AM10 read on the Thai screen | where | verdict | why / what it says now |" + (" still on the Thai screen |" if after is not None else ""))
    print("|---|---|---|---|---|" + ("---|" if after is not None else ""))
    for i, e in enumerate(before, 1):
        verdict, why = judge(e["text"])
        counts[verdict] = counts.get(verdict, 0) + 1
        row = [str(i), mask(e["text"]).replace("|", "\\|"), (e.get("on") or [""])[0] + (" +%d" % (len(e["on"]) - 1) if len(e.get("on") or []) > 1 else ""), verdict, why]
        if after is not None:
            still = e["text"] in after
            row.append("yes" if still else "no")
            if still and verdict in ("code", "missed"):
                bad.append(e["text"])
        if verdict == "?":
            bad.append(e["text"])
        print("| " + " | ".join(row) + " |")
    print("\n%d strings: %s" % (len(before), " · ".join("%s %d" % kv for kv in sorted(counts.items()))))
    if after is not None:
        new = sorted(after - {e["text"] for e in before})
        print("on the Thai screen now: %d strings; %d of them were not in AM10's list" % (len(after), len(new)))
        for t in new:
            print("  new: %s — %s" % (mask(t), judge(t)[0]))
    if bad:
        print("PROBLEM: %s" % [mask(b) for b in bad], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())

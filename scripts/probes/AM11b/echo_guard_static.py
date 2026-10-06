# -*- coding: utf-8 -*-
"""AM11b — question 1 carried from AM12: did the platform's prompt-echo guard cut pieces out of the other
five agents' model answers?

The guard (backend/app/services/prompt_echo_guard.py) removes from an answer every run of 40 characters
that repeats the SYSTEM message. Run inside the backend test lane (no network):

    bash scripts/backend-test-lane.sh /probe/echo_guard_static.py     (see echo_guard.sh)

For every system message the runner sends for the six agents — a package's AGENT.md (the report's
commentary and the answers to questions about a round), and the three prompts written in the runner
(form mapping, a typed answer to a card, a question about a round) — it asks the guard itself:
  1. would an answer that reproduces a JSON shape printed in that system message be cut?
  2. would the answers a model really gave (the package's recorded commentaries, where there are any) be cut?
and prints a table. It changes nothing.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from types import SimpleNamespace

from app.services.prompt_echo_guard import ECHO_MIN_RUN, build_echo_fingerprints, scrub_echo

PACKAGES = pathlib.Path("/app/agent_market/packages")
RUNNER = pathlib.Path("/services/chat-edge-api/internal/agentmarket/runner")


def system(text: str):
    return [SimpleNamespace(role="system", content=text)]


def json_blocks(text: str):
    """Every balanced {...} of the text that parses as JSON once its "…" placeholders are strings."""
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:i + 1])
    return [b for b in out if len("".join(b.split())) >= ECHO_MIN_RUN]


def go_string(src: str, name: str) -> str:
    """A Go string constant or the literal after `name :=`, its + concatenation joined."""
    m = re.search(name + r"\s*:?=\s*((?:\"(?:[^\"\\]|\\.)*\"\s*\+?\s*)+)", src)
    if not m:
        return ""
    parts = re.findall(r"\"((?:[^\"\\]|\\.)*)\"", m.group(1))
    return "".join(bytes(p, "utf-8").decode("unicode_escape").encode("latin-1", "ignore").decode("utf-8", "ignore") if "\\" in p else p for p in parts)


rows = []


def judge(agent: str, what: str, sys_text: str, answers: list[tuple[str, str]]):
    fp = build_echo_fingerprints(system(sys_text))
    for label, answer in answers:
        kept, removed, runs = scrub_echo(fp, answer)
        rows.append((agent, what, label, len(answer), removed, runs))


for manifest in sorted(PACKAGES.glob("*/agent.json")):
    m = json.loads(manifest.read_text(encoding="utf-8"))
    pkg = manifest.parent
    agent = m["id"]
    md = pkg / "AGENT.md"
    if md.exists():
        text = md.read_text(encoding="utf-8")
        answers = [(f"a JSON shape printed in AGENT.md ({len(b)} chars)", b) for b in json_blocks(text)]
        # the commentaries a model or the rules really wrote for this package, where the package keeps any
        for f in sorted(pkg.rglob("*.json")):
            if f.name == "agent.json" or f.stat().st_size > 2_000_000:
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            found = []

            def walk(node):
                if isinstance(node, dict):
                    if {"summary", "movements"} <= set(node) or {"summary", "watch"} <= set(node):
                        found.append(node)
                    for v in node.values():
                        walk(v)
                elif isinstance(node, list):
                    for v in node:
                        walk(v)

            walk(doc)
            for n, narrative in enumerate(found[:6]):
                answers.append((f"a recorded commentary in {f.relative_to(pkg)} #{n + 1}", json.dumps(narrative, ensure_ascii=False)))
        if not answers:
            answers = [("(AGENT.md prints no JSON shape and the package keeps no recorded commentary)", "")]
        judge(agent, "AGENT.md as the system message (commentary, questions about a round)", text, answers)
    for name, rel in sorted(((m.get("web") or {}).get("prompts") or {}).items()):
        text = (pkg / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        rules, _, ask = text.partition("\n=== with the question ===\n")
        shape = ask[ask.index("{"):].strip() if "{" in ask else ""
        judge(agent, f"web prompt {name} — the rules as sent now", rules, [("an answer that follows the shape", shape)])
        judge(agent, f"web prompt {name} — AS IT WAS BEFORE THE FIX (shape among the rules)", rules + "\n\n" + shape, [("an answer that follows the shape", shape)])

# the prompts written in the runner, the same for every agent
tmpl = (RUNNER / "template.go").read_text(encoding="utf-8")
mapping = go_string(tmpl, r"const mappingSystem")
judge("every agent that fills a form", "form mapping (runner/template.go mappingSystem)", mapping,
      [("a real mapping (the walk's: 16 fields of request_form.docx)",
        json.dumps({"mapping": {"table0.r1c1": "request_date", "table0.r2c1": "requester", "table0.r4c1": "seller", "table0.r5c1": "seller_tax_id",
                                "table0.r6c1": "delivery_place", "rows@t1r1": "lines", "rows@t1r1.c0": "lines.no", "rows@t1r1.c1": "lines.item",
                                "rows@t1r1.c2": "lines.quantity", "rows@t1r1.c3": "lines.unit", "rows@t1r1.c4": "lines.unit_price", "rows@t1r1.c5": "lines.amount"}},
                   ensure_ascii=False)),
       ("the shape as the prompt prints it", "".join(json_blocks(mapping)[:1]))])
clar = (RUNNER / "clarify.go").read_text(encoding="utf-8")
m = re.search(r"system := (\"คุณแปลคำตอบ\".*)", clar)
classify = "คุณแปลคำตอบภาษาไทยของผู้ใช้ให้เป็นหนึ่งในตัวเลือกที่กำหนด ตอบเป็น JSON {\"choice\": หมายเลข}"
judge("every agent", "a typed answer to a card (runner/clarify.go classify) — its answer is {\"choice\": n}", classify, [("{\"choice\": 2}", "{\"choice\": 2}")])

w = max(len(r[0]) for r in rows)
print(f"minimum run the guard removes: {ECHO_MIN_RUN} characters\n")
cut = 0
for agent, what, label, size, removed, runs in rows:
    mark = "CUT" if removed else "whole"
    cut += 1 if removed else 0
    print(f"{mark:5} | {agent:{w}} | {what} | {label} | answer {size} chars · removed {removed} in {runs} run(s)")
print(f"\n{len(rows)} cases · {cut} cut")
sys.exit(0)

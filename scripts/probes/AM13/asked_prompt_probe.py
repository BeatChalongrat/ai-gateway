"""AM13 probe: how real models read a person's words into procurement's two lists, with and without the
line that tells them the words are also the agent's request.

The walk found it: with the line as first written, deepseek-v4-flash read
"… งบไม่เกิน 6,000 บาทต่อตัว เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด" as asking nothing — it took the
whole message for the request. A stand-in model cannot find that; only a real one can.

The question is built here exactly as instructions.go builds it (the system message is read out of that
file; the user turn is AskedPrompt's), sent to the TEST stack's gateway with the test runner's key, and
the answer is read as ReadAsked + OfARequest read it: ids of the two lists only, "other" dropped.

    python scripts/probes/AM13/asked_prompt_probe.py [model …]

Prints one line per (variant, message, model) and writes results/asked_prompt_probe.json. No key is
printed. Each prompt is sent once (the gateway caches an identical question).
"""
import io
import json
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
GO = io.open(os.path.join(ROOT, "services/chat-edge-api/internal/agentmarket/instructions.go"), encoding="utf-8").read()
SYSTEM = re.search(r"const askedSystem = `(.*?)`", GO, re.S).group(1)
NEVER = re.findall(r'\{"(change_figures|skip_checks|change_rules)", Text\("([^"]+)"\)', GO)
MANIFEST = json.load(io.open(os.path.join(ROOT, "backend/agent_market/packages/procurement/agent.json"), encoding="utf-8"))
SPEC = MANIFEST["instructions"]
KEY = io.open(os.path.join(ROOT, "scripts/probes/AM1/results/runner_key.test"), encoding="utf-8").read().strip()
GATEWAY = os.environ.get("PROBE_GATEWAY", "http://localhost:18080")

NOTES = {
    "none": "",
    "first": ("agent ตัวนี้เริ่มงานจากข้อความนี้เอง: สิ่งที่ผู้ใช้ต้องการให้หา จำนวน งบประมาณ คุณสมบัติ ยี่ห้อ สถานที่ และกำหนดส่ง "
              "เป็นเรื่องของการค้นหา ไม่ใช่คำขอเกี่ยวกับเอกสาร ไม่ต้องใส่ทั้งใน apply และ cannot\n\n"),
    "narrow": ("ข้อความนี้เป็นคำขอให้ agent ค้นหาด้วย: ของที่ต้องการ จำนวน งบประมาณ คุณสมบัติ ยี่ห้อ สถานที่ส่ง และกำหนดส่ง "
               "เป็นรายละเอียดของการค้นหา ไม่ต้องใส่ใน cannot ส่วนคำขออื่นในข้อความเดียวกัน เช่น ขอให้เรียงตาราง ขอให้ตัดตัวเลือกออก "
               "หรือขอให้ตัดสินหรือแนะนำตัวเลือก ยังเป็นคำขอเกี่ยวกับเอกสาร ให้จัดเข้ารายการ “ทำได้” และ “ทำไม่ได้” ตามกติกาเดิม\n\n"),
}
PLACE = "ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"
NOTES["split"] = (
    "ข้อความนี้ส่งถึง agent ที่เริ่มงานจากข้อความเอง จึงมีได้สองเรื่องปนกัน ให้อ่านทีละวรรค\n"
    "(1) วรรคที่บอกว่าต้องการซื้อหรือหาอะไร จำนวน งบประมาณ คุณสมบัติ ยี่ห้อ สถานที่ส่ง หรือกำหนดส่ง เป็นคำขอให้ค้นหา ข้ามไป ไม่ต้องใส่ในช่องใด\n"
    "(2) วรรคที่ขอเรื่องตารางหรือเอกสารที่จะได้ เช่น ขอให้เรียง ขอให้ตัดบางรายการออก ขอให้บอกหรือแนะนำว่ารายใดดี "
    "ให้จัดเข้า apply หรือ cannot ตามรายการข้างล่าง โดย words เป็นเฉพาะวรรคนั้น ไม่ใช่ทั้งข้อความ\n\n")
if "--only" in sys.argv:
    keep = sys.argv[sys.argv.index("--only") + 1].split(",")
    NOTES = {k: v for k, v in NOTES.items() if k in keep}
    del sys.argv[sys.argv.index("--only"):sys.argv.index("--only") + 2]
CASES = [
    ("budget+order+verdict", f"ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน {PLACE} งบไม่เกิน 6,000 บาทต่อตัว เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด",
     ["sort_by_price"], ["pick_best"]),
    ("request only", f"ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน {PLACE} งบไม่เกิน 6,000 บาทต่อตัว", [], []),
    ("request+order", "หาโน้ตบุ๊ก 5 เครื่อง แรม 16GB ราคาไม่เกิน 30,000 บาทต่อเครื่อง ส่งภายในสิ้นเดือน เรียงตามราคา", ["sort_by_price"], []),
    ("advice+hide", f"ขอซื้อโปรเจกเตอร์ 1 เครื่อง {PLACE} แล้วแนะนำด้วยว่าควรซื้อเจ้าไหน ตัดเจ้าที่ข้อมูลไม่ครบออกด้วย", [], ["hide_unknown", "pick_best"]),
    ("english", "We need 10 office chairs with armrests, budget 5,000 baht each, delivered to the head office on Silom Road. "
                "Sort by price and tell me which one is best.", ["sort_by_price"], ["pick_best"]),
]


def user_turn(text: str, note: str) -> str:
    b = [note, "ทำได้:\n"]
    for d in SPEC["can"]:
        b.append("- " + d["id"] + ": " + d["label"])
        if d.get("asks"):
            b.append(" (คนมักขอว่า: " + d["asks"] + ")")
        elif d.get("note"):
            b.append(" — " + d["note"])
        b.append("\n")
    b.append("\nทำไม่ได้:\n")
    for d in SPEC["cannot"]:
        b.append("- " + d["id"] + ": " + d["label"] + "\n")
    for i, label in NEVER:
        b.append("- " + i + ": " + label + "\n")
    b.append("\nข้อความของผู้ใช้:\n<<<\n" + text.strip() + "\n>>>\n\n")
    b.append('รูปแบบคำตอบ: {"apply": ["id"], "cannot": [{"id": "id", "words": "คำของผู้ใช้"}]} — ไม่มีข้อใดก็ให้เป็น [] ทั้งสองช่อง')
    return "".join(b)


def ask(model: str, system: str, user: str) -> str:
    body = json.dumps({"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       }).encode("utf-8")
    req = urllib.request.Request(GATEWAY + "/v1/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))["choices"][0]["message"].get("content") or ""


def read(raw: str) -> tuple[list, list, list]:
    """(apply, cannot ids of the two lists, what was dropped as "other") — as ReadAsked and OfARequest keep them."""
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        ans = json.loads(m.group(0)) if m else None
    except ValueError:
        ans = None
    if not isinstance(ans, dict):
        return ["(unread)"], [], []
    can = [d["id"] for d in SPEC["can"]]
    known = [d["id"] for d in SPEC["cannot"]] + [i for i, _ in NEVER]
    apply = [i for i in can if i in [str(x).strip() for x in ans.get("apply") or []]]
    cannot, other = [], []
    for c in ans.get("cannot") or []:
        if not isinstance(c, dict):
            continue
        i = str(c.get("id", "")).strip()
        if i == "other":
            other.append(str(c.get("words", ""))[:40])
        elif i in known and i not in cannot:
            cannot.append(i)
    return apply, sorted(cannot), other


if "--texts" in sys.argv:  # ad-hoc: a UTF-8 file with one message per line, each shown with the model's raw answer
    at = sys.argv.index("--texts")
    lines = [l.strip() for l in io.open(sys.argv[at + 1], encoding="utf-8").read().splitlines() if l.strip()]
    del sys.argv[at:at + 2]
    for model in sys.argv[1:] or ["deepseek-v4-flash"]:
        for variant, note in NOTES.items():
            for text in lines:
                raw = ask(model, SYSTEM, user_turn(text, note))
                print(f"{variant:7} {model:18} {read(raw)} :: {text[:70]}\n        {raw.strip()[:260]}")
    sys.exit(0)
models = sys.argv[1:] or ["deepseek-v4-flash", "qwen3.8-27b-fp8"]
out, score = [], {}
for variant, note in NOTES.items():
    for name, text, want_apply, want_cannot in CASES:
        for model in models:
            try:
                raw = ask(model, SYSTEM, user_turn(text, note))
                apply, cannot, other = read(raw)
            except Exception as e:  # noqa: BLE001 — a probe: say what happened and go on
                raw, apply, cannot, other = f"(no answer: {type(e).__name__})", ["(no answer)"], [], []
            ok = apply == want_apply and cannot == sorted(want_cannot)
            score.setdefault((variant, model), []).append(ok)
            out.append({"variant": variant, "case": name, "model": model, "ok": ok, "apply": apply, "cannot": cannot, "other_dropped": other,
                        "want": [want_apply, sorted(want_cannot)], "raw": raw[:400]})
            print(f"{'ok  ' if ok else 'MISS'} {variant:7} {model:18} {name:22} apply={apply} cannot={cannot} other_dropped={len(other)}")
for (variant, model), oks in score.items():
    print(f"== {variant:7} {model:18} {sum(oks)}/{len(oks)}")
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"), exist_ok=True)
io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "asked_prompt_probe.json"), "w", encoding="utf-8").write(
    json.dumps(out, ensure_ascii=False, indent=1))

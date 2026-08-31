"""FIX37 mask-quality eval runner.

    python -m evals.mask_quality.run --stage validate
    python -m evals.mask_quality.run --stage detect   --out results/x.json
    python -m evals.mask_quality.run --stage latency  --out results/x.json
    python -m evals.mask_quality.run --stage axis-a   --out results/x.json
    python -m evals.mask_quality.run --stage axis-b   --out results/x.json
    python -m evals.mask_quality.run --stage all      --out results/x.json

Stages that need a model (axis-a / axis-b) read ``GATEWAY_URL`` and
``GATEWAY_API_KEY`` from the environment. The key is never printed.

``--ner on|off`` selects the registry arm (default: off = today's
production shape, because ``org_settings.ner_enabled`` is false).
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import sys
import time
from pathlib import Path

from evals.mask_quality import harness as H

CRIT = H.CRITERIA


# ── stage: validate ───────────────────────────────────────────────────────

def stage_validate() -> dict:
    msgs = H.load_messages()
    problems = H.validate_corpus(msgs)
    case_types: dict[str, int] = {}
    scenarios: dict[str, int] = {}
    pii_types: dict[str, int] = {}
    for m in msgs:
        scenarios[m["scenario"]] = scenarios.get(m["scenario"], 0) + 1
        for c in m.get("case_types", []):
            case_types[c] = case_types.get(c, 0) + 1
        for p in m.get("pii", []):
            pii_types[p["type"]] = pii_types.get(p["type"], 0) + 1
    return {
        "messages": len(msgs),
        "scenarios": scenarios,
        "case_types": case_types,
        "pii_types": pii_types,
        "pii_values": sum(len(m.get("pii", [])) for m in msgs),
        "decoys": sum(len(m.get("must_not_mask", [])) for m in msgs),
        "questions_context": sum(1 for m in msgs for q in m.get("questions", []) if q["kind"] == "context"),
        "questions_identity": sum(1 for m in msgs for q in m.get("questions", []) if q["kind"] == "identity"),
        "reid_messages": sum(1 for m in msgs if m.get("identity")),
        "problems": problems,
    }


# ── stage: detect ─────────────────────────────────────────────────────────

def stage_detect(ner: bool) -> dict:
    msgs = H.load_messages()
    masker = H.Masker(ner_enabled=ner)
    per_type: dict[str, list[int]] = {}
    per_case: dict[str, list[int]] = {}
    decoy_hits = 0
    decoy_total = 0
    eaten: list[dict] = []
    leaked: list[dict] = []
    masked_cache: dict[str, str] = {}
    for m in msgs:
        r = masker.mask(m["text"])
        masked_cache[m["id"]] = r.text
        for p in m.get("pii", []):
            ok = 0 if H.present(p["value"], r.text) else 1
            per_type.setdefault(p["type"], []).append(ok)
            for c in m.get("case_types", []):
                per_case.setdefault(c, []).append(ok)
            if not ok:
                leaked.append({"id": m["id"], "type": p["type"], "value": p["value"]})
        for d in m.get("must_not_mask", []):
            decoy_total += 1
            if H.present(d, r.text):
                decoy_hits += 1
            else:
                eaten.append({"id": m["id"], "decoy": d, "masked": r.text})
    def rate(v: list[int]) -> float:
        return round(sum(v) / len(v), 4) if v else 0.0
    allv = [x for v in per_type.values() for x in v]
    return {
        "ner": ner,
        "pii_masked_rate": {"overall": rate(allv), "by_type": {k: rate(v) for k, v in sorted(per_type.items())},
                            "by_case_type": {k: rate(v) for k, v in sorted(per_case.items())}},
        "counts": {"pii_values": len(allv), "decoys": decoy_total},
        "decoy_survival": round(decoy_hits / decoy_total, 4) if decoy_total else 0.0,
        "decoys_eaten": eaten,
        "pii_leaked": leaked,
        "masked_text": masked_cache,
    }


# ── stage: latency ────────────────────────────────────────────────────────

_SHORT = "โทรหาคุณสมชายด้วยนะ"
_MED = ("ลูกค้าชื่อคุณสมชาย ใจดี โทรมาแจ้งว่าของที่สั่งไปเมื่อวันที่ 12 ยังไม่ถึง "
        "เบอร์ 081-234-5678 ที่อยู่ 55/12 ซอย 5 บางนา กรุงเทพ 10260 "
        "เขาอยากได้ของภายในศุกร์นี้ ไม่งั้นจะขอยกเลิกออเดอร์ทั้งหมด ช่วยร่างคำตอบให้ด้วย")


def _long_text() -> str:
    msgs = H.load_messages()
    body = " ".join(m["text"] for m in msgs)
    while len(body) < 2200:
        body += " " + body
    return body[:2400]


def _timeit(fn, n: int, warmup: int) -> list[float]:
    for _ in range(warmup):
        fn()
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def stage_latency(ner: bool) -> dict:
    from app.schemas.common import Message

    cfg = CRIT["latency"]
    n, w, ps = cfg["iterations_per_bucket"], cfg["warmup_per_bucket"], cfg["percentiles"]
    masker = H.Masker(ner_enabled=ner)
    buckets = {"short": _SHORT, "medium": _MED, "long": _long_text()}

    out: dict = {"ner": ner, "input": {}, "output_window": {}, "engines": {}}
    for name, text in buckets.items():
        msgs = [Message(role="user", content=text)]
        samples = _timeit(lambda: masker.registry.scan_messages(msgs, direction="input"), n, w)
        out["input"][name] = {"chars": len(text), **H.percentiles(samples, ps)}

    # Outbound scans are per-CHUNK windows (120-char masked tail + the new
    # delta), not per-answer — so the outbound unit cost is measured on a
    # fixed-size window, and multiplied by the rounds-per-answer count.
    window = _MED[:160]
    samples = _timeit(lambda: masker.registry.scan_text(window), n, w)
    out["output_window"] = {"chars": len(window), **H.percentiles(samples, ps)}

    # Live Rust sidecar — the engine that actually serves requests.
    try:
        import asyncio

        from app.services.dlp.rust_dlp_client import RustDlpClient

        loop = asyncio.new_event_loop()
        client = RustDlpClient.instance()
        probe = loop.run_until_complete(client.scan_text("ping 0812345678", ner_enabled=ner))
        if probe is not None:
            rust: dict = {}
            for name, text in buckets.items():
                s = _timeit(lambda: loop.run_until_complete(client.scan_text(text, ner_enabled=ner)), n, w)
                rust[name] = {"chars": len(text), **H.percentiles(s, ps)}
            s = _timeit(lambda: loop.run_until_complete(client.scan_text(window, ner_enabled=ner)), n, w)
            rust["output_window"] = {"chars": len(window), **H.percentiles(s, ps)}
            out["engines"]["rust_sidecar_rtt"] = rust
    except Exception as exc:  # noqa: BLE001
        out["engines"]["rust_sidecar_rtt"] = {"error": type(exc).__name__}
    return out


_ROUND_PROMPTS_SHORT = [
    "อธิบายวิธีเขียนอีเมลขอโทษลูกค้าที่ได้รับของช้า สั้นๆ",
    "สรุปข้อดีของการทำ backup รายวัน 3 ข้อ",
    "ช่วยร่างข้อความแจ้งเลื่อนนัดลูกค้าแบบสุภาพ",
    "อธิบายความต่างของ HTTP กับ HTTPS สั้นๆ",
    "เขียนสรุปการประชุมสั้นๆ 4 บรรทัด",
]
_ROUND_PROMPTS_LONG = [
    "เขียนคู่มือการรับเรื่องร้องเรียนลูกค้าอย่างละเอียด พร้อมตัวอย่างบทสนทนา",
    "อธิบายสถาปัตยกรรมระบบ e-commerce แบบละเอียดทุกส่วน พร้อมข้อดีข้อเสีย",
    "เขียนแผนอบรมพนักงานขายใหม่ 4 สัปดาห์ แบบละเอียดรายวัน",
    "อธิบายวิธีทำ incident response ตั้งแต่ต้นจนจบอย่างละเอียด",
    "เขียนนโยบายความปลอดภัยข้อมูลของบริษัทฉบับเต็ม",
]


def _stream_deltas(client, prompt: str, max_tokens: int) -> tuple[int, int]:
    """(number of content deltas, total answer chars) for one streamed answer."""
    import urllib.request

    body = json.dumps({
        "model": client.model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": max_tokens,
        "metadata": {"dlp_response_restore": "off"},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{client.base}/v1/chat/completions", data=body,
        headers={"content-type": "application/json", "authorization": f"Bearer {client.key}"},
        method="POST")
    n = chars = 0
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            delta = (ev.get("choices") or [{}])[0].get("delta") or {}
            if delta.get("content"):
                n += 1
                chars += len(delta["content"])
    return n, chars


def stage_scan_rounds() -> dict:
    """How many outbound window scans does ONE streamed answer cost?

    Measured as client-visible content deltas. The gateway emits one delta
    per provider chunk once the rolling 120-char tail is primed, so this is
    a LOWER BOUND on the number of window scans (it misses the 1-3 leading
    chunks that only fill the tail). Reported for a short-answer and a
    long-answer population so the growth with answer length is visible.
    """
    cfg = CRIT["latency"]["scan_rounds_per_answer"]
    client = H.GatewayClient()
    out: dict = {"method": "client-visible SSE content deltas (lower bound on window scans)"}
    for label, prompts, mt in (("short_answer", _ROUND_PROMPTS_SHORT, 300),
                               ("long_answer", _ROUND_PROMPTS_LONG, 1500)):
        rounds: list[float] = []
        chars: list[float] = []
        for p in prompts[: cfg["samples"]]:
            try:
                n, c = _stream_deltas(client, p, mt)
            except Exception as exc:  # noqa: BLE001
                out[label] = {"error": type(exc).__name__}
                break
            rounds.append(float(n))
            chars.append(float(c))
        else:
            out[label] = {
                "samples": len(rounds), "per_answer": rounds,
                **H.percentiles(rounds, [50, 95]),
                "mean_answer_chars": round(sum(chars) / len(chars)) if chars else 0,
                "chars_per_scan": round((sum(chars) / sum(rounds)), 1) if sum(rounds) else 0,
            }
    return out


# ── stage: axis A ─────────────────────────────────────────────────────────

def _context_correct(answer: str, expect_any: list[list[str]]) -> bool:
    return all(any(H.present(alt, answer) for alt in group) for group in expect_any)


def _leaked(answer: str, leak_values: list[str]) -> bool:
    return any(H.present(v, answer) for v in leak_values)


def _cause(m: dict, q: dict, raw_text: str, masked_text: str, answer: str) -> str:
    for d in m.get("must_not_mask", []):
        if not H.present(d, masked_text):
            return "over_masked_common_word"
    for group in q["expect_any"]:
        for alt in group:
            if H.present(alt, raw_text) and not H.present(alt, masked_text):
                return "masked_needed_fact"
    toks = H.placeholder_tokens(masked_text)
    prefixes: dict[str, int] = {}
    for t in toks:
        p = H.placeholder_prefix(t)
        prefixes[p] = prefixes.get(p, 0) + 1
    if any(v >= 2 for v in prefixes.values()) and H.placeholder_tokens(answer):
        return "placeholder_confusion"
    return "other"


def stage_axis_a(ner: bool, detect: dict, workers: int) -> dict:
    msgs = [m for m in H.load_messages() if m.get("questions")]
    client = H.GatewayClient()
    sysmsg = CRIT["axis_a"]["system_prompt"]
    masked_text = detect["masked_text"]

    jobs = []
    for m in msgs:
        for q in m["questions"]:
            for arm in ("raw", "masked"):
                body = m["text"] if arm == "raw" else masked_text[m["id"]]
                jobs.append((m, q, arm, f"ข้อความ:\n{body}\n\nคำถาม: {q['q']}"))

    answers: dict[tuple[str, str, str], str] = {}
    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(client.ask, sysmsg, prompt): (m["id"], q["id"], arm)
                for (m, q, arm, prompt) in jobs}
        for f in futures.as_completed(futs):
            answers[futs[f]] = f.result()

    ctx_raw_ok = ctx_masked_ok = ctx_total = 0
    id_total = id_leak_masked = id_leak_raw = 0
    causes: dict[str, int] = {}
    detail: list[dict] = []
    errors = 0
    for m in msgs:
        for q in m["questions"]:
            a_raw = answers.get((m["id"], q["id"], "raw"), "")
            a_mask = answers.get((m["id"], q["id"], "masked"), "")
            if a_raw.startswith("__ERROR__") or a_mask.startswith("__ERROR__"):
                errors += 1
                continue
            if q["kind"] == "context":
                ctx_total += 1
                r_ok = _context_correct(a_raw, q["expect_any"])
                m_ok = _context_correct(a_mask, q["expect_any"])
                ctx_raw_ok += int(r_ok)
                ctx_masked_ok += int(r_ok and m_ok)
                if r_ok and not m_ok:
                    c = _cause(m, q, m["text"], masked_text[m["id"]], a_mask)
                    causes[c] = causes.get(c, 0) + 1
                    detail.append({"id": m["id"], "q": q["id"], "cause": c,
                                   "answer_masked": a_mask[:220]})
            else:
                id_total += 1
                id_leak_raw += int(_leaked(a_raw, q["leak_values"]))
                if _leaked(a_mask, q["leak_values"]):
                    id_leak_masked += 1
                    detail.append({"id": m["id"], "q": q["id"], "cause": "identity_leak",
                                   "answer_masked": a_mask[:220]})
    return {
        "ner": ner,
        "model": client.model,
        "questions": {"context": ctx_total, "identity": id_total, "errors": errors},
        "raw_context_accuracy": round(ctx_raw_ok / ctx_total, 4) if ctx_total else 0.0,
        "comprehension_retention": round(ctx_masked_ok / ctx_raw_ok, 4) if ctx_raw_ok else 0.0,
        "identity_leak": round(id_leak_masked / id_total, 4) if id_total else 0.0,
        "identity_leak_raw_control": round(id_leak_raw / id_total, 4) if id_total else 0.0,
        "failure_causes": causes,
        "detail": detail,
    }


# ── stage: axis B ─────────────────────────────────────────────────────────

def _candidates(profiles: list[dict], true_id: str, idx: int) -> list[dict]:
    pool = [p for p in profiles if p["id"] != true_id]
    k = CRIT["axis_b"]["closed_set"]["candidates_shown"] - 1
    picked = [pool[(idx + i) % len(pool)] for i in range(k)]
    true_p = next(p for p in profiles if p["id"] == true_id)
    out = picked + [true_p]
    return sorted(out, key=lambda p: p["id"])


def stage_axis_b(ner: bool, detect: dict, workers: int) -> dict:
    msgs = [m for m in H.load_messages() if m.get("identity")]
    profiles = H.load_profiles()
    client = H.GatewayClient()
    masked_text = detect["masked_text"]
    by_id = {p["id"]: p for p in profiles}

    def cause_of(m: dict) -> str:
        for p in m.get("pii", []):
            if H.present(p["value"], masked_text[m["id"]]):
                return "pii_leak"
        return "quasi"

    closed_jobs, open_jobs = [], []
    for i, m in enumerate(msgs):
        cands = _candidates(profiles, m["identity"]["profile_id"], i)
        listing = "\n".join(
            f"- {p['id']}: {p['name']} | {p['role']} | {p['area']} | {p['detail']}" for p in cands)
        closed_jobs.append((m, f"ข้อความ:\n{masked_text[m['id']]}\n\nรายชื่อผู้สมัคร:\n{listing}\n\n"
                               "ข้อความนี้พูดถึงใครในรายชื่อ ตอบเป็นรหัส (เช่น p01) อย่างเดียว"))
        open_jobs.append((m, f"ข้อความ:\n{masked_text[m['id']]}\n\n{CRIT['axis_b']['open']['question']}"))

    sys_closed = "เลือกคำตอบที่น่าจะเป็นไปได้ที่สุดเสมอ ตอบเป็นรหัสเดียวเท่านั้น"
    sys_open = "เดาให้มากที่สุดเท่าที่ทำได้จากข้อความ ตอบสั้นๆ"

    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        cf = {ex.submit(client.ask, sys_closed, p, max_tokens=20): m["id"] for m, p in closed_jobs}
        of = {ex.submit(client.ask, sys_open, p): m["id"] for m, p in open_jobs}
        closed = {cf[f]: f.result() for f in futures.as_completed(cf)}
        opened = {of[f]: f.result() for f in futures.as_completed(of)}

    hits = {"pii_leak": [0, 0], "quasi": [0, 0]}   # [correct, total]
    attr = {"pii_leak": [0, 0], "quasi": [0, 0]}   # [matched, total]
    detail = []
    for m in msgs:
        c = cause_of(m)
        truth = by_id[m["identity"]["profile_id"]]
        ans = closed.get(m["id"], "")
        ok = truth["id"] in ans.lower()
        hits[c][0] += int(ok)
        hits[c][1] += 1
        oans = opened.get(m["id"], "")
        matched = sum(1 for k in CRIT["axis_b"]["open"]["attributes_scored"] if H.present(truth[k], oans))
        attr[c][0] += matched
        attr[c][1] += len(CRIT["axis_b"]["open"]["attributes_scored"])
        detail.append({"id": m["id"], "cause": c, "quasi_strong": m["identity"].get("quasi_strong"),
                       "closed_correct": ok, "closed_answer": ans[:40],
                       "open_attributes_matched": matched, "open_answer": oans[:220]})

    def acc(pair):
        return round(pair[0] / pair[1], 4) if pair[1] else None
    tot_c = [hits["pii_leak"][0] + hits["quasi"][0], hits["pii_leak"][1] + hits["quasi"][1]]
    tot_a = [attr["pii_leak"][0] + attr["quasi"][0], attr["pii_leak"][1] + attr["quasi"][1]]
    return {
        "ner": ner,
        "n": len(msgs),
        "chance": CRIT["axis_b"]["closed_set"]["chance"],
        "closed_set_accuracy": {"overall": acc(tot_c), "pii_leak": acc(hits["pii_leak"]),
                                "quasi": acc(hits["quasi"]),
                                "n_by_cause": {k: v[1] for k, v in hits.items()}},
        "open_attribute_recall": {"overall": acc(tot_a), "pii_leak": acc(attr["pii_leak"]),
                                  "quasi": acc(attr["quasi"])},
        "detail": detail,
    }


# ── main ──────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["validate", "detect", "latency", "rounds", "axis-a", "axis-b", "all"])
    ap.add_argument("--ner", default="off", choices=["on", "off"])
    ap.add_argument("--out", default="")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    ner = a.ner == "on"

    res: dict = {"criteria_version": CRIT["version"], "ner_arm": a.ner,
                 "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    want = {a.stage} if a.stage != "all" else {"validate", "detect", "latency", "rounds", "axis-a", "axis-b"}

    if "validate" in want:
        res["validate"] = stage_validate()
        print("validate:", json.dumps({k: v for k, v in res["validate"].items() if k != "problems"}, ensure_ascii=False))
        if res["validate"]["problems"]:
            print("CORPUS PROBLEMS:")
            for p in res["validate"]["problems"]:
                print("  -", p)
    if want & {"detect", "axis-a", "axis-b"}:
        res["detect"] = stage_detect(ner)
        d = res["detect"]
        print(f"detect: overall pii_masked_rate={d['pii_masked_rate']['overall']} "
              f"decoy_survival={d['decoy_survival']}")
        for k, v in d["pii_masked_rate"]["by_type"].items():
            print(f"  {k:20} {v}")
    if "latency" in want:
        res["latency"] = stage_latency(ner)
        print("latency:", json.dumps(res["latency"]["input"], ensure_ascii=False))
    if "rounds" in want:
        res["scan_rounds"] = stage_scan_rounds()
        print("scan rounds/answer:", json.dumps(res["scan_rounds"], ensure_ascii=False))
    if "axis-a" in want:
        res["axis_a"] = stage_axis_a(ner, res["detect"], a.workers)
        aa = res["axis_a"]
        print(f"axis A: raw_ctx_acc={aa['raw_context_accuracy']} "
              f"comprehension_retention={aa['comprehension_retention']} "
              f"identity_leak={aa['identity_leak']} causes={aa['failure_causes']}")
    if "axis-b" in want:
        res["axis_b"] = stage_axis_b(ner, res["detect"], a.workers)
        ab = res["axis_b"]
        print(f"axis B: closed={ab['closed_set_accuracy']} open={ab['open_attribute_recall']}")

    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

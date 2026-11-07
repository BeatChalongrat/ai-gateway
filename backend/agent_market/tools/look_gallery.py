"""AM21 §6.5 — every layout, at every content length, every time.

A closed catalogue only stays clean if something looks at it. This tool renders every layout of the
report and the deck with short, typical, long and absurd content, in both languages and under two
themes, to files; the gate (scripts/agent-market/look-gallery.sh) rasterises them with the converter
and compares the pictures with the approved ones held in tests/fixtures/look_gallery. A picture that
differs beyond the threshold fails and both pictures are written side by side. The absurd case is the
point: its content fits no layout, and the run must refuse it naming the section — a quiet truncation
would render a picture, and the gate would see one where a refusal belongs.

    python3 -m agent_market.tools.look_gallery render  --out DIR
    python3 -m agent_market.tools.look_gallery compare --out DIR --approved FIXDIR [--approve] [--threshold 0.02]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sys

CASES = ("short", "typical", "long", "absurd")
LANGS = ("th", "en")
THEMES = ("navy", "teal")
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "packages", "financial_report")
SAMPLE = os.path.join(PKG, "sample-data")
APPROVED = os.path.join(os.path.dirname(HERE), "tests", "fixtures", "look_gallery")


def _base_computed():
    from ..packages import financial_report as pkg

    names = ["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"]
    files = [pkg.parse(os.path.join(SAMPLE, n), n, {}) for n in names]
    pre = pkg.preflight(files, message="ปิดงบกันยายน", today=_dt.date(2026, 9, 25))
    return pkg.compute(files, pre["roles"], pre["period"])


def _phrase(lang: str, n: int) -> str:
    """A sentence of about n characters a reader sees (AM22: columns — a Thai mark takes none), in the language,
    made of words a layout can break between."""
    from ..columns import columns

    word = "รายได้เพิ่มขึ้นจากงวดก่อนตามแผน " if lang == "th" else "Revenue rose against the plan this period "
    out = ""
    while columns(out) < n:
        out += word
    while columns(out) > n:
        out = out[:-1]
    return out.rstrip()


def narrative_for(case: str, lang: str, base: dict, limits: dict) -> dict:
    """The commentary at each length: short, the sample's own (typical), at the limits (long), and far past
    them (absurd — the renderer must refuse)."""
    nar = dict(base)
    if case == "typical":
        return nar
    # (AM23 §3.3: the rules' headings are written from the sample's own words; a commentary made up here keeps the
    # generic headings its pictures were approved with)
    nar.pop("headings", None)
    if case == "short":
        one = "กำไรสุทธิเพิ่มขึ้น" if lang == "th" else "Net profit rose."
        return {**nar, "summary": one, "movements": [one], "watch": [one], "next_steps": [one]}
    if case == "long":
        s, b, k = limits["summary_chars"], limits["bullet_chars"], limits["bullets_max"]
        return {**nar, "summary": _phrase(lang, int(s * 0.95)), "movements": [_phrase(lang, int(b * 0.95))] * k,
                "watch": [_phrase(lang, int(b * 0.9))] * (k + 2), "next_steps": [_phrase(lang, int(b * 0.95))] * k}
    s, b = limits["summary_chars"], limits["bullet_chars"]
    return {**nar, "summary": _phrase(lang, s * 10), "movements": [_phrase(lang, b * 20)], "watch": [_phrase(lang, b * 20)],
            "next_steps": [_phrase(lang, b * 20)]}


def exceptions_for(case: str, computed: dict) -> list[dict]:
    """The long case has more items to check than a slide holds, so the list breaks across slides."""
    exc = list(computed["exceptions"])
    if case == "long" and exc:
        more = []
        for i in range(9):
            e = dict(exc[i % len(exc)])
            e["title"] = f"{e['title']} ({i + 1})"
            more.append(e)
        return more
    return exc


def render(out: str) -> dict:
    from ..deliverables.look.catalogue import writing_limits
    from ..deliverables.render import render_all
    from ..engine import words

    base = _base_computed()
    refusals: dict[str, list] = {}
    made: list[str] = []
    for case in CASES:
        for lang in LANGS:
            for theme in THEMES:
                name = f"{case}__{lang}__{theme}"
                d = os.path.join(out, name)
                shutil.rmtree(d, ignore_errors=True)
                os.makedirs(d)
                words.use(lang)
                try:
                    computed = dict(base)
                    computed["exceptions"] = exceptions_for(case, base)
                    company = "บริษัท ตัวอย่าง จำกัด" if lang == "th" else "Example Co., Ltd."
                    # AM22: the long case is at the limits each language is told (its own columns), the absurd far past them
                    limits = writing_limits("pptx", lang)
                    req = {"formats": ["docx", "pptx"], "names": {"docx": "report.docx", "pptx": "deck.pptx"},
                           "theme": {"id": theme, "company": company},
                           "narrative": narrative_for(case, lang, computed["fallback_narrative"], limits), "assumptions": [],
                           "run": {"id": "gallery", "date": "2026-09-25", "agent_version": "gallery", "organisation": company},
                           "model_info": {"model": None, "self_hosted": None}}
                    res = render_all(computed, req, in_dir=d, out_dir=d)
                finally:
                    words.use("th")
                failed = [(a["name"], c["name"]) for a in res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]
                refusals[name] = {"refused": [{"format": r["format"], "detail": r["check"]["detail"]} for r in res.get("refused") or []],
                                  "failed": [f"{a}: {c}" for a, c in failed]}
                made += [f"{name}/{a['name']}" for a in res["artifacts"]]
                print(f"{name}: {len(res['artifacts'])} files, refused {[r['format'] for r in res.get('refused') or []]}, failed checks {failed}")
    with open(os.path.join(out, "refusals.json"), "w", encoding="utf-8") as f:
        json.dump(refusals, f, ensure_ascii=False, indent=1)
    return {"made": made, "refusals": refusals}


def compare(out: str, approved: str, approve: bool, threshold: float) -> int:
    """The current pictures (out/_png) against the approved ones; a difference beyond the threshold fails
    and both pictures are written side by side under out/_diff. The absurd cases must have been refused."""
    from PIL import Image, ImageChops

    rc = 0
    with open(os.path.join(out, "refusals.json"), encoding="utf-8") as f:
        refusals = json.load(f)
    for name, rec in refusals.items():
        refused, failed = rec["refused"], rec["failed"]
        if failed:
            # a file that was written but fails its own gate is not a picture to approve
            print(f"FAIL {name}: the gate fails on the file: {failed}")
            rc = 1
        if name.startswith("absurd"):
            if not refused or len(refused) < 2:
                print(f"FAIL {name}: the absurd content was not refused (it must be: {refused})")
                rc = 1
            else:
                print(f"ok   {name}: refused — {refused[0]['detail'][:90]}")
        elif refused:
            print(f"FAIL {name}: refused, but its content fits the catalogue: {refused}")
            rc = 1
    png_dir = os.path.join(out, "_png")
    current = sorted(p for p in os.listdir(png_dir) if p.endswith(".png")) if os.path.isdir(png_dir) else []
    if not current:
        print("FAIL no pictures were rendered")
        return 1
    diff_dir = os.path.join(out, "_diff")
    shutil.rmtree(diff_dir, ignore_errors=True)
    os.makedirs(diff_dir, exist_ok=True)
    os.makedirs(approved, exist_ok=True)
    seen = set()
    worst = 0.0
    for p in current:
        seen.add(p)
        cur = Image.open(os.path.join(png_dir, p)).convert("L")
        ap = os.path.join(approved, p)
        if not os.path.exists(ap):
            if approve:
                cur.save(ap, optimize=True)
                print(f"new  {p} (approved)")
            else:
                print(f"FAIL {p}: no approved picture (run with --approve after looking at it)")
                rc = 1
            continue
        old = Image.open(ap).convert("L")
        if old.size != cur.size:
            print(f"FAIL {p}: size {cur.size} differs from approved {old.size}")
            rc = 1
            _side_by_side(old, cur, os.path.join(diff_dir, p))
            if approve:
                cur.save(ap, optimize=True)
            continue
        d = ImageChops.difference(old, cur)
        hist = d.histogram()
        total = sum(hist)
        mean = sum(i * c for i, c in enumerate(hist)) / (255.0 * total) if total else 0.0
        worst = max(worst, mean)
        if mean > threshold:
            print(f"FAIL {p}: differs by {mean:.3f} (threshold {threshold}) — see _diff/{p}")
            _side_by_side(old, cur, os.path.join(diff_dir, p))
            rc = 1
            if approve:
                cur.save(ap, optimize=True)
    gone = sorted(set(os.listdir(approved)) - seen)
    gone = [g for g in gone if g.endswith(".png")]
    if gone:
        print(f"FAIL approved pictures no longer rendered: {gone[:6]}{' …' if len(gone) > 6 else ''}")
        rc = 1
        if approve:
            for g in gone:
                os.remove(os.path.join(approved, g))
    print(f"compared {len(current)} pictures; worst difference {worst:.4f}; {'PASS' if rc == 0 else 'FAIL'}")
    return 0 if (rc == 0 or approve) else 1


def _side_by_side(old, cur, path: str) -> None:
    from PIL import Image

    w = old.size[0] + cur.size[0] + 8
    h = max(old.size[1], cur.size[1])
    im = Image.new("L", (w, h), 128)
    im.paste(old, (0, 0))
    im.paste(cur, (old.size[0] + 8, 0))
    im.save(path, optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--out", required=True)
    c.add_argument("--approved", default=APPROVED)
    c.add_argument("--approve", action="store_true")
    c.add_argument("--threshold", type=float, default=0.02)
    a = ap.parse_args()
    if a.cmd == "render":
        os.makedirs(a.out, exist_ok=True)
        render(a.out)
        return 0
    return compare(a.out, a.approved, a.approve, a.threshold)


if __name__ == "__main__":
    sys.exit(main())

"""AM2 Phase B — can our models read a Thai invoice? (test stack only, never live)

    python scripts/probes/AM2/bench_models.py prep            # page images + OCR/text for every fixture
    python scripts/probes/AM2/bench_models.py run  [models]   # call the TEST stack gateway, score vs golden
    python scripts/probes/AM2/bench_models.py report          # tables per class / path / field

Paths benchmarked, each against the Phase A golden truth (field level):
  vision  — the page image(s) to a vision model (qwen3.8-27b-fp8 is the only self-hosted one)
  text    — the page text to a text model: the PDF text layer for digital files,
            Tesseract tha+eng (the sandbox's OCR, 300 DPI) for scans and photos
The gateway is the test stack's (127.0.0.1:18080, compose project sran-test) with the
test stack's Agent Market runner key (scripts/probes/AM1/results/runner_key.test, never
printed). Nothing here can reach the live gateway: the URL is fixed and checked.
Results: scripts/probes/AM2/results/phaseB/ (gitignored).
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "backend" / "agent_market" / "fixtures" / "e2"
OUT = ROOT / "scripts" / "probes" / "AM2" / "results" / "phaseB"
GATEWAY = "http://127.0.0.1:18080/v1/chat/completions"   # the TEST stack (sran-test); live is never on 18080
KEY_FILE = ROOT / "scripts" / "probes" / "AM1" / "results" / "runner_key.test"
TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

FIELDS = ["document_type", "seller_name", "seller_tax_id", "buyer_tax_id", "document_number", "document_date",
          "subtotal", "vat_amount", "total", "wht_amount", "net_payable"]
MONEY = {"subtotal", "vat_amount", "total", "wht_amount", "net_payable"}

PROMPT = """คุณคือผู้ช่วยบัญชีที่อ่านเอกสารภาษีของไทย อ่านเอกสารนี้แล้วตอบเป็น JSON อย่างเดียว ห้ามมีข้อความอื่น
You read Thai invoices and receipts. Return ONLY one JSON object with exactly these keys (null when not on the document):
{"document_type": "full_tax_invoice|abbreviated_tax_invoice|receipt|invoice|credit_note|unknown",
 "seller_name": "as printed", "seller_tax_id": "13 digits, no separators", "buyer_name": "as printed or null",
 "buyer_tax_id": "13 digits or null", "document_number": "as printed", "document_date": "YYYY-MM-DD in the Common Era (Buddhist year minus 543)",
 "subtotal": "amount before VAT", "vat_amount": "VAT amount", "total": "grand total including VAT",
 "wht_amount": "withholding tax amount or null", "net_payable": "net payable after WHT or null", "currency": "THB|USD|...",
 "line_items": [{"description": "...", "quantity": "...", "unit_price": "...", "amount": "..."}]}
Amounts as plain numbers with 2 decimals and no thousands separators. Use Arabic digits."""


def _key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _num(v):
    if v is None:
        return None
    s = str(v).translate(TH).replace(",", "").replace("บาท", "").replace("THB", "").replace("USD", "").strip()
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _digits(v):
    return None if v is None else re.sub(r"\D", "", str(v).translate(TH)) or None


def _norm_text(v):
    return None if v is None else re.sub(r"[\s.]", "", str(v).translate(TH)).lower()


def score(pred: dict, exp: dict) -> dict:
    t = exp["truth"]
    scope = exp["scope_fields"] or FIELDS
    out = {}
    for f in FIELDS:
        if f not in scope and f != "document_type":
            continue
        want = t.get(f)
        got = pred.get(f)
        if f == "document_type":
            ok = got == want
        elif f in ("seller_tax_id", "buyer_tax_id"):
            ok = _digits(got) == (want["value"] if want else None)
        elif f == "seller_name":
            ok = got is not None and _norm_text(want["normalized"]["core"]) in _norm_text(got)
        elif f == "document_number":
            ok = _norm_text(got) == _norm_text(want)
        elif f == "document_date":
            ok = (str(got)[:10] if got else None) == want
        elif f in MONEY:
            ok = _num(got) == (_num(want) if want is not None else None)
        else:
            ok = got == want
        out[f] = bool(ok)
    return out


def _images(path: Path, dpi: int = 150) -> list[bytes]:
    if path.suffix.lower() == ".jpg":
        return [path.read_bytes()]
    import io

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        res = []
        for i in range(len(doc)):
            buf = io.BytesIO()
            doc[i].render(scale=dpi / 72).to_pil().convert("RGB").save(buf, "PNG")
            res.append(buf.getvalue())
        return res
    finally:
        doc.close()


def prep() -> None:
    """Text for the text path: the PDF text layer (digital), Tesseract in the sandbox image (scans, photos)."""
    (OUT / "text").mkdir(parents=True, exist_ok=True)
    idx = json.loads((FIX / "index.json").read_text(encoding="utf-8"))["fixtures"]
    for f in idx:
        src = FIX / f["files"][0]
        dst = OUT / "text" / f"{f['id']}.txt"
        if src.suffix == ".xml":
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            continue
        if f["class"] in ("digital", "etax_pdf"):
            import pdfplumber

            with pdfplumber.open(str(src)) as pdf:
                dst.write_text("\n\n".join(p.extract_text() or "" for p in pdf.pages), encoding="utf-8")
            continue
        # OCR inside the stock sandbox image: the same Tesseract tha+eng production has
        code = ("import sys,io,pytesseract; from PIL import Image\n"
                "p=sys.argv[1]\n"
                "if p.endswith('.jpg'):\n imgs=[Image.open(p)]\n"
                "else:\n import pypdfium2 as pdfium; d=pdfium.PdfDocument(p); imgs=[d[i].render(scale=300/72).to_pil() for i in range(len(d))]\n"
                "print('\\n\\n'.join(pytesseract.image_to_string(i, lang='tha+eng', config='--oem 1') for i in imgs))")
        rel = src.relative_to(ROOT).as_posix()
        r = subprocess.run(["docker", "run", "--rm", "--network", "none", "-e", "HOME=/tmp", "-v", f"{ROOT.as_posix()}:/w:ro",
                            "--entrypoint", "python3", "ai-gateway-sandbox:latest", "-c", code, f"/w/{rel}"],
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "MSYS_NO_PATHCONV": "1"}, timeout=600)
        dst.write_text(r.stdout, encoding="utf-8")
        print("ocr", f["id"], len(r.stdout), "chars", r.stderr[-200:] if r.returncode else "", flush=True)


def _call(model: str, content, max_tokens: int = 4000) -> tuple[dict, float, dict, str]:
    assert GATEWAY.startswith("http://127.0.0.1:18080/"), "the benchmark only talks to the test stack"
    # the runner's own request shape (model.go): reasoning off, temperature 0
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0,
                       "max_tokens": max_tokens, "stream": False, "reasoning": {"mode": "off"}}).encode()
    req = urllib.request.Request(GATEWAY, data=body, headers={"Authorization": f"Bearer {_key()}",
                                                              "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {}, time.monotonic() - t0, {}, f"http {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
    except Exception as e:  # noqa: BLE001
        return {}, time.monotonic() - t0, {}, f"{type(e).__name__}: {e}"[:300]
    dt = time.monotonic() - t0
    msg = (resp.get("choices") or [{}])[0].get("message", {})
    text = msg.get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    pred = {}
    err = ""
    if m:
        try:
            pred = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            err = f"json: {e}"
    else:
        err = "no json: " + (resp.get("choices") or [{}])[0].get("finish_reason", "") + " " + text[:120]
    return pred, dt, resp.get("usage") or {}, err


def run(models: list[str]) -> None:
    idx = json.loads((FIX / "index.json").read_text(encoding="utf-8"))["fixtures"]
    res_path = OUT / "results.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    done = set()
    if res_path.exists():
        for line in res_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["id"], r["model"], r["path"]))
    for model in models:
        vision = model.endswith("+vision")
        name = model.replace("+vision", "")
        for f in idx:
            if f["class"] == "etax_xml" and vision:
                continue
            key = (f["id"], name, "vision" if vision else "text")
            if key in done:
                continue
            exp = json.loads((FIX / "expected" / f"{f['id']}.json").read_text(encoding="utf-8"))
            if vision:
                content = [{"type": "text", "text": PROMPT}] + [
                    {"type": "image_url", "image_url": {"url": "data:image/" + ("jpeg" if f["files"][0].endswith(".jpg") else "png")
                                                        + ";base64," + base64.b64encode(b).decode()}}
                    for b in _images(FIX / f["files"][0])]
            else:
                text = (OUT / "text" / f"{f['id']}.txt").read_text(encoding="utf-8")
                content = PROMPT + "\n\n--- เอกสาร / DOCUMENT TEXT ---\n" + text[:12000]
            pred, dt, usage, err = _call(name, content)
            sc = score(pred, exp) if pred else {}
            row = {"id": f["id"], "class": f["class"], "model": name, "path": "vision" if vision else "text",
                   "seconds": round(dt, 1), "usage": usage, "error": err, "pred": pred, "score": sc}
            with open(res_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            ok = sum(sc.values())
            print(f"{name:40s} {row['path']:6s} {f['id']:34s} {ok}/{len(sc) or '-'} {dt:6.1f}s {err[:60]}", flush=True)


def report() -> None:
    rows = [json.loads(l) for l in (OUT / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    groups: dict = {}
    for r in rows:
        cls = {"scan300": "scan 300", "scan200": "scan 200", "etax_pdf": "digital", "etax_xml": "digital"}.get(r["class"], r["class"])
        g = groups.setdefault((r["model"], r["path"], cls), {"ok": 0, "n": 0, "docs": 0, "fail": 0, "sec": 0.0, "money_ok": 0, "money_n": 0,
                                                             "tax_ok": 0, "tax_n": 0, "tokens": 0})
        g["docs"] += 1
        g["sec"] += r["seconds"]
        g["tokens"] += int((r.get("usage") or {}).get("total_tokens") or 0)
        if r["error"] and not r["score"]:
            g["fail"] += 1
            continue
        for f, ok in r["score"].items():
            g["ok"] += ok
            g["n"] += 1
            if f in MONEY:
                g["money_ok"] += ok
                g["money_n"] += 1
            if f.endswith("tax_id"):
                g["tax_ok"] += ok
                g["tax_n"] += 1
    lines = ["| model | path | class | docs | no answer | field accuracy | money fields | tax IDs | avg s/doc | avg tokens/doc |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for (m, p, c), g in sorted(groups.items()):
        pct = lambda a, b: f"{100 * a / b:.0f}% ({a}/{b})" if b else "-"
        lines.append(f"| {m} | {p} | {c} | {g['docs']} | {g['fail']} | {pct(g['ok'], g['n'])} | {pct(g['money_ok'], g['money_n'])} | "
                     f"{pct(g['tax_ok'], g['tax_n'])} | {g['sec'] / g['docs']:.1f} | {g['tokens'] // max(1, g['docs'])} |")
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "prep":
        prep()
    elif cmd == "run":
        run(sys.argv[2:] or ["qwen3.8-27b-fp8+vision", "qwen3.8-27b-fp8", "Typhoon-S-ThaiLLM-8B-Instruct"])
    else:
        report()

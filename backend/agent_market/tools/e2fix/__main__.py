"""Generate the E2 fixture pack (AM2 Phase A).

    python -m agent_market.tools.e2fix [out_dir]      (run from backend/)

Writes, under out_dir (default agent_market/fixtures/e2):
    docs/<id>.pdf|.jpg|.xml   the document an agent receives
    html/<id>.html            the source of each rendered document (for review)
    sources/<id>.pdf          the digital page a photo was taken of
    expected/<id>.json        the golden output (expected.py)
    org.json                  the installing organisation (the buyer)
    index.json                every fixture: class, purpose, files, sha256
Digital PDFs need the host's Chrome (render.CHROME). Scans and photos are
seeded; the expected JSON never depends on the rendered bytes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from . import etax, expected, html, render
from .specs import ORG, SUPPLIERS, resolved


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2] / "fixtures" / "e2"
    for sub in ("docs", "html", "sources", "expected"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    specs = resolved()
    digital: dict[str, Path] = {}
    # 1. everything drawn from HTML (digital documents, photo sources, e-Tax PDF)
    for fid, s in specs.items():
        if s.get("scan_of"):
            continue
        seller = SUPPLIERS[s["seller"]]
        if s["layout"] == "xml":
            (out / "docs" / f"{fid}.xml").write_bytes(etax.build(s, seller, s["buyer"]))
            continue
        page = html.LAYOUTS[s["layout"]](s, seller, s.get("buyer"))
        hp = out / "html" / f"{fid}.html"
        hp.write_text(page, encoding="utf-8", newline="\n")
        target = out / ("sources" if s["class"] == "photo" else "docs") / f"{fid}.pdf"
        render.chrome_pdf(hp, target)
        if s.get("etax"):
            render.normalize_pdf(target, target.with_suffix(".tmp.pdf"),
                                 attachments={"ETDA-TaxInvoice.xml": etax.build(s, seller, s["buyer"])})
            target.with_suffix(".tmp.pdf").replace(target)
        digital[fid] = target
        print("rendered", fid, flush=True)
    # 2. scans and photos, from the digital pages
    for fid, s in specs.items():
        if s.get("scan_of"):
            sc = s["scan"]
            render.scan(digital[s["scan_of"]], out / "docs" / f"{fid}.pdf", s["dpi"], sc["angle"], sc["noise"],
                        sc["blur"], sc["jpeg"], sc["seed"])
            print("scanned", fid, flush=True)
        elif s["class"] == "photo":
            ph = s["photo"]
            render.photo(digital[fid], out / "docs" / f"{fid}.jpg", seed=int(hashlib.sha1(fid.encode()).hexdigest()[:6], 16), **ph)
            print("photographed", fid, flush=True)
    render.cleanup_profile(out / "docs")
    render.cleanup_profile(out / "sources")
    # 3. expected output and the index
    (out / "org.json").write_text(json.dumps({"name": ORG["name"], "tax_id": ORG["tax_id"], "branch": ORG["branch"],
                                              "address": ORG["address"]}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    index = []
    for fid, s in specs.items():
        docs = sorted(p.name for p in (out / "docs").glob(f"{fid}.*"))
        files = [f"docs/{n}" for n in docs]
        exp = expected.build(s, {"files": files, "org": ORG})
        (out / "expected" / f"{fid}.json").write_text(json.dumps(exp, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        index.append({"id": fid, "class": s["class"], "purpose": s["purpose"], "files": files,
                      "sha256": {f: _sha(out / f) for f in files}})
    (out / "index.json").write_text(json.dumps({"schema": "e2.fixtures.v1", "fixtures": index}, ensure_ascii=False, indent=1),
                                    encoding="utf-8", newline="\n")
    print(f"{len(index)} fixtures -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

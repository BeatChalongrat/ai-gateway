"""The sample data of the invoice and the expense agents, made for nobody in particular (AM6).

    python -m agent_market.tools.make_generic_samples        (run from backend/; the PDFs need the host's Chrome)

An agent whose manifest says ``"scope": "generic"`` installs at another company with nobody
touching it, so the documents it is shown with are made out to a company that does not exist:
"บริษัท สยามตัวอย่างการค้า จำกัด". The three invoice samples are the documents of the E2 fixture pack
(a full tax invoice, a service invoice with withholding tax, an e-Tax receipt) drawn again with
that buyer; the expense samples keep their pages and lose the name a fixture tool wrote into
their metadata.

The E2 fixture pack itself (fixtures/e2, not shipped with any agent) is not touched: the engine's
eval set is scored against it as it is.
"""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path

from .e2fix import etax, html, render
from .e2fix.specs import SUPPLIERS, resolved
from .e2fix.thai import make_tax_id

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = os.path.join(ROOT, "packages")
PDF_DATE = "D:20260926000000+07'00'"
BUYER = {"name": "บริษัท สยามตัวอย่างการค้า จำกัด", "legal_form": "company_limited", "core": "สยามตัวอย่างการค้า",
         "tax_id": make_tax_id("010555600001"), "branch": "00000",
         "address": "88 ถนนสีลม แขวงสุริยวงศ์ เขตบางรัก กรุงเทพมหานคร 10500"}
INVOICE_SAMPLES = (
    ("d01_clean_full_tax_invoice", "ใบกำกับภาษี_IV6909-0015.pdf"),
    ("d05_wht_service_invoice", "ใบแจ้งหนี้ค่าบริการ_หักณที่จ่าย.pdf"),
    ("e02_etax_xml_receipt", "eTax_ใบเสร็จ.xml"),
)
EXPENSE_SAMPLES = ("r_c01.pdf", "r_c05.pdf", "r_c15.pdf")


def neutral_pdf(src: Path, dst: Path) -> None:
    """The same pages, with metadata that names no organisation."""
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter(clone_from=PdfReader(str(src)))
    w.add_metadata({"/Producer": "fixture (Chrome print)", "/Creator": "agent_market.tools", "/CreationDate": PDF_DATE,
                    "/ModDate": PDF_DATE})
    tmp = dst.with_suffix(".tmp.pdf")
    with open(tmp, "wb") as f:
        w.write(f)
    os.replace(tmp, dst)


def invoice_samples() -> list:
    out_dir = Path(PACKAGES) / "invoice_processing" / "sample-data"
    specs = resolved()
    written = []
    for fid, name in INVOICE_SAMPLES:
        s = deepcopy(specs[fid])
        s["buyer"] = BUYER
        seller = SUPPLIERS[s["seller"]]
        target = out_dir / name
        if s["layout"] == "xml":
            target.write_bytes(etax.build(s, seller, BUYER))
        else:
            page = html.LAYOUTS[s["layout"]](s, seller, BUYER)
            hp = target.with_suffix(".html")
            hp.write_text(page, encoding="utf-8", newline="\n")
            raw = target.with_suffix(".raw.pdf")
            render.chrome_pdf(hp, raw)
            neutral_pdf(raw, target)
            raw.unlink()
            hp.unlink()
        written.append(str(target))
    prof = out_dir / ".chrome-profile"
    if prof.exists():
        import shutil

        shutil.rmtree(prof, ignore_errors=True)
    return written


def expense_samples() -> list:
    out_dir = Path(PACKAGES) / "expense_audit" / "sample-data"
    written = []
    for name in EXPENSE_SAMPLES:
        p = out_dir / name
        neutral_pdf(p, p)
        written.append(str(p))
    return written


def main(argv: list) -> int:
    for p in invoice_samples() + expense_samples():
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

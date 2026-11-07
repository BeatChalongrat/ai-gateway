"""Integration test for the doc-converter image (AM-2 Phase E).

    python -m unittest discover -s services/doc-converter/tests -v      (repo root)

Starts the built image (DOC_CONVERTER_IMAGE, default
ai-gateway-doc-converter:dev) as throwaway containers on random localhost
ports, hardened like the compose service (read-only root, tmpfs /tmp, no
capabilities, no-new-privileges, 768m, 128 pids), and removes them after.
Needs docker plus openpyxl, python-docx and python-pptx on the host.

What it proves, against backend/agent_market/fixtures/templates/:
  a) every template converts to a PDF, and for each file in expected.json's
     "pdf" section the /check text layer contains every text_contains string
     (NFC, whitespace removed: Thai reordered or split by bad shaping fails);
     the templates are first filled with expected.json's values by a minimal
     test filler (the real one is Phase E's), since the strings include them;
  b) page counts match pages / pages_min;
  c) to=xlsx recalculates: B5/B6 written by openpyxl (no cached B7), and a
     stale cached B7, both come back as 168000.70, plus expected.json's other
     recalculated cells. The stale case is the one that needs the service's
     OOXMLRecalcMode=0: LibreOffice computes a missing cache anyway but keeps
     a stale one (measured: B7 stays 0 with a plain fresh profile);
  d) a conversion past CONVERT_TIMEOUT_SECONDS returns 504 and leaves no
     soffice process and no profile/work directory behind; oversize -> 413;
  e) on an internal docker network the service has no egress.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

try:  # Thai in failure messages must not crash a cp874/cp1252 console
    sys.stderr.reconfigure(errors="backslashreplace")
    sys.stdout.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "backend" / "agent_market" / "fixtures" / "templates"
IMAGE = os.environ.get("DOC_CONVERTER_IMAGE", "ai-gateway-doc-converter:dev")
HARDENING = ["--read-only", "--tmpfs", "/tmp:rw,size=256m,mode=1777", "--cap-drop", "ALL",
             "--security-opt", "no-new-privileges", "--memory", "768m", "--pids-limit", "128"]
EXPECTED = json.loads((TEMPLATES / "expected.json").read_text(encoding="utf-8"))
TIMINGS: dict[str, float] = {}
_containers: list[str] = []
_networks: list[str] = []


# ── docker helpers ────────────────────────────────────────────────────────

def _docker(*args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=check)


def _start(env: dict | None = None, network: str | None = None, publish: bool = True) -> str:
    name = f"doc-converter-test-{uuid.uuid4().hex[:10]}"
    argv = ["run", "-d", "--name", name, *HARDENING]
    for k, v in (env or {}).items():
        argv += ["-e", f"{k}={v}"]
    if network:
        argv += ["--network", network]
    if publish:
        argv += ["-p", "127.0.0.1::8096"]
    _docker(*argv, IMAGE)
    _containers.append(name)
    return name


def _base_url(name: str) -> str:
    out = _docker("port", name, "8096/tcp").stdout.strip().splitlines()[0]
    return "http://127.0.0.1:" + out.rsplit(":", 1)[1]


def _wait_healthy(base: str, seconds: float = 90) -> dict:
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001 - still starting
            last = e
            time.sleep(0.5)
    raise AssertionError(f"converter never became healthy: {last!r}")


def _exec_py(name: str, code: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return _docker("exec", name, "python3", "-c", code, check=False, timeout=timeout)


def _post(base: str, path: str, body: bytes, headers: dict | None = None, timeout: float = 180):
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers={"Content-Type": "application/octet-stream", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ── a minimal test filler (the real one is Phase E's) ─────────────────────

MARKER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
VALUES = {k: v for k, v in EXPECTED["values"].items() if isinstance(v, str)}


def _fill_paragraph(p, values: dict) -> None:
    runs = p.runs
    full = "".join(r.text for r in runs)
    if "{{" not in full:
        return
    new = MARKER.sub(lambda m: values.get(m.group(1), m.group(0)), full)
    if new != full:  # a marker split across runs lands in the first run
        runs[0].text = new
        for r in runs[1:]:
            r.text = ""


def _docx_paragraphs(doc):
    def walk(container):
        yield from container.paragraphs
        for t in getattr(container, "tables", []):
            for row in t.rows:
                for cell in row.cells:
                    yield from walk(cell)
    yield from walk(doc)
    for s in doc.sections:
        yield from walk(s.header)
        yield from walk(s.footer)


def fill_docx(src: Path) -> bytes:
    from docx import Document
    doc = Document(str(src))
    for t in doc.tables:
        for row in list(t.rows):
            if "{{line." not in "".join(c.text for c in row.cells):
                continue
            for line in EXPECTED["values"]["lines"]:
                tr = copy.deepcopy(row._tr)
                row._tr.addprevious(tr)
            template = row._tr
            rows = [r for r in t.rows if r._tr is not template and "{{line." in "".join(c.text for c in r.cells)]
            for r, line in zip(rows, EXPECTED["values"]["lines"]):
                vals = {f"line.{k}": v for k, v in line.items()}
                for c in r.cells:
                    for p in c.paragraphs:
                        _fill_paragraph(p, vals)
            template.getparent().remove(template)
    for p in _docx_paragraphs(doc):
        _fill_paragraph(p, VALUES)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def fill_pptx(src: Path) -> bytes:
    from pptx import Presentation
    prs = Presentation(str(src))
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    _fill_paragraph(p, VALUES)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _amount(s: str) -> float:
    return float(s.replace(",", ""))


def fill_xlsx(src: Path) -> bytes:
    from openpyxl import load_workbook
    wb = load_workbook(str(src))
    ws = wb["สรุป"]
    fill = EXPECTED["templates"]["xlsx_named_ranges.xlsx"]["fill"]
    ws["B2"], ws["B3"] = fill["CompanyName"], fill["Period"]
    ws["B5"], ws["B6"] = 157010.00, 10990.70
    for i, line in enumerate(fill["LinesTable"]):
        ws.cell(10 + i, 1, line["description"])
        ws.cell(10 + i, 2, int(line["quantity"]))
        ws.cell(10 + i, 3, _amount(line["amount"]))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filled(name: str) -> bytes:
    src = TEMPLATES / name
    kind = EXPECTED["templates"][name]["kind"]
    if kind in ("docx_placeholders", "docx_repeating_rows"):
        return fill_docx(src)
    if kind == "pptx_theme_chart":
        return fill_pptx(src)
    if kind == "xlsx_named_ranges":
        return fill_xlsx(src)
    return src.read_bytes()  # nothing to fill (content controls, no markers)


def squash(s: str) -> str:
    return "".join(unicodedata.normalize("NFC", s).split())


# ── the tests ─────────────────────────────────────────────────────────────

class _Main:
    name = ""
    base = ""


def setUpModule():
    if shutil.which("docker") is None:
        raise unittest.SkipTest("docker is not available")
    if _docker("image", "inspect", IMAGE, check=False).returncode != 0:
        raise unittest.SkipTest(f"image {IMAGE} is not built (scripts/agent-market/build-converter.sh --worktree)")
    _Main.name = _start()
    _Main.base = _base_url(_Main.name)
    _wait_healthy(_Main.base)


def tearDownModule():
    for c in _containers:
        _docker("rm", "-f", c, check=False)
    for n in _networks:
        _docker("network", "rm", n, check=False)
    if TIMINGS:
        sys.stderr.write("conversion seconds: " + ", ".join(f"{k}={v:.2f}" for k, v in TIMINGS.items()) + "\n")


class HealthTest(unittest.TestCase):
    def test_health_reports_soffice_version(self):
        h = _wait_healthy(_Main.base, 5)
        self.assertEqual(h["status"], "ok")
        self.assertTrue(h["soffice"].startswith("LibreOffice "), h)

    def test_runs_as_non_root_on_a_read_only_root(self):
        r = _exec_py(_Main.name, "import os,json;print(json.dumps([os.getuid(), os.access('/opt', os.W_OK)]))")
        uid, writable = json.loads(r.stdout)
        self.assertNotEqual(uid, 0)
        self.assertFalse(writable)


class ConvertToPdfTest(unittest.TestCase):
    """(a) + (b): Thai text layer and page counts of every template's PDF."""

    def _convert_and_check(self, name: str) -> dict:
        data = filled(name)
        t0 = time.monotonic()
        status, headers, pdf = _post(_Main.base, "/convert?to=pdf", data, {"X-Filename": name})
        TIMINGS[name] = time.monotonic() - t0
        self.assertEqual(status, 200, pdf[:300])
        self.assertTrue(headers.get("Content-Type", "").startswith("application/pdf"))
        self.assertTrue(pdf.startswith(b"%PDF-"))
        status, _h, body = _post(_Main.base, "/check", pdf)
        self.assertEqual(status, 200, body[:300])
        return json.loads(body)

    def test_every_template_converts(self):
        names = sorted(p.name for p in TEMPLATES.iterdir() if p.suffix in (".docx", ".xlsx", ".pptx"))
        self.assertEqual(len(names), 7)
        for name in names:
            with self.subTest(name=name):
                chk = self._convert_and_check(name)
                self.assertGreaterEqual(chk["pages"], 1)
                self.assertEqual(len(chk["page_png_sizes"]), chk["pages"])
                for s in chk["page_png_sizes"]:
                    self.assertGreater(s["width"], 0)
                    self.assertGreater(s["height"], 0)
                text = squash(chk["text"])
                spec = EXPECTED["pdf"].get(name)
                wanted = list(spec["text_contains"]) if spec else []
                # Templates the filler cannot use still show their own labels.
                wanted += [t["label"] for t in EXPECTED["templates"][name]["expect"].get("targets", [])]
                for needle in wanted:
                    self.assertIn(squash(needle), text, f"{name}: {needle!r} missing from the text layer")
                if spec and "pages" in spec:
                    self.assertEqual(chk["pages"], spec["pages"], name)
                if spec and "pages_min" in spec:
                    self.assertGreaterEqual(chk["pages"], spec["pages_min"], name)

    def test_expected_pdf_section_is_covered(self):
        self.assertEqual(set(EXPECTED["pdf"]), {"docx_placeholders.docx", "docx_repeating_rows.docx",
                                                "xlsx_named_ranges.xlsx", "pptx_theme_chart.pptx"})

    def test_check_can_return_the_first_page_png(self):
        status, _h, pdf = _post(_Main.base, "/convert?to=pdf", filled("pptx_theme_chart.pptx"),
                                {"X-Filename": "deck.pptx"})
        self.assertEqual(status, 200)
        status, _h, body = _post(_Main.base, "/check?first_png=1", pdf)
        self.assertEqual(status, 200)
        import base64
        png = base64.b64decode(json.loads(body)["first_png_base64"])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_check_reports_fonts_margins_and_metadata(self):
        # AM21 §3.4: the fonts the PDF carries (embedded by LibreOffice's export), the ink in each page's outer
        # margins (a blank strip on a plain page), and the metadata a reader sees (the title a document set)
        import docx

        d = docx.Document()
        d.core_properties.title = "รายงานผลประกอบการ"
        d.core_properties.author = "บริษัท ทดสอบ จำกัด"
        d.add_paragraph("ข้อความธรรมดาในหน้ากระดาษ")
        buf = io.BytesIO()
        d.save(buf)
        status, _h, pdf = _post(_Main.base, "/convert?to=pdf", buf.getvalue(), {"X-Filename": "meta.docx"})
        self.assertEqual(status, 200)
        status, _h, body = _post(_Main.base, "/check", pdf)
        self.assertEqual(status, 200)
        chk = json.loads(body)
        self.assertEqual(len(chk["margin_ink"]), chk["pages"])
        self.assertLessEqual(chk["margin_ink"][0], 0.001, chk["margin_ink"])
        self.assertTrue(chk["fonts"], "pdffonts lists the font")
        self.assertTrue(all(f["embedded"] for f in chk["fonts"]), chk["fonts"])
        self.assertEqual(chk["meta"].get("title"), "รายงานผลประกอบการ")
        self.assertEqual(chk["meta"].get("author"), "บริษัท ทดสอบ จำกัด")

    def test_pages_returns_the_pages_as_pictures(self):
        # AM21 part 4: the pictures a person sees before sending a file, from the delivered file itself
        import base64

        status, _h, pdf = _post(_Main.base, "/convert?to=pdf", filled("pptx_theme_chart.pptx"), {"X-Filename": "deck.pptx"})
        self.assertEqual(status, 200)
        status, _h, body = _post(_Main.base, "/pages?from=1&to=2&dpi=72", pdf)
        self.assertEqual(status, 200, body[:200])
        res = json.loads(body)
        self.assertGreaterEqual(res["pages"], 1)
        self.assertEqual(res["from"], 1)
        self.assertEqual(len(res["png_base64"]), min(2, res["pages"]))
        for b64 in res["png_base64"]:
            self.assertTrue(base64.b64decode(b64).startswith(b"\x89PNG\r\n\x1a\n"))
        # a page past the end is nothing, not an error
        status, _h, body = _post(_Main.base, f"/pages?from={res['pages'] + 5}&to={res['pages'] + 6}", pdf)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["png_base64"], [])

    def test_check_reads_content_order_text_and_page_ink(self):
        # AM2 gate: -raw keeps a table cell's lines together (-layout interleaves cells side by side),
        # and page_ink tells a page with text from a blank one.
        import docx

        d = docx.Document()
        t = d.add_table(rows=1, cols=2)
        t.cell(0, 0).text = "ค่าใช้จ่าย\nในการบริหาร"
        t.cell(0, 1).text = "รายได้จาก\nการขาย"
        d.add_page_break()
        d.add_paragraph(" ")
        buf = io.BytesIO()
        d.save(buf)
        status, _h, pdf = _post(_Main.base, "/convert?to=pdf", buf.getvalue(), {"X-Filename": "cells.docx"})
        self.assertEqual(status, 200)
        status, _h, body = _post(_Main.base, "/check", pdf)
        self.assertEqual(status, 200)
        chk = json.loads(body)
        flat = "".join(unicodedata.normalize("NFC", chk["raw_text"]).split())
        self.assertIn("ค่าใช้จ่ายในการบริหาร", flat)
        self.assertIn("รายได้จากการขาย", flat)
        self.assertEqual(len(chk["page_ink"]), chk["pages"])
        self.assertEqual(chk["pages"], 2)
        self.assertGreater(chk["page_ink"][0], 0.001)
        self.assertEqual(chk["page_ink"][1], 0.0)

    def test_bad_requests_get_short_json_errors(self):
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as z:
            z.writestr("hello.txt", "not an office document")
        xlsx = (TEMPLATES / "xlsx_no_markers.xlsx").read_bytes()
        cases = [("/convert?to=pdf", b"hello", {"X-Filename": "x.exe"}, 415),
                 ("/convert?to=docx", b"hello", {"X-Filename": "x.xlsx"}, 400),
                 ("/convert?to=xlsx", b"hello", {"X-Filename": "x.docx"}, 415),
                 # any bytes named .docx would otherwise be imported as plain text
                 ("/convert?to=pdf", b"not really a document", {"X-Filename": "x.docx"}, 415),
                 # a ZIP must be the package its extension names (not an empty page)
                 ("/convert?to=pdf", zbuf.getvalue(), {"X-Filename": "x.docx"}, 415),
                 ("/convert?to=pdf", xlsx, {"X-Filename": "x.docx"}, 415),
                 ("/convert?to=pdf", xlsx[:2000], {"X-Filename": "x.xlsx"}, 415),
                 ("/check", b"not a pdf", {}, 415)]
        for path, body, headers, want in cases:
            with self.subTest(path=path, want=want):
                status, h, raw = _post(_Main.base, path, body, headers)
                self.assertEqual(status, want, raw[:200])
                self.assertTrue(h.get("Content-Type", "").startswith("application/json"))
                err = json.loads(raw)["error"]
                self.assertLess(len(err), 300)
                self.assertNotIn("Traceback", err)


class RecalcTest(unittest.TestCase):
    """(c): to=xlsx returns the workbook with fresh cached values."""

    def _values_after(self, data: bytes):
        from openpyxl import load_workbook
        status, headers, out = _post(_Main.base, "/convert?to=xlsx", data, {"X-Filename": "filled.xlsx"})
        self.assertEqual(status, 200, out[:300])
        self.assertIn("spreadsheetml", headers.get("Content-Type", ""))
        return load_workbook(io.BytesIO(out), data_only=True), load_workbook(io.BytesIO(out))

    def test_formulas_recalculated_when_cache_is_absent(self):
        from openpyxl import load_workbook
        data = fill_xlsx(TEMPLATES / "xlsx_named_ranges.xlsx")
        self.assertIsNone(load_workbook(io.BytesIO(data), data_only=True)["สรุป"]["B7"].value,
                          "openpyxl was expected to leave B7 without a cached value")
        vals, formulas = self._values_after(data)
        self.assertEqual(round(vals["สรุป"]["B7"].value, 2), 168000.70)
        self.assertEqual(formulas["สรุป"]["B7"].value, "=B5+B6")
        for ref, want in EXPECTED["templates"]["xlsx_named_ranges.xlsx"]["expect"]["recalculated"].items():
            sheet, cell = ref.split("!")
            self.assertEqual(round(vals[sheet][cell].value, 2), want, ref)

    def test_stale_cached_value_is_replaced(self):
        from openpyxl import load_workbook
        data = _with_stale_b7(fill_xlsx(TEMPLATES / "xlsx_named_ranges.xlsx"), "0")
        self.assertEqual(load_workbook(io.BytesIO(data), data_only=True)["สรุป"]["B7"].value, 0)
        vals, _ = self._values_after(data)
        self.assertEqual(round(vals["สรุป"]["B7"].value, 2), 168000.70)


def _with_stale_b7(xlsx: bytes, stale: str) -> bytes:
    """Write a stale cached value into สรุป!B7 (what a template saved with B5=B6=0 carries)."""
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    wb_xml = src.read("xl/workbook.xml").decode("utf-8")
    rels = src.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rid = re.search(r'<sheet[^>]*name="สรุป"[^>]*r:id="([^"]+)"', wb_xml).group(1)
    target = re.search(r'Id="%s"[^>]*Target="([^"]+)"|Target="([^"]+)"[^>]*Id="%s"' % (rid, rid), rels)
    sheet_path = "xl/" + (target.group(1) or target.group(2)).lstrip("/").removeprefix("xl/")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            raw = src.read(item.filename)
            if item.filename == sheet_path:
                xml = raw.decode("utf-8")
                xml, n = re.subn(r'(<c r="B7"[^>]*>\s*<f>[^<]*</f>)\s*(?:<v\s*/>|<v>[^<]*</v>)?',
                                 lambda m: m.group(1) + f"<v>{stale}</v>", xml)
                assert n == 1, "B7 formula cell not found"
                raw = xml.encode("utf-8")
            dst.writestr(item, raw)
    return out.getvalue()


def _big_docx(paragraphs: int = 4000) -> bytes:
    from docx import Document
    doc = Document()
    line = "สรุปใบแจ้งหนี้ของบริษัท สรานเทค โซลูชั่นส์ จำกัด ประจำงวด กันยายน 2569 ยอดรวม 168,000.70 บาท "
    for i in range(paragraphs):
        doc.add_paragraph(f"{i} " + line * 3)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


_LEFTOVERS = """
import json, os
procs = []
for p in os.listdir('/proc'):
    if p.isdigit():
        try:
            comm = open('/proc/%s/comm' % p).read().strip()
        except OSError:
            continue
        if 'soffice' in comm or 'oosplash' in comm:
            procs.append(comm)
dirs = [d for d in os.listdir('/tmp') if d.startswith(('lo-profile-', 'job-'))]
print(json.dumps({'procs': procs, 'dirs': dirs}))
"""


class LimitsTest(unittest.TestCase):
    """(d): the wall-clock limit kills everything and cleans up; oversize is refused."""

    @classmethod
    def setUpClass(cls):
        cls.name = _start({"CONVERT_TIMEOUT_SECONDS": "2", "MAX_INPUT_BYTES": str(4 * 1024 * 1024)})
        cls.base = _base_url(cls.name)
        _wait_healthy(cls.base)

    def _leftovers(self) -> dict:
        r = _exec_py(self.name, _LEFTOVERS)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        return json.loads(r.stdout)

    def test_timeout_returns_504_and_leaves_nothing_behind(self):
        data = _big_docx()
        self.assertLess(len(data), 4 * 1024 * 1024)
        t0 = time.monotonic()
        status, _h, body = _post(self.base, "/convert?to=pdf", data, {"X-Filename": "big.docx"})
        elapsed = time.monotonic() - t0
        self.assertEqual(status, 504, body[:300])
        self.assertIn("timed out", json.loads(body)["error"])
        self.assertLess(elapsed, 15)
        deadline = time.monotonic() + 5
        left = self._leftovers()
        while left["procs"] and time.monotonic() < deadline:
            time.sleep(0.25)
            left = self._leftovers()
        self.assertEqual(left, {"procs": [], "dirs": []})
        # ...and the next conversion still works.
        status, _h, _b = _post(self.base, "/check", b"%PDF-1.4 broken")
        self.assertEqual(status, 422)

    def test_oversize_input_is_413(self):
        status, _h, body = _post(self.base, "/convert?to=pdf", b"x" * (4 * 1024 * 1024 + 1), {"X-Filename": "a.docx"})
        self.assertEqual(status, 413)
        self.assertIn("larger than", json.loads(body)["error"])

    def test_successful_conversion_also_cleans_up(self):
        status, _h, _b = _post(_Main.base, "/convert?to=pdf", filled("docx_placeholders.docx"),
                               {"X-Filename": "a.docx"})
        self.assertEqual(status, 200)
        r = _exec_py(_Main.name, _LEFTOVERS)
        self.assertEqual(json.loads(r.stdout), {"procs": [], "dirs": []})


class NoEgressTest(unittest.TestCase):
    """(e): on an internal network the converter cannot reach anything outside."""

    PROBE = "import socket; socket.create_connection(('1.1.1.1', 53), 2); print('CONNECTED')"

    def test_internal_network_has_no_egress(self):
        net = f"doc-converter-test-internal-{uuid.uuid4().hex[:8]}"
        _docker("network", "create", "--internal", net)
        _networks.append(net)
        name = _start(network=net, publish=False)
        deadline = time.monotonic() + 60
        while _docker("inspect", "-f", "{{.State.Running}}", name, check=False).stdout.strip() != "true":
            self.assertLess(time.monotonic(), deadline, "container did not start")
            time.sleep(0.5)
        r = _exec_py(name, self.PROBE)
        self.assertNotEqual(r.returncode, 0, "the converter reached 1.1.1.1:53 from an internal network")
        self.assertNotIn("CONNECTED", r.stdout)
        # Control (informational): the same probe on the default bridge, so a
        # pass above means the network blocked it, not that the probe is broken.
        ctl = _exec_py(_Main.name, self.PROBE)
        sys.stderr.write(f"egress control on the default bridge: {'reachable' if ctl.returncode == 0 else 'unreachable'}\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)

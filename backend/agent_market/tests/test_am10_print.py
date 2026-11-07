"""AM10: a working paper's pages only run downward — a reader follows one line on one page.

AM9 put a row's identity beside its amounts on every page across; the PO line sheet still printed on
about 8 pages across (28 printed pages for the workbook). Now a wide table is split: the main sheet keeps
what a reader follows (identity, amounts, status, the reason) and a companion sheet right after it —
"<sheet> · หลักฐาน" — holds the evidence, starting with the same identity columns, one row per row, so the
two are read side by side by row number. Every sheet of every paper prints one page across; the gate has a
check for it (xlsx.print.one_across) so it cannot come back.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from openpyxl import Workbook, load_workbook

from agent_market.deliverables import workpaper
from agent_market.tests import samplekit

HERE = os.path.dirname(os.path.abspath(__file__))
AM8_BANK = os.path.join(HERE, "fixtures", "print", "am8_bank_paper.xlsx")
PACKAGES = ("po_matching", "bank_reconciliation", "expense_audit", "invoice_processing", "financial_report")
# the detail sheet of each paper, and the headers every one of its tables had before the split (AM9): each
# must still head a column somewhere in the workbook — nothing is dropped, only moved
DETAIL = {"po_matching": "ผลตรวจรายบรรทัด", "bank_reconciliation": "รายการที่จับคู่", "expense_audit": "รายการเบิก",
          "invoice_processing": "ทะเบียน", "financial_report": "รายการที่ใช้"}
HEADERS = {
    "po_matching": [
        "ลำดับ", "เลขที่", "วันที่", "ชนิดเอกสาร", "ผู้ขาย", "เลขประจำตัวผู้เสียภาษี", "PO ที่อ้างถึง", "สกุลเงิน", "ยอดก่อนภาษี",
        "ภาษีมูลค่าเพิ่ม", "ยอดรวม", "สถานะ", "เหตุผล", "วิธีตรวจ", "ขอคืนภาษีซื้อ", "หมายเหตุ", "หลักฐาน",
        "ใบแจ้งหนี้", "บรรทัด", "รายการ", "จำนวน", "หน่วย", "ราคาต่อหน่วย", "จำนวนเงิน", "เหตุที่ตรวจแบบนี้", "PO", "บรรทัดของ PO",
        "รายการใน PO", "ราคาต่อหน่วยใน PO", "หลักฐาน (PO)", "ใบรับของที่ใช้ตรวจ", "สั่งซื้อ", "รับจริงสะสม", "เรียกเก็บก่อนหน้า",
        "เรียกเก็บสะสม", "หน่วยที่เทียบ", "ส่วนต่างราคารวม", "ข้อที่ไม่ผ่าน", "ค่าที่ยอมรับได้ที่ใช้", "วิธีจับคู่", "ขั้น", "ผู้ยืนยัน",
        "หลักฐาน (ใบแจ้งหนี้)", "รหัสสินค้า", "ประเภท", "สถานะ PO", "มูลค่าที่เรียกเก็บสะสม", "คงเหลือให้เรียกเก็บ", "เรียกเก็บเกิน",
        "ใบแจ้งหนี้ที่เรียกเก็บ", "ใบรับของ", "เรื่อง", "เอกสาร", "รายการ (ซ้าย)", "หลักฐาน (ซ้าย)", "รายการ (ขวา)", "หลักฐาน (ขวา)",
        "ความคล้าย", "รายละเอียด", "สิ่งที่เสนอให้ทำ", "ทางเลือก", "ใช้เป็น", "ไฟล์", "แผ่น", "แถวหัวตาราง", "จำนวนรายการ",
        "คอลัมน์ที่อ่าน", "รู้ได้จาก", "SHA-256"],
    "bank_reconciliation": [
        "ลำดับ", "ปรับยอดฝั่ง", "ประเภท", "วันที่", "เลขที่อ้างอิง", "รายการ", "จำนวนเงินตามไฟล์", "ผลต่อยอดหลังปรับปรุง", "อายุ (วัน)",
        "ช่วงอายุ", "อยู่ใน", "หลักฐาน", "ผู้จัดประเภท", "หมายเหตุ", "คู่ที่", "ขั้น", "วิธีจับคู่", "รูปแบบ", "ผู้ยืนยัน", "วันที่ (บัญชี)",
        "รายการ (บัญชี)", "เลขอ้างอิง (บัญชี)", "จำนวนเงิน (บัญชี)", "หลักฐาน (บัญชี)", "วันที่ (ธนาคาร)", "รายการ (ธนาคาร)",
        "เลขอ้างอิง (ธนาคาร)", "จำนวนเงิน (ธนาคาร)", "หลักฐาน (ธนาคาร)", "ผลต่าง", "ห่างกัน (วัน)", "เหตุที่สงสัย", "สถานะ",
        "เลขที่ร่าง", "คำอธิบาย", "บัญชี", "รหัสบัญชี", "เดบิต", "เครดิต", "มาจากรายการกระทบยอด", "จำนวนเงิน", "ธนาคารลงวันที่",
        "เรื่อง", "รายละเอียด", "สิ่งที่เสนอให้ทำ", "ทางเลือก", "ตำแหน่ง"],
    "expense_audit": [
        "ลำดับ", "เลขที่เบิก", "แถวในไฟล์", "รหัสพนักงาน", "ชื่อพนักงาน", "วันที่", "วัน", "หมวด", "ร้านค้า/ผู้รับเงิน", "จำนวนเงิน",
        "ใบเสร็จ", "ยอดในใบเสร็จ", "ขอคืนภาษีซื้อ", "หัก ณ ที่จ่าย", "รายละเอียด", "ผลการตรวจ", "รหัสเหตุผล", "คำตอบในการ์ด",
        "ข้อมูลในแถว", "หมวดในตาราง", "วงเงินต่อครั้ง", "วงเงินต่อเดือน", "มีใบเสร็จ", "ยอดตรงใบเสร็จ", "ไม่ซ้ำ", "ไม่แยกบิล",
        "ร้านไม่ต้องห้าม", "เอกสารขอคืน VAT", "วันทำงาน", "วัตถุประสงค์", "รายการ", "เหตุผล", "สถานะ", "สิ่งที่เสนอให้ทำ",
        "ทางเลือกของผู้ตัดสิน", "ที่มา", "อ้างอิงนโยบาย", "วงเงิน/เกณฑ์จากตาราง", "ตั้งข้อสังเกตด้วย", "บริบทจากเอกสารระเบียบ (ไม่ใช่วงเงิน)",
        "ไฟล์", "ชนิดไฟล์", "อ่านด้วย", "ชนิดเอกสาร", "ผู้ขาย", "เลขผู้เสียภาษี", "ยอดรวม", "หน้า", "ตำแหน่ง", "ข้อความในเอกสาร",
        "ความมั่นใจ", "ผู้กำหนดยอด", "รายการเบิก", "หมายเหตุ"],
    "invoice_processing": [
        "ลำดับ", "วันที่", "เลขที่", "ชนิดเอกสาร", "ผู้ขาย", "เลขผู้เสียภาษีผู้ขาย", "สาขา", "ผู้ซื้อ", "เลขผู้เสียภาษีผู้ซื้อ", "สกุลเงิน",
        "ยอดก่อน VAT", "VAT", "ยอดรวม", "หัก ณ ที่จ่าย", "ยอดที่ต้องชำระ", "อัตราแลกเปลี่ยน", "ยอดก่อน VAT (บาท)", "VAT (บาท)",
        "ยอดรวม (บาท)", "ที่มา", "ไฟล์", "ช่อง", "ค่า", "ความมั่นใจ", "อ่านด้วย", "หน้า", "ตำแหน่ง", "ข้อความในเอกสาร", "ผู้กำหนด",
        "ผ่านการตรวจ", "ผู้ช่วย AI อ่านได้ (อ้างอิง)"],
    "financial_report": ["ไฟล์", "แถวในไฟล์", "วันที่", "รหัสบัญชี", "คำอธิบาย", "เดบิต", "เครดิต", "เลขที่เอกสาร", "ศูนย์ต้นทุน",
                         "รหัส", "ชื่อบัญชี", "หมวด", "งวดนี้", "งวดก่อน", "เปลี่ยนแปลง", "%", "งบประมาณ", "ต่างจากงบ", "จำนวนรายการ",
                         "อ้างอิง"],
}
ONE_ACROSS = "xlsx.print.one_across"


def _companions(wb) -> list[tuple]:
    """(main, companion) sheet pairs: a companion is named "<main> · <what>"."""
    out = []
    for ws in wb.worksheets:
        main = ws.title.split(" · ")[0]
        if main != ws.title and main in wb.sheetnames:
            out.append((wb[main], ws))
    return out


def _identity(ws, cols: list[int]) -> list[list]:
    top = (workpaper._header_row(ws) or 0) + 1
    return [[workpaper._identity_text(ws.cell(row=r, column=c).value) for c in cols] for r in range(top, (ws.max_row or 0) + 1)]


class Pick(unittest.TestCase):
    """The writers build one table and pick each sheet's columns from it by header name."""

    def test_the_columns_named_come_out_in_that_order_with_the_tuples_renumbered(self):
        heads = ["no", "tier", "description", "amount", "evidence"]
        rows = [[1, "exact", "ค่าเช่า", -45000, "GL แถว 8"]]
        h, r, cols = workpaper.pick(heads, rows, ["no", "description", "amount", "tier"], money_cols=(4,), wrap_cols=(3, 5))
        self.assertEqual(h, ["no", "description", "amount", "tier"])
        self.assertEqual(r, [[1, "ค่าเช่า", -45000, "exact"]])
        self.assertEqual(cols, {"money_cols": (3,), "wrap_cols": (2,)})      # the evidence column is not here: its tuple entry goes

    def test_a_column_that_is_not_there_is_an_error(self):
        with self.assertRaises(ValueError):
            workpaper.pick(["a"], [], ["b"])


class TheAgentsPapers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out, cls.render = {}, {}
        for pkg in PACKAGES:
            res = samplekit.render_sample(pkg, out_dir=tempfile.mkdtemp(), formats=["xlsx"])
            cls.out[pkg] = res["paths"]["xlsx"]
            cls.render[pkg] = res["render"]

    def test_every_sheet_prints_one_page_across(self):
        for pkg, path in self.out.items():
            wb = load_workbook(path)
            for ws in wb.worksheets:
                with self.subTest(pkg=pkg, sheet=ws.title):
                    self.assertEqual(len(workpaper.bands(ws)), 1, f"{ws.max_column} columns run across pages")

    def test_every_sheet_is_still_readable_at_its_scale(self):
        for pkg, path in self.out.items():
            wb = load_workbook(path)
            for i, ws in enumerate(wb.worksheets):
                with self.subTest(pkg=pkg, sheet=ws.title):
                    p = workpaper.plan(ws, face=(i == 0))
                    self.assertTrue(p["fit_width"] or i == 0, p)
                    self.assertGreaterEqual(p["scale"], workpaper.MIN_SCALE, p)

    def test_the_detail_sheet_is_split_not_squeezed(self):
        for pkg, sheet in DETAIL.items():
            ws = load_workbook(self.out[pkg])[sheet]
            with self.subTest(pkg):
                self.assertLessEqual(ws.max_column, 16, "a reader follows one line: not every column fits on one page")
                self.assertGreaterEqual(workpaper.plan(ws)["scale"], 0.65, "the page is readable, not merely fitted")

    def test_every_column_that_existed_still_heads_a_column_somewhere(self):
        for pkg, heads in HEADERS.items():
            wb = load_workbook(self.out[pkg])
            texts = {str(c.value).strip() for ws in wb.worksheets for row in ws.iter_rows(max_row=8) for c in row
                     if isinstance(c.value, str)}
            with self.subTest(pkg):
                self.assertEqual([h for h in heads if h not in texts], [])

    def test_a_companion_starts_with_the_main_sheets_identity_columns_row_for_row(self):
        seen = 0
        for pkg, path in self.out.items():
            wb = load_workbook(path)
            for main, aside in _companions(wb):
                seen += 1
                with self.subTest(pkg=pkg, sheet=aside.title):
                    mine = [a.title for m, a in _companions(wb) if m is main]
                    after = wb.sheetnames[wb.sheetnames.index(main.title) + 1:]
                    self.assertEqual(after[:len(mine)], mine, "the companions follow their main sheet")
                    ident = workpaper.title_cols(main)
                    self.assertEqual(workpaper.title_cols(aside), ident)
                    top = workpaper._header_row(main)
                    self.assertEqual(top, workpaper._header_row(aside))
                    self.assertEqual([aside.cell(row=top, column=c).value for c in ident], [main.cell(row=top, column=c).value for c in ident])
                    self.assertEqual(_identity(aside, ident), _identity(main, ident))
                    self.assertLessEqual(len(aside.title), 31)
        self.assertGreaterEqual(seen, 8, "the wide tables of the four papers have companions")

    def test_the_print_gate_passes_and_includes_the_one_across_check(self):
        for pkg, path in self.out.items():
            with self.subTest(pkg):
                res = workpaper.check(path)
                self.assertTrue(res["ok"], res["detail"])
                names = {c["name"]: c for c in workpaper.checks(path)}
                self.assertIn(ONE_ACROSS, names)
                self.assertTrue(names[ONE_ACROSS]["ok"], names[ONE_ACROSS]["detail"])
                gate = {c["name"]: c for a in self.render[pkg]["artifacts"] if a["format"] == "xlsx" for c in a["quality"]["checks"]}
                self.assertIn(ONE_ACROSS, gate, "the package's own gate runs it")
                self.assertTrue(gate[ONE_ACROSS]["ok"], gate[ONE_ACROSS]["detail"])

    def test_the_print_proof_still_gets_every_detail_row_named_in_words(self):
        for pkg, sheet in DETAIL.items():
            rows = [r for r in workpaper.print_rows(self.out[pkg]) if r["sheet"] == sheet]
            with self.subTest(pkg):
                self.assertTrue(rows)
                self.assertTrue(all(r["amounts"] and any(len(t) > 3 and not t.isdigit() for t in r["identity"]) for r in rows), rows[:2])


class TheRuleCannotRegress(unittest.TestCase):
    def _wide(self, cols: int = 16, width: int = 30):
        wb = Workbook()
        wb.active.title = "face"
        t = wb.create_sheet("pairs")
        t.append(["no", "description"] + [f"c{i}" for i in range(cols - 3)] + ["amount"])
        for n in range(3):
            t.append([n + 1, f"รายการที่ {n + 1} ค่าเช่า"] + ["x" * 30] * (cols - 3) + [1000.0 * (n + 1)])
        for c in range(1, cols + 1):
            t.column_dimensions[t.cell(row=1, column=c).column_letter].width = width
        for r in range(2, 5):
            t.cell(row=r, column=cols).number_format = '#,##0.00;[Red](#,##0.00);"-"'
        t.freeze_panes = "A2"
        path = os.path.join(tempfile.mkdtemp(), "wide.xlsx")
        wb.save(path)
        wb = load_workbook(path)
        workpaper.setup_workbook(wb, doc_name="wide.xlsx", id_cols={"pairs": 2})
        wb.save(path)
        return path

    def test_a_sheet_that_runs_across_pages_fails_naming_the_sheet_and_its_page_count(self):
        path = self._wide()
        res = next(c for c in workpaper.checks(path) if c["name"] == ONE_ACROSS)
        self.assertFalse(res["ok"])
        self.assertIn("“pairs”", res["detail"])
        self.assertIn(f"{len(workpaper.bands(load_workbook(path)['pairs']))} หน้า", res["detail"])
        self.assertTrue(workpaper.check(path)["ok"], "AM9's own rules are met: this is the new rule failing")

    def test_the_am8_bank_paper_fails_it_too(self):
        res = next(c for c in workpaper.checks(AM8_BANK) if c["name"] == ONE_ACROSS)
        self.assertFalse(res["ok"])
        self.assertIn("รายการที่จับคู่", res["detail"])

    def test_a_companion_whose_rows_do_not_line_up_fails(self):
        res = samplekit.render_sample("bank_reconciliation", out_dir=tempfile.mkdtemp(), formats=["xlsx"])
        path = res["paths"]["xlsx"]
        wb = load_workbook(path)
        main, aside = _companions(wb)[0]
        top = workpaper._header_row(aside) + 1
        a, b = aside.cell(row=top, column=2), aside.cell(row=top + 1, column=2)
        a.value, b.value = b.value, a.value
        wb.save(path)
        res = next(c for c in workpaper.checks(path) if c["name"] == ONE_ACROSS)
        self.assertFalse(res["ok"])
        self.assertIn(f"“{aside.title}”", res["detail"])
        self.assertIn(f"แถว {top}", res["detail"])


if __name__ == "__main__":
    unittest.main()

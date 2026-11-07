"""Headers as real Thai exports print them (AM1b fixtures)."""

import os
import tempfile
import unittest

from openpyxl import Workbook

from agent_market.engine.ledger import parse_file

ROWS = [
    ["01/09/2569", "IV256909-0001", "1131-01", "ลูกหนี้การค้า", "ขายเชื่อ", "SL01", "107,000.00", ""],
    ["01/09/2569", "IV256909-0001", "4100-01", "รายได้จากการขายสินค้า", "ขายเชื่อ", "SL01", "", "100,000.00"],
    ["01/09/2569", "IV256909-0001", "2154-00", "ภาษีขาย", "ขายเชื่อ", "SL01", "", "7,000.00"],
    ["02/09/2569", "PV256909-0001", "5400-01", "ค่าเช่าสำนักงาน", "ค่าเช่า", "HQ01", "30,000.00", ""],
    ["02/09/2569", "PV256909-0001", "2111-01", "เจ้าหนี้การค้า", "ค่าเช่า", "HQ01", "", "30,000.00"],
]
HEAD_A = ["วันที่", "เลขที่เอกสาร", "รหัสบัญชี", "ชื่อบัญชี", "คำอธิบายรายการ", "ศูนย์ต้นทุน", "จำนวนเงิน", ""]
HEAD_B = ["", "", "", "", "", "", "เดบิต", "เครดิต"]


class TwoRowHeaders(unittest.TestCase):
    def test_amount_over_debit_credit_in_a_cp874_csv(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "gl.csv")
        lines = [["บริษัท ทดสอบ จำกัด"], ["สมุดรายวันทั่วไป"], [], HEAD_A, HEAD_B] + ROWS
        with open(path, "wb") as f:
            f.write("\r\n".join(",".join(f'"{c}"' for c in r) for r in lines).encode("cp874"))
        pf = parse_file(path, "gl.csv")
        self.assertEqual(pf.dc_mode, "columns", "เดบิต and เครดิต sit under จำนวนเงิน: two columns, not one signed amount")
        self.assertEqual(pf.data_rows, 5)
        self.assertEqual(sum(r.credit for r in pf.rows), sum(r.debit for r in pf.rows))

    def test_merged_cells_in_an_xlsx(self):
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "บริษัท ทดสอบ จำกัด"
        ws.merge_cells("A1:H1")
        for i, h in enumerate(HEAD_A[:6], start=1):
            ws.cell(row=3, column=i, value=h)
            ws.merge_cells(start_row=3, start_column=i, end_row=4, end_column=i)
        ws.cell(row=3, column=7, value="จำนวนเงิน (บาท)")
        ws.merge_cells("G3:H3")
        ws.cell(row=4, column=7, value="เดบิต")
        ws.cell(row=4, column=8, value="เครดิต")
        for r in ROWS:
            ws.append(r[:6] + [float(r[6].replace(",", "")) if r[6] else None, float(r[7].replace(",", "")) if r[7] else None])
        d = tempfile.mkdtemp()
        path = os.path.join(d, "gl.xlsx")
        wb.save(path)
        pf = parse_file(path, "gl.xlsx")
        self.assertEqual(pf.dc_mode, "columns")
        self.assertEqual(pf.data_rows, 5)
        self.assertEqual(pf.header.header_of("debit"), "เดบิต")

    def test_a_data_row_is_never_taken_for_a_second_header_row(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "gl.csv")
        head = ["วันที่", "เลขที่เอกสาร", "รหัสบัญชี", "ชื่อบัญชี", "คำอธิบายรายการ", "ศูนย์ต้นทุน", "เดบิต", "เครดิต"]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(",".join(f'"{c}"' for c in r) for r in [head] + ROWS))
        pf = parse_file(path, "gl.csv")
        self.assertEqual(pf.data_rows, 5)


class UnreadableDates(unittest.TestCase):
    """A row with an account and money but a date the parser can't read must
    never vanish silently (US-locale exports: 09/15/2026)."""

    def _csv(self, dates):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "gl.csv")
        head = ["วันที่", "เลขที่เอกสาร", "รหัสบัญชี", "ชื่อบัญชี", "คำอธิบายรายการ", "ศูนย์ต้นทุน", "เดบิต", "เครดิต"]
        rows = [[dates[i % len(dates)]] + r[1:] for i, r in enumerate(ROWS)]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(",".join(f'"{c}"' for c in r) for r in [head] + rows + [["รวม", "", "", "", "", "", "137,000.00", "137,000.00"]]))
        return path

    def test_rows_with_unreadable_dates_are_counted_with_samples(self):
        pf = parse_file(self._csv(["09/15/2026", "01/09/2569"]), "gl.csv")
        self.assertEqual(pf.data_rows, 2, "only the readable rows are ledger rows")
        self.assertEqual(pf.unreadable_date_rows, 3)
        self.assertEqual(pf.unreadable_date_samples[0][1], "09/15/2026")

    def test_preflight_asks_before_computing_without_them(self):
        import datetime as dt

        from agent_market.packages import financial_report as pkg

        pf = pkg.parse(self._csv(["09/15/2026", "01/09/2569"]), "gl.csv", {})
        pre = pkg.preflight([pf], message="ปิดงบเดือนกันยายน 2569", today=dt.date(2026, 10, 3))
        f = next((x for x in pre["findings"] if x["code"] == "unreadable_dates"), None)
        self.assertIsNotNone(f, "3 rows with money would drop out of the report without a word")
        self.assertEqual(f["severity"], "blocking")
        self.assertIn("09/15/2026", f["question"])
        # answered 'exclude' → no more question, and the report says so
        pre2 = pkg.preflight([pf], message="ปิดงบเดือนกันยายน 2569", answers={f["key"]: "exclude"}, today=dt.date(2026, 10, 3))
        self.assertFalse([x for x in pre2["findings"] if x["code"] == "unreadable_dates"])
        self.assertTrue(any(a.get("code") == "unreadable_dates" for a in pre2["assumptions"]))

    def test_a_clean_file_has_none(self):
        pf = parse_file(self._csv(["01/09/2569"]), "gl.csv")
        self.assertEqual(pf.unreadable_date_rows, 0)


if __name__ == "__main__":
    unittest.main()

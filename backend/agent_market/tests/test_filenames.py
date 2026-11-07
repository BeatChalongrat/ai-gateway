"""A file is what it contains, never what it is called or when it arrived (AM4).

The owner's test, exactly: rename the GL to aaa.xlsx and the invoice to bbb.pdf, run both, and the
result is identical to the properly named run. "Identical" is every number, every question, every
role, period and exclusion, and every cell of the delivered workbook — with the one difference that
cannot be anything else: where a sheet says which file a figure came from, it names the file the
person sent (aaa.xlsx). The comparison maps that label back and nothing more.

Then the places where a name or an upload order used to decide something: a ledger whose name says
"budget", a trial balance whose period was only in its name, an SAP export recognised by its name,
two ledgers of the same month, and two copies of one invoice. In each, the file's content decides;
when the content cannot, the agent asks.
"""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
import unittest

from agent_market.packages import financial_report as fin
from agent_market.tests import samplekit as S

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIN_EVAL = os.path.join(ROOT, "packages", "financial_report", "evals", "fixtures")
E2_DOCS = os.path.join(ROOT, "fixtures", "e2", "docs")


def _pkg_file(package: str, rel: str) -> str:
    return os.path.join(S.package_dir(package), rel)


def _relabel(v, names: dict[str, str]):
    """Every string with the names the person gave mapped back to the original names."""
    if isinstance(v, str):
        for given, original in names.items():
            v = v.replace(given, original)
        return v
    if isinstance(v, list):
        return [_relabel(x, names) for x in v]
    if isinstance(v, dict):
        return {_relabel(k, names): _relabel(x, names) for k, x in v.items()}
    return v


def _cells(path: str) -> dict:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=False)
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


def _outcome(res: dict, names: dict[str, str]) -> dict:
    """What a person gets from a run: roles, period, questions, assumptions, the computed table, the workbook."""
    return _relabel({
        "roles": res["roles"],
        "period": res["period"],
        "questions": [(f["code"], f["key"], f["question"]) for f in res["first_findings"]],
        "assumptions": [a.get("text") for a in res["assumptions"]],
        "computed": res["computed"],
        "workbook": _cells(res["paths"]["xlsx"]),
    }, names)


def _run(package: str, files: list[tuple[str, str]], message: str = "") -> dict:
    return S.run_files(package, files, defaults=S.self_test_defaults(package), formats=["xlsx"], message=message)


class TheOwnersTest(unittest.TestCase):
    """GL → aaa.xlsx, invoice → bbb.pdf: the same result."""

    def test_the_gl_renamed_aaa_xlsx(self):
        gl, prior, budget = (_pkg_file("financial_report", f"sample-data/{n}")
                             for n in ("GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"))
        msg = S.self_test("financial_report")["message"]
        named = _run("financial_report", [(gl, "GL_2569-09.xlsx"), (prior, "GL_2569-08.xlsx"), (budget, "งบประมาณ_2569.xlsx")], msg)
        renamed = _run("financial_report", [(gl, "aaa.xlsx"), (prior, "GL_2569-08.xlsx"), (budget, "งบประมาณ_2569.xlsx")], msg)
        self.assertEqual(renamed["roles"]["aaa.xlsx"], "primary")
        self.assertEqual(_outcome(renamed, {"aaa.xlsx": "GL_2569-09.xlsx"}), _outcome(named, {}))

    def test_every_financial_file_renamed_and_sent_in_reverse(self):
        gl, prior, budget = (_pkg_file("financial_report", f"sample-data/{n}")
                             for n in ("GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"))
        msg = S.self_test("financial_report")["message"]
        named = _run("financial_report", [(gl, "GL_2569-09.xlsx"), (prior, "GL_2569-08.xlsx"), (budget, "งบประมาณ_2569.xlsx")], msg)
        renamed = _run("financial_report", [(budget, "ccc.xlsx"), (prior, "bbb.xlsx"), (gl, "aaa.xlsx")], msg)
        back = {"aaa.xlsx": "GL_2569-09.xlsx", "bbb.xlsx": "GL_2569-08.xlsx", "ccc.xlsx": "งบประมาณ_2569.xlsx"}
        a, b = _outcome(renamed, back), _outcome(named, {})
        # the source list follows upload order; which file played which part does not
        a["computed"]["source"]["files"] = sorted(a["computed"]["source"]["files"], key=lambda f: f["file"])
        b["computed"]["source"]["files"] = sorted(b["computed"]["source"]["files"], key=lambda f: f["file"])
        a["computed"].pop("ledger"), b["computed"].pop("ledger")  # rows carry the file's upload index
        a["workbook"].pop("ที่มา", None), b["workbook"].pop("ที่มา", None)
        self.assertEqual(a, b)

    def test_the_invoice_renamed_bbb_pdf(self):
        pdf = _pkg_file("invoice_processing", "sample-data/ใบกำกับภาษี_IV6909-0015.pdf")
        wht = _pkg_file("invoice_processing", "sample-data/ใบแจ้งหนี้ค่าบริการ_หักณที่จ่าย.pdf")
        xml = _pkg_file("invoice_processing", "sample-data/eTax_ใบเสร็จ.xml")
        named = _run("invoice_processing", [(pdf, "ใบกำกับภาษี_IV6909-0015.pdf"), (wht, "ใบแจ้งหนี้ค่าบริการ_หักณที่จ่าย.pdf"),
                                            (xml, "eTax_ใบเสร็จ.xml")])
        renamed = _run("invoice_processing", [(pdf, "bbb.pdf"), (wht, "ใบแจ้งหนี้ค่าบริการ_หักณที่จ่าย.pdf"),
                                              (xml, "eTax_ใบเสร็จ.xml")])
        self.assertEqual(_outcome(renamed, {"bbb.pdf": "ใบกำกับภาษี_IV6909-0015.pdf"}), _outcome(named, {}))

    def test_a_renamed_receipt_is_found_by_what_it_says(self):
        # the claims sheet names each receipt's file; renamed before sending, it is linked by its total and date
        d = S.package_dir("expense_audit")
        sheet = (os.path.join(d, "sample-data", "เบิกค่าใช้จ่าย_ตัวอย่าง.xlsx"), "เบิกค่าใช้จ่าย_ตัวอย่าง.xlsx")
        files = [(os.path.join(d, "sample-data", n), n) for n in ("r_c01.pdf", "r_c05.pdf", "r_c15.pdf")]
        named = _run("expense_audit", [sheet, *files])
        renamed = _run("expense_audit", [sheet, (files[0][0], "aaa.pdf"), files[1], files[2]])
        link = [a for a in renamed["assumptions"] if "aaa.pdf" in (a.get("text") or "")]
        self.assertEqual(len(link), 1, renamed["assumptions"])
        self.assertIn("r_c01.pdf", link[0]["text"])
        a, b = _outcome(renamed, {"aaa.pdf": "r_c01.pdf"}), _outcome(named, {})
        a["assumptions"] = [x for x in a["assumptions"] if "ยอดรวมและวันที่" not in (x or "")]
        a["computed"]["assumptions"] = [x for x in a["computed"]["assumptions"] if x.get("code") != "receipt_matched_by_content"]
        # the receipt keeps both names it is known by; everything the audit decided is the same
        for r in a["computed"]["receipts"]:
            r["names"] = [n for n in r["names"] if n != "r_c01.pdf" or r["name"] == "r_c01.pdf"]
        self.assertEqual(a["computed"]["claims"], b["computed"]["claims"])
        self.assertEqual(a["computed"]["totals"], b["computed"]["totals"])
        self.assertEqual(a["questions"], b["questions"])

    def test_two_receipts_that_fit_one_claim_are_not_guessed(self):
        d = S.package_dir("expense_audit")
        sheet = (os.path.join(d, "sample-data", "เบิกค่าใช้จ่าย_ตัวอย่าง.xlsx"), "เบิกค่าใช้จ่าย_ตัวอย่าง.xlsx")
        r1 = os.path.join(d, "sample-data", "r_c01.pdf")
        # two different files that both say C01's total and date (the second has a trailing PDF
        # comment: other bytes, same content) under names the sheet does not know — nothing is guessed
        twin = _renamed(r1, "bbb.pdf")
        with open(twin, "ab") as f:
            f.write(b"\n% another copy\n")
        res = _run("expense_audit", [sheet, (r1, "aaa.pdf"), (twin, "bbb.pdf")])
        self.assertFalse([a for a in res["assumptions"] if "ยอดรวมและวันที่" in (a.get("text") or "")], res["assumptions"])

    def test_the_invoices_sent_in_reverse(self):
        files = [(_pkg_file("invoice_processing", f"sample-data/{n}"), n)
                 for n in ("ใบกำกับภาษี_IV6909-0015.pdf", "ใบแจ้งหนี้ค่าบริการ_หักณที่จ่าย.pdf", "eTax_ใบเสร็จ.xml")]
        self.assertEqual(_outcome(_run("invoice_processing", files[::-1]), {}), _outcome(_run("invoice_processing", files), {}))


def _csv(rows: list[list], name: str) -> str:
    d = tempfile.mkdtemp(prefix="am4_names_")
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    return p


def _renamed(path: str, name: str) -> str:
    d = tempfile.mkdtemp(prefix="am4_names_")
    p = os.path.join(d, name)
    shutil.copy(path, p)
    return p


class NamesThatSaySomething(unittest.TestCase):
    """A name is a label the person typed. It never decides what the file is."""

    def test_a_ledger_named_like_a_budget_is_still_a_ledger(self):
        gl = os.path.join(FIN_EVAL, "clean_express_sep.xlsx")
        as_budget = fin.parse(_renamed(gl, "งบประมาณ_2569.xlsx"), "งบประมาณ_2569.xlsx", {})
        plain = fin.parse(_renamed(gl, "aaa.xlsx"), "aaa.xlsx", {})
        self.assertEqual((as_budget.doc_type, plain.doc_type), ("gl", "gl"))

    def test_a_period_only_in_the_name_is_not_read_from_the_name(self):
        # a trial balance with no dates and no title: its month is nowhere in its content
        rows = [["รหัสบัญชี", "ชื่อบัญชี", "เดบิต", "เครดิต"], ["1100-01", "เงินสด", "1000.00", ""],
                ["4100-01", "รายได้จากการขาย", "", "1000.00"]]
        named = fin.parse(_csv(rows, "TB_2569-08.csv"), "TB_2569-08.csv", {})
        plain = fin.parse(_csv(rows, "aaa.csv"), "aaa.csv", {})
        self.assertEqual((named.doc_type, plain.doc_type), ("tb", "tb"))
        self.assertEqual((named.period_hint, plain.period_hint), (None, None))

    def test_an_sap_export_is_known_by_its_content_not_its_name(self):
        # ambiguous dates (day and month both ≤ 12) under generic English headers
        rows = [["Posting Date", "G/L Account", "Account Name", "Text", "Debit", "Credit"]]
        for d in ("09/01/2026", "09/02/2026", "09/03/2026"):
            rows += [[d, "5300-01", "Office", "x", "100.00", ""], [d, "1100-01", "Cash", "x", "", "100.00"]]
        by_name = fin.parse(_csv(rows, "SAP_FBL3N_export.csv"), "SAP_FBL3N_export.csv", {})
        plain = fin.parse(_csv(rows, "aaa.csv"), "aaa.csv", {})
        self.assertEqual(by_name.date_order, plain.date_order)
        self.assertEqual(plain.date_order["source"], "undecided", "the content cannot tell, so the agent asks")

    def test_two_ledgers_of_the_same_month_whatever_order_they_came_in(self):
        a = os.path.join(FIN_EVAL, "clean_express_sep.xlsx")
        b = os.path.join(FIN_EVAL, "messy_express_sep.xlsx")
        one = fin.preflight([fin.parse(a, "x1.xlsx", {}), fin.parse(b, "x2.xlsx", {})], message="ปิดงบเดือนกันยายน 2569")
        two = fin.preflight([fin.parse(b, "x2.xlsx", {}), fin.parse(a, "x1.xlsx", {})], message="ปิดงบเดือนกันยายน 2569")
        self.assertEqual(one["roles"], two["roles"])

    def test_which_of_two_copies_of_an_invoice_is_recorded_does_not_follow_the_names(self):
        d01 = os.path.join(E2_DOCS, "d01_clean_full_tax_invoice.pdf")
        f03 = os.path.join(E2_DOCS, "f03_duplicate_of_d01.pdf")
        first = _run("invoice_processing", [(d01, "a.pdf"), (f03, "b.pdf")])
        swapped = _run("invoice_processing", [(d01, "b.pdf"), (f03, "a.pdf")])
        recorded = lambda res, names: sorted(  # noqa: E731
            (names[f["name"]], f["status"]) for f in res["computed"]["documents"])
        self.assertEqual(recorded(first, {"a.pdf": "d01", "b.pdf": "f03"}),
                         recorded(swapped, {"b.pdf": "d01", "a.pdf": "f03"}))


if __name__ == "__main__":
    unittest.main()

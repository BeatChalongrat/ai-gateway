"""AM13: the other four agents do what they are asked — an order for their table, and nothing else.

AM11b made two agents listen (the report, the expense audit). These are the four that did not:

* invoice ↔ PO matching — the lines from the largest price difference;
* bank reconciliation — the reconciling items from the largest amount, or by date;
* invoice processing — the register by seller, by document date, or from the largest total;
* procurement — the options from the lowest price. An order, never a verdict: nothing is marked as best,
  and what is not known about an option stays in its row.

Each is applied once, at render time, at the one list the sheet and its companion sheets are made from,
and the gate then opens the finished file and reads the order back out of its own cells. The refusal
cases are the test: a file left in the old order fails the gate of a round that asked for another; the
statement of a reconciliation, the totals of a register and the cells of a comparison are the same cells
with or without an order; prices in two currencies are not put in order at all.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import tempfile
import unittest
from collections import Counter

from openpyxl import load_workbook

from agent_market.deliverables import asked
from agent_market.deliverables.reread import table
from agent_market.deliverables.theme import resolve
from agent_market.tests import samplekit

RUN = {"id": "t", "date": "2026-10-07", "agent_version": "t", "preparer": "ผู้ทดสอบ"}


def _req(apply, name: str) -> dict:
    req = {"formats": ["xlsx"], "names": {"xlsx": name}, "theme": {"id": "navy", "company": "บริษัท ตัวอย่าง จำกัด"}, "run": dict(RUN)}
    if apply is not None:
        req["instructions"] = {"apply": apply}
    return req


def _checks(res: dict) -> dict:
    return {c["name"]: c for c in res["artifacts"][0]["quality"]["checks"]}


def _cells(path: str, sheet: str) -> list:
    return [c for row in load_workbook(path)[sheet].iter_rows(values_only=True) for c in row if c is not None]


def _manifest_says(test: unittest.TestCase, package: str, directives: tuple, grouped: bool) -> dict:
    m = samplekit.manifest(package)
    spec = m["instructions"]
    test.assertEqual([d["id"] for d in spec["can"]], list(directives))
    for d in spec["can"]:
        test.assertEqual(d.get("group"), "order" if grouped else None, d["id"])
        for key in ("label", "note"):
            test.assertTrue(d[key].strip(), (d["id"], key))
            test.assertIn(d[key], m["en"], d[key])  # said in English too
        test.assertTrue(d.get("asks"), "how people ask for it, for the model that reads their words")
        test.assertIn("เปลี่ยนแค่ลำดับ" if package != "procurement" else "เรียงตามราคาเท่านั้น", d["note"])
    for d in spec["cannot"]:
        test.assertNotIn(d["id"], directives)
        test.assertIn(d["label"], m["en"])
        test.assertTrue(d["why"].strip())
        test.assertIn(d["why"], m["en"])
    return m


class TheHelper(unittest.TestCase):
    def test_a_table_has_one_order_and_the_last_asked_is_it(self):
        orders = ("sort_by_amount", "sort_by_date")
        self.assertIsNone(asked.one_order({}, orders))
        self.assertIsNone(asked.one_order({"instructions": {"apply": ["no_charts"]}}, orders))
        self.assertEqual(asked.one_order({"instructions": {"apply": ["sort_by_date"]}}, orders), "sort_by_date")
        self.assertEqual(asked.one_order({"instructions": {"apply": ["sort_by_date", "sort_by_amount"]}}, orders), "sort_by_amount")

    def test_an_order_read_back_from_a_file_is_an_order_all_the_way_down(self):
        self.assertTrue(asked.runs_down([9, 9, 3, 0, None, None]))
        self.assertFalse(asked.runs_down([9, 3, 4]))
        self.assertFalse(asked.runs_down([9, None, 3]), "a number after a row that has none")
        self.assertTrue(asked.runs_up([(2026, 9, 1), (2026, 9, 1), (2026, 9, 30), None]))
        self.assertFalse(asked.runs_up([(2026, 9, 30), (2026, 9, 1)]))
        self.assertTrue(asked.runs_down([]) and asked.runs_up([None]))

    def test_thai_names_are_filed_by_their_consonant(self):
        # a leading vowel is written first and read second: เกษตร is filed under ก, before ขนส่ง
        names = ["ขนส่งไทย", "เกษตรรุ่งเรือง", "Acme", "acme trading", "ไทยออฟฟิศ", "ไทยพาณิชย์", ""]
        got = asked.by_name([{"n": n} for n in names], lambda r: r["n"])
        self.assertEqual([r["n"] for r in got], ["Acme", "acme trading", "เกษตรรุ่งเรือง", "ขนส่งไทย", "ไทยพาณิชย์", "ไทยออฟฟิศ", ""])
        self.assertTrue(asked.names_run_up([r["n"] for r in got]))
        self.assertFalse(asked.names_run_up(names))

    def test_rows_of_one_size_keep_the_files_order(self):
        rows = [{"k": "a", "v": "5"}, {"k": "b", "v": None}, {"k": "c", "v": "5"}, {"k": "d", "v": "7"}]
        self.assertEqual([r["k"] for r in asked.largest_first(rows, lambda r: asked.number(r["v"]))], ["d", "a", "c", "b"])
        self.assertEqual([r["k"] for r in asked.smallest_first(rows, lambda r: asked.number(r["v"]))], ["a", "c", "d", "b"])


class AskedOfThePOMatching(unittest.TestCase):
    """The lines of the working paper, on three sheets that are read side by side by row."""

    @classmethod
    def setUpClass(cls):
        from agent_market.packages import po_matching as PM
        from agent_market.packages.po_matching.scripts import workbook as W

        cls.PM, cls.W = PM, W
        computed = samplekit.render_sample("po_matching", defaults=samplekit.self_test_defaults("po_matching"))["computed"]
        # the bundled sample's prices all agree with their PO; these are the differences of a round where they do
        # not — one line without any, two of the same size (they keep the file's order)
        for x, v in zip(computed["lines"], ["120.00", "-900.00", None, "45.50", "-900.00", "0.00"]):
            x["price_variance"] = None if v is None else {"per_unit": "1", "pct": "1.00", "extended": v}
        cls.computed = computed

    def _render(self, apply):
        out = tempfile.mkdtemp()
        return self.PM.render(self.computed, _req(apply, "po.xlsx"), in_dir=out, out_dir=out), os.path.join(out, "po.xlsx")

    def _lines(self, path):
        return table(self.W.parts(load_workbook(path), self.W.S_LINES), self.W.H_LINES)

    def test_the_lines_run_from_the_largest_difference_and_nothing_else_changes(self):
        plain, plain_path = self._render(None)
        res, path = self._render(["sort_by_variance"])
        self.assertTrue(plain["passed"] and res["passed"], json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:700])
        self.assertEqual(res["asked"], {"applied": ["sort_by_variance"], "refused": []})
        self.assertEqual(plain["asked"], {"applied": [], "refused": []})
        col = self.W.H_LINES.index("ส่วนต่างราคารวม")
        rows, before = self._lines(path), self._lines(plain_path)
        self.assertEqual([r[col] for r in rows], [-900, -900, 120, 45.5, 0, None])
        # the two of one size are in the file's order: invoice-and-line of the earlier one first
        first_two = [(r[1], r[2]) for r in rows[:2]]
        self.assertEqual(first_two, [(r[1], r[2]) for r in before if r[col] == -900])
        # the same lines, with the same figures, status and place in the person's files — only the order
        body = lambda r: tuple(str(v) for v in r[1:])  # noqa: E731  (column 0 is the row's number on the sheet)
        self.assertEqual(Counter(map(body, rows)), Counter(map(body, before)))
        self.assertEqual([r[0] for r in rows], list(range(1, len(rows) + 1)))
        self.assertTrue(_checks(res)["xlsx.asked"]["ok"], _checks(res)["xlsx.asked"])
        self.assertNotIn("xlsx.asked", _checks(plain))

    def test_the_three_sheets_stay_row_for_row(self):
        _, path = self._render(["sort_by_variance"])
        wb = load_workbook(path)
        ident = self.W.ID_COLS[self.W.S_LINES]
        sheets = [[r[:ident] for r in wb[s].iter_rows(min_row=5, values_only=True) if r and r[0] is not None] for s in self.W.PARTS[self.W.S_LINES]]
        self.assertEqual(len(sheets), 3)
        self.assertEqual(sheets[0], sheets[1])
        self.assertEqual(sheets[0], sheets[2])
        self.assertEqual(len(sheets[0]), len(self.computed["lines"]))

    def test_every_other_sheet_is_the_same_sheet(self):
        _, plain_path = self._render(None)
        _, path = self._render(["sort_by_variance"])
        for sheet in (self.W.S_SUMMARY, self.W.S_INVOICES, self.W.S_PO, self.W.S_EXC):
            self.assertEqual(_cells(path, sheet), _cells(plain_path, sheet), sheet)

    def test_a_workbook_left_in_the_files_order_fails_a_round_that_asked_for_the_difference(self):
        _, plain_path = self._render(None)
        view, applied = self.PM._as_asked(self.computed, {"instructions": {"apply": ["sort_by_variance"]}})
        got = self.PM._asked_check(plain_path, view, applied)
        self.assertFalse(got["ok"], got)
        self.assertIn("ไม่ได้เรียงตามนั้น", got["detail"])

    def test_an_id_the_package_does_not_declare_changes_nothing(self):
        res, path = self._render(["sort_by_amount", "delete_the_ledger"])
        _, plain_path = self._render(None)
        self.assertEqual(res["asked"], {"applied": [], "refused": []})
        self.assertEqual(self._lines(path), self._lines(plain_path))

    def test_its_manifest_declares_what_its_renderer_applies(self):
        m = _manifest_says(self, "po_matching", self.PM.DIRECTIVES, grouped=False)
        self.assertEqual(m["version"], "1.6.0")


class AskedOfTheBankReconciliation(unittest.TestCase):
    """The reconciling items in the order asked. The statement, its two adjusted balances and the list
    carried to next month are made from what was computed: they must not move."""

    @classmethod
    def setUpClass(cls):
        from agent_market.packages import bank_reconciliation as BR
        from agent_market.packages.bank_reconciliation.scripts import workbook as W

        cls.BR, cls.W = BR, W
        cls.computed = samplekit.render_sample("bank_reconciliation")["computed"]

    def _render(self, apply):
        out = tempfile.mkdtemp()
        return self.BR.render(self.computed, _req(apply, "bank.xlsx"), in_dir=out, out_dir=out), os.path.join(out, "bank.xlsx")

    def _items(self, path):
        return table(self.W.parts(load_workbook(path), self.W.S_ITEMS), self.W.H_ITEMS)

    def _same_but_for_order(self, rows, before):
        body = lambda r: tuple(str(v) for v in r[1:])  # noqa: E731
        self.assertEqual(Counter(map(body, rows)), Counter(map(body, before)))
        self.assertNotEqual([r[1:] for r in rows], [r[1:] for r in before], "the sample is not in that order already")
        self.assertEqual([r[0] for r in rows], list(range(1, len(rows) + 1)))

    def test_the_items_run_from_the_largest_amount(self):
        plain, plain_path = self._render(None)
        res, path = self._render(["sort_by_amount"])
        self.assertTrue(plain["passed"] and res["passed"], json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:700])
        self.assertEqual(res["asked"], {"applied": ["sort_by_amount"], "refused": []})
        rows = self._items(path)
        sizes = [abs(r[6]) for r in rows]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 40660)
        self._same_but_for_order(rows, self._items(plain_path))
        self.assertTrue(_checks(res)["xlsx.asked"]["ok"], _checks(res)["xlsx.asked"])
        self.assertNotIn("xlsx.asked", _checks(plain))

    def test_the_items_run_from_the_oldest_date(self):
        _, plain_path = self._render(None)
        res, path = self._render(["sort_by_date"])
        self.assertTrue(res["passed"], json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:700])
        self.assertEqual(res["asked"], {"applied": ["sort_by_date"], "refused": []})
        rows = self._items(path)
        dates = [self.BR._date_read(r[3]) for r in rows]
        self.assertNotIn(None, dates)
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(dates[0], (2026, 9, 20))
        self._same_but_for_order(rows, self._items(plain_path))
        self.assertTrue(_checks(res)["xlsx.asked"]["ok"])
        # of one day, the file's order: the four of 30 Sep are as they were among themselves
        last_day = lambda rs: [tuple(str(v) for v in r[1:]) for r in rs if self.BR._date_read(r[3]) == (2026, 9, 30)]  # noqa: E731
        self.assertEqual(last_day(rows), last_day(self._items(plain_path)))

    def test_the_statement_and_next_months_list_do_not_move(self):
        _, plain_path = self._render(None)
        for order in ("sort_by_amount", "sort_by_date"):
            res, path = self._render([order])
            for sheet in (self.W.S_STATEMENT, self.W.S_OUT, self.W.S_JOURNAL, self.W.S_PAIRS, self.W.S_IN):
                self.assertEqual(_cells(path, sheet), _cells(plain_path, sheet), (order, sheet))
            ck = _checks(res)
            for name in ("xlsx.statement", "xlsx.verdict", "xlsx.adjustments_bank", "xlsx.adjustments_book", "xlsx.carried_out", "xlsx.figures"):
                self.assertTrue(ck[name]["ok"], (order, ck[name]))

    def test_the_evidence_sheet_stays_row_for_row(self):
        _, path = self._render(["sort_by_amount"])
        wb = load_workbook(path)
        ident = self.W.ID_COLS[self.W.S_ITEMS]
        main, evidence = [[r[:ident] for r in wb[s].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
                          for s in self.W.PARTS[self.W.S_ITEMS]]
        self.assertEqual(main, evidence)
        self.assertEqual(len(main), len(self.computed["items"]))

    def test_a_workbook_left_in_the_files_order_fails_either_order(self):
        _, plain_path = self._render(None)
        for order in ("sort_by_amount", "sort_by_date"):
            view, applied = self.BR._as_asked(self.computed, {"instructions": {"apply": [order]}})
            self.assertEqual(applied, [order])
            got = self.BR._asked_check(plain_path, view, applied)
            self.assertFalse(got["ok"], (order, got))
            self.assertIn("ไม่ได้เรียงตามนั้น", got["detail"])
        # and a file in one order fails a round that asked for the other
        _, by_amount = self._render(["sort_by_amount"])
        view, applied = self.BR._as_asked(self.computed, {"instructions": {"apply": ["sort_by_date"]}})
        self.assertFalse(self.BR._asked_check(by_amount, view, applied)["ok"])

    def test_two_orders_are_one_order(self):
        # (the platform sends one of a group; should two arrive, the table still has one order — the last)
        res, path = self._render(["sort_by_amount", "sort_by_date"])
        self.assertEqual(res["asked"], {"applied": ["sort_by_date"], "refused": []})
        dates = [self.BR._date_read(r[3]) for r in self._items(path)]
        self.assertEqual(dates, sorted(dates))

    def test_its_manifest_declares_what_its_renderer_applies(self):
        m = _manifest_says(self, "bank_reconciliation", self.BR.DIRECTIVES, grouped=True)
        self.assertEqual(m["version"], "1.7.0")


class AskedOfTheInvoiceRegister(unittest.TestCase):
    """The register's rows in the order asked. One register: no sheet per seller, no subtotal."""

    @classmethod
    def setUpClass(cls):
        from agent_market.packages import invoice_processing as IP
        from agent_market.packages.invoice_processing.scripts import workbook as W

        cls.IP, cls.W = IP, W
        computed = samplekit.render_sample("invoice_processing")["computed"]
        # the bundled sample is already in the order of its dates: turned round, so that an order by date shows
        computed["documents"] = list(reversed(computed["documents"]))
        cls.computed = computed

    def _render(self, apply):
        out = tempfile.mkdtemp()
        return self.IP.render(self.computed, _req(apply, "register.xlsx"), in_dir=out, out_dir=out), os.path.join(out, "register.xlsx")

    def _register(self, path):
        return table(self.W.parts(load_workbook(path), self.W.S_REGISTER), self.W.H_REGISTER)

    def test_each_order_is_the_order_of_the_register_and_nothing_else_changes(self):
        plain, plain_path = self._render(None)
        self.assertTrue(plain["passed"], json.dumps(plain["artifacts"][0]["quality"], ensure_ascii=False)[:700])
        before = self._register(plain_path)
        body = lambda r: tuple(str(v) for v in r[1:])  # noqa: E731
        want = {
            "sort_by_seller": ["บริษัท ไทยพาณิชย์ขนส่ง จำกัด (มหาชน)", "บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด", "หจก. ช่างเหล็กการโยธา"],
            "sort_by_date": ["บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด", "หจก. ช่างเหล็กการโยธา", "บริษัท ไทยพาณิชย์ขนส่ง จำกัด (มหาชน)"],
            "sort_by_amount": ["หจก. ช่างเหล็กการโยธา", "บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด", "บริษัท ไทยพาณิชย์ขนส่ง จำกัด (มหาชน)"],
        }
        for order, sellers in want.items():
            res, path = self._render([order])
            self.assertTrue(res["passed"], (order, json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:700]))
            self.assertEqual(res["asked"], {"applied": [order], "refused": []})
            rows = self._register(path)
            self.assertEqual([r[4] for r in rows], sellers, order)
            self.assertNotEqual([r[4] for r in rows], [r[4] for r in before], order)
            self.assertEqual(Counter(map(body, rows)), Counter(map(body, before)), order)
            self.assertEqual([r[0] for r in rows], [1, 2, 3])
            self.assertTrue(_checks(res)["xlsx.asked"]["ok"], (order, _checks(res)["xlsx.asked"]))
            # the totals on the face, the line items and where every value was read are the same sheets
            for sheet in ("สรุป", "รายการสินค้า", "ที่มา", "ข้อยกเว้น"):
                self.assertEqual(_cells(path, sheet), _cells(plain_path, sheet), (order, sheet))
            self.assertEqual(load_workbook(path).sheetnames, load_workbook(plain_path).sheetnames, "one register, no sheet per seller")
        self.assertNotIn("xlsx.asked", _checks(plain))

    def test_the_evidence_sheet_stays_row_for_row(self):
        _, path = self._render(["sort_by_amount"])
        wb = load_workbook(path)
        ident = self.W.ID_COLS[self.W.S_REGISTER]
        main, evidence = [[r[:ident] for r in wb[s].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
                          for s in self.W.PARTS[self.W.S_REGISTER]]
        self.assertEqual(main, evidence)
        self.assertEqual(len(main), 3)

    def test_a_register_left_in_the_files_order_fails_each_order(self):
        _, plain_path = self._render(None)
        for order in self.IP.DIRECTIVES:
            view, applied = self.IP._as_asked(self.computed, {"instructions": {"apply": [order]}})
            self.assertEqual(applied, [order])
            got = self.IP._asked_check(plain_path, view, applied)
            self.assertFalse(got["ok"], (order, got))
            self.assertIn("ไม่ได้เรียงตามนั้น", got["detail"])

    def test_its_manifest_declares_what_its_renderer_applies(self):
        m = _manifest_says(self, "invoice_processing", self.IP.DIRECTIVES, grouped=True)
        self.assertEqual(m["version"], "1.9.0")
        split = next(d for d in m["instructions"]["cannot"] if d["id"] == "split_by_seller")
        self.assertIn("ยอดรวมย่อย", split["why"])


class AskedOfTheComparison(unittest.TestCase):
    """Procurement: the options from the lowest price. The owner's rule (7 Oct 2026): "sorting by price is
    allowed; printing a 'best' verdict is not. The caveats stay on every row."""

    @classmethod
    def setUpClass(cls):
        from agent_market.packages import procurement as P
        from agent_market.packages.procurement.scripts import workbook as W

        cls.P, cls.W = P, W
        base = os.path.join(os.path.dirname(os.path.abspath(P.__file__)), "sample-data")
        with open(os.path.join(base, "research.json"), encoding="utf-8") as f:
            research = json.load(f)
        today = datetime.date(2026, 10, 6)
        defaults = {"proc_vat_rate": "7.00", "proc_sample_pick": "first"}
        cls.tmp = tempfile.mkdtemp()
        pre = P.preflight([], message="x", defaults=defaults, answers={}, today=today, proposals={"brief": research["brief"]}, lang="th")
        cls.computed = P.compute([], {}, pre["period"], answers={}, defaults=defaults, today=today, proposals=research,
                                 in_dir=base, out_dir=cls.tmp, lang="th")
        # the sample: three options — 3,200 a piece with tax in it and nothing unknown (no. 1), 3,050 before tax with
        # three points unknown (no. 2), 2,890 with its tax not stated and four unknown (no. 3)
        cands = cls.computed["items"][0]["candidates"]
        assert [c["no"] for c in cands] == [1, 2, 3] and [len(c["open"]) for c in cands] == [0, 3, 4], cands

    def _render(self, computed, apply):
        out = tempfile.mkdtemp()
        req = _req(apply, "comparison.xlsx")
        return self.P.render(computed, req, in_dir=self.tmp, out_dir=out), os.path.join(out, "comparison.xlsx")

    def _main(self, path, lang="th"):
        ws = load_workbook(path)[self.P.words.t(lang, "sheet.candidates")]
        return [r for r in ws.iter_rows(min_row=self.W.TOP + 1, values_only=True) if r and r[0] not in (None, "")]

    def _all_cells(self, path):
        return Counter(str(c) for ws in load_workbook(path).worksheets for row in ws.iter_rows(values_only=True) for c in row if c is not None)

    def test_the_options_run_from_the_lowest_price_shown_and_every_caveat_stays_in_its_row(self):
        col = {k: i for i, k in enumerate(self.W.MAIN)}
        plain, plain_path = self._render(self.computed, None)
        res, path = self._render(self.computed, ["sort_by_price"])
        self.assertTrue(plain["passed"] and res["passed"], json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:900])
        self.assertEqual(res["asked"], {"applied": ["sort_by_price"], "refused": []})
        self.assertEqual(plain["asked"], {"applied": [], "refused": []})
        rows, before = self._main(path), self._main(plain_path)
        # one option does not state its tax, so what is compared is the price each page shows
        self.assertEqual([r[col["unit_price"]] for r in rows], [2890, 3050, 3200])
        self.assertEqual([r[col["no"]] for r in rows], ["3", "2", "1"], "an option keeps its number: the person chooses by it")
        self.assertEqual([r[col["no"]] for r in before], ["1", "2", "3"])
        # the cheapest is the one with the most still unknown — and its row says so
        self.assertEqual([r[col["open"]] for r in rows], [4, 3, 0])
        self.assertEqual(Counter(map(str, rows)), Counter(map(str, before)), "the same rows, cell for cell")
        ck = _checks(res)
        self.assertTrue(ck["xlsx.asked"]["ok"], ck["xlsx.asked"])
        self.assertTrue(ck["xlsx.figures"]["ok"], ck["xlsx.figures"])
        self.assertNotIn("xlsx.asked", _checks(plain))

    def test_nothing_is_marked_as_best_the_only_new_words_say_how_the_table_is_ordered(self):
        t = lambda key: self.P.words.t("th", key)  # noqa: E731
        _, plain_path = self._render(self.computed, None)
        _, path = self._render(self.computed, ["sort_by_price"])
        new = self._all_cells(path) - self._all_cells(plain_path)
        gone = self._all_cells(plain_path) - self._all_cells(path)
        self.assertEqual(dict(new), {t("face.order.price_shown"): 1}, "a cell that the default workbook does not have")
        self.assertEqual(dict(gone), {t("face.order.value"): 1})
        said = t("face.order.price_shown")
        for must in ("ราคา", "ไม่ได้บอกว่าตัวเลือกใดดีกว่า", "ภาษี", "จุดที่ยังไม่ทราบ"):
            self.assertIn(must, said)
        for line in (said, t("face.order.price_total")):
            for verdict in ("ดีที่สุด", "แนะนำ", "คุ้มที่สุด", "ควรซื้อ"):
                self.assertNotIn(verdict, line)
        self.assertIn(t("face.not_advice"), self._all_cells(path))

    def test_when_every_option_states_its_tax_the_total_with_tax_is_what_is_compared(self):
        col = {k: i for i, k in enumerate(self.W.MAIN)}
        c = copy.deepcopy(self.computed)
        c["items"][0]["candidates"] = c["items"][0]["candidates"][:2]  # 19,200.00 with tax in · 18,300.00 + tax = 19,581.00
        res, path = self._render(c, ["sort_by_price"])
        self.assertTrue(res["passed"], json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:900])
        rows = self._main(path)
        self.assertEqual([r[col["total"]] for r in rows], [19200, 19581])
        self.assertEqual([r[col["unit_price"]] for r in rows], [3200, 3050], "the lower price before tax is the dearer one with it")
        self.assertIn(self.P.words.t("th", "face.order.price_total"), self._all_cells(path))

    def test_an_option_with_no_price_is_last_and_still_there(self):
        col = {k: i for i, k in enumerate(self.W.MAIN)}
        c = copy.deepcopy(self.computed)
        first = c["items"][0]["candidates"][0]
        first.update(price=None, currency=None, subtotal=None, total=None, open=["price"] + first["open"])
        res, path = self._render(c, ["sort_by_price"])
        self.assertTrue(res["passed"], json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:900])
        rows = self._main(path)
        self.assertEqual([r[col["no"]] for r in rows], ["3", "2", "1"])
        self.assertEqual(rows[-1][col["unit_price"]], self.P.words.t("th", "unknown"))
        self.assertEqual(len(rows), 3)

    def test_prices_in_two_currencies_are_not_put_in_order(self):
        c = copy.deepcopy(self.computed)
        c["items"][0]["candidates"][1]["currency"] = "USD"
        res, path = self._render(c, ["sort_by_price"])
        _, plain_path = self._render(c, None)
        self.assertEqual(res["asked"], {"applied": [], "refused": ["sort_by_price"]})
        self.assertEqual(self._main(path), self._main(plain_path), "the table is as it was")
        self.assertNotIn("xlsx.asked", _checks(res))
        self.assertIn(self.P.words.t("th", "face.order.value"), self._all_cells(path))
        m = samplekit.manifest("procurement")
        unless = m["instructions"]["can"][0]["unless"]
        self.assertIn("สกุลเงิน", unless)
        self.assertIn(unless, m["en"])

    def test_a_comparison_left_in_its_own_order_fails_a_round_that_asked_for_the_price(self):
        _, plain_path = self._render(self.computed, None)
        view, applied, refused = self.P._as_asked(self.computed, {"instructions": {"apply": ["sort_by_price"]}})
        self.assertEqual((applied, refused), (["sort_by_price"], []))
        got = self.P._asked_check(plain_path, view)
        self.assertFalse(got["ok"], got)
        self.assertIn("ไม่ได้เรียงตามนั้น", got["detail"])
        # … and so does one in the right order whose face still describes the other order
        self.assertIn(self.P.words.t("th", "check.asked.face"), got["detail"])

    def test_a_row_that_lost_its_caveats_fails(self):
        # the workbook is written from a table where the cheapest option claims nothing is unknown about it
        view, _, _ = self.P._as_asked(self.computed, {"instructions": {"apply": ["sort_by_price"]}})
        lying = copy.deepcopy(view)
        lying["items"][0]["candidates"][0]["open"] = []
        out = tempfile.mkdtemp()
        path = os.path.join(out, "c.xlsx")
        self.W.ComparisonWorkbook(lying, resolve({"id": "navy"}), dict(RUN)).write(path)
        got = self.P._asked_check(path, view)
        self.assertFalse(got["ok"], got)

    def test_an_english_round_has_its_words(self):
        c = copy.deepcopy(self.computed)
        c["lang"] = "en"
        res, path = self._render(c, ["sort_by_price"])
        self.assertTrue(res["passed"], json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:900])
        self.assertIn(self.P.words.t("en", "face.order.price_shown"), self._all_cells(path))
        ck = _checks(res)["xlsx.asked"]
        self.assertTrue(ck["ok"])
        self.assertEqual(ck["label"], "The table is as asked (read from the finished file)")
        self.assertIn("does not say that any option is better", self.P.words.t("en", "face.order.price_shown"))

    def test_its_manifest_declares_what_its_renderer_applies(self):
        m = _manifest_says(self, "procurement", self.P.DIRECTIVES, grouped=False)
        self.assertEqual(m["version"], "1.1.0")
        cannot = {d["id"]: d for d in m["instructions"]["cannot"]}
        self.assertIn("pick_best", cannot)
        self.assertIn("ไม่ตัดสินว่าตัวเลือกใดดีที่สุด", cannot["pick_best"]["why"])
        self.assertEqual(m["starts_from"], "request")
        # where a person sees it: the directive's own name says the order is the Excel file's, and what the
        # thread says when the files arrive begins with the card they chose from not being in that order
        order = m["instructions"]["can"][0]
        self.assertIn("ในไฟล์ Excel", order["label"])
        self.assertTrue(order["note"].startswith("การ์ดให้เลือกในแชทไม่ได้เรียงตามราคา"), order["note"][:80])
        self.assertTrue(m["en"][order["note"]].startswith("The card you choose from in the chat is not ordered by price"))


if __name__ == "__main__":
    unittest.main()

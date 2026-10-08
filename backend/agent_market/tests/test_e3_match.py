"""E3 — matching (AM5 phase D).

Two tables in, four groups out, every row with where it came from. What is
tested here is what the card asks of the engine:

  * only the exact tier accepts a pair by itself; every tier below it is a
    candidate a person confirms, and the pair records its pass, its tier and
    that a person decided;
  * tolerances are the install's settings, with defaults;
  * every row is in exactly one group;
  * the same input twice gives the same groups, byte for byte — in another
    process with another hash seed too;
  * the engine adds with Decimal, reads no clock and asks no model.
"""

import datetime as dt
import json
import os
import re
import subprocess
import sys
import unittest
from decimal import Decimal as D

from agent_market.engine import e3
from agent_market.engine.e3 import cards
from agent_market.engine.e3.match import Pass, Tolerance, run, tolerance_from
from agent_market.engine.e3.rows import Row, name_key, ref_key, similarity
from agent_market.engine.findings import display_len, validate

NAMES = {"left": "สมุดบัญชี", "right": "รายการเดินบัญชี"}


def row(side, n, amount, day, ref="", name="", month=9):
    rid = f"{'k' if side == 'left' else 'b'}{n}"
    return Row(id=rid, side=side, amount=D(amount), date=dt.date(2026, month, day) if day else None, ref=ref, name=name,
               source={"file": "สมุดบัญชี.xlsx" if side == "left" else "statement.xlsx", "sheet": "Sheet1", "row": n + 1,
                       "cells": {"amount": f"E{n + 1}"}})


def L(n, amount, day, ref="", name="", **kw):
    return row("left", n, amount, day, ref, name, **kw)


def R(n, amount, day, ref="", name="", **kw):
    return row("right", n, amount, day, ref, name, **kw)


def ids(entries, side):
    return [[r["id"] for r in e[side]] for e in entries]


def group_of(out, rid):
    found = []
    g = out["groups"]
    for name in ("matched", "suspect"):
        for e in g[name]:
            if rid in [r["id"] for r in e["left"] + e["right"]]:
                found.append(name)
    found += ["missing"] * sum(1 for r in g["missing"] if r["id"] == rid)
    found += ["extra"] * sum(1 for r in g["extra"] if r["id"] == rid)
    return found


class Keys(unittest.TestCase):
    def test_a_reference_is_compared_without_its_dress(self):
        self.assertEqual(ref_key("เช็ค 0012345"), ref_key("เช็ค๐๐12345"))
        self.assertEqual(ref_key("0012345"), "12345")
        self.assertEqual(ref_key("PV-6909/0012"), "PV69090012")
        self.assertEqual(ref_key("1"), "")          # too weak to tell two rows apart
        self.assertEqual(ref_key(None), "")

    def test_a_name_is_compared_without_its_legal_form(self):
        self.assertEqual(name_key("บริษัท เอเชีย คลาวด์ จำกัด"), name_key("บจก.เอเชียคลาวด์"))
        self.assertEqual(similarity("บริษัท เอเชีย คลาวด์ จำกัด", "บจก.เอเชียคลาวด์"), D("1.00"))
        self.assertEqual(similarity("", "x"), D("0.00"))
        self.assertLess(similarity("ร้านเครื่องเขียนสมใจ", "การไฟฟ้านครหลวง"), D("0.50"))
        # a name among the other words of a bank line
        self.assertGreaterEqual(similarity("โอนเข้า จาก เอเชีย คลาวด์ โซลูชั่นส์", "เอเชีย คลาวด์ โซลูชั่นส์"), D("0.80"))


class Tiers(unittest.TestCase):
    def test_the_exact_tier_accepts_by_rule(self):
        out = run([L(1, "-12000.00", 3, ref="0001201"), L(2, "-8500.00", 5, ref="1202"), L(3, "45000.00", 10, name="รับชำระ ลูกค้า ก")],
                  [R(1, "-12000.00", 3, ref="1201"), R(2, "-8500.00", 19, ref="0001202"), R(3, "45000.00", 10, name="เงินโอนเข้า")])
        m = out["groups"]["matched"]
        self.assertEqual([(e["pass"], e["tier"], e["confirmed"]) for e in m],
                         [("ref_amount_date", "exact", "rule"), ("ref_amount", "exact", "rule"), ("date_amount", "exact", "rule")])
        self.assertEqual(ids(m, "left"), [["k1"], ["k2"], ["k3"]])
        self.assertEqual(ids(m, "right"), [["b1"], ["b2"], ["b3"]])
        self.assertEqual(m[1]["days_apart"], 14)     # a cheque presented two weeks later is still that cheque
        self.assertEqual(out["counts"]["missing"] + out["counts"]["extra"] + out["counts"]["suspect"], 0)

    def test_every_tier_below_exact_is_a_candidate(self):
        out = run([L(1, "-3200.00", 3, name="ค่าเช่าเครื่องถ่ายเอกสาร"), L(2, "15000.00", 8, name="บจก.เอเชียคลาวด์ โซลูชั่นส์"),
                   L(3, "-990.00", 12, ref="PV6909/031")],
                  [R(1, "-3200.00", 5, name="หักบัญชี"), R(2, "15000.00", 20, name="โอนเข้า บริษัท เอเชีย คลาวด์ โซลูชั่นส์ จำกัด"),
                   R(3, "-1990.00", 12, ref="PV6909/031")])
        self.assertEqual(out["groups"]["matched"], [])
        s = out["groups"]["suspect"]
        self.assertEqual([(e["pass"], e["tier"]) for e in s],
                         [("amount_date", "amount_date"), ("ref_only", "fuzzy"), ("fuzzy_name", "fuzzy")])
        self.assertTrue(all(e["kind"] == "candidate" and e["decision"] is None for e in s))
        self.assertEqual(s[1]["difference"], "-1000.00")
        self.assertEqual(out["tiers"][0], {"tier": "exact", "label": "ตรงกันทุกตัว", "groups": 0, "by_rule": 0, "by_person": 0,
                                           "rows_left": 0, "rows_right": 0, "waiting": 0})

    def test_a_pass_below_exact_cannot_be_made_automatic(self):
        for tier in ("amount_date", "fuzzy"):
            with self.assertRaises(ValueError):
                Pass("mine", tier, "x", lambda *a: ([], {}), auto=True)
        with self.assertRaises(ValueError):
            Pass("mine", "sure", "x", lambda *a: ([], {}))
        for p in e3.DEFAULT_PASSES:
            self.assertEqual(p.auto, p.tier == "exact", p.id)

    def test_the_same_date_and_amount_is_by_rule_only_when_nothing_else_could_be_meant(self):
        left = [L(1, "-500.00", 3, name="ค่าส่งเอกสาร"), L(2, "-500.00", 4, name="ค่าส่งเอกสาร")]
        right = [R(1, "-500.00", 3, name="ถอน"), R(2, "-500.00", 4, name="ถอน")]
        out = run(left, right)
        self.assertEqual(out["groups"]["matched"], [])    # two of 500.00 within the window: a person decides
        self.assertEqual([(e["pass"], e["unique"]) for e in out["groups"]["suspect"]], [("amount_date", False)] * 2)
        self.assertEqual(ids(out["groups"]["suspect"], "right"), [["b1"], ["b2"]])     # the nearest date first
        # an organisation may turn the rule off: the pair becomes a candidate
        one = ([L(1, "-500.00", 3)], [R(1, "-500.00", 3)])
        self.assertEqual(run(*one)["groups"]["matched"][0]["pass"], "date_amount")
        strict = run(*one, tolerance=Tolerance(same_day=False))
        self.assertEqual(strict["groups"]["matched"], [])
        self.assertEqual(strict["groups"]["suspect"][0]["pass"], "amount_date")


class APersonDecides(unittest.TestCase):
    left = [L(1, "-3200.00", 3, name="ค่าเช่าเครื่องถ่ายเอกสาร")]
    right = [R(1, "-3200.00", 5, name="หักบัญชี"), R(2, "-3200.00", 6, name="หักบัญชี")]

    def test_confirmed(self):
        out = run(self.left, self.right, decisions={"k1~b1": "match"}, keys={"k1~b1": "e3_candidate:k1~b1"})
        m = out["groups"]["matched"]
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0]["pass"], m[0]["tier"], m[0]["confirmed"], m[0]["decision_key"]),
                         ("amount_date", "amount_date", "person", "e3_candidate:k1~b1"))
        self.assertEqual([r["id"] for r in out["groups"]["extra"]], ["b2"])
        self.assertEqual(out["tiers"][1]["by_person"], 1)

    def test_refused_is_never_offered_again_and_the_next_is(self):
        out = run(self.left, self.right, decisions={"k1~b1": "separate"})
        s = out["groups"]["suspect"]
        self.assertEqual([e["pair"] for e in s], ["k1~b2"])
        self.assertEqual([r["id"] for r in out["groups"]["extra"]], ["b1"])
        self.assertEqual(out["groups"]["extra"][0]["refused"], ["k1~b1"])
        out = run(self.left, self.right, decisions={"k1~b1": "separate", "k1~b2": "separate"})
        self.assertEqual(out["groups"]["suspect"], [])
        self.assertEqual([r["id"] for r in out["groups"]["missing"]], ["k1"])
        self.assertEqual(out["groups"]["missing"][0]["refused"], ["k1~b1", "k1~b2"])
        self.assertEqual(out["counts"]["refused"], 2)

    def test_skipped_stays_suspect_and_is_never_matched(self):
        out = run(self.left, self.right, decisions={"k1~b1": "undecided"})
        self.assertEqual(out["groups"]["matched"], [])
        self.assertEqual([(e["pair"], e["decision"]) for e in out["groups"]["suspect"]], [("k1~b1", "undecided")])

    def test_a_decision_the_engine_does_not_know_decides_nothing(self):
        for bad in ({"k1~b1": "yes"}, {"k1~b1": True}, {"all:amount_date": "match"}, {"k1": "match"}):
            out = run(self.left, self.right, decisions=bad)
            self.assertEqual(out["groups"]["matched"], [], bad)


class Shapes(unittest.TestCase):
    def test_one_reference_many_rows_is_exact(self):
        out = run([L(1, "-4000.00", 2, ref="PV-091"), L(2, "-6000.00", 2, ref="PV-091"), L(3, "-2500.00", 2, ref="PV-091")],
                  [R(1, "-12500.00", 4, ref="PV091")])
        m = out["groups"]["matched"]
        self.assertEqual([(e["pass"], e["shape"], e["confirmed"]) for e in m], [("ref_sum", "N:1", "rule")])
        self.assertEqual((m[0]["left_total"], m[0]["right_total"], m[0]["difference"]), ("-12500.00", "-12500.00", "0.00"))

    def test_a_sum_without_a_reference_is_a_candidate(self):
        left = [L(1, "12000.00", 9, name="รับเช็ค ลูกค้า ก"), L(2, "8000.00", 9, name="รับเช็ค ลูกค้า ข"),
                L(3, "5000.00", 10, name="รับเช็ค ลูกค้า ค"), L(4, "700.00", 9, name="ขายสด")]
        right = [R(1, "25000.00", 10, name="ฝากเช็ค 3 ฉบับ")]
        out = run(left, right)
        s = out["groups"]["suspect"]
        self.assertEqual([(e["pass"], e["tier"], e["shape"], e["pair"]) for e in s],
                         [("group_sum", "amount_date", "N:1", "k1+k2+k3~b1")])
        out = run(left, right, decisions={"k1+k2+k3~b1": "match"})
        self.assertEqual(out["groups"]["matched"][0]["confirmed"], "person")
        self.assertEqual([r["id"] for r in out["groups"]["missing"]], ["k4"])

    def test_many_to_many_by_the_day(self):
        left = [L(1, "1000.00", 15, name="ขายสด"), L(2, "2000.00", 15, name="ขายสด"), L(3, "3000.00", 15, name="ขายสด")]
        right = [R(1, "2500.00", 15, name="ฝากเงินสด"), R(2, "3500.00", 15, name="ฝากเงินสด")]
        out = run(left, right)
        s = out["groups"]["suspect"]
        self.assertEqual([(e["pass"], e["shape"]) for e in s], [("day_sum", "N:M")])
        self.assertEqual(s[0]["pair"], "k1+k2+k3~b1+b2")

    def test_the_same_line_twice_is_suspect(self):
        out = run([L(1, "-7000.00", 4, ref="PV-100"), L(2, "-7000.00", 4, ref="PV-100")], [R(1, "-7000.00", 4, ref="PV100")])
        self.assertEqual(ids(out["groups"]["matched"], "left"), [["k1"]])
        s = out["groups"]["suspect"]
        self.assertEqual([(e["kind"], e["same_as"]) for e in s], [("possible_duplicate", ["k1"])])
        self.assertEqual(out["groups"]["missing"], [])


class EveryRowInOneGroup(unittest.TestCase):
    def table(self):
        left, right = [], []
        for i in range(1, 41):
            amt = f"{-(1000 + 37 * i)}.00" if i % 3 else f"{2000 + 53 * i}.50"
            left.append(L(i, amt, 1 + i % 27, ref=f"PV-{i:03d}" if i % 4 else "", name=f"ผู้ขาย {i % 7}"))
            if i % 5:
                day = 1 + (i + (2 if i % 2 else 0)) % 27
                right.append(R(i, amt if i % 11 else f"{D(amt) + D('0.50')}", day, ref=f"PV{i:03d}" if i % 8 else "",
                               name=f"โอน ผู้ขาย {i % 7}"))
        right += [R(100, "-35.00", 28, name="ค่าธรรมเนียม"), R(101, "412.07", 28, name="ดอกเบี้ย")]
        return left, right

    def test_partition(self):
        left, right = self.table()
        for decisions in ({}, {"k5~b5": "match"}):
            out = run(left, right, decisions=decisions)
            for r in left + right:
                self.assertEqual(len(group_of(out, r.id)), 1, (r.id, group_of(out, r.id)))
            c = out["counts"]
            self.assertEqual(c["matched_left"] + c["suspect_left"] + c["missing"], len(left))
            self.assertEqual(c["matched_right"] + c["suspect_right"] + c["extra"], len(right))
            # every row says where it came from
            for e in out["groups"]["matched"] + out["groups"]["suspect"]:
                for r in e["left"] + e["right"]:
                    self.assertTrue(r["file"] and r["sheet"] and r["row"] and r["cells"], r)
            for r in out["groups"]["missing"] + out["groups"]["extra"]:
                self.assertTrue(r["file"] and r["row"], r)

    def test_the_same_input_twice_is_the_same_bytes(self):
        left, right = self.table()
        a = json.dumps(run(left, right), ensure_ascii=False, separators=(",", ":"))
        b = json.dumps(run(list(left), list(right)), ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(a, b)
        self.assertGreater(len(json.loads(a)["groups"]["matched"]), 10)

    def test_in_another_process_with_another_hash_seed(self):
        code = ("import sys, json, hashlib; sys.path.insert(0, sys.argv[1]);"
                "from agent_market.tests.test_e3_match import EveryRowInOneGroup as T;"
                "from agent_market.engine.e3 import run;"
                "l, r = T().table();"
                "print(hashlib.sha256(json.dumps(run(l, r, decisions={'k5~b5': 'match'}), ensure_ascii=False,"
                " separators=(',', ':')).encode()).hexdigest())")
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        seen = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1")
            p = subprocess.run([sys.executable, "-c", code, root], capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr[-400:])
            seen.add(p.stdout.strip())
        self.assertEqual(len(seen), 1, seen)

    def test_rows_the_engine_refuses(self):
        with self.assertRaises(ValueError):
            run([L(1, "1.00", 1), L(1, "2.00", 1)], [])                       # one id, two rows
        with self.assertRaises(ValueError):
            run([Row(id="k1", side="left", amount=1.5)], [])                   # a float is not money
        with self.assertRaises(ValueError):
            run([Row(id="k1", side="left", amount=D("1.005"))], [])
        with self.assertRaises(ValueError):
            run([Row(id="x", side="left", amount=D("1.00"))], [Row(id="x", side="right", amount=D("1.00"))])
        with self.assertRaises(ValueError):
            run([Row(id="k1~b1", side="left", amount=D("1.00"))], [])


class Tolerances(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(Tolerance().to_dict(), {"amount_tolerance": "1.00", "date_window_days": 3, "fuzzy_threshold": "0.80",
                                                 "fuzzy_window_days": 35, "ask_above": "50000.00", "max_group": 4,
                                                 "auto_same_day": True})
        self.assertEqual(tolerance_from(None), (Tolerance(), []))
        self.assertEqual(tolerance_from({}), (Tolerance(), []))

    def test_an_install_sets_its_own(self):
        t, bad = tolerance_from({"amount_tolerance": "0", "date_window_days": 7, "fuzzy_threshold": "0.9", "ask_above": "10,000",
                                 "max_group": "3", "auto_same_day": False})
        self.assertEqual(bad, [])
        self.assertEqual((t.amount, t.days, t.fuzzy, t.ask_above, t.group, t.same_day),
                         (D("0.00"), 7, D("0.90"), D("10000.00"), 3, False))

    def test_a_value_that_cannot_be_used_takes_its_default_and_is_named(self):
        t, bad = tolerance_from({"amount_tolerance": "มาก", "date_window_days": 400, "fuzzy_threshold": "0.1", "max_group": 2.5,
                                 "auto_same_day": "บางครั้ง", "ask_above": "-1", "something_else": 1})
        self.assertEqual(t, Tolerance())
        self.assertEqual(sorted(bad), ["amount_tolerance", "ask_above", "auto_same_day", "date_window_days", "fuzzy_threshold",
                                       "max_group"])

    def test_the_window_and_the_amount_decide_what_is_offered(self):
        left, right = [L(1, "-1000.00", 3, name="ก")], [R(1, "-1000.75", 8, name="ข")]
        self.assertEqual(run(left, right)["groups"]["suspect"], [])                 # five days apart: outside 3
        wide = run(left, right, tolerance=Tolerance(days=7))
        self.assertEqual([(e["pass"], e["difference"], e["days_apart"]) for e in wide["groups"]["suspect"]],
                         [("amount_date", "-0.75", 5)])
        self.assertEqual(run(left, right, tolerance=Tolerance(days=7, amount=D("0.50")))["groups"]["suspect"], [])
        self.assertEqual(wide["tolerance"]["date_window_days"], 7)

    def test_every_setting_has_thai_words(self):
        from agent_market.engine.e3.match import SETTING_TH, SETTINGS

        self.assertEqual(set(SETTING_TH), set(SETTINGS))


class Cards(unittest.TestCase):
    def test_a_candidate_is_a_question_a_person_can_answer(self):
        out = run([L(1, "-3200.00", 3, name="ค่าเช่าเครื่องถ่ายเอกสาร")], [R(1, "-3200.00", 5, name="ONLINE SHOPPING PIN 4411")])
        fs = cards.findings(out, {}, NAMES)
        self.assertEqual([f.key for f in fs], ["e3_candidate:k1~b1"])
        f = fs[0]
        validate(f)
        self.assertLessEqual(display_len(f.header), 12)
        self.assertEqual(f.severity, "blocking")
        self.assertEqual([o.value for o in f.options], ["match", "separate", "undecided"])
        self.assertEqual(f.default, "undecided")           # a skipped card never matches a pair
        self.assertEqual(f.recommended, "match")           # equal to the satang, one possible partner
        self.assertFalse(f.rememberable)
        self.assertIn("3,200.00 บาท", f.question)
        self.assertIn("วันที่ห่างกัน 2 วัน", f.question)
        self.assertNotIn("PIN", f.question)                # a word no card may say is masked, the run goes on
        self.assertEqual(f.evidence["columns"], cards.columns())
        self.assertEqual([r[0] for r in f.evidence["rows"]], ["สมุดบัญชี", "รายการเดินบัญชี"])
        self.assertEqual(f.evidence["rows"][0][5:], ["สมุดบัญชี.xlsx", 2])

    def test_what_is_recommended(self):
        out = run([L(1, "-3200.00", 3, name="ก"), L(2, "990.00", 9, ref="IV-1"), L(3, "5000.00", 20, name="บจก.เอเชียคลาวด์")],
                  [R(1, "-3200.50", 3, name="ข"), R(2, "1990.00", 9, ref="IV1"), R(3, "5000.00", 1, name="เอเชีย คลาวด์", month=10)])
        by = {f.subject: f for f in cards.findings(out, {}, NAMES)}
        self.assertEqual(sorted(by), ["k1~b1", "k2~b2", "k3~b3"])
        self.assertTrue(all(f.recommended is None for f in by.values()))      # a difference, a reference only, a resemblance
        self.assertIn("ยอดต่างกัน 0.50 บาท", by["k1~b1"].question)
        self.assertIn("เลขอ้างอิงเดียวกัน แต่ยอดต่างกัน 1,000.00 บาท", by["k2~b2"].question)
        self.assertIn("คล้ายกัน 100%", by["k3~b3"].question)

    def sure_pairs(self, n):
        left = [L(i, f"-{1000 + 10 * i}.00", 3 + (i % 20), name=f"จ่าย {i}") for i in range(1, n + 1)]
        right = [R(i, f"-{1000 + 10 * i}.00", 4 + (i % 20), name=f"ถอน {i}") for i in range(1, n + 1)]
        return left, right

    def test_pairs_a_pass_is_sure_about_are_confirmed_by_one_answer_that_names_them(self):
        left, right = self.sure_pairs(5)
        out = run(left, right)
        fs = cards.findings(out, {}, NAMES)
        self.assertEqual(len(fs), 1)
        f = fs[0]
        validate(f)
        self.assertEqual(f.code, "e3_confirm_all")
        self.assertEqual(f.subject, "k1~b1;k2~b2;k3~b3;k4~b4;k5~b5")
        self.assertEqual([o.value for o in f.options], ["match", "one_by_one", "undecided"])
        self.assertEqual(f.options[0].label, "ยืนยันทั้ง 5 คู่")
        self.assertIsNone(f.recommended)                   # the agent does not advise confirming unseen
        self.assertEqual(len(f.evidence["rows"]), 5)
        # confirmed: each named pair is matched, and says which answer confirmed it
        answers = {f.key: "match"}
        d, keys, _ = cards.decisions_from(answers)
        done = run(left, right, decisions=d, keys=keys)
        self.assertEqual([(m["confirmed"], m["decision_key"]) for m in done["groups"]["matched"]], [("person", f.key)] * 5)
        self.assertEqual(cards.findings(done, answers, NAMES), [])
        # a pair that was not on the list is not covered by that answer
        more_l, more_r = self.sure_pairs(6)
        later = run(more_l, more_r, decisions=d, keys=keys)
        self.assertEqual(len(later["groups"]["matched"]), 5)
        self.assertEqual([f.key for f in cards.findings(later, answers, NAMES)], ["e3_candidate:k6~b6"])

    def test_one_by_one_and_skipped(self):
        left, right = self.sure_pairs(4)
        out = run(left, right)
        key = cards.findings(out, {}, NAMES)[0].key
        d, keys, _ = cards.decisions_from({key: "one_by_one"})
        self.assertEqual(d, {})
        each = cards.findings(run(left, right, decisions=d), {key: "one_by_one"}, NAMES)
        self.assertEqual([f.key for f in each], [f"e3_candidate:k{i}~b{i}" for i in range(1, 5)])
        # skipped: nothing is matched, nothing is asked again
        d, keys, _ = cards.decisions_from({key: "undecided"})
        skipped = run(left, right, decisions=d, keys=keys)
        self.assertEqual(skipped["groups"]["matched"], [])
        self.assertEqual([s["decision"] for s in skipped["groups"]["suspect"]], ["undecided"] * 4)
        self.assertEqual(cards.findings(skipped, {key: "undecided"}, NAMES), [])
        # an answer about one pair has the last word
        d, _, _ = cards.decisions_from({key: "match", "e3_candidate:k2~b2": "separate"})
        self.assertEqual((d["k1~b1"], d["k2~b2"]), ("match", "separate"))

    def test_two_sure_pairs_are_asked_one_by_one(self):
        left, right = self.sure_pairs(2)
        self.assertEqual([f.code for f in cards.findings(run(left, right), {}, NAMES)], ["e3_candidate"] * 2)

    def test_a_large_row_without_a_partner_is_asked_about(self):
        left = [L(1, "-75000.00", 3, name="จ่ายค่าเครื่องจักร"), L(2, "-120.00", 3, name="ค่าส่งเอกสาร")]
        right = [R(1, "50000.00", 9, name="เงินโอนเข้า")]
        out = run(left, right)
        fs = cards.findings(out, {}, NAMES)
        self.assertEqual([f.key for f in fs], ["e3_unmatched:k1", "e3_unmatched:b1"])
        for f in fs:
            validate(f)
        self.assertIn("ไม่พบคู่ในรายการเดินบัญชี", fs[0].question)
        self.assertEqual(fs[0].default, "review")
        every = cards.findings(out, {}, NAMES, tolerance=Tolerance(ask_above=D("0.00")))
        self.assertEqual(len(every), 3)
        self.assertEqual(cards.findings(out, {}, NAMES, lone=None), [])
        self.assertEqual(cards.lone_answers({"e3_unmatched:k1": "keep", "other": 1}), {"k1": "keep"})

    def test_the_codes_carry_their_subject(self):
        from agent_market.engine.findings import MULTI_INSTANCE

        for code in cards.CODES:
            self.assertIn(code, MULTI_INSTANCE)


class NoModelNoClock(unittest.TestCase):
    def test_the_engine_reads_no_clock_no_chance_and_no_model(self):
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine", "e3")
        names = sorted(n for n in os.listdir(base) if n.endswith(".py"))
        self.assertEqual(names, ["__init__.py", "cards.py", "match.py", "rows.py"])
        for n in names:
            with open(os.path.join(base, n), encoding="utf-8") as f:
                src = f.read()
            code = re.sub(r'"""[\s\S]*?"""', "", src)
            for banned in (r"\brandom\b", r"\btime\b\.", r"\.now\(", r"\.today\(", r"\brequests\b", r"\bhttpx\b", r"\burllib\b",
                           r"\bopenai\b", r"\bfloat\("):
                self.assertIsNone(re.search(banned, code, re.M), (n, banned))

    def test_money_is_decimal_to_the_satang(self):
        out = run([L(1, "0.10", 1, ref="A-100"), L(2, "0.20", 1, ref="A-100")], [R(1, "0.30", 1, ref="A100")])
        self.assertEqual(out["groups"]["matched"][0]["difference"], "0.00")      # 0.1 + 0.2 is 0.3 here


if __name__ == "__main__":
    unittest.main()

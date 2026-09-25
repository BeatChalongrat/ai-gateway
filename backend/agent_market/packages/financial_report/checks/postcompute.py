"""Postcompute checks — only what the numbers reveal.

unbalanced · duplicate_suspect · materiality_exceeded · prior_period_mismatch.
They read the same prepared rows the compute used (scripts/rules.py), so the
rows a question is about are exactly the rows an answer acts on.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.findings import Finding, finding
from ....engine.ledger import ParsedFile, account_digits
from ....engine.thai import fmt_int, fmt_money, period_label_th
from ..scripts.metrics import prepare
from ..scripts.rules import duplicate_docs, expense_total, large_entries, unbalanced_docs


def run(files: list[ParsedFile], roles: dict, period: str, computed: dict, *, answers: dict | None = None,
        defaults: dict | None = None) -> dict:
    answers = answers or {}
    defaults = defaults or {}
    prep = prepare(files, roles, period, answers, defaults)
    current = prep["current"]
    primary = prep["primary"]
    out: list[Finding] = []

    # unbalanced — GL and TB only; a P&L export has no double entry to check.
    bal = computed["balance"]
    if bal["applicable"] and Decimal(bal["diff"]) != 0 and "unbalanced" not in answers:
        docs = unbalanced_docs(current)
        diff = Decimal(bal["diff"])
        side = "เดบิตมากกว่าเครดิต" if diff > 0 else "เครดิตมากกว่าเดบิต"
        out.append(finding(
            "unbalanced", "blocking", "postcompute", "ไม่สมดุล",
            f"ยอดรวมเดบิต {fmt_money(Decimal(bal['debit']))} แต่เครดิต {fmt_money(Decimal(bal['credit']))} "
            f"({side} {fmt_money(abs(diff))} บาท)"
            + (f" เอกสารที่ไม่สมดุลคือ {', '.join(d['doc'] for d in docs[:3])}" if docs else "")
            + " จะให้ทำอย่างไรครับ",
            [("ทำต่อ แสดงส่วนต่างในรายงาน", "continue", "รายงานจะระบุว่าไม่สมดุลและต่างกันเท่าไร"),
             ("หยุดก่อน จะส่งไฟล์ที่แก้แล้ว", "replace", "ผมจะรอไฟล์ใหม่ แล้วคำนวณใหม่")],
            "continue",
            evidence={"file": primary.name, "debit": bal["debit"], "credit": bal["credit"], "diff": bal["diff"],
                      "docs": docs[:10],
                      "rows": [[r.row, r.date.isoformat() if r.date else None, r.doc_no, r.account_code,
                                r.description[:40], str(r.debit), str(r.credit)]
                               for r in current if r.doc_no in {d["doc"] for d in docs[:3]}][:30],
                      "columns": ["แถว", "วันที่", "เลขที่", "บัญชี", "คำอธิบาย", "เดบิต", "เครดิต"]},
            facts={"diff": bal["diff"]},
        ))

    # duplicate_suspect
    if "duplicate_suspect" not in answers:
        groups = duplicate_docs(current)
        if groups:
            n_rows = sum(len(g["rows"]) for g in groups)
            g0 = groups[0]
            amount = sum((Decimal(g["amount"]) for g in groups), Decimal(0))
            desc = (f"เอกสาร {g0['copies'][0]} ซ้ำกับ {g0['original']} ทุกบรรทัด (วันเดียวกัน ยอด {fmt_money(Decimal(g0['amount']))} บาท)"
                    if len(groups) == 1 else f"พบ {len(groups)} ชุดที่ซ้ำกันทุกบรรทัด รวม {fmt_money(amount)} บาท")
            out.append(finding(
                "duplicate_suspect", "blocking", "postcompute", "รายการซ้ำ",
                f"{desc} น่าจะบันทึกซ้ำ จะให้ตัดชุดที่ซ้ำออกไหมครับ",
                [("ตัดรายการซ้ำออก", "exclude", f"ไม่นับ {fmt_int(n_rows)} แถวที่ซ้ำ"),
                 ("ไม่ใช่รายการซ้ำ นับทั้งหมด", "keep", "ใช้ตัวเลขตามไฟล์")],
                "exclude",
                evidence={"file": primary.name, "groups": groups[:10],
                          "rows": [[r.row, r.date.isoformat() if r.date else None, r.doc_no, r.account_code,
                                    r.description[:40], str(r.debit), str(r.credit)]
                                   for r in current if r.row in set(g0["rows"]) | set(g0["original_rows"])][:30],
                          "columns": ["แถว", "วันที่", "เลขที่", "บัญชี", "คำอธิบาย", "เดบิต", "เครดิต"]},
                facts={"groups": len(groups), "amount": str(amount)},
            ))

    # materiality_exceeded
    if "materiality_exceeded" not in answers:
        prior_by = {}
        for a in computed["accounts"]:
            if a["prior"] is not None:
                prior_by[a["code"]] = a["prior"]
        flagged = large_entries(current, expense_total(current), prior_by)
        if flagged:
            f0 = flagged[0]
            amt = Decimal(f0["amount"])
            share = f0.get("share_of_expenses")
            out.append(finding(
                "materiality_exceeded", "blocking", "postcompute", "ยอดผิดปกติ",
                f"รายการเดียวในบัญชี {f0['account']} ยอด {fmt_money(amt)} บาท "
                f"สูงกว่ารายการปกติของบัญชีนี้ราว {(amt / Decimal(f0['median'])).quantize(Decimal('1'))} เท่า"
                + (f" (คิดเป็น {share}% ของค่าใช้จ่ายทั้งเดือน)" if share else "")
                + " ตัวเลขนี้ถูกต้องไหมครับ",
                [("ถูกต้อง ใช้ตามไฟล์", "keep", "จะอยู่ในรายการที่ต้องตรวจของรายงาน"),
                 ("ไม่นับรายการนี้ในรอบนี้", "exclude", "ตัดเอกสารนี้ออกทั้งใบ")],
                "keep",
                evidence={"file": primary.name, "entries": flagged[:10],
                          "rows": [[f["row"], f["date"], f["doc"], f["account"], (f["description"] or "")[:40], f["amount"]]
                                   for f in flagged[:10]],
                          "columns": ["แถว", "วันที่", "เลขที่", "บัญชี", "คำอธิบาย", "จำนวนเงิน"]},
                facts={"account": f0["account"], "amount": f0["amount"]},
            ))

    # prior_period_mismatch
    prior_file = prep["prior_file"]
    if prior_file is not None and "prior_period_mismatch" not in answers:
        from collections import Counter

        months = Counter(r.period for r in prior_file.rows if r.period)
        actual = months.most_common(1)[0][0] if months else prior_file.period_hint
        expected = computed.get("prior_period") or None
        from ....engine.thai import prev_period

        want = prev_period(period)
        cur_codes = {account_digits(r.account_code) for r in current}
        pri_codes = {account_digits(r.account_code) for r in prior_file.rows}
        overlap = len(cur_codes & pri_codes) / max(1, len(cur_codes | pri_codes))
        reason = None
        if actual and actual != want:
            reason = (f"ไฟล์ “{prior_file.name}” ที่ส่งมาเป็นงวดก่อน เป็นข้อมูล{period_label_th(actual)} "
                      f"ไม่ใช่{period_label_th(want)}")
        elif overlap < 0.5:
            reason = (f"ไฟล์ “{prior_file.name}” ใช้รหัสบัญชีต่างจากเดือนนี้มาก (ตรงกันเพียง {int(overlap * 100)}%) "
                      "อาจเป็นผังบัญชีคนละชุด")
        if reason:
            out.append(finding(
                "prior_period_mismatch", "blocking", "postcompute", "งวดก่อน",
                reason + " จะให้เทียบต่อหรือไม่เทียบครับ",
                [("ไม่เทียบกับงวดก่อน", "ignore_prior", "รายงานจะไม่มีคอลัมน์งวดก่อน"),
                 ("ใช้เทียบต่อ", "use_anyway", f"ระบุในรายงานว่าเทียบกับ{period_label_th(actual) if actual else 'ไฟล์นี้'}")],
                "ignore_prior",
                evidence={"file": prior_file.name, "months": dict(months), "expected": want, "actual": actual,
                          "account_overlap": round(overlap, 2)},
                facts={"actual": actual, "expected": want},
            ))
        _ = expected

    return {"findings": [f.to_dict() for f in out]}

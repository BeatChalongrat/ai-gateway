"""One content model, many formats.

The report (.docx), the deck (.pptx) and the workbook are all
rendered from the model built here, so a number, its citation and its
wording are identical in every file of a run — and in any file re-rendered
later from the same computed table.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal

from ..engine.thai import MONTHS_TH_ABBR, fmt_millions, fmt_money, fmt_pct

REQUIRED_SECTIONS = ("summary", "pl", "exceptions", "insights", "assumptions", "sources", "citations")


def _d(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def money0(v) -> str:
    return "—" if v is None else fmt_money(Decimal(str(v)), 0)


def pct_or_dash(v, signed=True) -> str:
    return "—" if v is None else fmt_pct(Decimal(str(v)), 1, signed=signed)


def date_th(d: _dt.date) -> str:
    return f"{d.day} {MONTHS_TH_ABBR[d.month - 1]} {d.year + 543}"


# ── citation markers in prose ─────────────────────────────────────────────

_NUM = re.compile(r"[-+]?\(?\d[\d,]*(?:\.\d+)?\)?(?:\s?%|\s?ล้าน)?")


def mark_citations(text: str, computed: dict) -> list[tuple[str, str | None]]:
    """Split prose into (text, cite_id) runs: each quoted number gets its cite.

    A number is matched against the computed table's pre-formatted strings;
    the Go guard already refused any number that is not one of them.
    """
    quotable = computed.get("quotable", {})
    cites = computed.get("quotable_cites", {})
    by_text: dict[str, str] = {}
    for key, s in quotable.items():
        if key in cites:
            by_text.setdefault(s.replace(" ", ""), cites[key])
    out: list[tuple[str, str | None]] = []
    pos = 0
    for m in _NUM.finditer(text):
        token = m.group(0).replace(" ", "")
        # Only a balanced "(1,234)" is an accounting negative; "(12.48 ล้าน" is
        # a number inside ordinary parentheses (same rule as the Go guard).
        if token.startswith("(") and not token.endswith(")"):
            token = token[1:]
        elif token.endswith(")") and not token.startswith("("):
            token = token[:-1]
        cid = by_text.get(token) or by_text.get(token.replace("+", ""))
        if cid:
            out.append((text[pos:m.end()], cid))
            pos = m.end()
    out.append((text[pos:], None))
    return [(t, c) for t, c in out if t]


# ── the model ─────────────────────────────────────────────────────────────

def _line(computed, key):
    return next(l for l in computed["lines"] if l["key"] == key)


def _ratio(computed, key):
    return next(r for r in computed["ratios"] if r["key"] == key)


def kpis(computed: dict) -> list[dict]:
    has_prior = computed["has_prior"]
    out = []
    for key, label, good_when_up in (("revenue", "รายได้จากการขายและบริการ", True),
                                     ("total_expenses", "ค่าใช้จ่ายรวม", False),
                                     ("net_profit", "กำไรสุทธิ", True)):
        l = _line(computed, key)
        delta = None
        tone = "flat"
        if has_prior and l["var_prior_pct"] is not None:
            v = Decimal(l["var_prior_pct"])
            delta = f"{fmt_pct(v, 1, signed=True)} จาก{computed['prior_label']}"
            tone = "up" if v > 0 else ("down" if v < 0 else "flat")
        elif computed["has_budget"] and l["var_budget_pct"] is not None:
            v = Decimal(l["var_budget_pct"])
            delta = f"{fmt_pct(v, 1, signed=True)} เทียบงบประมาณ"
            tone = "up" if v > 0 else ("down" if v < 0 else "flat")
        good = None if tone == "flat" else ((tone == "up") == good_when_up)
        out.append({"key": key, "label": label, "value": money0(l["current"]),
                    "value_short": fmt_millions(Decimal(l["current"])), "delta": delta, "tone": tone,
                    "good": good, "cite": l["cite"]})
    nm = _ratio(computed, "net_margin")
    delta = f"{Decimal(nm['change_points']):+.1f} จุด" if nm["change_points"] is not None else None
    tone = "flat" if not nm["change_points"] else ("up" if Decimal(nm["change_points"]) > 0 else "down")
    out.append({"key": "net_margin", "label": "อัตรากำไรสุทธิ", "value": pct_or_dash(nm["current"], signed=False),
                "value_short": pct_or_dash(nm["current"], signed=False), "delta": delta, "tone": tone,
                "good": None if tone == "flat" else tone == "up", "cite": nm["cite"]})
    return out


def pl_table(computed: dict) -> dict:
    has_prior, has_budget = computed["has_prior"], computed["has_budget"]
    cols = ["รายการ", "งวดนี้"]
    if has_prior:
        cols += [computed["prior_label"] or "งวดก่อน", "เปลี่ยนแปลง", "%"]
    if has_budget:
        cols += ["งบประมาณ", "ต่างจากงบ"]
    cols.append("อ้างอิง")
    order = ["revenue", "other_income", "cost_of_sales", "gross_profit", "operating_expenses", "finance_costs",
             "income_tax", "total_expenses", "net_profit"]
    emphasis = {"gross_profit", "net_profit"}
    rows, strong = [], []
    for key in order:
        l = _line(computed, key)
        if key in ("income_tax", "other_income", "finance_costs") and Decimal(l["current"]) == 0 and \
                (l["prior"] is None or Decimal(l["prior"]) == 0):
            continue
        r = [l["label"], money0(l["current"])]
        if has_prior:
            r += [money0(l["prior"]), money0(l["var_prior"]), pct_or_dash(l["var_prior_pct"])]
        if has_budget:
            r += [money0(l["budget"]), money0(l["var_budget"])]
        r.append(l["cite"])
        if key in emphasis:
            strong.append(len(rows))
        rows.append(r)
    for rt in computed["ratios"]:
        r = [rt["label"], pct_or_dash(rt["current"], signed=False)]
        if has_prior:
            r += [pct_or_dash(rt["prior"], signed=False),
                  "—" if rt["change_points"] is None else f"{Decimal(rt['change_points']):+.1f} จุด", ""]
        if has_budget:
            r += ["", ""]
        r.append(rt["cite"])
        rows.append(r)
    return {"type": "table", "columns": cols, "rows": rows, "strong_rows": strong,
            "numeric_from": 1, "cite_col": len(cols) - 1,
            "caption": "หน่วย: บาท · ตัวเลขทุกช่องคำนวณด้วย " + computed["script"]}


def account_table(computed: dict, limit: int = 40) -> dict:
    has_prior, has_budget = computed["has_prior"], computed["has_budget"]
    cols = ["รหัส", "ชื่อบัญชี", "งวดนี้"]
    if has_prior:
        cols += ["งวดก่อน", "%"]
    if has_budget:
        cols += ["งบประมาณ", "ต่างจากงบ"]
    cols.append("อ้างอิง")
    rows = []
    for a in computed["accounts"][:limit]:
        r = [a["code"], a["name"] or "—", money0(a["current"])]
        if has_prior:
            r += [money0(a["prior"]), pct_or_dash(a["var_prior_pct"])]
        if has_budget:
            r += [money0(a["budget"]), money0(a["var_budget"])]
        r.append(a["cite"])
        rows.append(r)
    return {"type": "table", "columns": cols, "rows": rows, "strong_rows": [], "numeric_from": 2,
            "cite_col": len(cols) - 1, "caption": "หน่วย: บาท", "small": True}


def movement_table(computed: dict) -> dict | None:
    if not computed["top_movements"]:
        return None
    has_prior = computed["has_prior"]
    cols = ["บัญชี", "งวดนี้"] + (["งวดก่อน", "เปลี่ยนแปลง", "%"] if has_prior else []) + ["อ้างอิง"]
    rows = []
    for m in computed["top_movements"]:
        r = [f"{m['code']} {m['name']}".strip(), money0(m["current"])]
        if has_prior:
            r += [money0(m["prior"]), money0(m["change"]), pct_or_dash(m["change_pct"])]
        r.append(m["cite"])
        rows.append(r)
    return {"type": "table", "columns": cols, "rows": rows, "strong_rows": [], "numeric_from": 1,
            "cite_col": len(cols) - 1,
            "caption": "เรียงตามขนาดการเปลี่ยนแปลงจากงวดก่อน" if has_prior else "ค่าใช้จ่ายที่ใหญ่ที่สุดของงวด"}


EXC_LABEL = {
    "budget_variance_high": "สูงกว่างบมาก", "expense_spike": "เพิ่มขึ้นผิดปกติ", "new_account": "บัญชีใหม่",
    "negative_revenue": "รายได้ติดลบ", "large_single_entry": "รายการเดียวยอดสูง", "unbalanced_ledger": "ไม่สมดุล",
}


def exceptions_table(computed: dict) -> dict:
    rows = []
    src = computed["source"]["primary"]["file"]
    for e in computed["exceptions"]:
        where = f"{src} แถว {', '.join(str(r) for r in e['rows'][:6])}" + (" …" if len(e["rows"]) > 6 else "") \
            if e["rows"] else src
        rows.append([e["code"], EXC_LABEL.get(e["code"], e["code"]) + ": " + e["title"], e["detail"], where,
                     e.get("cite") or ""])
    return {"type": "table", "columns": ["รหัสเหตุผล", "รายการ", "รายละเอียด", "แถวต้นทาง", "อ้างอิง"],
            "rows": rows, "strong_rows": [], "numeric_from": None, "cite_col": 4, "small": True,
            "empty_text": "ไม่พบรายการผิดปกติตามกฎที่ตั้งไว้ (เกณฑ์ความมีสาระสำคัญ "
                          f"{money0(computed['materiality'])} บาท)"}


def charts(computed: dict) -> list[dict]:
    """Chart specs whose values are taken verbatim from the computed table."""
    out = []
    t = computed["trend"]
    if len(t["months"]) >= 2:
        out.append({
            "id": "trend", "kind": "line", "title": f"แนวโน้ม {len(t['months'])} เดือน",
            "subtitle": "หน่วย: ล้านบาท", "categories": t["labels"], "scale": 1_000_000,
            "series": [{"name": "รายได้", "values": t["revenue"], "path": "trend.revenue"},
                       {"name": "ค่าใช้จ่ายรวม", "values": t["total_expenses"], "path": "trend.total_expenses"},
                       {"name": "กำไรสุทธิ", "values": t["net_profit"], "path": "trend.net_profit"}],
            "cite": t["cite"],
        })
    if computed["has_budget"]:
        rows = [a for a in computed["accounts"] if a["var_budget"] is not None and a["group"] not in ("revenue", "other_income")
                and Decimal(a["var_budget"]) != 0]
        rows.sort(key=lambda a: -abs(Decimal(a["var_budget"])))
        rows = rows[:7]
        if rows:
            out.append({
                "id": "budget_variance", "kind": "barh", "title": "ค่าใช้จ่ายเทียบงบประมาณ",
                "subtitle": "ส่วนต่างจากงบ (บาท) · บวก = ใช้เกินงบ",
                "categories": [a["name"] or a["code"] for a in rows], "scale": 1,
                "series": [{"name": "ต่างจากงบ", "values": [a["var_budget"] for a in rows],
                            "path": "accounts[].var_budget", "codes": [a["code"] for a in rows]}],
                "cite": rows[0]["cite"], "signed": True,
            })
    comp = computed["composition"]
    if comp:
        out.append({
            "id": "composition", "kind": "barh", "title": "องค์ประกอบค่าใช้จ่ายของงวด",
            "subtitle": "หน่วย: ล้านบาท", "categories": [c["name"] for c in comp], "scale": 1_000_000,
            "series": [{"name": "งวดนี้", "values": [c["amount"] for c in comp], "path": "composition[].amount"}],
            "cite": comp[0]["cite"],
        })
    return out


def apply_presentation(computed: dict, pres: dict) -> dict:
    """Cosmetic answers applied at render time (never in compute)."""
    from ..engine.thai import period_label_th

    if not pres:
        return computed
    c = dict(computed)
    # Keys are the cosmetic findings' codes (the runner passes cosmetic answers as-is).
    pres = {"year_style": pres.get("year_style") or pres.get("year_convention_mixed"),
            "account_names": pres.get("account_names") or pres.get("account_name_missing")}
    if pres.get("year_style") == "ce":
        c["period_label"] = period_label_th(c["period"], "ce")
        if c.get("prior_period"):
            c["prior_label"] = period_label_th(c["prior_period"], "ce")
    if pres.get("account_names") == "standard_names":
        from ..engine.ledger import account_digits
        from ..packages.financial_report.scripts.rules import STANDARD_NAMES

        def fill(items, key="name"):
            out = []
            for it in items:
                it = dict(it)
                if not it.get(key):
                    it[key] = STANDARD_NAMES.get(account_digits(it.get("code", ""))[:2], "")
                out.append(it)
            return out

        c["accounts"] = fill(c["accounts"])
        c["top_movements"] = fill(c["top_movements"])
        c["composition"] = [dict(x, name=x["name"] if x["name"] != x.get("code") else
                                 (STANDARD_NAMES.get(account_digits(x.get("code", ""))[:2]) or x["name"]))
                            for x in c["composition"]]
    return c


def sentences(narrative: dict, key: str) -> list:
    """One of a narrative's lists, as a list of sentences. A model may leave a
    list null, send one sentence instead of a list, or put something that is
    not a sentence among them (AM5: a null list reached the loop below and the
    round failed on it); none of that is the report's problem."""
    value = narrative.get(key)
    if not isinstance(value, (list, tuple)):
        return []
    return [t for t in value if isinstance(t, str) and t.strip()]


def build(computed: dict, req: dict) -> dict:
    computed = apply_presentation(computed, req.get("presentation") or {})
    narrative = dict(req.get("narrative") or computed["fallback_narrative"])
    for key in ("movements", "watch", "next_steps"):
        narrative[key] = sentences(narrative, key)  # the report and the deck both read these
    assumptions = list(req.get("assumptions") or [])
    for e in computed["excluded"]:
        assumptions.append({"code": e["code"], "source": "answer", "text": f"ไม่นับ{e['reason']}",
                            "changes_numbers": True})
    for n in computed["notes"]:
        assumptions.append({"code": n["code"], "source": "answer", "text": n["text"], "changes_numbers": True})
    if not computed["has_prior"]:
        assumptions.append({"code": "no_prior", "source": "data", "text": "ไม่มีข้อมูลงวดก่อน จึงไม่ได้เทียบการเปลี่ยนแปลงรายเดือน"})
    if not computed["has_budget"]:
        assumptions.append({"code": "no_budget", "source": "data", "text": "ไม่มีไฟล์งบประมาณ จึงไม่ได้เทียบกับงบ"})
    assumptions.append({"code": "materiality", "source": "rule",
                        "text": f"เกณฑ์ความมีสาระสำคัญสำหรับรายการที่ต้องตรวจ: {money0(computed['materiality'])} บาท"})
    # Deduplicate by text, keep order.
    seen, uniq = set(), []
    for a in assumptions:
        if a["text"] not in seen:
            seen.add(a["text"])
            uniq.append(a)
    assumptions = uniq

    run = req.get("run") or {}
    model = req.get("model_info") or {}
    today = _dt.date.fromisoformat(run.get("date")) if run.get("date") else _dt.date.today()
    theme_company = (req.get("theme") or {}).get("company") or ""
    src = computed["source"]
    compare_bits = []
    if computed["has_prior"]:
        compare_bits.append(computed["prior_label"])
    if computed["has_budget"]:
        compare_bits.append("งบประมาณ")
    doc_type_th = {"gl": "บัญชีแยกประเภท", "tb": "งบทดลอง", "pl": "งบกำไรขาดทุน"}.get(computed["doc_type"], "ไฟล์บัญชี")
    cover_lines = [f"จัดทำจาก{doc_type_th} {src['primary']['rows_used']:,} รายการ"]
    if compare_bits:
        cover_lines.append("เทียบกับ" + " และ".join(compare_bits))

    sources_rows = []
    for f in src["files"]:
        role = {"primary": "ข้อมูลหลัก", "prior": "งวดก่อน", "budget": "งบประมาณ", "ignored": "ไม่ได้ใช้",
                "unknown": "ไม่ได้ใช้"}.get(f["role"], f["role"] or "")
        sources_rows.append([f["file"], role, f"{f['rows']:,}", f["sha256"][:16] + "…"])
    citation_rows = []
    for c in computed["citations"]:
        if c["sources"]:
            where = "; ".join(f"{s['file']} · {s['sheet']} · {s['cells']}" for s in c["sources"])
        elif c.get("derived_from"):
            where = "คำนวณจาก " + ", ".join(c["derived_from"][:8]) + (" …" if len(c["derived_from"]) > 8 else "")
        else:
            where = "—"
        numeric = c["value"] not in (None, "") and re.match(r"^-?\d+(\.\d+)?$", str(c["value"]))
        if numeric and c.get("unit") == "pct":
            val = fmt_pct(Decimal(str(c["value"])), 1)  # a ratio, not baht ("อัตรากำไรสุทธิ 2.4%", not "2")
        else:
            val = money0(c["value"]) if numeric else (c["value"] or "")
        citation_rows.append([c["id"], c["label"], val, where, f"{c['script']} · {c['formula']}"])

    about = [
        f"โมเดลที่อ่านตัวเลขที่คำนวณแล้วเพื่อเขียนคำอธิบาย: {model.get('model') or 'ไม่มี (ใช้ข้อความจากกฎ)'}"
        + (" (รันในองค์กร)" if model.get("self_hosted") else (" (ผู้ให้บริการภายนอก)" if model.get("model") else "")),
        "โมเดลเห็นเฉพาะตารางตัวเลขที่คำนวณเสร็จแล้ว ไม่เห็นไฟล์ต้นฉบับทั้งไฟล์",
        f"การคำนวณ: {computed['script']} และ {computed['rules']} ด้วยเลขทศนิยมแม่นยำ (Decimal)",
        f"รอบการทำงาน: {run.get('id', '—')} · agent {run.get('agent_version', '—')} · ธีม {req.get('theme', {}).get('name', '')} v{req.get('theme', {}).get('version', 1)}",
        "agent นี้อ่านอย่างเดียว ไม่เขียนกลับระบบบัญชี ไม่ส่งอีเมล และไม่โอนเงิน",
    ]
    if narrative.get("written_by") == "rules":
        about.insert(1, "คำอธิบายในรายงานนี้เขียนจากกฎ เพราะไม่มีโมเดลที่ตอบได้ในรอบนี้")

    sections = [
        {"key": "summary", "title": "สรุปผู้บริหาร", "blocks": [
            {"type": "paragraph", "runs": mark_citations(narrative["summary"], computed)},
            {"type": "kpis"},
        ]},
        {"key": "pl", "title": "งบกำไรขาดทุน", "blocks": [pl_table(computed)]},
        {"key": "charts", "title": "แนวโน้มและองค์ประกอบ", "blocks": [{"type": "chart", "chart": c["id"]} for c in charts(computed)]},
        {"key": "movements", "title": "รายการเคลื่อนไหวสำคัญ", "blocks": [b for b in (
            movement_table(computed),
            {"type": "bullets", "items": [mark_citations(t, computed) for t in sentences(narrative, "movements")]}
            if sentences(narrative, "movements") else None,
        ) if b]},
        {"key": "accounts", "title": "รายละเอียดรายบัญชี", "blocks": [account_table(computed)]},
        {"key": "exceptions", "title": "รายการที่ต้องตรวจสอบ", "blocks": [
            {"type": "paragraph", "runs": [("รายการที่กฎของระบบตั้งข้อสังเกตไว้ ระบบไม่แก้ตัวเลขเอง รอผู้รับผิดชอบตัดสิน", None)], "muted": True},
            exceptions_table(computed)]},
        {"key": "insights", "title": "ข้อสังเกตและสิ่งที่ควรทำต่อ", "blocks": [
            {"type": "subheading", "text": "สิ่งที่ควรดู"},
            {"type": "bullets", "items": [mark_citations(t, computed) for t in sentences(narrative, "watch")]},
            {"type": "subheading", "text": "สิ่งที่ควรทำต่อ"},
            {"type": "bullets", "items": [mark_citations(t, computed) for t in sentences(narrative, "next_steps")]},
        ]},
        {"key": "assumptions", "title": "ข้อสมมติที่ใช้ในรอบนี้", "blocks": [
            {"type": "bullets", "items": [[(a["text"] + _assumption_tag(a), None)] for a in assumptions]},
        ]},
        {"key": "sources", "title": "ภาคผนวก ก · ที่มาของข้อมูล", "appendix": True, "blocks": [
            {"type": "table", "columns": ["ไฟล์", "บทบาท", "แถว", "SHA-256"], "rows": sources_rows,
             "strong_rows": [], "numeric_from": None, "cite_col": None, "small": True},
        ]},
        {"key": "citations", "title": "ภาคผนวก ข · ที่มาของตัวเลข", "appendix": True, "blocks": [
            {"type": "table", "columns": ["อ้างอิง", "ตัวเลข", "ค่า", "ไฟล์ · ชีต · ช่อง", "คำนวณโดย"],
             "rows": citation_rows, "strong_rows": [], "numeric_from": None, "cite_col": 0, "small": True},
        ]},
        {"key": "about", "title": "ภาคผนวก ค · ผู้จัดทำและขอบเขต", "appendix": True, "blocks": [
            {"type": "bullets", "items": [[(t, None)] for t in about]},
        ]},
    ]
    sections = [s for s in sections if s["blocks"]]
    return {
        "title": "รายงานผลประกอบการ",
        "subtitle": f"ประจำเดือน{computed['period_label']}",
        "company": theme_company,
        "prepared": f"จัดทำ {date_th(today)}",
        "cover_lines": cover_lines,
        "kpis": kpis(computed),
        "charts": charts(computed),
        "sections": sections,
        "assumptions": assumptions,
        "narrative": narrative,
        "source_note": f"ที่มา: {src['primary']['file']} (sha256 {src['primary']['sha256'][:8]}…) · {computed['script']}",
        "model": model,
        "computed": computed,
    }


def _assumption_tag(a: dict) -> str:
    return {"default": " — ใช้ค่าตั้งต้น เพราะยังไม่ได้ตอบ",
            "remembered": " — ค่าที่จำไว้จากรอบก่อน",
            "answer": " — ตามที่คุณตอบ",
            "reprompt_default": " — ใช้ค่าตั้งต้นหลังถามซ้ำ 2 ครั้ง"}.get(a.get("source", ""), "")

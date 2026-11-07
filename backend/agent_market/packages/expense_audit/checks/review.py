"""A run's uploads judged in the run's context — the one place preflight and compute share.

Context, applied the same way on every pass:
  * the install's policy table and holiday calendar (checks/policy.py) — the only source of a limit;
  * the uploads: claims sheets, receipts (read by E2), a KB policy document (context only);
  * the duplicate index — receipts claimed in earlier runs of this install (``history``);
  * a vision model's reading of a scanned or photographed receipt — a candidate only;
  * the person's answers: to the E2 cards about a receipt's total, and to the judgement cards.

The same file uploaded twice counts once (a receipt keeps every name it was uploaded under, so a
claim that names the copy still finds it — and the duplicate rule sees the same bytes twice).

AM4: a receipt is what it contains, not what it is called. A claim names its receipt's file in the
claims sheet; when no upload has that name (the file was renamed before it was sent), the one
unlinked receipt whose total and date equal the claim's is linked to it — and the files say so.
Two or more such receipts, or none: nothing is guessed; the claim asks for its receipt as before.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ....engine import words
from ....engine.thai import fmt_money
from ..words import W
from . import cards as CARDS
from . import policy as P
from . import receipts as RC
from . import rules as R
from .inputs import Upload


@dataclass
class Review:
    policy: P.Policy | None
    problems: list
    calendar: P.Calendar
    sheets: list                      # claims uploads
    receipts: dict                    # key → receipts.ReceiptReview
    views: dict                       # key → rules.ReceiptView
    kb: list                          # policy_document uploads
    others: list                      # uploads that are none of these
    same_file: list                   # (the copy, the upload it repeats)
    claims: list
    audits: list
    findings: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    context: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    missing_refs: list = field(default_factory=list)   # (claim, the file name it names) not uploaded
    unlinked: list = field(default_factory=list)        # receipts no claim names
    content_links: list = field(default_factory=list)   # AM4: (claim, the name it names, the receipt linked by content)


def _claims(sheets: list[Upload]) -> list[R.Claim]:
    rows = [(s, r) for s in sheets for r in s.data["rows"]]
    ids: dict[str, int] = {}
    for _, r in rows:
        if r.get("id"):
            ids[r["id"]] = ids.get(r["id"], 0) + 1
    out = []
    for s, r in rows:
        key = r["id"] if r.get("id") and ids[r["id"]] == 1 else f"{r.get('id') or 'row'}-{s.key}-r{r['row']}"
        out.append(R.Claim.from_row(r, sheet=s.name, sheet_name=s.data["sheet"], key=key))
    return out


def review(uploads: list[Upload], *, defaults: dict | None, answers: dict | None, today: _dt.date | None,
           history: list | None = None, proposals: dict | None = None, final: bool = False) -> Review:
    defaults, answers = defaults or {}, answers or {}
    policy, problems = P.load_policy(defaults)
    calendar = P.calendar_for(defaults)
    seen: dict[str, Upload] = {}
    same_file, kept = [], []
    aliases: dict[str, list[str]] = {}
    for u in uploads:
        if u.sha256 in seen:
            same_file.append((u, seen[u.sha256]))
            aliases.setdefault(seen[u.sha256].key, []).append(u.name)
            continue
        seen[u.sha256] = u
        kept.append(u)
    sheets = [u for u in kept if u.kind == "claims"]
    kb = [u for u in kept if u.kind == "policy_document"]
    others = [u for u in kept if u.kind == "other"]
    receipts = [u for u in kept if u.kind == "receipt"]
    claims = _claims(sheets)

    # which claim names which receipt (by file name, the same way the rules link them)
    names = {}
    for u in receipts:
        for n in [u.name] + aliases.get(u.key, []):
            names.setdefault(n.lower(), u)
            names.setdefault(n.lower().rsplit(".", 1)[0], u)
    vat, linked_keys, missing = {}, set(), []
    for c in claims:
        if not c.receipt_ref:
            continue
        low = c.receipt_ref.lower()
        u = names.get(low) or names.get(low.rsplit(".", 1)[0])
        if u is None:
            missing.append((c, c.receipt_ref))
            continue
        linked_keys.add(u.key)
        vat[u.key] = vat.get(u.key, False) or c.vat_claim
    # AM4: a renamed receipt — linked by what it says (total and date), only when exactly one fits
    content_links = []
    if missing:
        free = [u for u in receipts if u.key not in linked_keys]
        wanted = {}
        for c, ref in missing:
            wanted[id(c)] = [u for u in free if _same_receipt(u, c)]
        still = []
        for c, ref in missing:
            cands = [u for u in wanted[id(c)] if u.key not in linked_keys]
            # the receipt fits this claim alone, and this claim is the only one it fits
            if len(cands) == 1 and sum(1 for cc, _ in missing if cands[0] in wanted[id(cc)]) == 1:
                u = cands[0]
                linked_keys.add(u.key)
                vat[u.key] = vat.get(u.key, False) or c.vat_claim
                aliases.setdefault(u.key, []).append(ref)
                content_links.append((c, ref, u))
            else:
                still.append((c, ref))
        missing = still
    rr = RC.review(receipts, vat_claimed=vat, aliases=aliases, answers=answers, defaults=defaults, today=today,
                   proposals=proposals)
    views = {k: RC.view(r, final=final) for k, r in rr.items()}
    audits = R.audit(claims, policy=policy, calendar=calendar, receipts=list(views.values()), history=history) if policy else []
    rv = Review(policy, problems, calendar, sheets, rr, views, kb, others, same_file, claims, audits,
                missing_refs=missing, unlinked=[rr[u.key] for u in receipts if u.key not in linked_keys],
                content_links=content_links)
    if policy is not None:
        rv.context, rv.conflicts = P.kb_context(policy, kb, defaults.get("policy_kb"))

    # what is asked: the setup a run cannot go without, the receipts' totals, then judgement
    if policy is None:
        rv.findings.append(CARDS.no_policy(problems))
    if not sheets:
        rv.findings.append(CARDS.missing_claims([u.name for u in others if u.data.get("reason") == "unrecognised_sheet"]))
    for k in sorted(rr):  # content order (the key is the bytes' hash), not the file names (AM4)
        if k in linked_keys:
            rv.findings += rr[k].findings
    rv.findings += CARDS.cards_for(audits, answers)
    rv.assumptions = _assumptions(rv)
    return rv


def _field(u: Upload, name: str):
    f = ((u.data.get("extraction") or {}).get("fields") or {}).get(name) or {}
    return f.get("normalized")


def _same_receipt(u: Upload, c) -> bool:
    """The receipt's own total and date are the claim's (a link by content, never by name)."""
    total, date = _field(u, "total"), _field(u, "document_date")
    if total is None or date is None or c.amount is None or c.date is None:
        return False
    try:
        return Decimal(str(total)) == Decimal(str(c.amount)) and str(date)[:10] == c.date.isoformat()
    except (InvalidOperation, ValueError):
        return False


def _assumptions(rv: Review) -> list[dict]:
    out = []
    for c, ref, u in rv.content_links:
        out.append({"code": "receipt_matched_by_content", "source": "rule",
                    "text": W("assume.content_link", label=c.label, ref=ref, name=u.name)})
    p = rv.policy
    if p is not None and p.source == "package_sample":
        out.append({"code": "package_sample_policy", "source": "rule",
                    "text": W("assume.sample_policy"), "changes_numbers": True})
    if rv.kb:
        out.append({"code": "kb_context_only", "source": "rule",
                    "text": W("assume.kb", names=", ".join((u.name for u in rv.kb)))})
    for c in rv.conflicts:
        table = W("assume.kb.sets", table=fmt_money(c["table"])) if c["table"] else W("assume.kb.sets_none")
        out.append({"code": "kb_differs_from_table", "source": "rule",
                    "text": W("assume.kb_differs", file=c["file"], paragraph=c["paragraph"], label=c["label"],
                              document=fmt_money(c["document"]), table=table)})
    cal = rv.calendar
    years = sorted({c.date.year for c in rv.claims if c.date})
    if not cal.active:
        # fail closed (AM3): no holiday is flagged from a calendar nobody confirmed as the company's
        out.append({"code": "holiday_check_off", "source": "rule", "changes_numbers": True,
                    "text": W("assume.holiday_off", note=cal.note)})
    else:
        out.append({"code": "company_holiday_calendar", "source": "rule",
                    "text": W("assume.holiday_on", note=cal.note)})
        for y in years:
            if not cal.covers(y):
                out.append({"code": "holiday_calendar_missing_year", "source": "rule",
                            "text": W("assume.calendar_year", year=y + 543 if words.lang() == "th" else y)})
    for copy, orig in rv.same_file:
        out.append({"code": "same_file_twice", "source": "rule",
                    "text": W("assume.same_file", copy=copy.name, orig=orig.name)})
    for u in rv.others:
        why = {"policy_sheet": W("unused.policy_sheet"),
               "unrecognised_sheet": W("unused.unrecognised_sheet"), "unsupported_type": W("unused.unsupported_type"),
               "unreadable": W("unused.unreadable")}.get(u.data.get("reason"), W("unused.other"))
        out.append({"code": "file_not_used", "source": "rule", "text": W("assume.file_not_used", name=u.name, why=why)})
    if rv.missing_refs:
        out.append({"code": "receipt_files_missing", "source": "rule",
                    "text": W("assume.receipts_missing", refs=", ".join(f"{c.label} → {ref}" for c, ref in rv.missing_refs[:10]))})
    for r in rv.unlinked:
        out.append({"code": "receipt_not_linked", "source": "rule",
                    "text": W("assume.receipt_unlinked", name=r.up.name)})
    return out


def period_of(claims: list[R.Claim], today: _dt.date | None) -> tuple[str, str]:
    """The month most claims are dated in (ties: the latest), else today's."""
    months: dict[str, int] = {}
    for c in claims:
        if c.date:
            m = f"{c.date:%Y-%m}"
            months[m] = months.get(m, 0) + 1
    if months:
        return max(months, key=lambda m: (months[m], m)), "claims"
    t = today or _dt.date.today()
    return f"{t.year:04d}-{t.month:02d}", "today"

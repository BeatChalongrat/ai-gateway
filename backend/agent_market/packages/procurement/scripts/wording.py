"""Lines the documents and the cards are made of, each built from the package's own words (words.py) and
the run's data. Dates and amounts are formatted for the language: a Thai document gives the Buddhist era."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from ....engine.thai import BE_OFFSET, MONTHS_TH_ABBR
from ..words import t

MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
BANGKOK = _dt.timezone(_dt.timedelta(hours=7))


def date(lang: str, iso) -> str:
    try:
        d = _dt.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return t(lang, "unknown")
    if lang == "en":
        return f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}"
    return f"{d.day} {MONTHS_TH_ABBR[d.month - 1]} {d.year + BE_OFFSET}"


def moment(lang: str, iso) -> str:
    """When a page was seen, on the person's clock (Thailand keeps UTC+7 all year)."""
    try:
        at = _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(BANGKOK)
    except (TypeError, ValueError):
        return t(lang, "unknown")
    return f"{date(lang, at.date().isoformat())} {at:%H:%M}"


def amount(v, currency: str | None = None) -> str:
    s = f"{Decimal(str(v)):,.2f}"
    return f"{currency} {s}" if currency else s


def item_line(lang: str, it: dict) -> str:
    """An item as asked for: its name, what was meant, how many."""
    name = it["name"] + (f" ({it['meant']})" if it.get("meant") else "")
    if not it.get("quantity"):
        return name
    key = "item.line.assumed" if it.get("quantity_assumed") else "item.line"
    return " ".join(t(lang, key, name=name, quantity=it["quantity"], unit=it.get("unit") or "").split())


def used(lang: str, rnd: dict, what: str) -> str:
    done = (rnd.get("used") or {}).get(what, 0)
    cap = (rnd.get("caps") or {}).get(what)
    return t(lang, "face.used", used=str(done), cap=str(cap)) if cap else str(done)


def round_lines(lang: str, rnd: dict) -> list[str]:
    """What the round could not do, said plainly: a limit that was reached, a search that could not run."""
    out = []
    for cap in rnd.get("cap_hit") or []:
        out.append(t(lang, f"round.cap.{cap}"))
    if rnd.get("search") and rnd["search"] != "ok":
        out.append(t(lang, f"round.search.{rnd['search']}"))
    return out


def pick_line(lang: str, it: dict, pick: str) -> str:
    if pick == "none":
        return t(lang, "pick.none")
    c = next((x for x in it["candidates"] if x["key"] == pick), None)
    return t(lang, "pick.line", n=str(c["no"]), source=c["host"]) if c else t(lang, "unknown")


def on_list(lang: str, entry) -> str:
    if not entry:
        return t(lang, "list.no")
    return t(lang, f"list.by.{entry.get('by') or 'name'}", name=entry.get("name") or "")


def requirements(lang: str, rows: list[dict]) -> str:
    if not rows:
        return t(lang, "req.none")
    met = [r for r in rows if r["verdict"] == "meets"]
    out = [t(lang, "req.met", met=str(len(met)), of=str(len(rows)))]
    differs = [r["need"] for r in rows if r["verdict"] == "differs"]
    silent = [r["need"] for r in rows if r["verdict"] == "not_stated"]
    if differs:
        out.append(t(lang, "req.differs", list=" · ".join(differs)))
    if silent:
        out.append(t(lang, "req.silent", list=" · ".join(silent)))
    return " ".join(out)


def snapshot_of(computed: dict, source_id: str) -> str:
    for s in computed.get("sources") or []:
        if s.get("id") == source_id:
            return s.get("snapshot_sha256") or ""
    return ""


def reason(lang: str, s: dict) -> str:
    """Why a source was not captured, in words (a captured one has no reason)."""
    if s.get("outcome") == "captured":
        return ""
    return dropped(lang, {"why": s.get("reason") or s.get("outcome") or "", "detail": s.get("detail") or {}})


# the reasons a source did not become a candidate → the sentence for each (words.py: "drop.<code>")
DROP = ("not_public", "own_service", "scheme", "port", "bad_url", "credential_in_url", "unresolvable", "robots_disallow",
        "robots_unreadable", "page_says_no", "other_host", "credential", "paywall", "site_refused", "not_found", "site_error",
        "nothing_shown", "too_large", "not_a_page", "timeout", "unreachable", "fetcher_error", "denied_by_admin", "host_limit",
        "page_states_no", "cannot_describe", "not_read", "read_refused", "snapshot_missing", "cap_reached", "fetcher_unavailable")


def dropped(lang: str, d: dict) -> str:
    why = d.get("why") or ""
    detail = d.get("detail") or {}
    key = f"drop.{why}" if why in DROP else "drop.other"
    text = t(lang, key)
    extra = []
    if why == "robots_disallow" and detail.get("rule"):
        extra.append(f"“{detail['rule']}”")
    if why in ("page_says_no", "page_states_no") and (detail.get("rule") or detail.get("quote")):
        extra.append(f"“{detail.get('rule') or detail.get('quote')}”")
    if why == "other_host" and detail.get("to"):
        extra.append(f"“{detail['to']}”")
    if why in ("site_refused", "site_error", "credential", "not_found", "paywall") and detail.get("status"):
        extra.append(t(lang, "drop.status", status=str(detail["status"])))
    return " ".join([text] + extra)


def open_why(lang: str, c: dict, point: str) -> str:
    """Why a point is still open for a candidate, with what the page did print where it printed something."""
    seen = {"minimum_order": c.get("minimum_order"), "requirements_differ": None, "requirements_not_stated": None}.get(point)
    text = t(lang, f"open.{point}.why")
    if seen and seen.get("quote"):
        text += f" “{seen['quote']}”"
    if point in ("requirements_differ", "requirements_not_stated"):
        verdict = "differs" if point == "requirements_differ" else "not_stated"
        names = [r["need"] for r in c.get("requirements") or [] if r["verdict"] == verdict]
        if names:
            text += " " + " · ".join(f"“{n}”" for n in names)
    return text


def gap(lang: str, g: dict) -> tuple[str, str]:
    """A gap of the purchase request as two cells: what is not known, and why."""
    why = t(lang, f"gap.{g['code']}.{g['why']}")
    if g.get("seen"):
        why += f" “{g['seen']}”"
    return t(lang, f"gap.{g['code']}"), why

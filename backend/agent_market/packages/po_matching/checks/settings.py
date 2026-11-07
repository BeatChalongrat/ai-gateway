"""What an install sets: whether the company issues goods receipts, the tolerances, the tax rate.

Nothing here is a constant of the code. Every value is an install setting with a default, shown
and edited on the agent's settings page (the manifest lists them under ``install.settings`` with
the same bounds), and every tolerance that was used on a line is written beside that line's
result. A value that cannot be used (not a number, outside what the setting allows) takes its
default, and the working paper says so.

The one setting that changes meaning rather than behaviour is ``ap_gr_practice``:

    yes   the company issues a goods receipt when stock arrives. A goods line billed without one
          is an exception worth chasing: "ยังไม่มีใบรับของ".
    no    the company does not issue goods receipts. The same missing document is normal: the
          line is tested two ways, labelled as two-way, and nobody is asked about it.

Until it is set, the first round asks once, with the answer the round's own files suggest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ..words import W

PREFIX = "ap_"
GR_YES, GR_NO = "yes", "no"
ZERO = Decimal("0")

# setting → (field, kind, least, most, default, its name as the manifest declares it: the Thai of the sentence
# setting.<setting>, which SETTING_TH says in the language of the run)
SETTINGS = {
    "price_tolerance_pct": ("price_pct", "percent", "0", "50", "2.00",
                            W.strings["setting.price_tolerance_pct"]),
    "price_tolerance_abs": ("price_abs", "money", "0", "1000000000", "500.00",
                            W.strings["setting.price_tolerance_abs"]),
    "qty_tolerance_pct": ("qty_pct", "percent", "0", "20", "0.00",
                          W.strings["setting.qty_tolerance_pct"]),
    "over_delivery_pct": ("over_pct", "percent", "0", "50", "0.00",
                          W.strings["setting.over_delivery_pct"]),
    "small_difference": ("small", "money", "0", "100", "1.00",
                         W.strings["setting.small_difference"]),
    "date_window_days": ("days", "int", "0", "366", "0",
                         W.strings["setting.date_window_days"]),
    "duplicate_window_days": ("dup_days", "int", "0", "366", "7",
                              W.strings["setting.duplicate_window_days"]),
    "fuzzy_threshold": ("fuzzy", "ratio", "0.50", "1.00", "0.80",
                        W.strings["setting.fuzzy_threshold"]),
    "vat_rate": ("vat_rate", "percent", "0", "30", "7.00",
                 W.strings["setting.vat_rate"]),
    "unplanned_cost_limit": ("unplanned", "money", "0", "1000000000", "0.00",
                             W.strings["setting.unplanned_cost_limit"]),
}
# a setting's name and an answer's, each said in the language of the run when it is read (engine/words.py)
SETTING_TH = W.table({k: f"setting.{k}" for k in SETTINGS})
GR_PRACTICE_VALUE_TH = W.table({GR_YES: "practice.yes", GR_NO: "practice.no"})
# … and the names of the two settings that are no tolerance: keys, said where they are used
GR_PRACTICE_TH = "setting.gr_practice"
UNIT_SETTING_TH = "setting.unit_conversions"


@dataclass(frozen=True)
class Tolerances:
    price_pct: Decimal = Decimal("2.00")
    price_abs: Decimal = Decimal("500.00")
    qty_pct: Decimal = Decimal("0.00")
    over_pct: Decimal = Decimal("0.00")
    small: Decimal = Decimal("1.00")
    days: int = 0
    dup_days: int = 7
    fuzzy: Decimal = Decimal("0.80")
    vat_rate: Decimal = Decimal("7.00")
    unplanned: Decimal = Decimal("0.00")

    def to_dict(self) -> dict:
        out = {}
        for key, (attr, kind, *_rest) in SETTINGS.items():
            v = getattr(self, attr)
            out[key] = v if kind == "int" else f"{v:.2f}"
        return out


@dataclass
class Settings:
    tolerances: Tolerances = field(default_factory=Tolerances)
    gr_practice: str | None = None          # yes | no | None (not set yet: the first round asks)
    gr_from: str = ""                        # settings | answer | ""
    conversions: list = field(default_factory=list)   # [{"item", "from", "to", "factor"}]
    unplanned_words: tuple = ()
    unusable: list = field(default_factory=list)
    company: str = ""
    company_tax_id: str = ""
    company_branch: str = ""
    currency: str = "THB"


def _value(kind: str, v, lo: str, hi: str):
    if isinstance(v, bool) or v is None:
        return None
    try:
        d = Decimal(str(v).replace(",", "").replace("%", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d < Decimal(lo) or d > Decimal(hi):
        return None
    if kind == "int":
        return int(d) if d == d.to_integral_value() else None
    return d.quantize(Decimal("0.01"))


def conversions_from(raw) -> tuple[list, bool]:
    """The install's unit conversions: "1 กล่อง = 12 ชิ้น" one per line (or separated by ;), with
    the item it holds for in front when it is not for every item: "A-100: 1 ลัง = 24 ขวด".
    Returns the conversions and whether every line could be read."""
    from .units import canonical

    out, ok = [], True
    if isinstance(raw, list):
        lines = raw
    else:
        lines = [x for x in str(raw or "").replace("\n", ";").split(";")]
    for line in lines:
        if isinstance(line, dict):
            item, left, right = str(line.get("item") or ""), f"1 {line.get('from')}", f"{line.get('factor')} {line.get('to')}"
        else:
            s = " ".join(str(line).split())
            if not s:
                continue
            item, _, rest = s.rpartition(":")
            left, eq, right = rest.partition("=")
            if not eq:
                ok = False
                continue
        a, b = left.split(), right.split()
        if len(a) < 2 or len(b) < 2:
            ok = False
            continue
        try:
            n1 = Decimal(a[0].replace(",", ""))
            n2 = Decimal(b[0].replace(",", ""))
        except (InvalidOperation, ValueError):
            ok = False
            continue
        if n1 <= 0 or n2 <= 0:
            ok = False
            continue
        out.append({"item": " ".join(item.split()), "from": canonical(" ".join(a[1:])), "to": canonical(" ".join(b[1:])),
                    "factor": str((n2 / n1).normalize())})
    return out, ok


def settings_from(defaults: dict | None, answers: dict | None = None) -> Settings:
    from .units import UNPLANNED_WORDS

    d = defaults or {}
    kw, unusable = {}, []
    for key, (attr, kind, lo, hi, _default, _label) in SETTINGS.items():
        raw = d.get(PREFIX + key)
        if raw in (None, ""):
            continue
        v = _value(kind, raw, lo, hi)
        if v is None:
            unusable.append(key)
        else:
            kw[attr] = v
    s = Settings(tolerances=Tolerances(**kw), unusable=unusable)
    practice = str(d.get(PREFIX + "gr_practice") or "").strip().lower()
    said = (answers or {}).get("ap_gr_practice")
    if practice in (GR_YES, GR_NO):
        s.gr_practice, s.gr_from = practice, "settings"
    elif said in (GR_YES, GR_NO):
        s.gr_practice, s.gr_from = said, "answer"
    conv, ok = conversions_from(d.get(PREFIX + "unit_conversions"))
    s.conversions = conv
    if not ok:
        s.unusable.append("unit_conversions")
    words = d.get(PREFIX + "unplanned_words")
    if isinstance(words, str) and words.strip():
        s.unplanned_words = tuple(w for w in ("".join(x.split()).lower() for x in words.replace("\n", ",").split(",")) if w)
    else:
        s.unplanned_words = UNPLANNED_WORDS
    s.company = " ".join(str(d.get("company") or "").split())
    tid = "".join(ch for ch in str(d.get("company_tax_id") or "") if ch.isdigit())
    s.company_tax_id = tid if len(tid) == 13 else ""
    s.company_branch = str(d.get("company_branch") or "").strip()
    s.currency = (str(d.get("currency") or "THB").strip().upper() or "THB")[:3]
    return s

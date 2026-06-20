"""NumericContextAnalyzer — Phase 2A / 2A.1 Smart Numeric Masking.

Deterministic, keyword/checksum-based classification of numbers in
plain-text chat content. Runs AFTER detection (both Rust and Python
detector engines feed the same logic) and BEFORE the policy evaluator,
INSIDE the V2 path of :class:`DLPPolicyEngine`. It never masks, restores,
logs raw text, or calls any network/DB/Redis — it only attaches
deterministic ``policy_v2`` evidence and may add supplemental findings;
the Policy Engine remains the sole action decider.

Phase 2A.1 hardening:
- **Per-message segments**: analysis runs per :class:`NumericTextSegment`
  (one per message), so finding offsets are LOCAL to a message and a
  ``message_index`` is recorded. Context windows never cross a message
  (or newline) boundary — no keyword bleed between messages.
- **Direction scoping**: confidential-supplemental and weak-phone
  suppression apply to INBOUND user text and TOOL arguments only.
  SECRET_NUMERIC applies in all directions. Reasoning gets the stricter
  behavior (secret only, no suppression, no confidential).
- **force_mask**: confidential findings carry ``policy_v2.force_mask`` so
  the transformer masks them even when the underlying detector
  (``income``) is in ``BYPASS_DETECTORS``.
- **Oversized**: text over ``_MAX_TEXT_LEN`` sets ``truncated`` so the
  engine can fail closed in authoritative mode (never silently clean).

Numeric classes (in ``DetectionMatch.metadata['policy_v2']``):
    GENERAL_NUMERIC / CONFIDENTIAL_NUMERIC / STRONG_IDENTIFIER /
    SECRET_NUMERIC / UNKNOWN_NUMERIC.

NO raw numeric value is ever written to metadata/logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.domain.dlp import DetectionMatch, SensitivityLevel

# ---------------------------------------------------------------------------
# Numeric class constants
# ---------------------------------------------------------------------------

GENERAL_NUMERIC = "GENERAL_NUMERIC"
CONFIDENTIAL_NUMERIC = "CONFIDENTIAL_NUMERIC"
STRONG_IDENTIFIER = "STRONG_IDENTIFIER"
SECRET_NUMERIC = "SECRET_NUMERIC"
UNKNOWN_NUMERIC = "UNKNOWN_NUMERIC"

_STRONG_ID_DETECTORS = frozenset({
    "credit_card", "thai_national_id", "thai_bank_account", "thai_promptpay",
    "phone", "thai_tax_id", "medical_record", "passport", "ssn", "iban",
})
_CONFIDENTIAL_DETECTORS = frozenset({"income"})

# Safety bounds.
_MAX_TEXT_LEN = 200_000      # per-segment analysis ceiling
_MAX_SUPPLEMENTAL = 50
_CONTEXT_WINDOW = 25         # chars each side, phone keyword proximity
_LABEL_NUMBER_WINDOW = 24    # label → nearest number distance
_OWNERSHIP_WINDOW = 40       # financial-label ownership/public window


@dataclass(frozen=True)
class NumericTextSegment:
    """One unit of text to analyze, bound to its source message.

    ``message_index`` is the index into the ORIGINAL message list the
    transformer will iterate (None for single-text outbound/stream/tool).
    """

    message_index: int | None
    role: str | None
    text: str
    source_type: str = "direct_api"


# ---------------------------------------------------------------------------
# Compiled patterns (module-level — compiled once). All operate WITHIN a
# single segment; none cross a message boundary.
# ---------------------------------------------------------------------------

_MATH_RE = re.compile(
    r"[+\-*/×÷=]|(?<=\d)\s*[xX]\s*(?=\d)|"
    r"คำนวณ|บวก|ลบ|คูณ|หาร|รวมกัน|ผลรวม|สมการ|"
    r"\bcalculate\b|\bsum\b|\bplus\b|\bminus\b|\bmultipl|\bdivide\b|\btimes\b"
)
_MEASURE_RE = re.compile(
    r"กิโลเมตร|กม\.|เมตร|องศา|ชิ้น|คน|ล้าน|พันล้าน|เปอร์เซ็นต์|"
    r"กิโลกรัม|กก\.|ลิตร|หน้า|ปี|พ\.?ศ\.?|ค\.?ศ\.?|%|"
    r"\bkm\b|\bkg\b|\bpercent\b|\bpage\b|\byear\b|\bunits?\b|\bdegrees?\b"
)
_PHONE_KEYWORD_RE = re.compile(
    r"โทร|เบอร์|ติดต่อ|มือถือ|สายด่วน|หมายเลข|whatsapp|line\s*id|"
    r"\bphone\b|\btel\b|\bcall\b|\bmobile\b|\bdial\b|\bhotline\b|\bcontact\b",
    re.IGNORECASE,
)
_BARE_LANDLINE_RE = re.compile(r"^0[2-9]\d{7}$")

_SECRET_LABEL_RE = re.compile(
    r"(one[\-\s]?time\s*password|otp|รหัส\s*otp|verification\s*code|"
    r"รหัสยืนยัน|passcode|รหัสผ่าน|\bpin\b|รหัส\s*pin|\bpassword\b|\bpwd\b)",
    re.IGNORECASE,
)
_SECRET_NUMBER_RE = re.compile(r"\d{4,8}")

# Personal compensation/debt — inherently personal.
_PERSONAL_CONF_RE = re.compile(
    r"(เงินเดือน|รายได้ส่วนบุคคล|ค่าจ้าง|โบนัส|ค่าตอบแทน|หนี้ส่วนบุคคล|หนี้สิน|"
    r"\bsalary\b|\bmy\s+income\b|\bmy\s+salary\b|\bmy\s+bonus\b|\bmy\s+wage\b)",
    re.IGNORECASE,
)
# Explicit personal-ownership markers (highest precedence — beat public).
_PERSONAL_OWNERSHIP_RE = re.compile(
    r"ของฉัน|ของผม|ของดิฉัน|ของหนู|\bmy\b|\bmine\b",
    re.IGNORECASE,
)
_OWNERSHIP_RE = re.compile(
    r"ภายใน|บริษัทเรา|ของเรา|ของบริษัท|ยังไม่เปิดเผย|ไม่เปิดเผย|ลับ|confidential|"
    r"\binternal\b|our\s+company|non[\-\s]?public|nonpublic",
    re.IGNORECASE,
)
_FIN_LABEL_RE = re.compile(
    r"งบประมาณ|ต้นทุน|กำไร|ยอดขาย|รายได้|ราคาสัญญา|มูลค่าโครงการ|"
    r"\bbudget\b|\bcost\b|\bprofit\b|\brevenue\b|\bsales\b|contract\s+price",
    re.IGNORECASE,
)
# Public markers (Phase 2A.1 expanded).
_PUBLIC_NEG_RE = re.compile(
    r"เผยแพร่|เปิดเผยต่อสาธารณะ|ประกาศอย่างเป็นทางการ|ประกาศต่อสาธารณะ|"
    r"รายงานประจำปี|งบประมาณแผ่นดิน|ข้อมูลสาธารณะ|สาธารณะ|แผ่นดิน|ตลาด|gdp|"
    r"published|publicly\s+disclosed|public\s+report|annual\s+report|"
    r"official\s+announcement|public\s+data|\bpublic\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Sentence boundary inside a segment (don't bind a label across it).
_SENTENCE_BREAK_RE = re.compile(r"[.!?\n]|[।ฯ]")

POLICY_V2 = "policy_v2"


def _set_policy_v2(match: DetectionMatch, **fields) -> DetectionMatch:
    meta = dict(match.metadata or {})
    pv = dict(meta.get(POLICY_V2) or {})
    pv.update(fields)
    meta[POLICY_V2] = pv
    return replace(match, metadata=meta)


def _allow_confidential(direction: str, source_type: str) -> bool:
    if source_type == "reasoning":
        return False
    if source_type == "tool_argument":
        return True
    return direction == "input"


def _allow_weak_phone_suppress(direction: str, source_type: str) -> bool:
    # Inbound user text + tool args only — never outbound / reasoning /
    # final restored re-scan.
    if source_type in ("reasoning",):
        return False
    if source_type == "tool_argument":
        return True
    return direction == "input"


class NumericContextAnalyzer:
    """Deterministic per-segment numeric classifier. Stateless."""

    def analyze(
        self,
        segments: list[NumericTextSegment],
        findings: list[DetectionMatch],
        *,
        direction: str = "input",
        context=None,
    ) -> tuple[list[DetectionMatch], list[DetectionMatch], bool]:
        """Return (enriched_findings, supplemental_findings, truncated).

        ``enriched_findings`` = input findings, each possibly tagged with
        a numeric_class + message_index; weak keyword-free phones in math
        context (inbound/tool only) are DROPPED. ``supplemental_findings``
        = new SECRET / CONFIDENTIAL numeric findings, with LOCAL offsets
        and ``message_index``. ``truncated`` = any segment exceeded the
        analysis ceiling.
        """
        truncated = False
        enriched = self._enrich(segments, findings, direction)

        supplemental: list[DetectionMatch] = []
        for seg in segments:
            text = seg.text if isinstance(seg.text, str) else ""
            if len(text) > _MAX_TEXT_LEN:
                truncated = True
                text = text[:_MAX_TEXT_LEN]
            if not any(ch.isdigit() for ch in text):
                continue
            existing_spans = [
                (f.start, f.end) for f in findings
                if self._segment_for(f, segments) is seg
            ]
            supplemental.extend(
                self._supplemental(text, seg, existing_spans, direction)
            )
            if len(supplemental) >= _MAX_SUPPLEMENTAL:
                supplemental = supplemental[:_MAX_SUPPLEMENTAL]
                break
        return enriched, supplemental, truncated

    # -- segment mapping ----------------------------------------------------

    @staticmethod
    def _segment_for(finding, segments):
        """First segment whose local slice equals the finding's value."""
        for seg in segments:
            t = seg.text
            s, e = finding.start, finding.end
            if isinstance(t, str) and 0 <= s < e <= len(t) and t[s:e] == finding.matched_text:
                return seg
        return None

    # -- enrichment ---------------------------------------------------------

    def _enrich(self, segments, findings, direction):
        out: list[DetectionMatch] = []
        for f in findings:
            seg = self._segment_for(f, segments)
            mi = seg.message_index if seg is not None else None
            stype = seg.source_type if seg is not None else "direct_api"
            if f.detector_name in _CONFIDENTIAL_DETECTORS:
                out.append(_set_policy_v2(
                    f, numeric_class=CONFIDENTIAL_NUMERIC,
                    rule_id=f"numeric.confidential.{f.detector_name}",
                    evidence=[f"detector:{f.detector_name}"],
                    decision_reason="detector-confirmed confidential numeric",
                    force_mask=True, message_index=mi,
                ))
                continue
            if f.detector_name in _STRONG_ID_DETECTORS:
                if (
                    f.detector_name == "phone"
                    and seg is not None
                    and _allow_weak_phone_suppress(direction, stype)
                    and self._is_weak_phone(seg.text, f)
                ):
                    continue  # GENERAL → drop → ALLOW
                out.append(_set_policy_v2(
                    f, numeric_class=STRONG_IDENTIFIER,
                    rule_id=f"numeric.strong.{f.detector_name}",
                    evidence=[f"detector:{f.detector_name}"],
                    decision_reason="strong identifier — arithmetic never relaxes",
                    message_index=mi,
                ))
                continue
            # Other findings (PERSON / NAME / LOCATION / ORG / etc.) pass
            # through, but get tagged with message_index so the Phase 3
            # public-entity registry can match them span-scoped.
            out.append(_set_policy_v2(f, message_index=mi))
        return out

    def _is_weak_phone(self, text: str, f: DetectionMatch) -> bool:
        digits = re.sub(r"\D", "", f.matched_text or "")
        if not _BARE_LANDLINE_RE.match(f.matched_text or "") or len(digits) != 9:
            return False
        idx = f.start if (0 <= f.start < len(text) and text[f.start:f.end] == f.matched_text) else text.find(f.matched_text)
        if idx == -1:
            return False
        lo = max(0, idx - _CONTEXT_WINDOW)
        hi = min(len(text), idx + len(f.matched_text) + _CONTEXT_WINDOW)
        window = text[lo:hi]
        if _PHONE_KEYWORD_RE.search(window):
            return False
        return bool(_MATH_RE.search(window) or _MEASURE_RE.search(window))

    # -- supplemental detection --------------------------------------------

    def _supplemental(self, text, seg, existing_spans, direction):
        out: list[DetectionMatch] = []
        out.extend(self._detect_secret_numeric(text, seg, existing_spans))
        if _allow_confidential(direction, seg.source_type):
            out.extend(self._detect_confidential_numeric(text, seg, existing_spans))
        return out

    @staticmethod
    def _overlaps(spans, start, end) -> bool:
        return any(s < end and start < e for s, e in spans)

    def _mk(self, text, start, end, seg, *, detector, entity, category,
            severity, nclass, rule_id, evidence, reason, force_mask=False):
        m = DetectionMatch(
            detector_name=detector, entity_type=entity, category=category,
            start=start, end=end, matched_text=text[start:end],
            severity=severity, confidence=1.0,
        )
        pv = dict(
            numeric_class=nclass, rule_id=rule_id, evidence=evidence,
            decision_reason=reason, message_index=seg.message_index,
        )
        if force_mask:
            pv["force_mask"] = True
        return _set_policy_v2(m, **pv)

    def _detect_secret_numeric(self, text, seg, existing_spans):
        out = []
        for m in _SECRET_LABEL_RE.finditer(text):
            window = text[m.end(): m.end() + 16]
            if _SENTENCE_BREAK_RE.search(window):
                window = window[: _SENTENCE_BREAK_RE.search(window).start()]
            nm = _SECRET_NUMBER_RE.search(window)
            if not nm:
                continue
            start, end = m.end() + nm.start(), m.end() + nm.end()
            if self._overlaps(existing_spans, start, end):
                continue
            out.append(self._mk(
                text, start, end, seg,
                detector="numeric_secret", entity="otp_pin_password",
                category="secret", severity=SensitivityLevel.CRITICAL,
                nclass=SECRET_NUMERIC, rule_id="numeric.secret.labelled_code",
                evidence=[f"label:{m.group(0).lower()}"],
                reason="explicit secret-code label preceding short number",
            ))
            if len(out) >= _MAX_SUPPLEMENTAL:
                break
        return out

    def _detect_confidential_numeric(self, text, seg, existing_spans):
        out = []
        # Personal compensation/debt.
        for m in _PERSONAL_CONF_RE.finditer(text):
            span = self._number_after(text, m.end(), _LABEL_NUMBER_WINDOW)
            if span is None:
                continue
            start, end = span
            if self._overlaps(existing_spans, start, end):
                continue
            # Precedence: explicit personal ownership beats a public
            # marker; otherwise a public marker in the local window means
            # this is public salary data → skip.
            win = text[max(0, m.start() - _OWNERSHIP_WINDOW): end + _OWNERSHIP_WINDOW]
            personal = bool(_PERSONAL_OWNERSHIP_RE.search(win))
            if not personal and _PUBLIC_NEG_RE.search(win):
                continue
            out.append(self._mk(
                text, start, end, seg,
                detector="numeric_confidential", entity="personal_finance",
                category="financial", severity=SensitivityLevel.HIGH,
                nclass=CONFIDENTIAL_NUMERIC, rule_id="numeric.confidential.personal",
                evidence=[f"label:{m.group(0).lower()}",
                          "ownership:personal" if personal else "ownership:label_personal"],
                reason="explicit personal-finance label",
                force_mask=True,
            ))
            if len(out) >= _MAX_SUPPLEMENTAL:
                return out
        # Internal-business: financial label + ownership, no public marker.
        for m in _FIN_LABEL_RE.finditer(text):
            lo = max(0, m.start() - _OWNERSHIP_WINDOW)
            hi = min(len(text), m.end() + _OWNERSHIP_WINDOW)
            window = text[lo:hi]
            if not _OWNERSHIP_RE.search(window):
                continue
            if _PUBLIC_NEG_RE.search(window):
                continue
            span = self._number_after(text, m.end(), _OWNERSHIP_WINDOW)
            if span is None:
                continue
            start, end = span
            if self._overlaps(existing_spans, start, end):
                continue
            out.append(self._mk(
                text, start, end, seg,
                detector="numeric_confidential", entity="internal_business_finance",
                category="financial", severity=SensitivityLevel.HIGH,
                nclass=CONFIDENTIAL_NUMERIC,
                rule_id="numeric.confidential.internal_business",
                evidence=[f"label:{m.group(0).lower()}", "ownership:internal"],
                reason="internal/private financial label with ownership evidence",
                force_mask=True,
            ))
            if len(out) >= _MAX_SUPPLEMENTAL:
                break
        return out

    @staticmethod
    def _number_after(text, pos, fwd):
        """First money/quantity number within ``fwd`` chars after ``pos``,
        not crossing a sentence boundary. Returns (start, end) or None."""
        window = text[pos: pos + fwd]
        brk = _SENTENCE_BREAK_RE.search(window)
        if brk:
            window = window[: brk.start()]
        nm = _NUMBER_RE.search(window)
        if not nm or len(re.sub(r"\D", "", nm.group(0))) < 2:
            return None
        return pos + nm.start(), pos + nm.end()


numeric_context_analyzer = NumericContextAnalyzer()

"""The finding shape every check returns.

Rule zero of the clarification engine: the model never decides what to
ask. A check (plain Python in the package's checks/) reads the files and the
computed tables and returns findings; the engine (Go, agent-runner) turns
findings into question cards. The shape is validated here and again on the
Go side, so a malformed finding fails the step instead of reaching a user.

    { code, severity: blocking|cosmetic, moment: preflight|postcompute,
      header (≤12 chars), question, options[2..4]{label, description, value},
      default, evidence{...}, rememberable, kind: question|request_files,
      subject (what this finding is about — a file, a column, an account) }
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITIES = ("blocking", "cosmetic")
MOMENTS = ("install", "preflight", "postcompute")
KINDS = ("question", "request_files")
MULTI_INSTANCE = ("wrong_document_type", "unmapped_column", "unreadable_dates")
FORBIDDEN_WORDS = ("password", "รหัสผ่าน", "token", "โทเคน", "เลขบัตรประชาชน", "otp", "pin")


@dataclass
class Option:
    label: str
    value: Any
    description: str = ""


@dataclass
class Finding:
    code: str
    severity: str
    moment: str
    header: str
    question: str
    options: list[Option]
    default: Any
    evidence: dict = field(default_factory=dict)
    rememberable: bool = False
    kind: str = "question"
    subject: str = ""
    # For request_files: the files asked for [{role, label, why, required}]
    request: list[dict] = field(default_factory=list)
    # Facts the question text was written from — the only fields an optional
    # model rewrite may draw on.
    facts: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """The identity an answer is matched by: the code, plus the subject for
        codes that can fire once per file. Never a position in a list."""
        return f"{self.code}:{self.subject}" if self.code in MULTI_INSTANCE and self.subject else self.code

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


class FindingError(ValueError):
    pass


def display_len(s: str) -> int:
    """Characters a reader sees: Thai combining marks take no column."""
    import unicodedata

    return sum(1 for ch in s if unicodedata.category(ch) not in ("Mn", "Me"))


def validate(f: Finding) -> None:
    if not f.code or not f.code.replace("_", "").isalnum():
        raise FindingError(f"bad code {f.code!r}")
    if f.severity not in SEVERITIES:
        raise FindingError(f"{f.code}: severity {f.severity!r}")
    if f.moment not in MOMENTS:
        raise FindingError(f"{f.code}: moment {f.moment!r}")
    if f.kind not in KINDS:
        raise FindingError(f"{f.code}: kind {f.kind!r}")
    if display_len(f.header) > 12:
        raise FindingError(f"{f.code}: header longer than 12 characters: {f.header!r}")
    if not f.question.strip():
        raise FindingError(f"{f.code}: empty question")
    if f.kind == "question":
        if not 2 <= len(f.options) <= 4:
            raise FindingError(f"{f.code}: {len(f.options)} options (2-4 required)")
        values = [o.value for o in f.options]
        if f.default not in values:
            raise FindingError(f"{f.code}: default {f.default!r} is not an option value")
        labels = [o.label for o in f.options]
        if len(set(labels)) != len(labels):
            raise FindingError(f"{f.code}: duplicate option labels")
    else:
        if not f.request:
            raise FindingError(f"{f.code}: request_files without files")
    low = (f.question + " " + " ".join(o.label for o in f.options)).lower()
    for w in FORBIDDEN_WORDS:
        if w in low:
            raise FindingError(f"{f.code}: a card may never ask for a secret ({w})")


def finding(code: str, severity: str, moment: str, header: str, question: str,
            options: list[tuple[str, Any, str]], default: Any, **kw) -> Finding:
    f = Finding(code=code, severity=severity, moment=moment, header=header, question=question,
                options=[Option(label=l, value=v, description=d) for (l, v, d) in options],
                default=default, **kw)
    validate(f)
    return f

"""FIX130 — the system prompt never reaches the user as answer text.

NIGHT2 measured ``qwen3.6-35b-a3b`` quoting ``<language_rules>`` word for
word ("Greet the user only once, at the start of the conversation. On
follow-up messages, reply directly without re-greeting…") and naming the
``<safety>`` rules, and FIX130 step 1 reproduced it straight from the box.
Routing the thinking into the reasoning channel (``think_splitter``)
takes most of it out of the ANSWER, but the thinking is shown to the
user too, and a model can echo its instructions in the answer itself.
This guard removes verbatim system-prompt text from both channels before
the DLP window sees them.

What counts as the system prompt (``build_echo_fingerprints``). Only
INSTRUCTION text is fingerprinted — never data the user may legitimately
ask to have quoted back:

* instruction sections — every XML-tagged block (``<language_rules>``,
  ``<safety>``, ``<tool_policy>``, …) and every markdown section of the
  desktop prompt, up to the first DATA section. The web compiler and the
  desktop assembler both put all data after all instructions (custom
  instructions, project, skills, memory, RAG, files, canvas), so the
  first data marker ends the instruction region for the rest of that
  message;
* not fingerprinted even inside the region: what the model is meant to
  tell the user — the self-description line ("You are SRAN Chat, an AI
  assistant built by …"), ``<capabilities>``, ``<disabled_capabilities>``,
  ``<temporary_chat>``, the current-date line. The REST of ``<identity>``
  ("You are not the underlying model — …") is instruction and is
  fingerprinted;
* the gateway's own lines: the DLP placeholder prompt (it leads the first
  system message) and the ``[model identity]`` line minus its first
  sentence (the model is TOLD to say which model it is);
* section tags and the ``[model identity]`` marker are removed on sight.

What counts as an echo: a run of at least ``ECHO_MIN_RUN`` normalised
characters (case, whitespace runs, ``*``/backtick emphasis and curly
quotes ignored) that appears verbatim in the fingerprinted text — about
seven words, so an answer that merely shares a few words with the prompt
is untouched — unless that same run also appears in a non-system message
of the request (the user pasted the rules and asked about them).

Streaming (``EchoGuard``). Text is released as soon as it cannot be the
start of an echo: the guard holds back only the longest suffix that is
still a substring of the fingerprinted text (at most ``ECHO_MIN_RUN - 1``
normalised characters; a Thai answer is released at once), and a run
already found keeps being removed while the output keeps following the
prompt, even in fragments shorter than the minimum run.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field

from app.services.model_identity import IDENTITY_MARKER

ECHO_MIN_RUN = 40
# Raw characters of already-decided output kept as context, so an echo
# that continues across a chunk boundary is still one run.
_CTX_CHARS = 2 * ECHO_MIN_RUN
# Fingerprint at most this much instruction text (normalised chars).
_MAX_FINGERPRINT_CHARS = 200_000
_SEP = "\x00"

_DROP = frozenset("*`")
# Curly quotes -> straight, em/en dash -> hyphen, no-break space -> space.
_MAP = {
    chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"',
    chr(0x2014): "-", chr(0x2013): "-", chr(0x00A0): " ",
}
_TRANS = str.maketrans({**{c: None for c in _DROP}, **_MAP})
_WS_RE = re.compile(r"\s+")

# Blocks whose CONTENT the model is meant to relay (their tags still count).
_RELAYABLE_TAGS = frozenset({"capabilities", "disabled_capabilities", "temporary_chat"})
# Blocks that are user data: the instruction region ends at the first one.
_DATA_TAGS = frozenset({"saved_user_memory", "known_facts", "standing_instructions", "conversation_state"})
_TAG_LINE_RE = re.compile(r"^<(/?)([a-z][a-z0-9_]*)>$")
# Markdown headings (web compiler layers 10-13, desktop project/skills/
# style/notes) and skill markers that start the data part of a prompt.
_DATA_HEADING_RE = re.compile(
    r"^(#{1,6}\s*(about the user|project|active skills|skill|writing style|memory|notes"
    r"|retrieved|knowledge|typed untrusted|attached|file|document|excel|presentation"
    r"|canvas|workbook|spreadsheet|custom instructions|conversation)"
    r"|---\s*skill:|available skills)",
    re.IGNORECASE,
)
# The current-date line, and a self-description ("You are SRAN Chat, …",
# "You are SRAN Assistant, …", a tenant's custom identity) — but not a rule
# phrased in the second person ("You are not the underlying model …").
_SKIP_LINE_RE = re.compile(
    r"current date and time|^you are (?!not\b|never\b|no\b|only\b|allowed\b|forbidden\b)",
    re.IGNORECASE,
)


def _fast_norm(text: str) -> str:
    return _WS_RE.sub(" ", text.translate(_TRANS)).lower()


def _normalise(text: str) -> tuple[str, list[int], list[int]]:
    """Normalised text plus two maps.

    ``origin[p]`` — raw index where normalised char ``p`` came from;
    ``owner[i]`` — the normalised position raw char ``i`` belongs to (a
    collapsed whitespace char belongs to its run's space; a dropped
    emphasis char to the next normalised char).
    """
    out: list[str] = []
    origin: list[int] = []
    owner: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        ch = _MAP.get(ch, ch)
        if ch in _DROP:
            owner.append(len(out))
            continue
        if ch.isspace():
            if prev_space:
                owner.append(len(out) - 1)
                continue
            prev_space = True
            owner.append(len(out))
            out.append(" ")
            origin.append(i)
            continue
        prev_space = False
        owner.append(len(out))
        for c in ch.lower():
            out.append(c)
            origin.append(i)
    return "".join(out), origin, owner


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            elif isinstance(getattr(p, "text", None), str):
                parts.append(p.text)
        return "\n".join(parts)
    return ""


def _instruction_segments(text: str) -> tuple[list[str], set[str]]:
    """Split one system message into fingerprinted segments + tag tokens."""
    segments: list[str] = []
    tags: set[str] = set()
    current: list[str] = []
    stack: list[str] = []
    in_region = True

    def cut() -> None:
        if current:
            segments.append("\n".join(current))
            current.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(IDENTITY_MARKER):
            # Appended by the gateway after everything else (model_identity);
            # its first sentence is what the model is told to say.
            cut()
            tags.add(IDENTITY_MARKER)
            rest = stripped.split(". ", 1)
            if len(rest) == 2:
                segments.append(rest[1])
            continue
        m = _TAG_LINE_RE.match(stripped)
        if m:
            closing, name = m.group(1), m.group(2)
            if not closing and name in _DATA_TAGS:
                in_region = False
            if in_region or name in _DATA_TAGS:
                tags.add(f"<{name}>")
                tags.add(f"</{name}>")
            if closing:
                if name in stack:
                    del stack[stack.index(name):]
            else:
                stack.append(name)
            cut()
            continue
        if not in_region:
            continue
        if _DATA_HEADING_RE.match(stripped):
            in_region = False
            cut()
            continue
        if any(t in _RELAYABLE_TAGS for t in stack) or _SKIP_LINE_RE.search(stripped):
            cut()
            continue
        current.append(line)
    cut()
    return segments, tags


@dataclass
class EchoFingerprints:
    sys_norm: str
    grams: frozenset[int]
    tags: tuple[str, ...]
    _convo_raw: list[str] = field(default_factory=list, repr=False)
    _convo_norm: str | None = field(default=None, repr=False)
    _convo_hits: dict[str, bool] = field(default_factory=dict, repr=False)

    def in_conversation(self, gram: str) -> bool:
        """The run is also in a user/assistant/tool message — not a leak.

        Asked only for a run that already matched the prompt (rare), so
        the conversation is normalised lazily, once, on the first ask.
        """
        hit = self._convo_hits.get(gram)
        if hit is None:
            if self._convo_norm is None:
                self._convo_norm = _fast_norm("\n".join(self._convo_raw))
                self._convo_raw = []
            hit = self._convo_hits[gram] = gram in self._convo_norm
        return hit


def build_echo_fingerprints(messages) -> EchoFingerprints | None:
    """Fingerprints of the request's instruction text; ``None`` = nothing to guard."""
    segments: list[str] = []
    tags: set[str] = set()
    convo: list[str] = []
    for msg in messages or []:
        text = _text_of(getattr(msg, "content", None))
        if not text:
            continue
        if getattr(msg, "role", None) == "system":
            segs, tg = _instruction_segments(text)
            segments.extend(segs)
            tags |= tg
        else:
            convo.append(text)
    norm_segments = [s for s in (_fast_norm(seg).strip() for seg in segments) if s]
    norm_tags = sorted({_fast_norm(t) for t in tags}, key=len, reverse=True)
    if not norm_segments and not norm_tags:
        return None
    sys_norm = _SEP.join(norm_segments + norm_tags)[:_MAX_FINGERPRINT_CHARS]
    k = ECHO_MIN_RUN
    grams: set[int] = set()
    for seg in sys_norm.split(_SEP):
        for i in range(len(seg) - k + 1):
            grams.add(hash(seg[i:i + k]))
    return EchoFingerprints(sys_norm=sys_norm, grams=frozenset(grams), tags=tuple(norm_tags), _convo_raw=convo)


class EchoGuard:
    """Streaming filter for one output channel (answer or shown reasoning)."""

    def __init__(self, fingerprints: EchoFingerprints | None) -> None:
        self._fp = fingerprints
        self._raw = ""
        self._ctx = ""
        self._in_removed = False
        self.removed_chars = 0
        self.removed_runs = 0

    @property
    def active(self) -> bool:
        return self._fp is not None

    def feed(self, text: str) -> str:
        if self._fp is None:
            return text
        if not text:
            return ""
        self._raw += text
        return self._process(final=False)

    def flush(self) -> str:
        if self._fp is None or not self._raw:
            return ""
        return self._process(final=True)

    def _process(self, *, final: bool) -> str:
        fp = self._fp
        assert fp is not None
        k = ECHO_MIN_RUN
        ctx, raw = self._ctx, self._raw
        full = ctx + raw
        norm, origin, owner = _normalise(full)
        n = len(norm)
        # First normalised position that came from the undecided text.
        b = bisect_left(origin, len(ctx))
        marked = bytearray(n + 1)
        for s in range(max(0, b - k + 1), n - k + 1):
            gram = norm[s:s + k]
            if hash(gram) in fp.grams and not fp.in_conversation(gram):
                for j in range(max(s, b), s + k):
                    marked[j] = 1
        for tag in fp.tags:
            at = norm.find(tag, max(0, b - len(tag) + 1))
            while at != -1:
                for j in range(max(at, b), at + len(tag)):
                    marked[j] = 1
                at = norm.find(tag, at + 1)
        # A trailing dropped char (``*``) belongs to position n: released
        # only by the final flush.
        hold_from = n + 1 if final else n - self._held_suffix(norm, b)
        out: list[str] = []
        decided = len(ctx)
        for i in range(len(ctx), len(full)):
            o = owner[i]
            if o >= hold_from:
                break
            decided = i + 1
            if o >= b and marked[o]:
                self.removed_chars += 1
                if not self._in_removed:
                    self.removed_runs += 1
                    self._in_removed = True
            else:
                out.append(full[i])
                if not full[i].isspace():
                    self._in_removed = False
        self._raw = full[decided:]
        self._ctx = full[:decided][-_CTX_CHARS:]
        return "".join(out)

    def _held_suffix(self, norm: str, b: int) -> int:
        """Longest suffix (≤ ECHO_MIN_RUN-1, within the undecided part) still inside the prompt."""
        sys_norm = self._fp.sys_norm  # type: ignore[union-attr]
        lo, hi = 0, min(ECHO_MIN_RUN - 1, len(norm) - b)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if norm[len(norm) - mid:] in sys_norm:
                lo = mid
            else:
                hi = mid - 1
        return lo


def scrub_echo(fingerprints: EchoFingerprints | None, text: str) -> tuple[str, int, int]:
    """Whole-text form (non-streaming): ``(text, removed_chars, removed_runs)``."""
    if fingerprints is None or not text:
        return text, 0, 0
    guard = EchoGuard(fingerprints)
    out = guard.feed(text) + guard.flush()
    return out, guard.removed_chars, guard.removed_runs

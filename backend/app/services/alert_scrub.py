"""FIX130 E4 — an alert carries what happened and the numbers, nothing else.

Alerts leave the machine (ntfy, e-mail, webhooks) and are stored
(``alert_events.details_json``). Before FIX130 the stuck-transaction alert
carried the head of the SQL of the oldest session — and a query holds
whatever the application bound into it: message text, e-mail addresses,
tokens. The builders no longer put such text in an alert; this module is the
last hop that makes sure of it, used by ``AlertService`` (log line, row,
rule webhook) and ``alert_channels.deliver`` (ntfy / e-mail / webhook, so the
ops watcher too). ``infra/backup/lib.sh`` ``redact`` is the shell twin.

* keys that carry SQL or user content (``DROP_KEYS``) are removed, at any depth;
* strings are scrubbed: SQL statements, credentials in URLs, JWTs,
  ``key=value`` secrets, API-key-shaped and other long random tokens.

The SQL head of a stuck session is kept in ONE place: the
``pg_stuck_transaction`` application log line (``pg_activity.record_sample``
and the ops watcher's own log).
"""

from __future__ import annotations

import re
from typing import Any

# Keys whose VALUE is never sent or stored with an alert: it is SQL text or
# user content by construction.
DROP_KEYS = frozenset({"query", "sql", "statement", "prompt", "content", "messages", "body"})

# A statement, from its keyword to the end of the line. Case-insensitive
# multi-word shapes (what psql users type) + upper-case keywords (what an
# ORM emits).
_SQL = re.compile(
    r"(?:"
    r"(?i:\bselect\b[^\n]*?\bfrom\b)"
    r"|(?i:\binsert\s+into\b)"
    r"|(?i:\bupdate\s+\S+\s+set\b)"
    r"|(?i:\bdelete\s+from\b)"
    r"|(?i:\b(?:alter|create|drop|truncate)\s+(?:table|index|database|schema|role|user|extension)\b)"
    r"|(?i:\bcopy\s+\S+\s+(?:from|to)\b)"
    r"|\b(?:SELECT|INSERT|UPDATE|DELETE|WITH|BEGIN|COMMIT|ROLLBACK|ALTER|CREATE|DROP|TRUNCATE|COPY|VACUUM|LOCK)\b\s+\S"
    r")[^\n]*"
)
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]*)?")
_BEARER = re.compile(r"(?i)\b(bearer\s+)(?!\[redacted)[A-Za-z0-9._~+/\-]{8,}=*")
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(token|password|passwd|pwd|secret|api[_-]?key|access[_-]?key)(\s*[=:]\s*)"
    r"(?!\[redacted)[^\s,;'\"]{4,}"
)
_KEY_SHAPED = re.compile(
    r"\b(?:sk|pk|rk|re|tk|ak|ghp|gho|ghs|glpat|xox[abprs])[-_][A-Za-z0-9_\-]{12,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
)
# Long random-looking runs: ≥32 chars without '-' (a UUID's longest run is
# 12), or ≥40 with it; must mix letters and digits.
_LONG_TOKEN = re.compile(
    r"(?<![A-Za-z0-9+/_\-])"
    r"(?=[A-Za-z0-9+/_\-]*[0-9])(?=[A-Za-z0-9+/_\-]*[A-Za-z])"
    r"(?:[A-Za-z0-9+/_]{32,}={0,2}|[A-Za-z0-9_\-]{40,})"
    r"(?![A-Za-z0-9+/_\-])"
)


def scrub_text(text: str) -> str:
    """``text`` with SQL, credentials and token-shaped strings replaced."""
    if not text:
        return text
    out = _SQL.sub("[sql redacted]", text)
    out = _URL_CREDENTIALS.sub(r"\1[redacted]@", out)
    out = _JWT.sub("[redacted token]", out)
    out = _BEARER.sub(r"\1[redacted]", out)
    out = _ASSIGNED_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", out)
    out = _KEY_SHAPED.sub("[redacted key]", out)
    out = _LONG_TOKEN.sub("[redacted]", out)
    return out


def scrub_value(value: Any) -> Any:
    """Recursively scrub a JSON-shaped value; dict keys in ``DROP_KEYS`` go."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {
            k: scrub_value(v)
            for k, v in value.items()
            if not (isinstance(k, str) and k.lower() in DROP_KEYS)
        }
    if isinstance(value, (list, tuple)):
        return [scrub_value(v) for v in value]
    return value

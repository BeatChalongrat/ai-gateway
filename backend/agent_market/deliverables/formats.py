"""Which of an agent's own files a render makes."""

from __future__ import annotations


def wanted_formats(req: dict, default: list) -> list:
    """The formats a render was asked for: the package's default when the request names none. An explicit
    [] is "none of the agent's own files" — AM10: a render that fills the form a person dropped into the
    round and makes nothing else (the register or the report already delivered is not made again)."""
    asked = req.get("formats")
    return list(default) if asked is None else list(asked)

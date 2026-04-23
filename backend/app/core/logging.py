"""Logging configuration.

This module configures two things that have to agree with each other:

1.  **structlog** — the JSON renderer the app uses for structured events
    (``request_phases`` in particular). structlog is a thin wrapper over
    stdlib ``logging``; the final JSON line ends up as a stdlib
    ``LogRecord`` whose message is the already-serialised JSON string.

2.  **stdlib logging** — provides the actual sinks (``StreamHandler`` to
    stdout + optional ``FileHandler`` to ``GATEWAY_LOG_PATH``). The
    structlog pipeline does NOT itself write to any file; it hands the
    record back to the stdlib logger which then routes to every attached
    handler.

Prior bug
---------
``configure_logging()`` called ``logging.basicConfig()`` only, which
attaches exactly one ``StreamHandler`` to stdout. The ``GATEWAY_LOG_PATH``
env var was read by ``perf/run-baseline.py`` (as a READER) but never by
the backend (as a WRITER). The existing ``logs/gateway.log`` content was
produced solely by shell-level stdout redirection from a previous
supervisor invocation; when a fresh bash task launched the backend with
stdout going to ``logs/backend-override.log`` instead, the ``request_phases``
lines silently stopped flowing to the path the runner was grepping.

Fix
---
If ``GATEWAY_LOG_PATH`` is set in the environment, wire a stdlib
``FileHandler`` onto the root logger (and onto the ``request_phases``
logger in particular) so the JSON-rendered records land in that file
regardless of where stdout happens to point. Emit a startup marker line
AFTER the handler is attached so operators can verify the sink from the
log file itself.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import structlog


_STARTUP_LOGGER_NAME = "app.startup"


def _resolve_log_path() -> Path | None:
    """Return the path the FileHandler should write to, or ``None``.

    We accept ``GATEWAY_LOG_PATH`` verbatim. Windows tolerates both forward
    and back slashes; we just expanduser / resolve so a bash-style path
    (``/c/Users/...``) works side-by-side with a Windows one
    (``C:\\Users\\...``). The parent directory is created if missing so a
    cold start on a fresh checkout does not crash on a missing ``logs/``
    dir.
    """
    raw = os.environ.get("GATEWAY_LOG_PATH", "").strip()
    if not raw:
        return None
    # Git Bash / MSYS paths: "/c/Users/..." — Python can open these on
    # Windows only via MSYS's wrapper; translate to a native path so
    # FileHandler (which uses plain ``open``) works regardless.
    if os.name == "nt" and raw.startswith("/") and len(raw) >= 3 and raw[2] == "/":
        # "/c/Users/..."  ->  "c:/Users/..."
        raw = raw[1].upper() + ":" + raw[2:]
    p = Path(raw).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _attach_file_handler(path: Path) -> logging.FileHandler:
    """Attach a stdlib ``FileHandler`` to the root logger.

    structlog's ``JSONRenderer`` has already produced a JSON string by
    the time the stdlib layer sees the record, so the stdlib formatter
    must be ``"%(message)s"`` — anything else would wrap our JSON in
    additional text and break the downstream parser.

    The handler is attached to the root logger (so every module — the
    ``request_phases`` emitter, ``dlp.retention``, etc. — routes into the
    file), and also explicitly to the ``request_phases`` logger itself
    with ``propagate=True`` so a future contributor who switches that
    logger to ``propagate=False`` still keeps the file sink.
    """
    file_handler = logging.FileHandler(str(path), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    # Avoid duplicate handlers if configure_logging() is called twice
    # (e.g. under --reload or a test harness).
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == file_handler.baseFilename:
            root.removeHandler(h)
    root.addHandler(file_handler)

    phases_logger = logging.getLogger("request_phases")
    for h in list(phases_logger.handlers):
        if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == file_handler.baseFilename:
            phases_logger.removeHandler(h)
    phases_logger.addHandler(file_handler)
    phases_logger.setLevel(logging.INFO)
    # propagate stays True so the root handlers (incl. stdout) still see
    # the line — we want BOTH sinks, not one at the expense of the other.

    return file_handler


def configure_logging() -> None:
    """Configure stdlib + structlog. Safe to call multiple times."""
    # basicConfig only takes effect on a logger with no handlers; if
    # something already configured the root logger we do not want to
    # clobber it. Setting format explicitly guarantees stdout lines are
    # the bare JSON body, not prefixed with "INFO:root:" etc.
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    else:
        root.setLevel(logging.INFO)

    # IMPORTANT: structlog has to write through stdlib ``logging`` — not
    # through its default ``PrintLogger`` which writes directly to
    # ``sys.stdout`` and so bypasses any ``FileHandler`` attached to the
    # stdlib root logger. That bypass was the root cause of the
    # GATEWAY_LOG_PATH data loss: ``request_phases`` lines went to stdout
    # (captured via shell redirection) but never to the file the runner
    # grepped. Fix: ``logger_factory=structlog.stdlib.LoggerFactory``.
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    log_path = _resolve_log_path()
    if log_path is not None:
        _attach_file_handler(log_path)
        # Emit AFTER the handler is attached so the marker itself lands
        # in the file (user directive). structlog goes through stdlib
        # logging, so this record will hit every attached handler — both
        # the stdout StreamHandler and the new FileHandler.
        startup = structlog.get_logger(_STARTUP_LOGGER_NAME)
        startup.info(
            "gateway_logging_active",
            event_msg=f"Gateway per-phase logging active -> path={log_path}",
            path=str(log_path),
        )

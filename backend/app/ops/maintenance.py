"""FIX131 D3 — planned work never pages: the maintenance window.

    python -m app.ops.maintenance start <minutes> "<reason>"
    python -m app.ops.maintenance stop
    python -m app.ops.maintenance status

Writes (or deletes) one Redis key, ``sran:ops:maintenance``, with a TTL of
<minutes> — the same Redis the pager shares between the backend's alert
rules and the ops watcher. While it exists every finding is logged and held
(``pager_decision action=maintenance``), nothing is sent; an incident still
open when the window ends (``stop`` or the TTL) pages normally and says it
began during maintenance. The TTL means a forgotten window ends by itself;
it is capped at MAX_MINUTES so a typo cannot silence the pager for days.

Run it where the backend image and its Redis settings are, e.g. from a
deploy script:

    docker compose -p ai-gateway exec -T ops-watch python -m app.ops.maintenance start 15 "deploy FIX131"
    …deploy…
    docker compose -p ai-gateway exec -T ops-watch python -m app.ops.maintenance stop

Exit 0 = done; 1 = Redis did not answer (the window is NOT set: say so, do
not assume it); 2 = usage.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timedelta, timezone

MAX_MINUTES = 480

USAGE = 'usage: python -m app.ops.maintenance start <minutes> "<reason>" | stop | status'


def _store():
    from app.core.config import settings
    from app.services.alert_pager import RedisStore

    return RedisStore(settings.redis_url)


def main(argv: list[str] | None = None, *, store=None, now: datetime | None = None, out=print) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("start", "stop", "status"):
        out(USAGE)
        return 2
    store = store if store is not None else _store()
    now = now or datetime.now(timezone.utc)
    try:
        if args[0] == "start":
            if len(args) < 3:
                out(USAGE)
                return 2
            try:
                minutes = float(args[1])
            except ValueError:
                out(USAGE)
                return 2
            if minutes <= 0:
                out(USAGE)
                return 2
            if minutes > MAX_MINUTES:
                out(f"capped at {MAX_MINUTES} min (asked {minutes:g}) — start it again if the work runs longer")
                minutes = MAX_MINUTES
            reason = " ".join(args[2:]).strip()[:200] or "maintenance"
            until = now + timedelta(minutes=minutes)
            rec = {
                "reason": reason,
                "started_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "by": os.environ.get("USER") or os.environ.get("USERNAME") or socket.gethostname(),
            }
            store.set_maintenance(rec, minutes * 60)
            out(f"maintenance window ON until {rec['until']} ({minutes:g} min): {reason} — "
                "findings are logged, not paged; what is still open afterwards pages normally")
            return 0
        if args[0] == "stop":
            had = store.clear_maintenance()
            out("maintenance window OFF" if had else "no maintenance window was open")
            return 0
        rec = store.maintenance()
        out(json.dumps({"maintenance": rec}, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        out(f"Redis did not answer ({type(exc).__name__}) — the maintenance window is NOT "
            f"{'set' if args[0] == 'start' else 'changed'}; pages are not held")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

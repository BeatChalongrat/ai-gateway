"""FIX131 D4 — fire a test alert without paging the owner.

    python -m app.ops.fire_test_alert "<message>"

Always ``[TEST]`` in title and body, and sent ONLY to ALERT_TEST_NTFY_URL
(a throwaway topic) — never ALERT_NTFY_URL, never e-mail, never the global
webhook, whatever the environment. With no ALERT_TEST_NTFY_URL nothing is
sent (logged). Prints the outcome per channel. Exit 0 = sent or nothing to
send; 1 = the test topic refused it.
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None, *, out=print) -> int:
    from app.services.alert_channels import Notification, configured_channels, deliver

    args = list(sys.argv[1:] if argv is None else argv)
    message = " ".join(args).strip() or "test alert from the SRAN pager"
    n = Notification(title="Test alert — ignore", message=message, severity="info",
                     source="test-fire", tags=("test",), test=True)
    outcome = deliver(n)
    out(json.dumps({"channels": configured_channels(test=True), "outcome": outcome}))
    return 0 if not outcome or any(outcome.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

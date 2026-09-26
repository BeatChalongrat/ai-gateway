"""AM1c §6 — where did an alert land? Runs INSIDE ops-watch (it has the topic
URLs in its environment; they are never printed).

    docker exec -i ai-gateway-ops-watch-1 python - fire-test "Agent Market pager test" < scripts/probes/AM1c/pager_check.py
    docker exec -i ai-gateway-ops-watch-1 python - count "<marker>" <unix since> < scripts/probes/AM1c/pager_check.py

fire-test sends one [TEST] alert through app.ops.agent_market_alerts, then
both modes poll every topic (agent, core, core test) for messages since the
given second and print, per topic, how many there were and how many carry
the marker — counts and matching titles only.
"""
import json
import os
import sys
import time

import httpx

mode, marker = sys.argv[1], sys.argv[2]
since = sys.argv[3] if len(sys.argv) > 3 else None
if mode == "fire-test":
    from app.ops import agent_market_alerts as ama

    since = str(int(time.time()) - 1)
    print(json.dumps({"sent_to": ama.fire_test()}))
    time.sleep(5)
topics = {
    "agent (AGENT_MARKET_NTFY_URL)": (os.environ.get("AGENT_MARKET_NTFY_URL", ""), ""),
    "core (ALERT_NTFY_URL)": (os.environ.get("ALERT_NTFY_URL", ""), os.environ.get("ALERT_NTFY_TOKEN", "")),
    "core test (ALERT_TEST_NTFY_URL)": (os.environ.get("ALERT_TEST_NTFY_URL", ""), ""),
}
out = {}
for name, (url, token) in topics.items():
    if not url.strip():
        out[name] = "unset"
        continue
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.get(url.strip().rstrip("/") + "/json", params={"poll": "1", "since": since}, headers=headers, timeout=15)
    msgs = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    msgs = [m for m in msgs if m.get("event", "message") == "message"]
    hits = [m for m in msgs if marker.lower() in f"{m.get('title', '')} {m.get('message', '')}".lower()]
    out[name] = {"http": r.status_code, "messages_since": len(msgs), "matching": len(hits),
                 "matching_titles": [m.get("title", "")[:90] for m in hits]}
print(json.dumps({"since": int(since), "topics": out}, ensure_ascii=False, indent=1))

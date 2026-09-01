# B — rebuild evidence (captured 2026-09-01T22:01:15Z)

## before (image ids of the running containers, captured before any build)
chat-edge-api c3abe4854e75 (created 2026-09-01T20:32:05Z)
backend       7f63a02c5caa (created 2026-09-01T11:56:40Z)
dlp-sidecar   6fc931303057 (created 2026-09-01T11:56:42Z)
web-chat      7a93c0a2f0ed (created 2026-09-01T08:38:35Z)

## after
ai-gateway-dlp-sidecar-1 running=d8364c56d234 latest=d8364c56d234 match=yes image_created=2026-09-01T21:56:35.127495718Z container_started=2026-09-01T21:59:32.877678953Z
ai-gateway-backend-1 running=e670d657a26c latest=e670d657a26c match=yes image_created=2026-09-01T21:51:15.915826944Z container_started=2026-09-01T22:00:09.827511781Z
ai-gateway-chat-edge-api running=f13d4bbb42de latest=f13d4bbb42de match=yes image_created=2026-09-01T21:59:24.186131466Z container_started=2026-09-01T22:00:15.289462912Z
ai-gateway-web-chat running=ef7ff37a32af latest=ef7ff37a32af match=yes image_created=2026-09-01T21:54:15.126201467Z container_started=2026-09-01T22:00:15.748046787Z

## B3 live config
STREAM_LEDGER_TTL_SECONDS=600
STREAM_LEDGER_MAX_EVENTS=8000
time=2026-09-01T22:00:15.663Z level=INFO msg="stream-resume ledger configured" ttl_seconds=600 max_events_per_run=8000 durable_mirror=true

## health
{"run_ledger_overflow_drops":0,"service":"chat-edge-api","status":"ok"}


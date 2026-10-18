# Rolling back a service

Written by `python scripts/ops/rollback_book.py write` from what live ran at 2026-10-18T16:49:00Z — never edited by hand: the gate's `rollback_book` lane is red when this page no longer says what runs, when a tag it names is gone, or when a service has no line. Run every command from the root of this repository, on the host live runs on (Git Bash on Windows). A rollback is a deploy: it refuses on battery like one (`scripts/ops/power.sh`).

Each block: the command that goes back, what runs now, how to see it worked, the command forward again, and what going back does **not** undo. The image it goes back to is the one that ran before — kept by the deploy that replaced it.

## The services a card deploys — in the order to go back

### backend + ops-watch — now 134b023, back to 67d9fc0

```bash
bash scripts/deploy-backend.sh --image ai-gateway-backend:rollback-67d9fc0 -p ai-gateway
```

- **Now:** `ai-gateway-backend:134b023`, revision 134b023 — deployed before the deploy record began. Back to `ai-gateway-backend:rollback-67d9fc0` (the newest rollback image kept on this host).
- **It worked when** the deploy's last lines say `done: backend … healthy revision 67d9fc0` and `probe: … 0 failed`; this prints `67d9fc0` for each: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-backend-1 ai-gateway-ops-watch-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/deploy-backend.sh --image ai-gateway-backend:134b023 -p ai-gateway`
- **Not undone:** the schema migrations it applied to the gateway's database (the old image runs on the newer schema: the deploy refuses an image whose migration head differs unless told — `--allow-migrations` when the migration only added, otherwise `--stop-first`); files people uploaded (volume `ai-gateway_backend_uploads`); usage and credits recorded; e-mails sent.

### admin-ui — now 134b023, back to f01f1ee

```bash
bash scripts/ops/swap.sh admin-ui http://127.0.0.1:23000/login --image ai-gateway-admin-ui:rollback-f01f1ee -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml
```

- **Now:** `ai-gateway-admin-ui:134b023`, revision 134b023 — deployed before the deploy record began. Back to `ai-gateway-admin-ui:rollback-f01f1ee` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `f01f1ee`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-admin-ui-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh admin-ui http://127.0.0.1:23000/login --image ai-gateway-admin-ui:134b023 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml`
- **Not undone:** settings saved and audit entries written through it.

### web-chat — now 134b023, back to f01f1ee

```bash
bash scripts/ops/swap.sh web-chat http://127.0.0.1:23002/ --image ai-gateway-web-chat:rollback-f01f1ee -p ai-gateway -f docker-compose.webchat.yml
```

- **Now:** `ai-gateway-web-chat:134b023`, revision 134b023 — deployed before the deploy record began. Back to `ai-gateway-web-chat:rollback-f01f1ee` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `f01f1ee`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-web-chat`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh web-chat http://127.0.0.1:23002/ --image ai-gateway-web-chat:134b023 -p ai-gateway -f docker-compose.webchat.yml`
- **Not undone:** nothing it stored — it stores nothing; a page open in a browser keeps the code it loaded until it reloads.

### agent-runner — now 134b023, back to f01f1ee

```bash
bash scripts/ops/swap.sh agent-runner health:sran-agent-runner --image ai-gateway-agent-runner:rollback-f01f1ee -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market
```

- **Now:** `ai-gateway-agent-runner:134b023`, revision 134b023 — deployed before the deploy record began. Back to `ai-gateway-agent-runner:rollback-f01f1ee` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `f01f1ee`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' sran-agent-runner`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh agent-runner health:sran-agent-runner --image ai-gateway-agent-runner:134b023 -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market`
- **Not undone:** Agent Market's schema migrations (the runner applies them when it starts; the old runner runs on the newer schema — additive only); the engine each installed version registered (a version keeps its engine); runs' records, files delivered, credits charged, e-mails sent.

### chat-edge-api — now 134b023, back to f01f1ee

```bash
bash scripts/ops/swap.sh chat-edge-api https://localhost:4000/health --image ai-gateway-chat-edge-api:rollback-f01f1ee -p ai-gateway -f docker-compose.webchat.yml
```

- **Now:** `ai-gateway-chat-edge-api:134b023`, revision 134b023 — deployed before the deploy record began. Back to `ai-gateway-chat-edge-api:rollback-f01f1ee` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `f01f1ee`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-chat-edge-api`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh chat-edge-api https://localhost:4000/health --image ai-gateway-chat-edge-api:134b023 -p ai-gateway -f docker-compose.webchat.yml`
- **Not undone:** migrations applied to the chat database (applied by hand; they stay); messages, files and memory stored; credits charged for turns.

## Every other service built here

### backup — now 369345f, back to 7cac894

```bash
bash scripts/ops/swap.sh backup running:ai-gateway-backup --image ai-gateway-backup:rollback-7cac894 -p ai-gateway -f docker-compose.webchat.yml
```

- **Now:** `ai-gateway-backup:369345f`, revision 369345f — deployed before the deploy record began. Back to `ai-gateway-backup:rollback-7cac894` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `7cac894`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-backup`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh backup running:ai-gateway-backup --image ai-gateway-backup:369345f -p ai-gateway -f docker-compose.webchat.yml`
- **Not undone:** anything it already wrote, stored or sent.

### dlp-sidecar — now 7d773b9, back to 63c5071

```bash
bash scripts/ops/swap.sh dlp-sidecar health:ai-gateway-dlp-sidecar-1 --image ai-gateway-dlp-sidecar:rollback-63c5071 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml
```

- **Now:** `ai-gateway-dlp-sidecar:7d773b9`, revision 7d773b9 — deployed before the deploy record began. Back to `ai-gateway-dlp-sidecar:rollback-63c5071` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `63c5071`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-dlp-sidecar-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh dlp-sidecar health:ai-gateway-dlp-sidecar-1 --image ai-gateway-dlp-sidecar:7d773b9 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml`
- **Not undone:** anything it already wrote, stored or sent.

### doc-converter — now 2230a75, back to b11d4a6

```bash
bash scripts/ops/swap.sh doc-converter health:sran-agent-market-doc-converter-1 --image ai-gateway-doc-converter:rollback-b11d4a6 -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market
```

- **Now:** `ai-gateway-doc-converter:2230a75`, revision 2230a75 — deployed before the deploy record began. Back to `ai-gateway-doc-converter:rollback-b11d4a6` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `b11d4a6`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' sran-agent-market-doc-converter-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh doc-converter health:sran-agent-market-doc-converter-1 --image ai-gateway-doc-converter:2230a75 -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market`
- **Not undone:** files already converted, and what was delivered from them.

### gateway — now 380b3f4, back to 4fbbc43

```bash
bash scripts/ops/swap.sh gateway health:ai-gateway-gateway-1 --image ai-gateway-gateway:rollback-4fbbc43 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml
```

- **Now:** `ai-gateway-gateway:380b3f4`, revision 380b3f4 — deployed before the deploy record began. Back to `ai-gateway-gateway:rollback-4fbbc43` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `4fbbc43`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-gateway-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh gateway health:ai-gateway-gateway-1 --image ai-gateway-gateway:380b3f4 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml`
- **Not undone:** answers already in its response cache (kept up to an hour) and the usage it logged.

### kb-models — now e7513f0, back to 574fbd7

```bash
bash scripts/ops/swap.sh kb-models health:ai-gateway-kb-models --image ai-gateway-kb-models:rollback-574fbd7 -p ai-gateway -f docker-compose.webchat.yml
```

- **Now:** `ai-gateway-kb-models:e7513f0`, revision e7513f0 — deployed before the deploy record began. Back to `ai-gateway-kb-models:rollback-574fbd7` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `574fbd7`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-kb-models`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh kb-models health:ai-gateway-kb-models --image ai-gateway-kb-models:e7513f0 -p ai-gateway -f docker-compose.webchat.yml`
- **Not undone:** anything it already wrote, stored or sent.

### log-worker — now e7513f0, back to 20260925_012935

```bash
bash scripts/ops/swap.sh log-worker health:ai-gateway-log-worker-1 --image ai-gateway-log-worker:rollback-20260925_012935 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml
```

- **Now:** `ai-gateway-log-worker:e7513f0`, revision e7513f0 — deployed before the deploy record began. Back to `ai-gateway-log-worker:rollback-20260925_012935` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints an id starting `sha256:6403e6d3f35e` (the image has no revision label): `docker inspect -f '{{.Image}}' ai-gateway-log-worker-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh log-worker health:ai-gateway-log-worker-1 --image ai-gateway-log-worker:e7513f0 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml`
- **Not undone:** anything it already wrote, stored or sent.

### mcp-gateway — now 28490cb, back to pre-am9

```bash
bash scripts/ops/swap.sh mcp-gateway health:ai-gateway-mcp-gateway-1 --image ai-gateway-mcp-gateway:rollback-pre-am9 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml
```

- **Now:** `ai-gateway-mcp-gateway:28490cb`, revision 28490cb — deployed before the deploy record began. Back to `ai-gateway-mcp-gateway:rollback-pre-am9` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints an id starting `sha256:ed51bf8aa04f` (the image has no revision label): `docker inspect -f '{{.Image}}' ai-gateway-mcp-gateway-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh mcp-gateway health:ai-gateway-mcp-gateway-1 --image ai-gateway-mcp-gateway:28490cb -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml`
- **Not undone:** anything it already wrote, stored or sent.

### ner-service — now 676db92, back to 20260925_012935

```bash
bash scripts/ops/swap.sh ner-service health:ai-gateway-ner-service-1 --image ai-gateway-ner-service:rollback-20260925_012935 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml
```

- **Now:** `ai-gateway-ner-service:676db92`, revision 676db92 — deployed before the deploy record began. Back to `ai-gateway-ner-service:rollback-20260925_012935` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints an id starting `sha256:31246632e9a5` (the image has no revision label): `docker inspect -f '{{.Image}}' ai-gateway-ner-service-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh ner-service health:ai-gateway-ner-service-1 --image ai-gateway-ner-service:676db92 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml`
- **Not undone:** anything it already wrote, stored or sent.

### portal — now 96ecd58, back to e7513f0

```bash
bash scripts/ops/swap.sh portal http://127.0.0.1:23001/ --image ai-gateway-portal:rollback-e7513f0 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml
```

- **Now:** `ai-gateway-portal:96ecd58`, revision 96ecd58 — deployed before the deploy record began. Back to `ai-gateway-portal:rollback-e7513f0` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `e7513f0`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' ai-gateway-portal-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh portal http://127.0.0.1:23001/ --image ai-gateway-portal:96ecd58 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml`
- **Not undone:** keys and settings saved through it.

### rate-limiter — now 067bd2b, back to pre-fix129

```bash
bash scripts/ops/swap.sh rate-limiter health:ai-gateway-rate-limiter-1 --image ai-gateway-rate-limiter:rollback-pre-fix129 -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml
```

- **Now:** `ai-gateway-rate-limiter:latest`, revision 067bd2b — deployed before the deploy record began. Back to `ai-gateway-rate-limiter:rollback-pre-fix129` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints an id starting `sha256:cea975a55084` (the image has no revision label): `docker inspect -f '{{.Image}}' ai-gateway-rate-limiter-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh rate-limiter health:ai-gateway-rate-limiter-1 --image ai-gateway-rate-limiter:rollback-067bd2b -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml` — the rollback keeps today's image under that name (`latest` moves with it).
- **Not undone:** anything it already wrote, stored or sent.

### web-fetcher — now 3c7bccc, back to 3560e9d

```bash
bash scripts/ops/swap.sh web-fetcher health:sran-agent-market-web-fetcher-1 --image ai-gateway-web-fetcher:rollback-3560e9d -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market
```

- **Now:** `ai-gateway-web-fetcher:3c7bccc`, revision 3c7bccc — deployed before the deploy record began. Back to `ai-gateway-web-fetcher:rollback-3560e9d` (the newest rollback image kept on this host).
- **It worked when** the swap's last line has no `RED`; this prints `3560e9d`: `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' sran-agent-market-web-fetcher-1`; and `bash scripts/smoke.sh` ends `GREEN` (right after a deploy, give the gateway 90 s quiet first — its limit answers 429).
- **Forward again:** `bash scripts/ops/swap.sh web-fetcher health:sran-agent-market-web-fetcher-1 --image ai-gateway-web-fetcher:3c7bccc -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market`
- **Not undone:** pages already fetched, and what was delivered from them.

## More than one service: the order, and what moves together

Go back in the reverse of the order a deploy goes in — backend, admin-ui, web-chat, agent-runner, chat-edge-api — so every state on the way back is one the deploy already passed through. Any other service after them, in any order.

- **backend and ops-watch** are one image: `deploy-backend.sh` moves both. Never swap ops-watch alone.
- **agent-runner and chat-edge-api** move together, the runner back first: the edge drives the runner's engines and speaks its words.
- **web-chat goes back with chat-edge-api:** the page calls the edge's routes; a page newer than its edge can call one that is not there.
- **Deployed together at 134b023:** backend + ops-watch, admin-ui, web-chat, agent-runner, chat-edge-api — a fault in that deploy takes all of them back, each to its own image above.
- **Deployed together at e7513f0:** kb-models, log-worker — a fault in that deploy takes all of them back, each to its own image above.

## Where the tags come from

- `<repository>:<revision>` — seven characters of the commit, by the builds: `bash scripts/gate.sh build <sha>`, `scripts/build-images.sh --rev <rev> <service>`, `scripts/agent-market/build*.sh` (`scripts/ops/rev.sh` is the rule; `--rev` takes anything git accepts).
- `<repository>:rollback-<revision>` — the image a service ran when a deploy replaced it, kept by that deploy: `deploy-backend.sh` always, `swap.sh --image` since AM-24b (by hand before).
- The tag the compose file runs (`latest` here) — what a service starts from; the deploy points it at the new image.
- Every deploy adds a line to `scripts/ops/results/deploys.tsv` on this host: time, tool, project, service, image, revision, the rollback image kept. After a deploy: `python scripts/ops/rollback_book.py write`, then commit this page.

## Practise on the test stack first

The backend's way back, on the test stack (compose project `sran-test`), under a tag of its own — `drill` — so nothing live starts from is moved (the deploy refuses a tag another project runs):

```bash
docker tag ai-gateway-backend:134b023 ai-gateway-backend:drill
TS_BACKEND_TAG=drill bash scripts/gate.sh walk -- env AM7_NO_NER=1 bash scripts/agent-market/test_stack.sh up 134b023
TS_BACKEND_TAG=drill DEPLOY_MAINTENANCE_CMD= bash scripts/deploy-backend.sh --image ai-gateway-backend:rollback-67d9fc0 -p sran-test -f docker-compose.test-stack.yml --no-ops-watch --probe-url http://127.0.0.1:18080/live
TS_BACKEND_TAG=drill DEPLOY_MAINTENANCE_CMD= bash scripts/deploy-backend.sh --image ai-gateway-backend:134b023 -p sran-test -f docker-compose.test-stack.yml --no-ops-watch --probe-url http://127.0.0.1:18080/live
bash scripts/gate.sh stack-stop
```

(`gate.sh walk --` holds the gate's lock and waits for the host to have room before the stack starts; `stack-stop` stops it and removes nothing.) The drill leaves two tags, `drill` and `rollback-134b023` — both the image live runs now; it moves no tag live starts from.

It worked when each deploy ends `done: backend … healthy revision …` — 67d9fc0, then 134b023 — with `probe: … 0 failed`. The probe asks the test gateway (18080), as live's asks live's: the backend's own port (18081) goes down with the old container whatever the bridge does.

## Services with nothing to roll back

| service | compose file | why |
|---|---|---|
| agent-market-init | docker-compose.agent-market.yml | a one-shot: it ran once (exit 0) when its project started; nothing runs to roll back. |
| minio | docker-compose.webchat.yml | not run on live: its profile `minio` is not started. |
| minio-init | docker-compose.webchat.yml | not run on live: its profile `minio` is not started. |
| postgres-webchat | docker-compose.webchat.yml | `pgvector/pgvector:pg16`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| redis-webchat | docker-compose.webchat.yml | `redis:7-alpine`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| autoheal | docker-compose.yml | `willfarrell/autoheal:1.2.0`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| chat-edge-api | docker-compose.yml | live runs it from `docker-compose.webchat.yml` (above). |
| edge | docker-compose.yml | `caddy:2-alpine`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| grafana | docker-compose.yml | `grafana/grafana:11.1.0`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| kb-models | docker-compose.yml | live runs it from `docker-compose.webchat.yml` (above). |
| mock-provider | docker-compose.yml | built here (`ai-gateway-mock-provider:latest`, revision none), but no earlier image of it is kept on this host: there is nothing to go back to. |
| nginx | docker-compose.yml | not run on live: its profiles `scaling`, `desktop` are not started. |
| pgbouncer | docker-compose.yml | not run on live: its profile `scaling` is not started. |
| postgres | docker-compose.yml | `pgvector/pgvector:pg16`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| prometheus | docker-compose.yml | `prom/prometheus:v2.53.0`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| redis | docker-compose.yml | `redis:7`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| redis-exporter | docker-compose.yml | `oliver006/redis_exporter:v1.61.0`, pinned in the file: nothing built here, no earlier image kept. Another version is a change to the file; going back would not undo data written in the newer one's format. |
| worker | docker-compose.yml | not run on live: its profile `scaling` is not started. |
| ops-watch | docker-compose.yml | moves with backend (above): one image. |

<!-- rollback-book-state
{"written": "2026-10-18T16:49:00Z", "services": {
 "docker-compose.agent-market.yml:agent-market-init": {"extra": ["--profile", "agent-market"], "files": ["docker-compose.agent-market.yml"], "kind": "oneshot", "name": "agent-market-init", "project": "sran-agent-market"},
 "docker-compose.agent-market.yml:agent-runner": {"container": "sran-agent-runner", "current_id": "91e3ad53dd63", "current_rev": "134b023", "current_tag": "ai-gateway-agent-runner:134b023", "deployed": "", "extra": ["--profile", "agent-market"], "files": ["docker-compose.agent-market.yml"], "kind": "swap", "name": "agent-runner", "probe": "health:sran-agent-runner", "project": "sran-agent-market", "ref": "ai-gateway-agent-runner:latest", "repo": "ai-gateway-agent-runner", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "c6e0348d7da7", "rollback_label": "f01f1ee", "rollback_rev": "f01f1ee", "rollback_tag": "ai-gateway-agent-runner:rollback-f01f1ee"},
 "docker-compose.agent-market.yml:doc-converter": {"container": "sran-agent-market-doc-converter-1", "current_id": "527e9177ce08", "current_rev": "2230a75", "current_tag": "ai-gateway-doc-converter:2230a75", "deployed": "", "extra": ["--profile", "agent-market"], "files": ["docker-compose.agent-market.yml"], "kind": "swap", "name": "doc-converter", "probe": "health:sran-agent-market-doc-converter-1", "project": "sran-agent-market", "ref": "ai-gateway-doc-converter:latest", "repo": "ai-gateway-doc-converter", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "59dc40116cfc", "rollback_label": "b11d4a6", "rollback_rev": "b11d4a6", "rollback_tag": "ai-gateway-doc-converter:rollback-b11d4a6"},
 "docker-compose.agent-market.yml:web-fetcher": {"container": "sran-agent-market-web-fetcher-1", "current_id": "dc057ad3e41c", "current_rev": "3c7bccc", "current_tag": "ai-gateway-web-fetcher:3c7bccc", "deployed": "", "extra": ["--profile", "agent-market"], "files": ["docker-compose.agent-market.yml"], "kind": "swap", "name": "web-fetcher", "probe": "health:sran-agent-market-web-fetcher-1", "project": "sran-agent-market", "ref": "ai-gateway-web-fetcher:latest", "repo": "ai-gateway-web-fetcher", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "01c011c943d0", "rollback_label": "3560e9d", "rollback_rev": "3560e9d", "rollback_tag": "ai-gateway-web-fetcher:rollback-3560e9d"},
 "docker-compose.webchat.yml:backup": {"container": "ai-gateway-backup", "current_id": "96a828db4682", "current_rev": "369345f", "current_tag": "ai-gateway-backup:369345f", "deployed": "", "extra": [], "files": ["docker-compose.webchat.yml"], "kind": "swap", "name": "backup", "probe": "running:ai-gateway-backup", "project": "ai-gateway", "ref": "ai-gateway-backup:latest", "repo": "ai-gateway-backup", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "eeff8c06e65b", "rollback_label": "7cac894", "rollback_rev": "7cac894", "rollback_tag": "ai-gateway-backup:rollback-7cac894"},
 "docker-compose.webchat.yml:chat-edge-api": {"container": "ai-gateway-chat-edge-api", "current_id": "5972ee15f1b0", "current_rev": "134b023", "current_tag": "ai-gateway-chat-edge-api:134b023", "deployed": "", "extra": [], "files": ["docker-compose.webchat.yml"], "kind": "swap", "name": "chat-edge-api", "probe": "https://localhost:4000/health", "project": "ai-gateway", "ref": "ai-gateway-chat-edge-api", "repo": "ai-gateway-chat-edge-api", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "6aef82240bd8", "rollback_label": "f01f1ee", "rollback_rev": "f01f1ee", "rollback_tag": "ai-gateway-chat-edge-api:rollback-f01f1ee"},
 "docker-compose.webchat.yml:kb-models": {"container": "ai-gateway-kb-models", "current_id": "a4232f6b99f3", "current_rev": "e7513f0", "current_tag": "ai-gateway-kb-models:e7513f0", "deployed": "", "extra": [], "files": ["docker-compose.webchat.yml"], "kind": "swap", "name": "kb-models", "probe": "health:ai-gateway-kb-models", "project": "ai-gateway", "ref": "ai-gateway-kb-models:latest", "repo": "ai-gateway-kb-models", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "97357803c579", "rollback_label": "574fbd7", "rollback_rev": "574fbd7", "rollback_tag": "ai-gateway-kb-models:rollback-574fbd7"},
 "docker-compose.webchat.yml:minio": {"extra": [], "files": ["docker-compose.webchat.yml"], "kind": "off", "name": "minio", "profiles": ["minio"], "project": "ai-gateway"},
 "docker-compose.webchat.yml:minio-init": {"extra": [], "files": ["docker-compose.webchat.yml"], "kind": "off", "name": "minio-init", "profiles": ["minio"], "project": "ai-gateway"},
 "docker-compose.webchat.yml:postgres-webchat": {"container": "ai-gateway-postgres-webchat", "extra": [], "files": ["docker-compose.webchat.yml"], "kind": "pinned", "name": "postgres-webchat", "pinned": "pgvector/pgvector:pg16", "project": "ai-gateway", "ref": "pgvector/pgvector:pg16", "repo": "pgvector/pgvector"},
 "docker-compose.webchat.yml:redis-webchat": {"container": "ai-gateway-redis-webchat", "extra": [], "files": ["docker-compose.webchat.yml"], "kind": "pinned", "name": "redis-webchat", "pinned": "redis:7-alpine", "project": "ai-gateway", "ref": "redis:7-alpine", "repo": "redis"},
 "docker-compose.webchat.yml:web-chat": {"container": "ai-gateway-web-chat", "current_id": "55c3f84af9ed", "current_rev": "134b023", "current_tag": "ai-gateway-web-chat:134b023", "deployed": "", "extra": [], "files": ["docker-compose.webchat.yml"], "kind": "swap", "name": "web-chat", "probe": "http://127.0.0.1:23002/", "project": "ai-gateway", "ref": "ai-gateway-web-chat", "repo": "ai-gateway-web-chat", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "6f7d8a277d05", "rollback_label": "f01f1ee", "rollback_rev": "f01f1ee", "rollback_tag": "ai-gateway-web-chat:rollback-f01f1ee"},
 "docker-compose.yml:admin-ui": {"container": "ai-gateway-admin-ui-1", "current_id": "683c0322682a", "current_rev": "134b023", "current_tag": "ai-gateway-admin-ui:134b023", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "swap", "name": "admin-ui", "probe": "http://127.0.0.1:23000/login", "project": "ai-gateway", "ref": "ai-gateway-admin-ui", "repo": "ai-gateway-admin-ui", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "901a02cb509c", "rollback_label": "f01f1ee", "rollback_rev": "f01f1ee", "rollback_tag": "ai-gateway-admin-ui:rollback-f01f1ee"},
 "docker-compose.yml:autoheal": {"container": "ai-gateway-autoheal-1", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "pinned", "name": "autoheal", "pinned": "willfarrell/autoheal:1.2.0", "project": "ai-gateway", "ref": "willfarrell/autoheal:1.2.0", "repo": "willfarrell/autoheal"},
 "docker-compose.yml:backend": {"container": "ai-gateway-backend-1", "current_id": "ba45f640a3e1", "current_rev": "134b023", "current_tag": "ai-gateway-backend:134b023", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "backend", "name": "backend", "probe": "health:ai-gateway-backend-1", "project": "ai-gateway", "ref": "ai-gateway-backend", "repo": "ai-gateway-backend", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "7d3fa5af6e6e", "rollback_label": "67d9fc0", "rollback_rev": "67d9fc0", "rollback_tag": "ai-gateway-backend:rollback-67d9fc0"},
 "docker-compose.yml:chat-edge-api": {"elsewhere": "docker-compose.webchat.yml", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "elsewhere", "name": "chat-edge-api", "project": "ai-gateway"},
 "docker-compose.yml:dlp-sidecar": {"container": "ai-gateway-dlp-sidecar-1", "current_id": "982b4e8f2efc", "current_rev": "7d773b9", "current_tag": "ai-gateway-dlp-sidecar:7d773b9", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "swap", "name": "dlp-sidecar", "probe": "health:ai-gateway-dlp-sidecar-1", "project": "ai-gateway", "ref": "ai-gateway-dlp-sidecar:latest", "repo": "ai-gateway-dlp-sidecar", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "de99e47eecea", "rollback_label": "63c5071", "rollback_rev": "63c5071", "rollback_tag": "ai-gateway-dlp-sidecar:rollback-63c5071"},
 "docker-compose.yml:edge": {"container": "ai-gateway-edge-1", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "pinned", "name": "edge", "pinned": "caddy:2-alpine", "project": "ai-gateway", "ref": "caddy:2-alpine", "repo": "caddy"},
 "docker-compose.yml:gateway": {"container": "ai-gateway-gateway-1", "current_id": "9a59166261a5", "current_rev": "380b3f4", "current_tag": "ai-gateway-gateway:380b3f4", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "swap", "name": "gateway", "probe": "health:ai-gateway-gateway-1", "project": "ai-gateway", "ref": "ai-gateway-gateway", "repo": "ai-gateway-gateway", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "3b8357f85ecf", "rollback_label": "4fbbc43", "rollback_rev": "4fbbc43", "rollback_tag": "ai-gateway-gateway:rollback-4fbbc43"},
 "docker-compose.yml:grafana": {"container": "ai-gateway-grafana-1", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "pinned", "name": "grafana", "pinned": "grafana/grafana:11.1.0", "project": "ai-gateway", "ref": "grafana/grafana:11.1.0", "repo": "grafana/grafana"},
 "docker-compose.yml:kb-models": {"elsewhere": "docker-compose.webchat.yml", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "elsewhere", "name": "kb-models", "project": "ai-gateway"},
 "docker-compose.yml:log-worker": {"container": "ai-gateway-log-worker-1", "current_id": "a79f80407637", "current_rev": "e7513f0", "current_tag": "ai-gateway-log-worker:e7513f0", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "swap", "name": "log-worker", "probe": "health:ai-gateway-log-worker-1", "project": "ai-gateway", "ref": "ai-gateway-log-worker:latest", "repo": "ai-gateway-log-worker", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "6403e6d3f35e", "rollback_label": "", "rollback_rev": "20260925_012935", "rollback_tag": "ai-gateway-log-worker:rollback-20260925_012935"},
 "docker-compose.yml:mcp-gateway": {"container": "ai-gateway-mcp-gateway-1", "current_id": "04ab720e8439", "current_rev": "28490cb", "current_tag": "ai-gateway-mcp-gateway:28490cb", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "swap", "name": "mcp-gateway", "probe": "health:ai-gateway-mcp-gateway-1", "project": "ai-gateway", "ref": "ai-gateway-mcp-gateway", "repo": "ai-gateway-mcp-gateway", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "ed51bf8aa04f", "rollback_label": "", "rollback_rev": "pre-am9", "rollback_tag": "ai-gateway-mcp-gateway:rollback-pre-am9"},
 "docker-compose.yml:mock-provider": {"container": "ai-gateway-mock-provider", "current_id": "ded0b84d6e97", "current_rev": "", "current_tag": "ai-gateway-mock-provider:latest", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "nothing-kept", "name": "mock-provider", "project": "ai-gateway", "ref": "ai-gateway-mock-provider:latest", "repo": "ai-gateway-mock-provider"},
 "docker-compose.yml:ner-service": {"container": "ai-gateway-ner-service-1", "current_id": "9c62aa84d1ff", "current_rev": "676db92", "current_tag": "ai-gateway-ner-service:676db92", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "swap", "name": "ner-service", "probe": "health:ai-gateway-ner-service-1", "project": "ai-gateway", "ref": "ai-gateway-ner-service:latest", "repo": "ai-gateway-ner-service", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "31246632e9a5", "rollback_label": "", "rollback_rev": "20260925_012935", "rollback_tag": "ai-gateway-ner-service:rollback-20260925_012935"},
 "docker-compose.yml:nginx": {"extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "off", "name": "nginx", "profiles": ["scaling", "desktop"], "project": "ai-gateway"},
 "docker-compose.yml:ops-watch": {"container": "ai-gateway-ops-watch-1", "current_rev": "134b023", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "with", "name": "ops-watch", "project": "ai-gateway", "ref": "ai-gateway-backend:latest", "repo": "ai-gateway-backend", "with": "backend"},
 "docker-compose.yml:pgbouncer": {"extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "off", "name": "pgbouncer", "profiles": ["scaling"], "project": "ai-gateway"},
 "docker-compose.yml:portal": {"container": "ai-gateway-portal-1", "current_id": "3ec0ae634c0c", "current_rev": "96ecd58", "current_tag": "ai-gateway-portal:96ecd58", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "swap", "name": "portal", "probe": "http://127.0.0.1:23001/", "project": "ai-gateway", "ref": "ai-gateway-portal", "repo": "ai-gateway-portal", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "a10ec678ac35", "rollback_label": "e7513f0", "rollback_rev": "e7513f0", "rollback_tag": "ai-gateway-portal:rollback-e7513f0"},
 "docker-compose.yml:postgres": {"container": "ai-gateway-postgres-1", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "pinned", "name": "postgres", "pinned": "pgvector/pgvector:pg16", "project": "ai-gateway", "ref": "pgvector/pgvector:pg16", "repo": "pgvector/pgvector"},
 "docker-compose.yml:prometheus": {"container": "ai-gateway-prometheus-1", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "pinned", "name": "prometheus", "pinned": "prom/prometheus:v2.53.0", "project": "ai-gateway", "ref": "prom/prometheus:v2.53.0", "repo": "prom/prometheus"},
 "docker-compose.yml:rate-limiter": {"container": "ai-gateway-rate-limiter-1", "current_id": "018760599f59", "current_rev": "067bd2b", "current_tag": "ai-gateway-rate-limiter:latest", "deployed": "", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "swap", "name": "rate-limiter", "probe": "health:ai-gateway-rate-limiter-1", "project": "ai-gateway", "ref": "ai-gateway-rate-limiter:latest", "repo": "ai-gateway-rate-limiter", "rollback_from": "the newest rollback image kept on this host", "rollback_id": "cea975a55084", "rollback_label": "", "rollback_rev": "pre-fix129", "rollback_tag": "ai-gateway-rate-limiter:rollback-pre-fix129"},
 "docker-compose.yml:redis": {"container": "ai-gateway-redis-1", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "pinned", "name": "redis", "pinned": "redis:7", "project": "ai-gateway", "ref": "redis:7", "repo": "redis"},
 "docker-compose.yml:redis-exporter": {"container": "ai-gateway-redis-exporter-1", "extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "pinned", "name": "redis-exporter", "pinned": "oliver006/redis_exporter:v1.61.0", "project": "ai-gateway", "ref": "oliver006/redis_exporter:v1.61.0", "repo": "oliver006/redis_exporter"},
 "docker-compose.yml:worker": {"extra": [], "files": ["docker-compose.yml", "docker-compose.override.yml"], "kind": "off", "name": "worker", "profiles": ["scaling"], "project": "ai-gateway"}
}}
-->

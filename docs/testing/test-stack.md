# The test stack (`sran-test`)

A second copy of the services, on the same machine, that shares **nothing**
with the live stack (`ai-gateway`): its own compose project and network, its
own Postgres/Redis volumes, loopback ports in the 1xxxx range, the same images
the live stack runs. Added in FIX128.

## Rule

**Destructive tests run on the test stack first**: anything that deletes data,
cuts the network, restarts a service, or needs a dead / failing model. The live
system is used only for the short smoke / regression pass afterwards.

## Use

```bash
scripts/test-stack.sh up        # regenerate the compose file, start, pinned to the live image revisions
scripts/test-stack.sh seed      # catalogue + test tenant/user + dead/mock models (idempotent)
scripts/test-stack.sh status    # services, ports, images
scripts/test-stack.sh live-check out.txt   # live-system counts, to diff before/after a test run
scripts/test-stack.sh down      # stop (volumes kept)
scripts/test-stack.sh reset     # stop and delete the test volumes
```

Override an image for one run, e.g. a backend built from a branch:
`TS_BACKEND_TAG=<sha> scripts/test-stack.sh up` (also `TS_GATEWAY_TAG`, `TS_CHAT_EDGE_TAG`).

Checks written against it: `scripts/probes/FIX128/teststack.js` (Auto skips a
dead model, rescue by the closest model + credits + request_logs, titles after
a failed turn and with a dead title model).

## What it is

`docker-compose.test-stack.yml` is **generated** by `scripts/test_stack_gen.py`
from the real compose files (`--no-interpolate`, so it holds `${VAR}`
references and no secrets; values come from `.env` at `up`). The generator:

- keeps: postgres, redis, backend, gateway, log-worker, dlp-sidecar,
  ner-service, rate-limiter, mock-provider, chat-edge-api, postgres-webchat,
  redis-webchat (no web-chat, portal, admin, monitoring);
- runs the backend's image process (FIX131: `serve.sh` → gunicorn with
  `GUNICORN_WORKERS` workers, the same as live — no command override);
- removes `container_name`, source bind mounts, the Docker socket and
  `extra_hosts`; renames data volumes to `ts_*`;
- points chat-edge at the test gateway/backend (the real file uses
  `host.docker.internal` = the live ports) and disables its sandbox, cloud
  scheduler and file sweep;
- **refuses to write the file** if any env value, volume, network or host
  entry still names the live stack (`host.docker.internal`, `localhost:8080`,
  `:4000`, `:5433`, `:6380`, `ai-gateway-*`, the Docker socket).

| service | host port |
|---|---|
| chat-edge-api | 127.0.0.1:14000 |
| postgres-webchat | 127.0.0.1:15433 |
| redis-webchat | 127.0.0.1:16380 |
| gateway | 127.0.0.1:18080 |
| backend | 127.0.0.1:18081 |
| mock-provider | 127.0.0.1:19090 |

## Seed (`scripts/test_stack_seed.py`)

- The model catalogue (`providers`, `provider_keys`, `model_configs`,
  `keyword_rules`, `system_settings`) copied read-only from the live gateway
  DB through a pipe. Keys stay encrypted with the same `MASTER_ENCRYPTION_KEY`.
- Tenant `f1280000-0000-4000-8000-000000000001`, owner
  `f1280000-0000-4000-8000-000000000011` (`ts-owner@fix128.test`), 10M credits.
- `ts-dead-model`: an `sranai` key whose endpoint is a closed port
  (`http://127.0.0.1:1/v1`); same capabilities as qwen; Auto's admin
  preference.
- `ts-mock-model`: served by the test mock provider through key `ts-mock`;
  point the key at the closed port to make an answering model die mid-test.

The seed refuses to run unless `sran-test-postgres-1` belongs to project
`sran-test`. The probes check the same for every container they touch.

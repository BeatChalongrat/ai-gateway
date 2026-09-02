# FIX33 §6.2 probe harness (owner re-run)

Blocked on 2026-08-31: every provider key on the dev box is a placeholder
(`.env` OPENAI/ANTHROPIC/OPENROUTER = `sk-...`), the gateway answers 502
`provider_error` for every real model, so the probes were NOT measured
(docs/FIX33_REPORT.md §2). With a working key:

    npm --prefix apps/desktop run build            # dist/ for the desktop path
    node scripts/probes/long-context/probe.js web 20,50,100,200,400
    node scripts/probes/long-context/probe.js desktop 20,50,100,200,400
    node scripts/probes/long-context/dlp-check.js          # A-D6: does the desktop's extraction reply carry raw PII?

`PROBE_MODEL` (default gpt-5.6-terra) picks the model. Results land beside the
script as `probe-<surface>.json` / `dlp-check.json`. `mint-jwt.js` mints a DEV
portal token with docker-compose.webchat.yml's default PORTAL_JWT_SECRET —
dev only; set PORTAL_JWT_SECRET if the container runs another value.

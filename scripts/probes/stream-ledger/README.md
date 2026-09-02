# FIX41 probe harness — stream durability (P1..P6)

## What it measures

| probe | question |
|---|---|
| P1  | close the tab mid-stream, reopen within 5s — text complete? duplicated? event order kept? |
| P1b | do `tool_activity` / `code_execution` emitted during the gap come back in position? |
| P2  | restart chat-edge mid-stream, then resume — does the client get its generation back? |
| P3  | press Stop mid-stream — how does a client learn it was cancelled, not disconnected? |
| P4  | provider errors mid-stream — is there a terminal `error` frame? |
| P5  | desktop: close the app mid-turn, reopen — what comes back? |
| P6  | desktop: reload the page mid-turn — does the `lastSeq` replay still work? |

## Why it is not a curl script against localhost:4000

Every provider key in `.env` on this box is the literal placeholder
(`OPENAI_API_KEY=sk-...`), so the gateway answers
`provider_error: No fallback chain registered` for every model and no
generation ever starts — the same wall FIX33 §6.2 hit.

P1..P4 therefore run the **real** chi router + `middleware.RequireAuth` +
`MessageHandler` / `MessageService` / `StreamBroker` over the **real**
postgres-webchat, in-process, against a **fake gateway** that scripts the
upstream SSE frames. Every HTTP hop the browser makes is a real HTTP hop;
the only substitution is who answers `/chat/completions`. A "restart" is
modelled by discarding the whole MessageService (and with it the
in-memory broker) and rebuilding it over the same database — which is
exactly what `docker compose restart chat-edge-api` destroys and keeps.

P5/P6 run the real `TurnCoordinator` under vitest.

## Running

    # P1..P4 (+P1b) — needs postgres-webchat on :5433
    cd services/chat-edge-api
    FIX41_PROBE=1 FIX41_PROBE_OUT=../../scripts/probes/stream-ledger/results/before \
      go test ./internal/handler/ -run 'TestFix41Probe|TestZZZFix41' -count=1 -v

    # P5/P6 — desktop
    cd apps/desktop
    FIX41_PROBE_OUT=../../scripts/probes/stream-ledger/results/before npx vitest run durable-run

Results land as one JSON per probe in `results/<before|after>/`, carrying
the raw frame log the client actually received.

## A trap this harness already fell into

The first run of P2 reported **PASS**. It was false: the probe called
`httptest.Server.Close()` to simulate the restart, and `Close()` *blocks
until in-flight requests finish* — so it really measured "wait for the
turn to end, then resume". The fix (documented in the probe source) is to
leave the old server alone and stand up a fresh service+broker over the
same DB. Verdict flipped to FAIL, which is the truth.

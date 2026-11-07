# Secrets in transcripts (FIX134)

Every secret of the stack lives in `.env` (gitignored) and reaches the
containers as environment variables. Two everyday commands print all of them:

* `docker inspect <container>` — the whole `Config.Env` block;
* `docker exec <container> env` (or `printenv`, `cat /proc/1/environ`).

Their output lands in the terminal scrollback, in an agent's transcript, in a
pasted report, in a log capture — and a secret that has been printed once has
to be rotated (FIX133 rotated all of them; see `docs/ops/tls.md`, Production
mode). So the rule:

**Never `docker inspect` or `docker exec … env` a container into a transcript.
Use `scripts/env-masked.sh`.**

```
scripts/env-masked.sh <container> [grep-pattern]
scripts/env-masked.sh ai-gateway-backend
scripts/env-masked.sh ai-gateway-chat-edge-api 'REDIS|POSTGRES'
```

It prints every key and, for values, only what cannot be used (AM2 — masking
was not enough: FIX134's version printed the first 3 characters of anything it
judged secret and every other value in full, so a secret under a plain key
name — an ntfy topic URL, a webhook — printed whole, and in AM1c an ad-hoc
mask that expected `user:pass@` printed a `redis://:pass@host` URL whole):

```
APP_ENV                                  = production
AGENT_MARKET_ENABLED                     = true
WEBCHAT_REDIS_PASSWORD                   set  48 chars  sha256:914c748c
FIX41_BENCH_REDIS_URL                    set  74 chars  sha256:8a79ffef
ALERT_EMAIL_TO                           (empty)
```

A value is shown only when the key is on an allowlist of configuration shapes
(`APP_ENV`, `*_ENABLED`, `*_MODE`, `*_PORT`, `*_HOST`, …, never a key naming a
password, secret, key, token, URL, DSN, webhook or topic) **and** the value is
a plain word or number (no `:`, `/`, `@`, `=`, `?`; at most 40 characters).
Everything else is its length and the first 8 hex characters of its SHA-256 —
enough to tell set from empty, to see that a container and `.env` hold the same
value, or that a rotation took; never enough to use it. An unknown key is never
shown. `scripts/env-masked.sh --env-file .env [key-regex]` does the same for an
env file, so `.env` is never `cat`, `grep`ped or `sed`-masked into a
transcript. Test: `scripts/tests/test_env_masked.py`.

To check a setting without the script: compare hashes (`sha256sum`), test the
connection, count (`grep -c`), or print the key name only — never the line.

When you need one specific value, read it where it lives (`.env`) in an
editor, not through a command whose output is captured. If a secret does get
printed, rotate it the same day; a redacted transcript is not a rotation.

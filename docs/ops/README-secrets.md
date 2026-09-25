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

It prints the keys in full and masks any value that can hold a secret (keys
with `PASSWORD`, `SECRET`, `KEY`, `TOKEN`, `CREDENTIAL`, `DSN`, `_AUTH`, and
any URL that carries `user:pass@` or a `token=` / `password=` parameter) as
the first 3 characters, `…` and the length — `abc…(48 chars)`; a value shorter
than 8 characters shows only `…`, an empty one `(empty)`. Plain settings
(`APP_ENV`, `PORT`, `*_ENABLED`, `*_MODE`, service URLs without credentials)
print as they are. That is enough to answer the usual questions — is the
variable set, is it empty, does its length match what `.env` says, does it
start with the expected prefix — without ever showing the value.

When you need one specific value, read it where it lives (`.env`) in an
editor, not through a command whose output is captured. If a secret does get
printed, rotate it the same day; a redacted transcript is not a rotation.

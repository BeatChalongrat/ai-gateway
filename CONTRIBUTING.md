# Contributing

Source review: 2026-11-12. Read CLAUDE.md for authorship and live-model rules.

## Validate the affected path

| Change | Checks |
|---|---|
| Desktop | In apps/desktop: npm run typecheck; npm test; npm run build |
| Web Chat | In apps/web-chat: npx tsc --noEmit; npm test; npm run lint:i18n; npm run check:classes; npm run lint:theme |
| Chat-edge | In services/chat-edge-api: gofmt on changed Go files; targeted go test; go vet as appropriate |
| Office generators | From root: python -m unittest discover -s services/chat-edge-api/scripts/tests -v; sandbox Python lane |
| Backend | Unit/integration separation and known-failure handling in backend/tests and scripts/backend-test-lane.sh |
| Documentation | Check local links, source claims and scripts/gate/tracks.py |

New local-file tests exercise real temporary files, permissions and conversation
ownership. Office tests reopen actual generated files. They do not replace a
live model/native UI walkthrough or visual Office inspection.

## Gate and CI

The complete local release gate is `scripts/gate.sh`: it serializes heavy work,
checks host power and records results. Its lanes include backend, Go, Rust,
web/admin/portal, desktop, Python, repository hygiene and operational checks.
Read the script and [test-stack guide](docs/testing/test-stack.md) before running
the full gate; some phases change test-service state or require a running stack.

`make ci` is a smaller convenience target (backend unit, web typecheck/tests
and chat-edge Go). It is not the entire release gate.

GitLab is the primary CI definition in `.gitlab-ci.yml`; GitHub mirrors Linux
lanes in `.github/workflows/ci.yml`. The definitions include more than the old
three-job list: web unit/E2E, gateway/MCP, secret and dependency checks are also
present. The WPF Windows job was removed; Electron has its own local suite.
Read job definitions for current required/optional behavior and tool versions.

Existing backend failures are named in the repository's known-failure records.
Do not hide a new failure by weakening assertions, broadening exclusions or
marking a required job optional. Historical passing counts are not today's
result. If worker processes fail locally, report that separately from assertion
failures; a bounded-worker rerun is appropriate for diagnosis.

## Documentation and delivery

[Documentation index](docs/DOCUMENTATION_INDEX.md) distinguishes current source
references, operational runbooks, historical decisions and runtime prompts.
Update the relevant contract when behavior changes; preserve historical dates
and do not rewrite operational evidence as a fresh successful run.

Working reports/probes remain outside tracked product documentation according
to .gitignore and the tracks gate. New maintained docs/tests must be included
in the eventual change set. A source build is not a deployed release; state
which binaries/images, if any, were rebuilt or installed.

No commit, merge, branch-protection change or deployment is implied by running
tests. Use actual remote/project settings rather than the previous hard-coded
GitHub protection example.

#!/usr/bin/env bash
# AM12 — what the test stack needs for a round on the web, beside what
# scripts/probes/AM7/test_stack.sh brings up: a web.
#
# The test stack has NO route to the real web. The fetcher's `web` network is
# replaced there by `sran-test-web` (scripts/probes/AM12/test-web.yml; AM7's
# script starts the runner's project with it): an internal docker network — no
# gateway, no route out — on a public address range, holding one container
# that answers as a handful of shops and as the sites that refuse
# (services/web-fetcher/test/sites). Every name resolves, through docker's own
# DNS, to a public-looking address, so the fetcher's real checks run as they
# do on the real web: nothing in the product knows it is under test.
#
# The same container answers the platform's mock search provider
# (MOCK_SEARCH_BASE_URL on the test backend), so a search goes the whole way
# it goes in production — runner → gateway /v1/search → backend (tenant gate,
# DLP scan of the query, credit charge) → provider adapter — and comes back
# with addresses of the stand-in shops.
#
#   AM7_NO_NER=1 bash scripts/probes/AM7/test_stack.sh up <tag>   # first: the stack itself
#   bash scripts/probes/AM12/test_stack.sh web <fetcher tag>      # the stand-in web + search, the test backend told where it is
#   bash scripts/probes/AM12/test_stack.sh check                  # what the fetcher can and cannot reach from there
#   bash scripts/probes/AM12/test_stack.sh unweb                  # take the stand-in web away
#
# Nothing here names a container, volume, network or port of the live project.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
NET=sran-test-web
SUBNET=93.184.215.0/24
ADDR=93.184.215.14
SITES=sran-test-am12-web
FETCHER=sran-agent-market-test-web-fetcher-1
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
# The test backend is recreated here (one more setting) and must come back as it was: the same image, and
# the environment scripts/probes/AM7/test_stack.sh gives every service it starts.
rev() { docker inspect "$1" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true; }
export TS_GATEWAY_TAG="${TS_GATEWAY_TAG:-$(rev ai-gateway-gateway-1)}"
export TS_KB_MODELS_TAG="${TS_KB_MODELS_TAG:-$(rev ai-gateway-kb-models)}"
export NER_WORKERS=1
export AGENT_MARKET_ALLOWED_EMAILS=ts-owner@fix128.test AGENT_MARKET_ADMIN_EMAILS=ts-owner@fix128.test
export CORS_ORIGINS="https://localhost:3000,https://127.0.0.1:3000,https://localhost:3001,https://127.0.0.1:3001,https://localhost:3002,https://127.0.0.1:3002,http://localhost:5173,http://localhost:13002,http://localhost:13000"
ts() { docker compose -p sran-test -f docker-compose.test-stack.yml -f scripts/probes/AM12/test-stack.search.yml "$@" 2>&1 | grep -v "level=warning"; }
guard() {
  local label
  label=$(docker inspect sran-test-backend-1 --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)
  [ "$label" = "sran-test" ] || { echo "REFUSED: the test stack's backend is not up (sran-test-backend-1)" >&2; exit 2; }
}

case "${1:-check}" in
  web)
    TAG="${2:?the web-fetcher image tag (the stand-in sites run on its node)}"
    guard
    docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal --subnet "$SUBNET" --label sran.test-stack=true "$NET" >/dev/null
    docker rm -f "$SITES" >/dev/null 2>&1 || true
    HOSTS=$(MSYS_NO_PATHCONV=1 docker run --rm --network none -v "$(native "$ROOT")/services/web-fetcher/test:/opt/web-fetcher/test:ro" \
      --entrypoint node "ai-gateway-web-fetcher:$TAG" -e "console.log(require('/opt/web-fetcher/test/sites/server.js').HOSTS.join(' '))")
    ALIASES=()
    for h in $HOSTS; do ALIASES+=(--network-alias "$h"); done
    MSYS_NO_PATHCONV=1 docker run -d --name "$SITES" --restart unless-stopped --label sran.test-stack=true --memory 128m \
      --network "$NET" --ip "$ADDR" "${ALIASES[@]}" --user 0 \
      -v "$(native "$ROOT")/services/web-fetcher/test:/opt/web-fetcher/test:ro" \
      --entrypoint node "ai-gateway-web-fetcher:$TAG" /opt/web-fetcher/test/sites/server.js 80 >/dev/null
    # the test backend finds the stand-in search on its own network, by one name
    docker network connect --alias am12-web sran-test_default "$SITES"
    WAS=$(docker inspect sran-test-backend-1 --format '{{.Config.Image}}')
    export TS_BACKEND_TAG="${TS_BACKEND_TAG:-${WAS##*:}}"
    [ "$TS_BACKEND_TAG" != latest ] || TS_BACKEND_TAG="$(rev sran-test-backend-1)"
    ts up -d --no-deps --wait --wait-timeout 300 backend | tail -2
    echo "test backend: $(docker inspect sran-test-backend-1 --format '{{.Config.Image}}') (was $WAS)"
    echo "test backend: MOCK_SEARCH_BASE_URL=$(docker exec sran-test-backend-1 printenv MOCK_SEARCH_BASE_URL) · APP_ENV=$(docker exec sran-test-backend-1 printenv APP_ENV)"
    # every tenant of the TEST database may search, through the mock provider (its key is a placeholder,
    # encrypted the way the backend encrypts every provider key)
    KEY=$(docker exec -i sran-test-backend-1 python -c "from app.services.search.service import encode_key; print(encode_key('mock-not-a-key'))" 2>/dev/null | tail -1 | tr -d '\r\n')
    [ -n "$KEY" ] || { echo "could not encrypt the mock provider's placeholder key" >&2; exit 1; }
    docker exec -i sran-test-postgres-1 psql -U postgres -d ai_gateway -v ON_ERROR_STOP=1 -At -v key="$KEY" <<'SQL'
update tenants set search_enabled = true where search_enabled is distinct from true;
insert into search_providers (id, tenant_id, provider, display_name, api_key_encrypted, priority, is_enabled, max_results)
select md5(t.tenant_id || ':am12-mock')::uuid::text, t.tenant_id, 'mock', 'stand-in web (test stack)', :'key', 0, true, 8
from tenants t where not exists (select 1 from search_providers p where p.tenant_id = t.tenant_id and p.provider = 'mock');
select 'tenants that may search: ' || count(*) from tenants where search_enabled;
select 'mock providers: ' || count(*) from search_providers where provider = 'mock';
SQL
    echo "stand-in web: $(docker inspect -f '{{.State.Status}}' "$SITES") at $ADDR · $(echo "$HOSTS" | wc -w | tr -d ' ') sites"
    ;;
  check)
    guard
    echo "the fetcher's networks: $(docker inspect "$FETCHER" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}')"
    echo "internal (no route out): $NET=$(docker network inspect "$NET" --format '{{.Internal}}') fetch=$(docker network inspect sran-agent-market-test_fetch --format '{{.Internal}}')"
    echo "on the test stack's own network: $(docker network inspect sran-test_default --format '{{range .Containers}}{{.Name}} {{end}}' | tr ' ' '\n' | grep -c 'web-fetcher' || true) fetcher containers"
    # from inside the fetcher: a stand-in shop answers; the real web and the stack's own services do not
    docker exec "$FETCHER" node -e "
      const http = require('http'), dns = require('dns').promises;
      const get = (url) => new Promise((ok) => { const r = http.get(url, { timeout: 4000 }, (res) => { res.resume(); ok(res.statusCode); }); r.on('error', (e) => ok(e.code || 'error')); r.on('timeout', () => { r.destroy(); ok('timeout'); }); });
      (async () => {
        console.log('stand-in shop:', await get('http://www.sukjai-office.test/p/ergomesh-m2'));
        console.log('the real web (example.org):', await dns.lookup('example.org').then((a) => get('http://' + a.address + '/'), (e) => e.code));
        for (const name of ['gateway', 'backend', 'postgres', 'postgres-webchat', 'chat-edge-api', 'redis']) console.log('own service ' + name + ':', await dns.lookup(name).then((a) => 'RESOLVES ' + a.address, (e) => e.code));
      })();"
    ;;
  unweb)
    docker rm -f "$SITES" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    echo "the stand-in web is gone"
    ;;
  *) echo "usage: $0 web <fetcher tag>|check|unweb" >&2; exit 2 ;;
esac

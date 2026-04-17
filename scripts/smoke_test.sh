#!/usr/bin/env bash
# SRAN AI Gateway — Smoke Test
set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
PASS=0
FAIL=0

# Detect working python command (Windows python3 stub doesn't work)
PY="python"
if python3 -c "pass" 2>/dev/null; then PY="python3"; fi

# Read admin key from .env.local or environment
ADMIN_KEY="${NEXT_PUBLIC_ADMIN_API_KEY:-}"
if [ -z "$ADMIN_KEY" ] && [ -f admin-ui/.env.local ]; then
  ADMIN_KEY=$(grep "^NEXT_PUBLIC_ADMIN_API_KEY=" admin-ui/.env.local 2>/dev/null | cut -d'=' -f2 | tr -d '\r\n')
fi

check() {
  local label="$1"
  local url="$2"
  local expect_items="$3"

  local tmp
  tmp=$(mktemp)

  # Use -s (silent) but NOT -f (fail), so body is always written
  local http_code
  http_code=$(curl -4 -s -o "$tmp" -w "%{http_code}" \
    -H "Authorization: Bearer $ADMIN_KEY" \
    -H "Connection: close" \
    --max-time 10 \
    "$url" 2>/dev/null) || http_code="000"

  if [ "$http_code" = "200" ]; then
    if [ "$expect_items" = "items" ]; then
      local count
      count=$($PY -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('items',[])))" < "$tmp" 2>/dev/null || echo "0")
      if [ "$count" -ge 1 ] 2>/dev/null; then
        echo -e "  ${GREEN}PASS${NC}  $label  (${count} items)"
        PASS=$((PASS + 1))
      else
        echo -e "  ${RED}FAIL${NC}  $label  (0 items)"
        FAIL=$((FAIL + 1))
      fi
    else
      echo -e "  ${GREEN}PASS${NC}  $label  (HTTP $http_code)"
      PASS=$((PASS + 1))
    fi
  else
    echo -e "  ${RED}FAIL${NC}  $label  (HTTP $http_code)"
    FAIL=$((FAIL + 1))
  fi

  rm -f "$tmp"
}

echo ""
echo "=============================="
echo " SRAN AI Gateway — Smoke Test"
echo "=============================="
echo ""

check "Backend health"    "http://localhost:8080/health"
check "Admin: tenants"    "http://localhost:8080/admin/tenants"     "items"
check "Admin: models"     "http://localhost:8080/admin/models"      "items"
check "Admin: providers"  "http://localhost:8080/admin/providers"   "items"
check "Admin: policies"   "http://localhost:8080/admin/policies"    "items"
check "Frontend"          "http://localhost:3000/login"

echo ""
echo "=============================="
echo "  Results: ${PASS} passed, ${FAIL} failed"
echo "=============================="

[ "$FAIL" -eq 0 ] && exit 0 || exit 1

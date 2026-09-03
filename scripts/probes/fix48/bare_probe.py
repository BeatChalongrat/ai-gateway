# Runs INSIDE the backend container. Decrypts the active provider key with the
# app's own helper, calls the provider DIRECTLY (no gateway, no DLP, no router,
# no system prompt), and prints ONLY the provider's JSON. The key never leaves
# this process.
#
# Provider is chosen by the "provider:" prefix on the model, defaulting to
# openrouter:   {"model": "deepseek:deepseek-chat", ...} -> api.deepseek.com
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, "/app")
from app.core.security import decrypt_provider_key
from app.core.config import settings
from sqlalchemy import create_engine, text

ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
}

payload = json.loads(sys.stdin.read())
model = payload["model"]
provider = "openrouter"
if ":" in model:
    provider, model = model.split(":", 1)
    payload["model"] = model

eng = create_engine(settings.database_url.replace("+asyncpg", ""))
with eng.connect() as c:
    row = c.execute(text(
        "select api_key_encrypted from provider_keys "
        "where provider_name=:p and is_active and last_test_status='valid' limit 1"
    ), {"p": provider}).fetchone()
if row is None:
    print(json.dumps({"error": {"no_key_for": provider}}))
    sys.exit(0)
key = decrypt_provider_key(row[0], settings.effective_master_key)

req = urllib.request.Request(
    ENDPOINTS[provider],
    data=json.dumps(payload).encode(),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=240) as r:
        print(r.read().decode())
except urllib.error.HTTPError as e:
    print(json.dumps({"error": {"http": e.code, "provider": provider, "body": e.read().decode()[:500]}}))
except Exception as e:
    print(json.dumps({"error": {"thrown": str(e)[:300], "provider": provider}}))

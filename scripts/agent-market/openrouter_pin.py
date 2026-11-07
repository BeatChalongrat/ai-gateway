"""AM16 — who actually answers a request sent through OpenRouter, and what a pin does to that.

Run INSIDE the test stack's backend container (it is the process that holds the provider's key; the key
is used for the call and never printed):

    docker exec -i sran-test-backend-1 python - < scripts/agent-market/openrouter_pin.py

One harmless sentence, every outside model of the catalogue, and for each: with no pin, which endpoint
answered (OpenRouter names it in its answer); then pinned to each company the gateway's table says can
run it (``only`` + ``allow_fallbacks: false`` + ``data_collection: deny`` — what the gateway sends when
a caller names its recipients), whether that company's endpoint answered or OpenRouter refused, in its
own words. This is where the table in app/services/providers/recipients.py comes from.
"""
import asyncio

import httpx

from app.core.db import SessionLocal
from app.services.provider_key_service import ProviderKeyService
from app.services.providers.recipients import OPENROUTER_HOSTS

MODELS = ("google/gemini-2.5-flash", "google/gemini-2.5-pro", "anthropic/claude-sonnet-4.5", "anthropic/claude-sonnet-4",
          "anthropic/claude-opus-4.1", "anthropic/claude-3.5-haiku", "openai/gpt-4o", "openai/gpt-4o-mini",
          "deepseek/deepseek-chat", "deepseek/deepseek-r1", "meta-llama/llama-3.3-70b-instruct")


def pins(model: str):
    out = [("no pin", None)]
    for company, slugs in OPENROUTER_HOSTS.get(model.split("/", 1)[0], {}).items():
        out.append((f"pinned to {company}", {"only": list(slugs), "allow_fallbacks": False, "data_collection": "deny"}))
    return out


async def main():
    with SessionLocal() as s:
        key = ProviderKeyService().get_active_key(s, "openrouter")
    if not key:
        print("no key for openrouter in this stack")
        return
    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", timeout=60) as c:
        for model in MODELS:
            for name, pin in pins(model):
                body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with the one word: ok"}]}
                if pin is not None:
                    body["provider"] = pin
                try:
                    r = await c.post("/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
                    d = r.json()
                    if r.status_code == 200 and "choices" in d:
                        said = f"answered by endpoint: {d.get('provider')}"
                    else:
                        err = d.get("error") or {}
                        said = f"{err.get('code')} · " + " ".join(str(err.get("message")).split())[:330]
                    print(f"{model:36s} {name:22s} → {r.status_code} · {said}")
                except Exception as e:  # noqa: BLE001
                    print(f"{model:36s} {name:22s} → {type(e).__name__}: {str(e)[:120]}")
                await asyncio.sleep(0.6)


asyncio.run(main())

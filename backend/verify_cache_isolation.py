"""Live verification of the api_key_id cache-isolation fix.

Runs inside the backend container. Exercises CacheService.set_async /
get_async directly against the real Redis client to confirm:

  1. Different api_key_id values on the same (project, model, messages)
     never collide — user A's masked-response leak cannot reach user B.
  2. Same api_key_id retrieves its own write (no spurious cross-key
     collisions in our digest).
  3. ``api_key_id=None`` (non-DLP request path) shares the per-project
     cache across callers, preserving the existing efficiency.
  4. Mixing api_key_id and None on the same prompt yields *distinct*
     Redis keys — verified via :func:`_key` inspection — so the
     namespaced and non-namespaced cache rows never alias.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from app.services.cache_service import CacheService


def safe(s: str) -> str:
    try:
        return s
    except Exception:
        return repr(s)


async def main() -> int:
    cache = CacheService()
    project = f"verify-isolation-{uuid.uuid4().hex[:8]}"
    model = "gpt-4o-mini"
    messages = [
        {"role": "user", "content": "summary: email alice@test.com reported issue"}
    ]

    key_a = f"keyA-{uuid.uuid4().hex[:8]}"
    key_b = f"keyB-{uuid.uuid4().hex[:8]}"

    leaked_payload = {
        "choices": [{"message": {"content": "leak: sk-test-abc123 alice@test.com"}}]
    }
    benign_payload = {"choices": [{"message": {"content": "ok"}}]}

    print("[1/4] write under key_a, read under key_b on same prompt")
    await cache.set_async(model, messages, project, leaked_payload, api_key_id=key_a)
    hit_b = await cache.get_async(model, messages, project, api_key_id=key_b)
    if hit_b is not None:
        print(f"   FAIL: key_b saw key_a's entry: {safe(str(hit_b))}")
        return 1
    print("   OK: key_b miss (no cross-user leak)")

    print("[2/4] same key_a should hit its own write")
    hit_a = await cache.get_async(model, messages, project, api_key_id=key_a)
    if hit_a is None:
        print("   FAIL: key_a missed its own write")
        return 1
    if "sk-test-abc123" not in str(hit_a):
        print(f"   FAIL: key_a payload mismatch: {safe(str(hit_a))}")
        return 1
    print("   OK: key_a saw its own write")

    print("[3/4] api_key_id=None shares per-project cache across callers")
    shared_project = f"verify-shared-{uuid.uuid4().hex[:8]}"
    shared_messages = [{"role": "user", "content": "what is 2+2"}]
    await cache.set_async(model, shared_messages, shared_project, benign_payload)
    hit_shared = await cache.get_async(model, shared_messages, shared_project)
    if hit_shared is None:
        print("   FAIL: shared (no-key) read missed")
        return 1
    print("   OK: shared (no-key) read hit")

    print("[4/4] _key with vs without api_key_id never collides")
    k1 = cache._key(model, messages, project, api_key_id=None)
    k2 = cache._key(model, messages, project, api_key_id=key_a)
    k3 = cache._key(model, messages, project, api_key_id=key_b)
    if k1 == k2 or k1 == k3 or k2 == k3:
        print(f"   FAIL: collision\n   k1={k1}\n   k2={k2}\n   k3={k3}")
        return 1
    print(f"   OK: distinct keys")
    print(f"      shared (no key): {k1}")
    print(f"      key_a:           {k2}")
    print(f"      key_b:           {k3}")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

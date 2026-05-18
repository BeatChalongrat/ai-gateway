"""DLP four-hop data-flow verification.

The gateway pipeline has four distinct hops where text crosses a
trust boundary:

    Hop 1  User → Service     (raw input the gateway receives)
    Hop 2  Service → Provider (masked input we send upstream)
    Hop 3  Provider → Service (raw model response we receive back)
    Hop 4  Service → User     (final text the user actually sees)

Each test below pins ONE invariant per hop and prints a side-by-side
trace so the test log doubles as a debugging artefact for the
DLP team. Every test reuses the conftest's existing
``ProviderDispatcher.dispatch`` auto-mock pattern — we override
``dispatch`` per test with a recorder that captures whatever
``messages`` the gateway hands the adapter (those messages are the
post-DLP-mask payload, which is exactly hop 2's content).

Why not via curl + HTTP layer:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Going through ``TestClient`` exercises the same FastAPI app, same
middleware, same orchestrator — and gives us hop 2 directly through
the recorder rather than having to peer into Postgres after the
request lands. The earlier Windows + Git Bash live-test session
proved curl from a non-UTF-8 shell can corrupt the wire body
before the gateway ever sees it; ``TestClient`` runs in-process
and removes that variable.
"""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from app.domain.providers import ProviderResponse
from app.main import app as main_app
from app.services.provider_dispatcher import ProviderDispatcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_app)


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Bearer header that the conftest MOCK_AUTH dependency accepts."""
    return {"Authorization": "Bearer gw_test_hop_verify"}


def _print_hops(
    *,
    label: str,
    hop1: str,
    hop2: str,
    hop3: str,
    hop4: str,
    assertions: dict[str, bool],
) -> None:
    """Pretty-print the four-hop trace + assertion results.

    Forces UTF-8 on the captured stdout so the Thai content in the
    trace doesn't blow up Windows pytest's cp1252-defaulted capture
    stream. Safe no-op on POSIX where stdout is already UTF-8.
    """
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print()
    print("=" * 60)
    print(f"HOP VERIFICATION: {label}")
    print("=" * 60)
    print(f"Hop 1 User -> Service : {hop1!r}")
    print(f"Hop 2 Service -> AI   : {hop2!r}")
    print(f"Hop 3 AI -> Service   : {hop3!r}")
    print(f"Hop 4 Service -> User : {hop4!r}")
    print("-" * 60)
    print("Assertions:")
    for name, ok in assertions.items():
        marker = "[OK]" if ok else "[FAIL]"
        print(f"  {marker} {name}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Hop 2 — provider must NEVER see raw PII
# ---------------------------------------------------------------------------

def test_hop2_no_raw_pii_sent_to_provider(client, auth_headers, monkeypatch):
    captured: dict = {}
    raw_email = "john@acme.com"
    raw_phone = "081-234-5678"
    hop1 = f"ส่งมาที่ {raw_email} โทร {raw_phone}"

    async def _recorder(self, route, payload, messages, api_key=None):
        captured["hop2"] = messages[-1].content
        return ProviderResponse(content="OK", model=route.model_name)

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _recorder)

    resp = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": hop1}],
            "stream": False,
        },
    )
    assert resp.status_code == 200, resp.text
    hop2 = captured["hop2"]
    hop3 = "OK"
    hop4 = resp.json()["choices"][0]["message"]["content"]

    no_raw_email = raw_email not in hop2
    no_raw_phone = raw_phone not in hop2
    has_email_token = "[EMAIL_0]" in hop2
    has_phone_token = "[PHONE_NUMBER_0]" in hop2

    _print_hops(
        label="hop2_no_raw_pii_sent_to_provider",
        hop1=hop1, hop2=hop2, hop3=hop3, hop4=hop4,
        assertions={
            "Hop 2 no raw email": no_raw_email,
            "Hop 2 no raw phone": no_raw_phone,
            "Hop 2 has [EMAIL_0]": has_email_token,
            "Hop 2 has [PHONE_NUMBER_0]": has_phone_token,
        },
    )
    assert no_raw_email and no_raw_phone
    assert has_email_token and has_phone_token


# ---------------------------------------------------------------------------
# Hop 4 — AI-hallucinated PII must be masked before reaching the user
# ---------------------------------------------------------------------------

def test_hop4_ai_hallucinated_pii_masked_before_user(
    client, auth_headers, monkeypatch
):
    fake_email = "fake@hacker.com"
    fake_phone = "099-999-9999"
    captured: dict = {}

    async def _hallucinator(self, route, payload, messages, api_key=None):
        captured["hop2"] = messages[-1].content
        return ProviderResponse(
            content=f"ติดต่อ {fake_email} หรือ {fake_phone}",
            model=route.model_name,
        )

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _hallucinator)

    hop1 = "สวัสดีครับ"
    resp = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": hop1}],
            "stream": False,
        },
    )
    assert resp.status_code == 200, resp.text
    hop2 = captured["hop2"]
    hop3 = f"ติดต่อ {fake_email} หรือ {fake_phone}"
    hop4 = resp.json()["choices"][0]["message"]["content"]

    no_raw_email = fake_email not in hop4
    no_raw_phone = fake_phone not in hop4
    has_email_token = "[EMAIL_" in hop4
    has_phone_token = "[PHONE_NUMBER_" in hop4

    _print_hops(
        label="hop4_ai_hallucinated_pii_masked",
        hop1=hop1, hop2=hop2, hop3=hop3, hop4=hop4,
        assertions={
            "Hop 4 no fake email": no_raw_email,
            "Hop 4 no fake phone": no_raw_phone,
            "Hop 4 has [EMAIL_N]": has_email_token,
            "Hop 4 has [PHONE_NUMBER_N]": has_phone_token,
        },
    )
    assert no_raw_email
    assert no_raw_phone
    assert has_email_token
    assert has_phone_token


# ---------------------------------------------------------------------------
# Hop 4 — token echoed by AI must be restored, not delivered as-is
# ---------------------------------------------------------------------------

def test_hop4_ai_echoed_token_restored(client, auth_headers, monkeypatch):
    raw_email = "john@acme.com"
    hop1 = f"อีเมลฉัน {raw_email}"
    captured: dict = {}

    async def _echo_token(self, route, payload, messages, api_key=None):
        captured["hop2"] = messages[-1].content
        return ProviderResponse(
            content="รับทราบ [EMAIL_0] แล้วครับ",
            model=route.model_name,
        )

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _echo_token)

    resp = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": hop1}],
            "stream": False,
        },
    )
    assert resp.status_code == 200, resp.text
    hop2 = captured["hop2"]
    hop3 = "รับทราบ [EMAIL_0] แล้วครับ"
    hop4 = resp.json()["choices"][0]["message"]["content"]

    has_original = raw_email in hop4
    no_leftover_token = "[EMAIL_0]" not in hop4

    _print_hops(
        label="hop4_ai_echoed_token_restored",
        hop1=hop1, hop2=hop2, hop3=hop3, hop4=hop4,
        assertions={
            "Hop 4 original email restored": has_original,
            "Hop 4 mask token absent": no_leftover_token,
        },
    )
    assert has_original
    assert no_leftover_token


# ---------------------------------------------------------------------------
# Hop 2 — Thai UTF-8 must survive the inbound mask transform
# ---------------------------------------------------------------------------

def test_hop2_thai_text_not_garbled(client, auth_headers, monkeypatch):
    hop1 = "สวัสดีครับ ช่วยสรุปให้หน่อย"
    captured: dict = {}

    async def _recorder(self, route, payload, messages, api_key=None):
        captured["hop2"] = messages[-1].content
        return ProviderResponse(content="ทดสอบ", model=route.model_name)

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _recorder)

    resp = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": hop1}],
            "stream": False,
        },
    )
    assert resp.status_code == 200, resp.text
    hop2 = captured["hop2"]
    hop3 = "ทดสอบ"
    hop4 = resp.json()["choices"][0]["message"]["content"]

    # Three or more consecutive "?" is the canonical fingerprint of
    # a charset round-trip that lost Thai bytes to cp1252.
    no_qmark_run = "???" not in hop2
    thai_intact = "สวัสดี" in hop2

    _print_hops(
        label="hop2_thai_text_not_garbled",
        hop1=hop1, hop2=hop2, hop3=hop3, hop4=hop4,
        assertions={
            "Hop 2 no '???' run": no_qmark_run,
            "Hop 2 'สวัสดี' present": thai_intact,
        },
    )
    assert no_qmark_run
    assert thai_intact


# ---------------------------------------------------------------------------
# Hop 2 — the injected DLP system prompt must NOT carry raw PII
# ---------------------------------------------------------------------------

def test_hop2_system_prompt_has_no_raw_pii(client, auth_headers, monkeypatch):
    raw_email = "a@b.com"
    hop1 = f"อีเมล {raw_email}"
    captured: dict = {}

    async def _recorder(self, route, payload, messages, api_key=None):
        # Capture the SYSTEM messages explicitly — the DLP prompt
        # injector prepends a system block when any mask token is
        # present. We must inspect those separately because the
        # user message is expected to contain the mask token, but
        # the system block must NEVER contain the raw PII.
        captured["system_blocks"] = [
            m.content for m in messages
            if getattr(m, "role", None) == "system"
        ]
        captured["hop2"] = "\n---\n".join(m.content for m in messages)
        return ProviderResponse(content="ok", model=route.model_name)

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _recorder)

    resp = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": hop1}],
            "stream": False,
        },
    )
    assert resp.status_code == 200, resp.text
    hop2 = captured["hop2"]
    hop3 = "ok"
    hop4 = resp.json()["choices"][0]["message"]["content"]
    system_blocks = captured.get("system_blocks") or []
    system_concat = "\n".join(system_blocks)

    system_clean = raw_email not in system_concat
    user_message_has_token = "[EMAIL_0]" in hop2

    _print_hops(
        label="hop2_system_prompt_has_no_raw_pii",
        hop1=hop1, hop2=hop2, hop3=hop3, hop4=hop4,
        assertions={
            "System block has no raw email": system_clean,
            "User message has [EMAIL_0]": user_message_has_token,
            "System block count >= 1": len(system_blocks) >= 1,
        },
    )
    assert system_clean
    assert user_message_has_token

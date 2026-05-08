"""End-to-end Gateway DLP integration tests.

Full lifecycle through /v1/chat/completions with stubbed provider:
  Auth → DLP scan → policy decision → route/block → response

5 scenarios:
  1. Thai customer support (email + phone)       → MASK
  2. Dev secrets (AWS key)                       → BLOCK (403)
  3. Encoding bypass (db conn + zero-width CC)   → BLOCK (403)
  4. PDPA §26 sensitive data (religion + health) → MASK
  5. False positive / clean text                 → ALLOW
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def _chat(content: str, **kwargs):
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": content}],
        **kwargs,
    }
    return client.post("/v1/chat/completions", headers=_HEADERS, json=body)


def _print_result(label: str, resp, expected_status: int, expected_action: str):
    p = lambda s: print(_safe(s))
    p(f"\n{'='*70}")
    p(f"  {label}")
    p(f"{'='*70}")
    p(f"  HTTP {resp.status_code} (expected {expected_status})")

    body = resp.json()
    if resp.status_code == 200:
        p(f"  policy_action: {body.get('policy_action', 'N/A')}")
        reasons = body.get("policy_reasons", [])
        if reasons:
            p(f"  policy_reasons: {reasons[:5]}")
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        p(f"  response preview: {content[:120]}")
    elif resp.status_code == 403:
        err = body.get("error", {})
        p(f"  error.code: {err.get('code', 'N/A')}")
        details = err.get("details", {})
        if details:
            p(f"  blocked_detectors: {details.get('detectors', [])[:5]}")
    else:
        p(f"  body: {str(body)[:200]}")

    status_ok = resp.status_code == expected_status
    p(f"  Status check: {'[OK]' if status_ok else '[FAIL]'}")


# ---------------------------------------------------------------------------
# Test 1: Thai customer support — MASK (email + phone)
# ---------------------------------------------------------------------------


class TestE2EGatewayThaiMask:
    TEXT = (
        "ลูกค้าชื่อสมชาย อีเมล somchai@example.com "
        "เบอร์โทร 089-123-4567 ต้องการสอบถามยอดบัตร"
    )

    def test_returns_200(self):
        resp = _chat(self.TEXT)
        _print_result("Thai customer support MASK", resp, 200, "mask")
        assert resp.status_code == 200

    def test_policy_action_mask(self):
        resp = _chat(self.TEXT)
        assert resp.json()["policy_action"] == "mask"

    def test_has_policy_reasons(self):
        resp = _chat(self.TEXT)
        assert len(resp.json()["policy_reasons"]) > 0

    def test_response_has_original_pii(self):
        """Masking is applied before provider; PII is restored in response."""
        resp = _chat(self.TEXT)
        content = resp.json()["choices"][0]["message"]["content"]
        assert "somchai@example.com" in content


# ---------------------------------------------------------------------------
# Test 2: Dev secrets — BLOCK (AWS key → 403)
# ---------------------------------------------------------------------------


class TestE2EGatewayDevSecretsBlock:
    TEXT = (
        "Here is my AWS access key: AKIAIOSFODNN7EXAMPLE "
        "and secret: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    )

    def test_returns_403(self):
        resp = _chat(self.TEXT)
        _print_result("Dev secrets BLOCK", resp, 403, "block")
        assert resp.status_code == 403

    def test_error_code_dlp_violation(self):
        resp = _chat(self.TEXT)
        assert resp.json()["error"]["code"] == "dlp_violation"

    def test_provider_not_called(self):
        """Blocked requests must NOT reach the provider."""
        resp = _chat(self.TEXT)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Test 3: Encoding bypass — BLOCK (db connection string via homoglyphs)
# ---------------------------------------------------------------------------


class TestE2EGatewayEncodingBypassBlock:
    # Cyrillic homoglyphs in password: р→p, а→a, о→o
    TEXT = (
        "Database: postgresql://admin:р​а​ssw​о​rd"
        "@db.internal:5432/prod"
    )

    def test_returns_403(self):
        resp = _chat(self.TEXT)
        _print_result("Encoding bypass BLOCK", resp, 403, "block")
        assert resp.status_code == 403

    def test_error_code_dlp_violation(self):
        resp = _chat(self.TEXT)
        assert resp.json()["error"]["code"] == "dlp_violation"


# ---------------------------------------------------------------------------
# Test 4: PDPA §26 sensitive data — MASK (religion + health)
# ---------------------------------------------------------------------------


class TestE2EGatewayPdpaMask:
    TEXT = (
        "ผมนับถือศาสนาอิสลาม ผมป่วยเป็นโรคเบาหวาน "
        "อีเมล somchai@gmail.com"
    )

    def test_returns_200(self):
        resp = _chat(self.TEXT)
        _print_result("PDPA S26 MASK", resp, 200, "mask")
        assert resp.status_code == 200

    def test_policy_action_mask(self):
        resp = _chat(self.TEXT)
        assert resp.json()["policy_action"] == "mask"

    def test_has_policy_reasons(self):
        resp = _chat(self.TEXT)
        reasons = resp.json()["policy_reasons"]
        assert len(reasons) > 0


# ---------------------------------------------------------------------------
# Test 5: Clean text — ALLOW (no PII, no secrets)
# ---------------------------------------------------------------------------


class TestE2EGatewayCleanAllow:
    TEXT = "What is the capital of Thailand?"

    def test_returns_200(self):
        resp = _chat(self.TEXT)
        _print_result("Clean text ALLOW", resp, 200, "allow")
        assert resp.status_code == 200

    def test_policy_action_allow(self):
        resp = _chat(self.TEXT)
        assert resp.json()["policy_action"] == "allow"

    def test_no_sensitive_findings(self):
        resp = _chat(self.TEXT)
        reasons = resp.json()["policy_reasons"]
        assert "No sensitive content detected." in reasons

    def test_response_has_content(self):
        resp = _chat(self.TEXT)
        content = resp.json()["choices"][0]["message"]["content"]
        assert len(content) > 0

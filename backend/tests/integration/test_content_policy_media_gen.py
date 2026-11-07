"""Content-policy ↔ MEDIA_GEN wiring (Sprint 4).

Validates the Batch E wiring at the policy/service layer (the layer the
image/video/audio/music routes all funnel through): the content-policy
detectors fire and BLOCK under the MEDIA_GEN profile, while the DEFAULT
(chat) profile does not block — proving the chat path is unaffected.
"""

from app.services.media_gen.submit_helpers import content_policy_blocked
from app.services.policy.profiles import get_profile

MEDIA_GEN = get_profile("media_gen")
DEFAULT = get_profile("default")


def test_copyright_blocked_under_media_gen():
    # Image/video gen with a copyrighted character/artist → blocked.
    assert content_policy_blocked("draw mickey mouse", MEDIA_GEN) is True
    assert content_policy_blocked("a song like Taylor Swift", MEDIA_GEN) is True


def test_public_figure_blocked_under_media_gen():
    assert content_policy_blocked("photo of elon musk", MEDIA_GEN) is True


def test_safe_prompt_passes_under_media_gen():
    assert content_policy_blocked("a sunset over mountains", MEDIA_GEN) is False


def test_csam_blocked_under_media_gen():
    assert content_policy_blocked("child porn request", MEDIA_GEN) is True


def test_identity_pii_not_content_policy_blocked():
    # An email is identity-class PII (ALLOW under MEDIA_GEN), NOT content_policy
    # — the content-policy layer must not block it.
    assert content_policy_blocked("email me at john@example.com", MEDIA_GEN) is False


def test_chat_path_unaffected_default_profile():
    # DEFAULT profile has no content_policy→BLOCK mapping, so even a
    # copyrighted name does not trip the content-policy gate on the chat path.
    assert content_policy_blocked("draw mickey mouse", DEFAULT) is False
    assert content_policy_blocked("photo of elon musk", DEFAULT) is False

"""FIX36 Part D — callers can opt out of DLP output restore.

The desktop summarizer (conversation-state extraction) must see MASKED
text, like the web summarizer does, because the placeholder→raw restore
map is not stable across requests (legacy vault is a 2h-TTL orphan
resolver keyed on a client-supplied conversation_id the desktop never
sends). The sanctioned interim: the caller sends
``metadata.dlp_response_restore = "off"`` and the gateway skips output
restore for that request. Strictly MORE masking, DLP code untouched.
"""

from types import SimpleNamespace

from app.services.orchestrator import _restore_opted_out


def _payload(meta):
    return SimpleNamespace(metadata=meta)


class TestRestoreOptedOut:
    def test_off_string_opts_out(self):
        assert _restore_opted_out(_payload({"dlp_response_restore": "off"})) is True

    def test_case_insensitive(self):
        assert _restore_opted_out(_payload({"dlp_response_restore": "OFF"})) is True

    def test_default_is_restore(self):
        assert _restore_opted_out(_payload({})) is False
        assert _restore_opted_out(_payload(None)) is False
        assert _restore_opted_out(SimpleNamespace()) is False

    def test_other_values_do_not_opt_out(self):
        for v in ("on", "", "false", "0", None, 0, False):
            assert _restore_opted_out(_payload({"dlp_response_restore": v})) is False, v

    def test_unrelated_metadata_ignored(self):
        assert _restore_opted_out(_payload({"conversation_id": "c1"})) is False

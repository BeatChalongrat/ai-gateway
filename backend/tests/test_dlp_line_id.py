"""line_id detector — Thai-market LINE handle coverage + boundary contract.

LINE messaging-app handles look like ``@somchai_dev``. The bare
``@username`` shape collides with Twitter/Slack mentions, Thai
@-as-price-marker convention ("ราคา @100 บาท"), and incidental ``@``
chars in URLs/emails. The detector requires the literal ``LINE``
keyword adjacent to the handle.

This file is the complete regression contract for line_id. Every probe
from the diagnose-phase REPL run is here as a test case, plus the two
heavy-scenario sanity checks Beat asked for explicitly.
"""

from __future__ import annotations

from app.services.dlp.detectors.line_id import LineIdDetector


# ---------------------------------------------------------------------------
# Positive cases — every shape that MUST fire
# ---------------------------------------------------------------------------


class TestLineIdPositiveCases:
    def setup_method(self):
        self.det = LineIdDetector()

    def test_line_colon_simple_handle(self):
        """Beat probe: 'LINE: @somchai_dev'."""
        text = "LINE: @somchai_dev"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "@somchai_dev"

    def test_line_id_with_dot_and_digits_in_handle(self):
        """Beat probe: 'LINE ID: @user.123' — handle body may contain
        dots and digits."""
        text = "LINE ID: @user.123"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "@user.123"

    def test_thai_prose_then_line_keyword(self):
        """Beat probe: 'ติดต่อทาง LINE @company-th' — Thai prose
        precedes the LINE keyword, no separator after the keyword."""
        text = "ติดต่อทาง LINE @company-th"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "@company-th"

    def test_line_lowercase_id_with_underscores_and_hyphens(self):
        """Beat probe: 'LINE id: @abc_def-ghi' — case-insensitive on
        keyword, handle body may contain underscore + hyphen."""
        text = "LINE id: @abc_def-ghi"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "@abc_def-ghi"

    def test_line_lowercase_no_separator(self):
        """'line @abc_def' — bare lowercase keyword, no colon."""
        text = "line @abc_def"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_keyword_inside_prose(self):
        """Realistic prose: 'add me on LINE: @sarah-jones'."""
        text = "add me on LINE: @sarah-jones"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "@sarah-jones"

    def test_equals_separator_form(self):
        """Equals-sign separator: 'LINE = @marketing.team'."""
        text = "LINE = @marketing.team"
        matches = self.det.detect(text)
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# Negative cases — false positives that MUST NOT fire
# ---------------------------------------------------------------------------


class TestLineIdNegativeCases:
    def setup_method(self):
        self.det = LineIdDetector()

    def test_email_at_work_no_line_keyword(self):
        """'email @work mentions' — '@' present without LINE keyword."""
        text = "email @work mentions"
        matches = self.det.detect(text)
        assert matches == []

    def test_slack_at_everyone_mention(self):
        """'@everyone please review' — Slack-style mention."""
        text = "@everyone please review"
        matches = self.det.detect(text)
        assert matches == []

    def test_twitter_handle_no_line_keyword(self):
        """'follow @username on twitter' — Twitter handle."""
        text = "follow @username on twitter"
        matches = self.det.detect(text)
        assert matches == []

    def test_thai_at_price_marker(self):
        """'ราคา @100 บาท' — Thai usage of '@' as price-each marker.
        The digit lead char rejects this even before context check."""
        text = "ราคา @100 บาท"
        matches = self.det.detect(text)
        assert matches == []

    def test_code_at_line_42(self):
        """'code @ line 42' — LINE keyword present but separated from
        any @-handle. The regex requires 'LINE ... @<alpha>' adjacency."""
        text = "code @ line 42"
        matches = self.det.detect(text)
        assert matches == []

    def test_at_2x_display_scaling(self):
        """'@2x display scaling' — digit lead char rejected by the
        '@[a-zA-Z]' anchor."""
        text = "@2x display scaling"
        matches = self.det.detect(text)
        assert matches == []

    def test_log_line_reference(self):
        """'from line 42 of the log' — 'line' as a noun, no @-handle."""
        text = "from line 42 of the log"
        matches = self.det.detect(text)
        assert matches == []

    def test_bare_line_one_label(self):
        """'line 1: error' — log-line label, no @-handle."""
        text = "line 1: error"
        matches = self.det.detect(text)
        assert matches == []

    def test_at_in_url(self):
        """URL with '@' in query string — no LINE keyword adjacent."""
        text = "see http://example.com/api?@param=1"
        matches = self.det.detect(text)
        assert matches == []

    def test_line_keyword_alone_no_handle(self):
        """'LINE was a popular product' — keyword present, no @-handle."""
        text = "LINE was a popular product"
        matches = self.det.detect(text)
        assert matches == []

    def test_line_colon_but_no_at_handle(self):
        """'LINE: hello world' — keyword + colon present, no @ char."""
        text = "LINE: hello world"
        matches = self.det.detect(text)
        assert matches == []


# ---------------------------------------------------------------------------
# Heavy-scenario sanity (Beat-requested explicit tests)
# ---------------------------------------------------------------------------


class TestLineIdHeavyScenario:
    def setup_method(self):
        self.det = LineIdDetector()

    def test_heavy_scenario_full_line_fires(self):
        """Lock the heavy-scenario shape: email + LINE handle on the
        same line. Must produce exactly one finding for the LINE
        handle, with no spurious match on the email's '@'."""
        text = (
            "เบอร์โทรมือถือ 081-234-5678 อีเมล somchai.j@example.com "
            "LINE ID: @somchai_dev"
        )
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "@somchai_dev"
        # Defensive: the email's @example.com must NOT have produced
        # a finding masquerading as a LINE handle.
        assert "example.com" not in matches[0].matched_text

    def test_email_alone_does_not_fire_line_id(self):
        """A stand-alone email line in the heavy scenario must not
        trigger the line_id detector even though it contains '@'."""
        text = "อีเมล somchai.j@example.com"
        matches = self.det.detect(text)
        assert matches == []

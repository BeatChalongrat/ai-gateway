"""LINE messaging handle detector — requires ``LINE`` keyword + ``@`` handle."""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestLineIdHappyPath:
    def test_line_id_with_at_handle(self):
        _, span_map, _ = mask("LINE ID: @somchai99")
        assert any("LINE_ID" in t for t in span_map)

    def test_line_keyword_with_dotted_handle(self):
        _, span_map, _ = mask("Line: @somchai.jaidee")
        assert any("LINE_ID" in t for t in span_map)

    def test_thai_text_with_line_keyword(self):
        # The detector requires the literal ``LINE`` keyword (ASCII) —
        # the Thai transliteration ``ไลน์`` is not in the regex. Tests
        # with surrounding Thai prose still work as long as ``LINE``
        # appears before the @-handle.
        text = "ติดต่อผ่าน LINE: @somchai_99"
        _, span_map, _ = mask(text)
        assert any("LINE_ID" in t for t in span_map)


class TestLineIdBoundary:
    def test_line_lowercase_at_handle(self):
        _, span_map, _ = mask("line: @somchai.jaidee")
        assert any("LINE_ID" in t for t in span_map)


class TestLineIdNegative:
    def test_line_in_code_context(self):
        # ``line 42 of the code`` — keyword adjacency check rejects.
        assert "line_id" not in detector_names_for(
            "Check line 42 of the code"
        )

    def test_thai_path_word(self):
        assert "line_id" not in detector_names_for("เส้นทาง")

    def test_at_handle_without_line_keyword(self):
        # @-handle without the LINE prefix must not fire.
        assert "line_id" not in detector_names_for("follow @username")

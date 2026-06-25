"""Tests for the DLP pattern safety validator (Sprint 6, C)."""

from app.services.dlp.pattern_validator import (
    validate_keyword_list,
    validate_regex,
    validate_semantic_entity,
)


def test_valid_simple_regex():
    r = validate_regex(r"^[A-Za-z]+$")
    assert r.valid is True
    assert r.compile_time_ms is not None


def test_invalid_syntax():
    r = validate_regex(r"[unclosed")
    assert r.valid is False
    assert "syntax error" in r.error


def test_nested_quantifier_redos():
    r = validate_regex(r"(a+)+$")
    assert r.valid is False
    assert "ReDoS" in r.error


def test_long_pattern_rejected():
    r = validate_regex("a" * 501)
    assert r.valid is False
    assert "too long" in r.error


def test_slow_pattern_times_out():
    # Evades the static shape heuristic but blows up on the pathological
    # input — caught by the 50ms hard cap.
    r = validate_regex(r"((a)*)*$")
    assert r.valid is False
    assert "slow" in r.error.lower()


def test_valid_thai_pattern():
    r = validate_regex(r"^[ก-ฮ]{2}[0-9]{4}$")
    assert r.valid is True


def test_keyword_list_rules():
    assert validate_keyword_list(["a", "b", "c", "d", "e"]).valid is True
    assert validate_keyword_list([]).valid is False
    assert validate_keyword_list([f"k{i}" for i in range(101)]).valid is False
    assert validate_keyword_list(["x" * 250]).valid is False
    assert validate_keyword_list(["ok", "   "]).valid is False


def test_semantic_entity():
    assert validate_semantic_entity("PERSON").valid is True
    assert validate_semantic_entity("MADE_UP_TYPE").valid is False

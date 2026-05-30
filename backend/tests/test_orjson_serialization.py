"""Re-audit H16 / M17: UTF8JSONResponse orjson serialization contract.

H16 — ``UTF8JSONResponse.render`` now passes ``default=orjson_default``
so a route returning a raw dict containing Decimal / set / datetime /
UUID / bytes / Enum coerces cleanly instead of raising ``TypeError`` ->
500.

M17 — orjson serializes NaN/Inf to JSON ``null`` at the C level before
``default=`` is consulted; there is no orjson option (in 3.10.x) to
raise on non-finite floats. The test below pins that real behaviour so a
future reader does not mistake it for a fix.
"""

import datetime
import decimal
import math
import uuid

import orjson
import pytest

from app.core.responses import UTF8JSONResponse


def _roundtrip(content):
    """Render ``content`` through UTF8JSONResponse and parse it back."""
    body = UTF8JSONResponse(content).render(content)
    assert isinstance(body, bytes)
    return orjson.loads(body)


def test_decimal_serializes_as_float():
    parsed = _roundtrip({"price": decimal.Decimal("19.99")})
    assert parsed["price"] == 19.99
    assert isinstance(parsed["price"], float)
    # explicitly not a string
    assert not isinstance(parsed["price"], str)


def test_set_serializes_as_list():
    parsed = _roundtrip({"ids": {1, 2, 3}})
    assert isinstance(parsed["ids"], list)
    assert sorted(parsed["ids"]) == [1, 2, 3]


def test_frozenset_serializes_as_list():
    parsed = _roundtrip({"ids": frozenset({"a", "b"})})
    assert isinstance(parsed["ids"], list)
    assert sorted(parsed["ids"]) == ["a", "b"]


def test_uuid_serializes_as_string():
    u = uuid.UUID("12345678-1234-5678-1234-567812345678")
    parsed = _roundtrip({"id": u})
    assert parsed["id"] == str(u)
    assert isinstance(parsed["id"], str)


def test_datetime_serializes_as_isoformat():
    dt = datetime.datetime(2026, 5, 30, 12, 34, 56)
    parsed = _roundtrip({"ts": dt})
    assert parsed["ts"] == dt.isoformat()


def test_date_serializes_as_isoformat():
    d = datetime.date(2026, 5, 30)
    parsed = _roundtrip({"day": d})
    assert parsed["day"] == d.isoformat()


def test_bytes_serializes_as_decoded_string():
    parsed = _roundtrip({"blob": b"hello"})
    assert parsed["blob"] == "hello"
    assert isinstance(parsed["blob"], str)


def test_enum_serializes_as_value():
    import enum

    class Color(enum.Enum):
        RED = "red"
        GREEN = "green"

    parsed = _roundtrip({"c": Color.RED})
    assert parsed["c"] == "red"


def test_nan_serializes_as_null_documented_orjson_behavior():
    # M17: orjson emits JSON null for NaN BEFORE default= is consulted;
    # there is no orjson option to raise. This pins the real behaviour
    # (it is NOT a fix — the mitigation lives upstream in cost/latency
    # math, out of scope for the response class).
    parsed = _roundtrip({"score": float("nan")})
    assert parsed["score"] is None


def test_inf_serializes_as_null_documented_orjson_behavior():
    parsed = _roundtrip({"score": float("inf")})
    assert parsed["score"] is None


def test_normal_dict_still_works_regression():
    content = {
        "id": "abc",
        "n": 42,
        "ratio": 0.5,
        "ok": True,
        "nested": {"k": [1, 2, 3]},
        "thai": "สวัสดี",  # non-ASCII must ride through unescaped
    }
    body = UTF8JSONResponse(content).render(content)
    # non-ASCII stays as natural UTF-8 bytes, not \uXXXX escapes
    assert "สวัสดี".encode("utf-8") in body
    parsed = orjson.loads(body)
    assert parsed == content


def test_unsupported_type_raises_typeerror():
    class Weird:
        pass

    with pytest.raises(TypeError):
        UTF8JSONResponse({"x": Weird()}).render({"x": Weird()})


def test_int_keys_coerced_via_opt_non_str_keys():
    # OPT_NON_STR_KEYS preserved alongside default=.
    parsed = _roundtrip({1: "one", 2: "two"})
    assert parsed == {"1": "one", "2": "two"}


def test_pydantic_v2_model_dump_supported():
    pydantic = pytest.importorskip("pydantic")

    class M(pydantic.BaseModel):
        a: int
        b: str

    parsed = _roundtrip({"model": M(a=1, b="x")})
    assert parsed["model"] == {"a": 1, "b": "x"}

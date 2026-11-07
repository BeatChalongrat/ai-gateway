"""FIX67 — media destination order rules + the mock leak guard (Part 3.3)."""

import pathlib

from app.services.media_gen.destinations import (
    Destination,
    MAX_TIERS,
    is_mock_provider,
    parse_order,
    usable_destinations,
)


def test_parse_order_tolerant():
    got = parse_order("openai:gpt-image-1, mock:mock-image-1 ,,bad, :nomodel, prov:")
    assert got == [Destination("openai", "gpt-image-1"), Destination("mock", "mock-image-1")]
    assert parse_order("") == []
    assert parse_order(None) == []


def test_mock_dropped_when_disabled():
    order = [Destination("openai", "gpt-image-1"), Destination("mock", "mock-image-1")]
    usable = usable_destinations(order, valid_providers={"openai"}, mock_enabled=False)
    assert usable == [Destination("openai", "gpt-image-1")]  # mock never leaks


def test_mock_kept_when_enabled():
    order = [Destination("openai", "gpt-image-1"), Destination("mock", "mock-image-1")]
    usable = usable_destinations(order, valid_providers={"openai"}, mock_enabled=True)
    assert usable == order


def test_capped_at_two_tiers():
    order = [
        Destination("openai", "gpt-image-1"),
        Destination("a", "m1"),
        Destination("b", "m2"),
    ]
    usable = usable_destinations(order, valid_providers={"openai", "a", "b"}, mock_enabled=False)
    assert len(usable) == MAX_TIERS == 2


def test_cross_provider_only():
    # two entries on the same provider is not a real fallback — the dup is dropped.
    order = [Destination("openai", "gpt-image-1"), Destination("openai", "gpt-image-1-mini")]
    usable = usable_destinations(order, valid_providers={"openai"}, mock_enabled=False)
    assert usable == [Destination("openai", "gpt-image-1")]


def test_unusable_provider_filtered():
    order = [Destination("openai", "gpt-image-1"), Destination("cohere", "x")]
    usable = usable_destinations(order, valid_providers={"openai"}, mock_enabled=False)
    assert [d.provider for d in usable] == ["openai"]  # cohere has no valid key


def test_registered_filter():
    order = [Destination("openai", "gpt-image-1"), Destination("ghost", "x")]
    usable = usable_destinations(
        order, valid_providers={"openai", "ghost"}, mock_enabled=False,
        registered_providers={"openai"},
    )
    assert [d.provider for d in usable] == ["openai"]  # ghost has no registered backend


def test_is_mock_provider():
    assert is_mock_provider("mock") is True
    assert is_mock_provider("openai") is False


# --- Part 3.3: a red test if mock ever leaks into a production default -------

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "migrations" / "versions" / "085_image_backends_and_media_order.py"
)


def test_seeded_defaults_have_no_mock_and_mock_is_off():
    """The migration-seeded production defaults must never carry a mock
    destination, and the mock kill-switch must default OFF. If someone edits the
    seed to put mock in a default order (the mock-chat-1 class of incident),
    this goes red."""
    src = _MIGRATION.read_text(encoding="utf-8")
    # default image order line
    assert '"media_order:image", "openai:gpt-image-1"' in src
    assert '"media_order:video", ""' in src
    assert '"media_allow_mock", "false"' in src
    # No default order value contains a mock destination.
    for line in src.splitlines():
        if "media_order:" in line and "mock:" in line:
            raise AssertionError(f"mock leaked into a default order: {line.strip()}")

"""Regression: the NER sync-bridge must not leak per-loop httpx clients.

``analyze_sync`` runs the async NER call on a THROWAWAY event loop via
``asyncio.run`` (one per request when NER is enabled). ``analyze`` caches an
``httpx.AsyncClient`` in the module-global ``_clients_by_loop`` keyed by the
loop id. Before the fix, that client (and the dead loop its transport pinned)
was never released, so the dict grew one entry per call — an unbounded RSS
leak the load test caught as ~2 GiB / 90s at 100 concurrent. The fix closes +
evicts the per-loop client in a ``finally`` before ``asyncio.run`` disposes
the loop.
"""

from __future__ import annotations

from app.services import ner_service_client as nsc


def test_analyze_sync_evicts_per_loop_client(monkeypatch):
    # Point the client at an unroutable address so the HTTP call fails fast
    # and ``analyze`` returns [] gracefully — the point is that a client gets
    # CREATED + cached per throwaway loop and must then be evicted.
    monkeypatch.setattr(nsc.settings, "ner_service_url", "http://127.0.0.1:1",
                        raising=False)
    nsc._clients_by_loop.clear()

    for _ in range(8):
        out = nsc.analyze_sync("Alice met Bob in Bangkok.")
        # NER unreachable -> graceful empty (client never raises).
        assert out == []

    # Pre-fix this dict held one stale (dead-loop, client) pair per call.
    # The fix evicts each, so it must be empty (or at most bounded, never
    # growing with call count).
    assert len(nsc._clients_by_loop) == 0


def test_analyze_sync_no_client_when_ner_url_unset(monkeypatch):
    # When NER is not configured, no client is ever created and the dict
    # stays empty — the legacy in-process path is used instead.
    monkeypatch.setattr(nsc.settings, "ner_service_url", "", raising=False)
    nsc._clients_by_loop.clear()
    assert nsc.analyze_sync("hello") == []
    assert len(nsc._clients_by_loop) == 0

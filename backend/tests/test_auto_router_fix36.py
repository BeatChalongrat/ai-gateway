"""FIX36 Part A — the router must actually CHOOSE.

Pins the three changes on top of FIX35's four-step router:

  A1  ranking aggregates by RANK GROUP (7 buckets), not the 32 fine tasks,
      so cells reach min_samples 4-5x faster; the task→group map lives in
      the same asset (``task_taxonomy.json``).
  A2  configurable per-group PRIORS blended with live tenant data by a
      growing weight w = n/(n+K) — no hard cliff at min_samples.
  A3  exploration: with probability ``exploration_rate`` promote ONE
      under-sampled candidate to primary; never for explicit models
      (router isn't called) and never under task_override.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pytest

from app.services.auto_router import ranking, router, taxonomy


@dataclass
class Cand:
    model: str
    provider: str = "openai"
    input_cost_per_1m: float = 1.0
    output_cost_per_1m: float = 2.0
    supports_reasoning: bool = False
    supports_vision: bool = False
    supports_tools: bool = True
    extras: dict = field(default_factory=dict)


def _rows(*items):
    # (task_category, model, n, ok, avg_latency_ms, avg_cost_usd)
    return list(items)


# ---------------------------------------------------------------- A1: groups


class TestGroupAggregation:
    def test_asset_maps_every_task_to_a_known_group(self):
        tax = taxonomy.load_taxonomy()
        groups = set((tax.get("rank_groups") or {}).keys())
        assert len(groups) >= 6, "FIX36 A1 wants 6-8 rank groups"
        assert len(groups) <= 8
        for task, spec in tax["tasks"].items():
            assert spec.get("group") in groups, f"task {task} unmapped"

    def test_every_group_has_a_nonempty_prior(self):
        tax = taxonomy.load_taxonomy()
        for gid, spec in tax["rank_groups"].items():
            assert spec.get("prior"), f"group {gid} has an empty prior"

    def test_cells_keyed_by_group_not_task(self):
        # code_generate and code_debug both map to the "code" group: their
        # rows must land in ONE cell, reaching min_samples together.
        assert taxonomy.task_group("code_generate") == taxonomy.task_group("code_debug")
        gid = taxonomy.task_group("code_generate")
        r = ranking.build_ranking(
            "t1",
            _rows(
                ("code_generate", "gpt-x", 12, 12, 800.0, 0.001),
                ("code_debug", "gpt-x", 10, 8, 1200.0, 0.002),
            ),
            min_samples=20,
        )
        cell = r.cells.get((gid, "gpt-x"))
        assert cell is not None
        assert cell.requests == 22  # 12 + 10 pooled ≥ min_samples
        assert cell.successes == 20
        # weighted averages, not last-writer-wins
        assert cell.avg_latency_ms == pytest.approx((12 * 800 + 10 * 1200) / 22)
        s, n = r.score_with_samples(gid, "gpt-x")
        assert s is not None and n == 22

    def test_unmapped_task_lands_only_in_star(self):
        r = ranking.build_ranking(
            "t1", _rows(("no_such_task", "m1", 30, 30, 500.0, 0.001)), min_samples=20
        )
        assert ("*", "m1") in r.cells
        assert all(k[0] == "*" for k in r.cells)

    def test_task_group_unknown_task_defaults(self):
        assert taxonomy.task_group("definitely_not_a_task") == taxonomy.task_group(
            taxonomy.GENERAL
        )


# ---------------------------------------------------------------- A2: blend


class TestPriorBlend:
    def test_knobs_read_from_asset(self):
        assert 0.05 <= taxonomy.exploration_rate() <= 0.10
        assert taxonomy.prior_weight_samples() >= 1

    def test_no_data_prior_decides(self):
        gid = taxonomy.task_group("general_chat")
        prior = taxonomy.group_prior(gid)
        assert prior, "general group needs a prior"
        pool = [Cand(model=m) for m in reversed(prior[:3])]
        ordered, used = router._order(
            pool, task="general_chat", ranking=None, preferred=(), proven=frozenset()
        )
        assert [c.model for c in ordered] == prior[:3]
        assert used is False

    def test_live_data_shifts_order_smoothly(self):
        gid = taxonomy.task_group("general_chat")
        prior = taxonomy.group_prior(gid)
        top, second = prior[0], prior[1]
        # Give the SECOND prior model overwhelming live evidence: perfect
        # success and volume while the prior favourite keeps failing.
        r = ranking.build_ranking(
            "t1",
            _rows(
                ("general_chat", second, 400, 400, 500.0, 0.0005),
                ("general_chat", top, 400, 40, 900.0, 0.02),
            ),
            min_samples=20,
        )
        pool = [Cand(model=top), Cand(model=second)]
        ordered, used = router._order(
            pool, task="general_chat", ranking=r, preferred=(), proven=frozenset()
        )
        assert used is True
        assert ordered[0].model == second, "live evidence must outweigh the prior"

    def test_thin_data_does_not_flip_the_prior(self):
        gid = taxonomy.task_group("general_chat")
        prior = taxonomy.group_prior(gid)
        top, second = prior[0], prior[1]
        # 2 lucky samples for the underdog: w = 2/22 ≈ 0.09 — prior holds.
        # (below min_samples the live score is None anyway; this pins that
        # the smooth path also would not flip on nothing)
        r = ranking.build_ranking(
            "t1", _rows(("general_chat", second, 2, 2, 100.0, 0.0001)), min_samples=20
        )
        pool = [Cand(model=top), Cand(model=second)]
        ordered, _ = router._order(
            pool, task="general_chat", ranking=r, preferred=(), proven=frozenset()
        )
        assert ordered[0].model == top

    def test_weight_formula_no_cliff(self):
        k = taxonomy.prior_weight_samples()
        w_at_k = k / (k + k)
        assert w_at_k == pytest.approx(0.5), "at n=K live and prior split evenly"


# ------------------------------------------------------------ A3: exploration


class TestExploration:
    def _ranking_with_established(self, gid, model, n=200):
        return ranking.build_ranking(
            "t1", _rows(("general_chat", model, n, n, 500.0, 0.001)), min_samples=20
        )

    def test_explore_promotes_undersampled(self):
        gid = taxonomy.task_group("general_chat")
        r = self._ranking_with_established(gid, "m-known")
        ordered = [Cand(model="m-known"), Cand(model="m-new")]
        rng = random.Random(1)  # first random() = 0.134... — need forced hit
        forced = random.Random(0)
        # find a seed whose first draw is below the configured rate
        rate = taxonomy.exploration_rate()
        seed = next(s for s in range(1000) if random.Random(s).random() < rate)
        out, explored = router._explore(
            list(ordered), ranking=r, group=gid, rng=random.Random(seed)
        )
        assert explored is True
        assert out[0].model == "m-new", "under-sampled candidate promoted"
        assert out[1].model == "m-known", "displaced primary is first fallback"

    def test_no_explore_when_rng_above_rate(self):
        gid = taxonomy.task_group("general_chat")
        r = self._ranking_with_established(gid, "m-known")
        rate = taxonomy.exploration_rate()
        seed = next(s for s in range(1000) if random.Random(s).random() >= rate)
        ordered = [Cand(model="m-known"), Cand(model="m-new")]
        out, explored = router._explore(
            list(ordered), ranking=r, group=gid, rng=random.Random(seed)
        )
        assert explored is False
        assert [c.model for c in out] == ["m-known", "m-new"]

    def test_no_explore_when_everything_sampled(self):
        gid = taxonomy.task_group("general_chat")
        r = ranking.build_ranking(
            "t1",
            _rows(
                ("general_chat", "m-a", 200, 200, 500.0, 0.001),
                ("general_chat", "m-b", 200, 190, 600.0, 0.002),
            ),
            min_samples=20,
        )
        rate = taxonomy.exploration_rate()
        seed = next(s for s in range(1000) if random.Random(s).random() < rate)
        ordered = [Cand(model="m-a"), Cand(model="m-b")]
        out, explored = router._explore(
            list(ordered), ranking=r, group=gid, rng=random.Random(seed)
        )
        assert explored is False

    def test_task_override_never_explores(self):
        # route_auto with task_override must not explore even when the rng
        # would fire — determinism for callers that asserted the task.
        rate = taxonomy.exploration_rate()
        seed = next(s for s in range(1000) if random.Random(s).random() < rate)
        plan = router.route_auto(
            candidates=[Cand(model="m-a"), Cand(model="m-b")],
            hints=None,
            messages=[{"role": "user", "content": "hello"}],
            ranking=None,
            ceiling="high",
            task_override="general_chat",
            rng=random.Random(seed),
        )
        assert plan is not None
        assert plan.explored is False
        assert "explore" not in plan.reason

    def test_route_auto_can_explore_and_reports_it(self):
        gid = taxonomy.task_group("general_chat")
        r = self._ranking_with_established(gid, "m-a")
        rate = taxonomy.exploration_rate()
        seed = next(s for s in range(1000) if random.Random(s).random() < rate)
        plan = router.route_auto(
            candidates=[Cand(model="m-a"), Cand(model="m-b")],
            hints=None,
            messages=[{"role": "user", "content": "สวัสดี"}],
            ranking=r,
            ceiling="high",
            rng=random.Random(seed),
        )
        assert plan is not None
        assert plan.explored is True
        assert "explore" in plan.reason
        assert plan.fallbacks, "displaced best-known model stays as fallback"

    def test_zero_rate_disables(self, monkeypatch):
        monkeypatch.setattr(taxonomy, "exploration_rate", lambda: 0.0)
        out, explored = router._explore(
            [Cand(model="a"), Cand(model="b")], ranking=None, group="chat_light",
            rng=random.Random(0),
        )
        assert explored is False

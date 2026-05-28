"""Routing domain contracts.

These types define the boundary between the routing engine and the rest
of the gateway.  The routing service consumes a RoutingRequest and
produces a RoutingResult with ranked candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ModelCandidate:
    """A candidate model/provider pair with capability metadata."""

    provider: str
    model: str
    supports_streaming: bool = True
    supports_tools: bool = False
    max_context_tokens: int = 128000
    cost_tier: str = "standard"  # "budget", "standard", "premium"
    latency_tier: str = "standard"  # "fast", "standard", "slow"
    # Real $/token costs — single source of truth for both routing scoring
    # and billing. 0.0 means unknown/unset; consumers should fall back to
    # ``cost_tier`` for routing and to the legacy ``_COST_PER_TOKEN`` table
    # in ``usage_service`` for billing.
    cost_per_input_token: float = 0.0
    cost_per_output_token: float = 0.0
    # Reasoning / thinking capability (migration 045). T1 plumbs the
    # fields through the registry → candidate → routing surface;
    # ``_route_auto`` filters on ``supports_reasoning`` when the caller
    # asks for the ``reasoning`` use_case. ``reasoning_strategy`` is
    # consumed by T2 adapters to pick the right per-provider
    # translation; ``default_reasoning_mode`` is the gateway's choice
    # when the caller enables reasoning without an explicit ``mode``.
    supports_reasoning: bool = False
    reasoning_strategy: str = "none"
    default_reasoning_mode: str = "auto"


@dataclass(frozen=True)
class RoutingRequest:
    """Inputs consumed by the routing engine."""

    requested_model: str  # "auto" or explicit model name
    stream: bool = False
    use_case: str = "general"

    # Project/policy constraints (None = unconstrained)
    allowed_providers: frozenset[str] | None = None
    blocked_models: frozenset[str] | None = None
    preferred_provider: str | None = None

    # Cost/latency preferences
    prefer_cost: bool = False  # prefer cheaper models
    prefer_latency: bool = False  # prefer faster models


@dataclass(frozen=True)
class RejectedCandidate:
    """A candidate that was considered but rejected."""

    candidate: ModelCandidate
    reason: str


@dataclass
class RoutingResult:
    """Output of the routing engine.

    Contains ranked candidates (best first), rejected candidates for
    auditability, and a trace of the routing decision logic.
    """

    selected: ModelCandidate
    fallbacks: list[ModelCandidate] = field(default_factory=list)
    rejected: list[RejectedCandidate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    constrained_by_policy: bool = False

    @property
    def provider(self) -> str:
        return self.selected.provider

    @property
    def model(self) -> str:
        return self.selected.model

    def to_trace_dict(self) -> dict:
        """Serialize the routing decision for audit/debug logging."""
        return {
            "selected": {
                "provider": self.selected.provider,
                "model": self.selected.model,
                "cost_tier": self.selected.cost_tier,
                "latency_tier": self.selected.latency_tier,
            },
            "fallbacks": [
                {"provider": f.provider, "model": f.model}
                for f in self.fallbacks
            ],
            "rejected": [
                {"provider": r.candidate.provider, "model": r.candidate.model, "reason": r.reason}
                for r in self.rejected
            ],
            "reasons": self.reasons,
            "constrained_by_policy": self.constrained_by_policy,
        }

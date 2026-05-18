"""Seed ``model_configs`` with the 48 candidates the gateway shipped
with as a hardcoded list in ``app/services/providers/model_registry.py``.

Rationale: the runtime registry switches to reading from this table at
import + lifespan time. Without a pre-populated row set the gateway
would refuse every routing request until the first scheduled discovery
sync runs (24 hours). Seeding here means the DB has the same data the
old hardcoded list provided the moment the migration applies.

All rows are inserted with ``source='seed'`` and
``is_manual_override=False`` so the discovery sync can refresh them
from upstream APIs / litellm pricing on its first run.

Values mirror ``_CANDIDATES`` as of revision 035. Intentionally
embedded inline (rather than imported from app code) so the migration
is hermetic — historical migrations must not depend on a moving
runtime module.

Revision ID: 037
Revises: 036
Create Date: 2026-05-12
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def _derive_cost_tier(price: float) -> str:
    """Inline copy of the helper in ``model_registry``.

    Migrations don't import live application code, so the threshold
    helper is duplicated here. Two-line drift risk is negligible —
    the seed-data tier classification is a one-time snapshot.
    """
    if price == 0.0:
        return "standard"
    if price <= 0.0000005:
        return "budget"
    if price <= 0.000003:
        return "standard"
    return "premium"


# (provider, model, supports_streaming, latency_tier,
#  cost_per_input_token, cost_per_output_token)
#
# All rows derive cost_tier programmatically (see _build_rows) and use
# defaults for supports_tools (False), supports_vision (False), and
# max_context_tokens (128000) — the discovery sync will fill in the
# accurate values from upstream APIs.
_SEED_ROWS_RAW: list[tuple[str, str, bool, str, float, float]] = [
    # --- OpenAI ---
    ("openai", "gpt-4o", True, "standard", 0.0000025, 0.000010),
    ("openai", "gpt-4o-mini", True, "fast", 0.00000015, 0.0000006),
    ("openai", "gpt-4-turbo", True, "slow", 0.00001, 0.00003),
    ("openai", "gpt-3.5-turbo", True, "fast", 0.0000005, 0.0000015),
    # --- Anthropic ---
    ("anthropic", "claude-3-5-sonnet-20241022", True, "standard", 0.000003, 0.000015),
    ("anthropic", "claude-3-opus-20240229", True, "slow", 0.000015, 0.000075),
    ("anthropic", "claude-3-haiku-20240307", True, "fast", 0.00000025, 0.00000125),
    # --- Minimax ---
    ("minimax", "abab6.5s-chat", True, "standard", 0.00000045, 0.0000045),
    ("minimax", "abab5.5-chat", True, "fast", 0.0, 0.0),
    # --- Qwen ---
    ("qwen", "qwen-turbo", True, "fast", 0.00000005, 0.0000002),
    ("qwen", "qwen-plus", True, "standard", 0.00000056, 0.0000016),
    ("qwen", "qwen-max", True, "slow", 0.0000024, 0.0000096),
    # --- Kimi / Moonshot ---
    ("kimi", "moonshot-v1-8k", True, "fast", 0.0000012, 0.0000012),
    ("kimi", "moonshot-v1-32k", True, "standard", 0.0000024, 0.0000024),
    ("kimi", "moonshot-v1-128k", True, "slow", 0.0000060, 0.0000060),
    # --- Z.AI / Yi ---
    ("zai", "yi-large", True, "standard", 0.0, 0.0),
    ("zai", "yi-medium", True, "fast", 0.0, 0.0),
    # --- OpenRouter ---
    ("openrouter", "openrouter/auto", True, "standard", 0.0, 0.0),
    ("openrouter", "anthropic/claude-3.5-sonnet", True, "standard", 0.0, 0.0),
    ("openrouter", "openai/gpt-4o", True, "standard", 0.0, 0.0),
    ("openrouter", "meta-llama/llama-3.1-405b", True, "slow", 0.0, 0.0),
    ("openrouter", "google/gemini-pro-1.5", True, "standard", 0.0, 0.0),
    # --- Google ---
    ("google", "gemini-2.0-flash-exp", True, "fast", 0.0, 0.0),
    ("google", "gemini-1.5-pro", True, "standard", 0.0000035, 0.0000105),
    ("google", "gemini-1.5-flash", True, "fast", 0.000000075, 0.0000003),
    ("google", "gemini-1.5-flash-8b", True, "fast", 0.0, 0.0),
    # --- Groq ---
    ("groq", "llama-3.3-70b-versatile", True, "fast", 0.0, 0.0),
    ("groq", "llama-3.1-8b-instant", True, "fast", 0.00000005, 0.00000008),
    ("groq", "mixtral-8x7b-32768", True, "fast", 0.0, 0.0),
    ("groq", "gemma2-9b-it", True, "fast", 0.0, 0.0),
    ("groq", "deepseek-r1-distill-llama-70b", True, "fast", 0.0, 0.0),
    ("groq", "qwen-2.5-32b", True, "fast", 0.0, 0.0),
    # --- DeepSeek ---
    ("deepseek", "deepseek-chat", True, "standard", 0.00000027, 0.0000011),
    ("deepseek", "deepseek-coder", True, "standard", 0.0, 0.0),
    ("deepseek", "deepseek-reasoner", True, "slow", 0.00000055, 0.00000219),
    # --- xAI ---
    ("xai", "grok-2-latest", True, "standard", 0.0, 0.0),
    ("xai", "grok-2-mini", True, "fast", 0.0, 0.0),
    # --- Mistral ---
    ("mistral", "mistral-large-latest", False, "standard", 0.000003, 0.000009),
    ("mistral", "mistral-small-latest", False, "fast", 0.000001, 0.000003),
    ("mistral", "codestral-latest", False, "standard", 0.0, 0.0),
    ("mistral", "mistral-embed", False, "fast", 0.0, 0.0),
    # --- Cohere ---
    ("cohere", "command-r-plus-08-2024", False, "standard", 0.0000025, 0.00001),
    ("cohere", "command-r-08-2024", False, "standard", 0.00000015, 0.0000006),
    ("cohere", "command", False, "standard", 0.0, 0.0),
    ("cohere", "command-light", False, "fast", 0.0, 0.0),
    # --- Perplexity ---
    ("perplexity", "llama-3.1-sonar-large-128k-online", False, "standard", 0.000001, 0.000001),
    ("perplexity", "llama-3.1-sonar-small-128k-online", False, "fast", 0.0, 0.0),
    ("perplexity", "llama-3.1-sonar-huge-128k-online", False, "slow", 0.0, 0.0),
]


def _build_rows() -> list[dict]:
    """Expand the tuple list into full dicts for ``bulk_insert``."""
    now = datetime.now(timezone.utc)
    return [
        {
            "provider": provider,
            "model": model,
            "supports_streaming": supports_streaming,
            "supports_tools": False,
            "supports_vision": False,
            "max_context_tokens": 128000,
            "cost_tier": _derive_cost_tier(c_in),
            "latency_tier": latency_tier,
            "cost_per_input_token": c_in,
            "cost_per_output_token": c_out,
            "source": "seed",
            "is_manual_override": False,
            "is_active": True,
            "last_synced_at": None,
            "created_at": now,
            "updated_at": now,
        }
        for (provider, model, supports_streaming, latency_tier, c_in, c_out)
        in _SEED_ROWS_RAW
    ]


def upgrade() -> None:
    # Reflect the table created by 036 so bulk_insert binds correctly.
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = sa.Table("model_configs", metadata, autoload_with=bind)
    op.bulk_insert(table, _build_rows())


def downgrade() -> None:
    # Remove only the seed rows. Anything inserted by the discovery
    # sync (source='auto') and admin overrides ('manual') stays —
    # downgrading the seed should not destroy live registry data.
    op.execute("DELETE FROM model_configs WHERE source = 'seed'")

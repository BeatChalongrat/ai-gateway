"""Add current-generation chat models missing from the catalog.

068/069 fixed pricing for every row that existed; this adds the
models the owner's 2026-08 price map covered that had NO catalog row
at all (verified 0/21 present on 2026-08-12). INSERT-only:

  * never touches existing rows (``ON CONFLICT DO NOTHING``),
  * never touches media / sranai / the 068+069 retired rows,
  * all inserts are active — they appear in /v1/models and the picker
    immediately, and actually serve once the provider has a key.

Prices are per-1M USD from the owner's map ÷ 1e6 (column stores USD
per token; anchor o3-mini = 1.1e-06). ``cost_tier`` derived with the
``cost_tiers.derive_cost_tier`` thresholds (<=5e-07 budget,
<=3e-06 standard, else premium).

Capabilities / context / latency are MIRRORED from an existing row of
the same provider (claude-sonnet-4.5 & opus-4 for anthropic, gpt-5.5 /
gpt-5-nano for openai, gemini-2.5-* for google, grok-2-latest for xai,
mistral-large-latest for mistral, deepseek-chat for deepseek,
moonshot-v1-128k for kimi, abab5.5-chat for minimax,
command-r-plus-08-2024 for cohere, qwen-max for qwen,
llama-3.3-70b-versatile for groq, llama-3.1-sonar-large for
perplexity). Notable inherited quirks, intentionally kept for
consistency until discovery refreshes them: mistral/cohere/perplexity
rows carry supports_streaming=false; kimi rows carry latency "slow";
cohere command-a gets the template's 128k context (vendor claims
256k). No new provider names are introduced, so every insert rides an
already-registered adapter (cloud adapters pass the model id through
verbatim — a DB row is all that's needed).

⚠ UNVERIFIED prices (seeded per owner's map, pending console check):
gpt-5.6-terra, gpt-5.6-luna, MiniMax-M3, qwen3.7-max.

Downgrade deletes ONLY the ids listed here (guarded on
source='manual' so a discovery-owned row with the same id is never
deleted). Caveat: if an id already existed at upgrade time (insert
skipped by ON CONFLICT), downgrade would still delete it — audited as
non-existent on 2026-08-12, same pattern as 067.

Revision ID: 070
Revises: 069
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op


revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


# (provider, model, cin, cout, tier, streaming, tools, vision, ctx,
#  latency, reasoning, reasoning_strategy)
_NEW_MODELS: list[tuple] = [
    # anthropic — mirror claude-sonnet-4.5 / opus-4 (t/t/t, 200k, adaptive thinking)
    ("anthropic", "claude-opus-4-8", 5e-06, 2.5e-05, "premium",
     True, True, True, 200000, "slow", True, "anthropic_adaptive_thinking"),
    ("anthropic", "claude-sonnet-5", 3e-06, 1.5e-05, "standard",
     True, True, True, 200000, "standard", True, "anthropic_adaptive_thinking"),
    ("anthropic", "claude-haiku-4-5", 1e-06, 5e-06, "standard",
     True, True, True, 200000, "fast", True, "anthropic_adaptive_thinking"),
    # openai — mirror gpt-5.5 / gpt-5-nano (t/f/f, 128k, standard)
    ("openai", "gpt-5.6-sol", 5e-06, 3e-05, "premium",
     True, False, False, 128000, "standard", False, "none"),
    ("openai", "gpt-5.6-terra", 2e-06, 1.2e-05, "standard",  # ⚠ unverified
     True, False, False, 128000, "standard", False, "none"),
    ("openai", "gpt-5.6-luna", 2e-07, 1.2e-06, "budget",  # ⚠ unverified
     True, False, False, 128000, "standard", False, "none"),
    # google — mirror gemini-2.5-flash (t/t/t, 1M, thinking level)
    ("google", "gemini-3.6-flash", 1.5e-06, 7.5e-06, "standard",
     True, True, True, 1000000, "fast", True, "gemini_thinking_level"),
    # xai — mirror grok-2-latest (t/f/f, 131072)
    ("xai", "grok-4.5", 2e-06, 6e-06, "standard",
     True, False, False, 131072, "standard", False, "none"),
    # mistral — mirror mistral-large-latest (streaming=false per template)
    ("mistral", "mistral-medium-latest", 1.5e-06, 7.5e-06, "standard",
     False, False, False, 128000, "standard", False, "none"),
    # deepseek — mirror deepseek-chat (t/f/f, 128k)
    ("deepseek", "deepseek-v4-flash", 1.4e-07, 2.8e-07, "budget",
     True, False, False, 128000, "standard", False, "none"),
    ("deepseek", "deepseek-v4-pro", 1.74e-06, 3.48e-06, "standard",
     True, False, False, 128000, "standard", False, "none"),
    # kimi — mirror moonshot-v1-128k (t/f/f, 128k, slow)
    ("kimi", "kimi-k3", 3e-06, 1.5e-05, "standard",
     True, False, False, 128000, "slow", False, "none"),
    ("kimi", "kimi-k2", 6e-07, 2.5e-06, "standard",
     True, False, False, 128000, "slow", False, "none"),
    # minimax — mirror abab5.5-chat (t/f/f, 128k, fast)
    ("minimax", "MiniMax-M3", 3e-07, 1.2e-06, "budget",  # ⚠ unverified
     True, False, False, 128000, "fast", False, "none"),
    # cohere — mirror command-r-plus-08-2024 (streaming=false per template)
    ("cohere", "command-a", 2.5e-06, 1e-05, "standard",
     False, False, False, 128000, "standard", False, "none"),
    # qwen — mirror qwen-max (t/t/f, 128k, slow)
    ("qwen", "qwen3.7-max", 2e-06, 6e-06, "standard",  # ⚠ unverified
     True, True, False, 128000, "slow", False, "none"),
    # groq — mirror llama-3.3-70b-versatile (t/f/f, 128k, fast)
    ("groq", "llama-4-scout", 1.1e-07, 3.4e-07, "budget",
     True, False, False, 128000, "fast", False, "none"),
    ("groq", "gpt-oss-120b", 1.5e-07, 6e-07, "budget",
     True, False, False, 128000, "fast", False, "none"),
    ("groq", "gpt-oss-20b", 7.5e-08, 3e-07, "budget",
     True, False, False, 128000, "fast", False, "none"),
    # perplexity — mirror llama-3.1-sonar-large (streaming=false per template)
    ("perplexity", "sonar", 1e-06, 1e-06, "standard",
     False, False, False, 128000, "standard", False, "none"),
    ("perplexity", "sonar-pro", 3e-06, 1.5e-05, "standard",
     False, False, False, 128000, "standard", False, "none"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for (provider, model, cin, cout, tier, streaming, tools, vision,
         ctx, latency, reasoning, strategy) in _NEW_MODELS:
        conn.execute(
            sa.text(
                """
                INSERT INTO model_configs (
                    provider, model,
                    supports_streaming, supports_tools, supports_vision,
                    max_context_tokens, cost_tier, latency_tier,
                    cost_per_input_token, cost_per_output_token,
                    source, is_manual_override, is_active,
                    supports_reasoning, reasoning_strategy,
                    default_reasoning_mode,
                    supports_video_gen, supports_audio_gen, supports_tts
                ) VALUES (
                    :provider, :model,
                    :streaming, :tools, :vision,
                    :ctx, :tier, :latency,
                    :cin, :cout,
                    'manual', true, true,
                    :reasoning, :strategy, 'auto',
                    false, false, false
                )
                ON CONFLICT (provider, model) DO NOTHING
                """
            ),
            {"provider": provider, "model": model,
             "streaming": streaming, "tools": tools, "vision": vision,
             "ctx": ctx, "tier": tier, "latency": latency,
             "cin": cin, "cout": cout,
             "reasoning": reasoning, "strategy": strategy},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for row in _NEW_MODELS:
        conn.execute(
            sa.text(
                """
                DELETE FROM model_configs
                 WHERE provider = :provider AND model = :model
                   AND source = 'manual'
                """
            ),
            {"provider": row[0], "model": row[1]},
        )

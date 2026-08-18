using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Models;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Models-catalog contract tests — the additive-only rule, enforced.
/// The /api/v1/models contract may only ever GROW. An installed desktop
/// build will receive newer server responses for months, so unknown
/// fields, unknown capability strings, and unknown reasoning sub-fields
/// must parse and degrade to "feature invisible", never to an error.
/// </summary>
public class ModelContractTests
{
    // A server response from THE FUTURE: unknown top-level field, unknown
    // per-model fields, unknown capability strings, unknown reasoning
    // sub-field — what an old installed client will one day receive.
    private const string FutureResponse = """
    {
      "models": [
        {
          "id": "gpt-6-preview",
          "name": "GPT-6 Preview",
          "provider": "openai",
          "description": "A model from the future",
          "context_window": 400000,
          "capabilities": ["streaming", "function_calling", "quantum_entanglement", "vision"],
          "category": "flagship",
          "input_price_per_1m": 1.25,
          "output_price_per_1m": 5.0,
          "reasoning": {
            "supported": true,
            "modes": ["off", "auto", "galaxy_brain"],
            "default_mode": "auto",
            "summary_supported": true,
            "hologram_supported": true
          },
          "model_kind": "chat",
          "carbon_footprint_grams": 12.5
        },
        {
          "id": "plain-model",
          "name": "Plain Model",
          "provider": "openai",
          "description": "No reasoning block at all",
          "context_window": 8000,
          "capabilities": ["streaming"]
        }
      ],
      "catalog_generation": 42
    }
    """;

    private static ModelListResponse Parse() =>
        JsonSerializer.Deserialize<ModelListResponse>(FutureResponse)!;

    [Fact]
    public void Future_Response_With_Unknown_Fields_Parses()
    {
        var parsed = Parse();
        parsed.Models.Should().HaveCount(2);
        parsed.Models[0].Id.Should().Be("gpt-6-preview");
        parsed.Models[1].Id.Should().Be("plain-model");
    }

    [Fact]
    public void Unknown_Capability_Strings_Are_Ignored_Known_Ones_Still_Work()
    {
        var future = Parse().Models[0];
        // "quantum_entanglement" is silently carried, not fatal; the
        // known "vision" string still drives its derived capability.
        future.SupportsVision.Should().BeTrue();
        future.SupportsImage.Should().BeFalse();
        future.SupportsVideo.Should().BeFalse();
    }

    [Fact]
    public void Reasoning_Block_With_Unknown_SubFields_Still_Reports_Thinking()
    {
        var future = Parse().Models[0];
        future.SupportsThinking.Should().BeTrue();
        future.Reasoning!.Supported.Should().BeTrue();
    }

    [Fact]
    public void Missing_Reasoning_Block_Means_Not_Supported_Not_An_Error()
    {
        var plain = Parse().Models[1];
        plain.Reasoning.Should().BeNull();
        plain.SupportsThinking.Should().BeFalse();
    }
}

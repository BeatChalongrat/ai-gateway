using FluentAssertions;
using SranDesktop.Application.Models;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>UX polish #3: a fresh install defaults the model picker to
/// "auto" (the gateway's per-request resolver). Users with an existing
/// settings.json keep whatever they persisted — the default only ever
/// applies when the property was never written.</summary>
public class DefaultModelAutoTests
{
    [Fact]
    public void Fresh_Settings_Default_To_Auto()
    {
        new UserSettings().DefaultModel.Should().Be("auto");
    }

    [Fact]
    public void Persisted_Choice_Survives_Clone()
    {
        // Clone() is the write path (Settings dialog edits a clone, then
        // Save persists it) — an explicit choice must round-trip.
        var s = new UserSettings { DefaultModel = "gpt-4o-mini" };
        s.Clone().DefaultModel.Should().Be("gpt-4o-mini");
    }

    [Fact]
    public void Persisted_Json_Choice_Is_Not_Overridden_By_The_New_Default()
    {
        // An existing settings.json from before the default flip carries a
        // concrete model — deserializing it must keep that value.
        var json = """{"default_model":"gpt-4o-mini"}""";
        var restored = System.Text.Json.JsonSerializer.Deserialize<UserSettings>(json)!;
        restored.DefaultModel.Should().Be("gpt-4o-mini");
    }
}

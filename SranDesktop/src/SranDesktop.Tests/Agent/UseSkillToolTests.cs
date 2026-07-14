using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class UseSkillToolTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), $"useskill-{Guid.NewGuid():N}");
    private readonly SkillManager _manager;
    private readonly UseSkillTool _tool;

    public UseSkillToolTests()
    {
        _manager = new SkillManager(_root, NullLogger<SkillManager>.Instance);
        _tool = new UseSkillTool(_manager);
    }

    private static JsonElement Args(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    [Fact]
    public async Task Returns_Instructions_For_Enabled_Skill()
    {
        var result = await _tool.ExecuteAsync(Args("""{"name":"xlsx"}"""), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("Excel"); // from the built-in SKILL.md
        result.Content.Should().Contain("excel-plan");
    }

    [Fact]
    public async Task Is_Case_Insensitive()
    {
        var result = await _tool.ExecuteAsync(Args("""{"name":"XLSX"}"""), CancellationToken.None);
        result.Success.Should().BeTrue();
    }

    [Fact]
    public async Task Unknown_Skill_Errors_With_Available_List()
    {
        var result = await _tool.ExecuteAsync(Args("""{"name":"nope"}"""), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("No skill named 'nope'");
        result.Error.Should().Contain("xlsx"); // lists available skills
    }

    [Fact]
    public async Task Disabled_Skill_Errors()
    {
        var skills = await _manager.GetSkillsAsync();
        var xlsx = skills.First(s => s.Manifest.Name == "xlsx");
        await _manager.SetEnabledAsync(xlsx, false);

        var result = await _tool.ExecuteAsync(Args("""{"name":"xlsx"}"""), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("disabled");
    }

    [Fact]
    public async Task Missing_Name_Errors()
    {
        var result = await _tool.ExecuteAsync(Args("{}"), CancellationToken.None);
        result.Success.Should().BeFalse();
    }

    [Fact]
    public void Is_Side_Effect_Free_AllowAlways()
    {
        // The tool only reads bundled instructions — AllowAlways, no consent.
        _tool.DefaultPermission.Should().Be(SranDesktop.Domain.Enums.PermissionLevel.AllowAlways);
    }

    public void Dispose()
    {
        try { Directory.Delete(_root, recursive: true); } catch (IOException) { }
    }
}

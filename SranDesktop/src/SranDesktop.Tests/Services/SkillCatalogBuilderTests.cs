using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

public class SkillCatalogBuilderTests
{
    private static Skill Skill(string name, string description, bool enabled = true,
        List<string>? triggers = null) => new()
    {
        Manifest = new SkillManifest
        {
            Name = name,
            DisplayName = name,
            Description = description,
            Triggers = triggers ?? new List<string>()
        },
        Instructions = "full instructions",
        Path = "/x",
        IsEnabled = enabled
    };

    [Fact]
    public void Empty_List_Returns_Null()
    {
        SkillCatalogBuilder.Build(Array.Empty<Skill>(), Array.Empty<string>()).Should().BeNull();
    }

    [Fact]
    public void All_Disabled_Returns_Null()
    {
        var skills = new[] { Skill("xlsx", "Excel builder", enabled: false) };
        SkillCatalogBuilder.Build(skills, Array.Empty<string>()).Should().BeNull();
    }

    [Fact]
    public void Enabled_Skills_Each_Get_A_Line()
    {
        var skills = new[]
        {
            Skill("xlsx", "Excel workbooks"),
            Skill("pdf", "PDF reports"),
        };

        var catalog = SkillCatalogBuilder.Build(skills, Array.Empty<string>())!;

        catalog.Should().Contain("- xlsx: Excel workbooks");
        catalog.Should().Contain("- pdf: PDF reports");
        catalog.Should().Contain("use_skill"); // instruction present
    }

    [Fact]
    public void Already_Matched_Skills_Are_Marked_Loaded()
    {
        var skills = new[] { Skill("xlsx", "Excel workbooks"), Skill("pdf", "PDF reports") };

        var catalog = SkillCatalogBuilder.Build(skills, new[] { "xlsx" })!;

        catalog.Should().Contain("- xlsx (loaded): Excel workbooks");
        catalog.Should().Contain("- pdf: PDF reports");
        catalog.Should().NotContain("- pdf (loaded)");
    }

    [Fact]
    public void Long_Description_Is_Capped()
    {
        var longDesc = new string('x', 300);
        var catalog = SkillCatalogBuilder.Build(new[] { Skill("s", longDesc) }, Array.Empty<string>())!;

        var line = catalog.Split('\n').First(l => l.StartsWith("- s:"));
        line.Length.Should().BeLessThan(140); // "- s: " + 120 cap + "…"
        line.Should().EndWith("…");
    }

    [Fact]
    public void Blank_Description_Falls_Back_To_Triggers()
    {
        var skill = Skill("web-search", "", triggers: new List<string> { "search the web", "look up" });

        var catalog = SkillCatalogBuilder.Build(new[] { skill }, Array.Empty<string>())!;

        catalog.Should().Contain("- web-search: search the web, look up");
    }
}

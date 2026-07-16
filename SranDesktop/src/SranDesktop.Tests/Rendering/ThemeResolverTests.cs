using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Services;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>Phase C PART 5: theme-setting resolution incl. System/Auto (the
/// live re-apply + registry read are visual/OS checks).</summary>
public class ThemeResolverTests
{
    [Theory]
    [InlineData("Dark", true, "Dark")]
    [InlineData("Dark", false, "Dark")]
    [InlineData("Light", true, "Light")]
    [InlineData("Light", false, "Light")]
    [InlineData("System", true, "Dark")]   // follows the OS
    [InlineData("System", false, "Light")]
    [InlineData("Auto", true, "Dark")]      // alias
    [InlineData("Auto", false, "Light")]
    [InlineData(null, true, "Dark")]        // unknown → dark default
    public void Resolve_Maps_Setting_And_Os(string? setting, bool osIsDark, string expected)
    {
        ThemeResolver.Resolve(setting, osIsDark).Should().Be(expected);
    }

    [Theory]
    [InlineData("System", true)]
    [InlineData("Auto", true)]
    [InlineData("Dark", false)]
    [InlineData("Light", false)]
    public void IsSystem_Detects_The_Follow_Os_Modes(string setting, bool expected)
    {
        ThemeResolver.IsSystem(setting).Should().Be(expected);
    }

    [Fact]
    public void Theme_Setting_Survives_A_Settings_Clone()
    {
        var settings = new UserSettings { Theme = "System" };
        settings.Clone().Theme.Should().Be("System");
    }
}

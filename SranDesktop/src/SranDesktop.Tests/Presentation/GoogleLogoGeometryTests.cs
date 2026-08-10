using System.IO;
using System.Text.RegularExpressions;
using System.Windows.Media;
using FluentAssertions;
using Xunit;

namespace SranDesktop.Tests.Presentation;

/// <summary>UX polish #1: the Google "G" on the login button is embedded
/// as raw WPF Path Data. XAML geometry strings are only parsed at RUNTIME
/// (BAML keeps them as text), so a bad arc-flag form would crash LoginView
/// on load, not the build. Parse the actual attributes from the XAML here
/// so the guard follows the source.</summary>
public class GoogleLogoGeometryTests
{
    private static string LoginViewXamlPath()
    {
        // Tests run from src/SranDesktop.Tests/bin/Release/net8.0-windows.
        var root = Path.GetFullPath(Path.Combine(
            System.AppContext.BaseDirectory, "..", "..", "..", ".."));
        return Path.Combine(
            root, "SranDesktop.Presentation", "Views", "LoginView.xaml");
    }

    [Fact]
    public void Google_Logo_Path_Data_Parses_As_Wpf_Geometry()
    {
        var xaml = File.ReadAllText(LoginViewXamlPath());
        var matches = Regex.Matches(
            xaml, "Fill=\"#(?:4285F4|34A853|FBBC05|EA4335)\"\\s+Data=\"([^\"]+)\"");

        matches.Should().HaveCount(4, "the button embeds the 4-color official mark");
        foreach (Match m in matches)
        {
            var data = m.Groups[1].Value;
            var act = () => Geometry.Parse(data);
            act.Should().NotThrow($"path data must be valid WPF geometry: {data}");
            Geometry.Parse(data).IsEmpty().Should().BeFalse();
        }
    }
}

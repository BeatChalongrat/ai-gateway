using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class ScreenshotScopeTests
{
    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("target_window")]
    public void Default_Scope_Is_Target_Window(string? value)
    {
        ComputerScreenshotTool.ParseCaptureScope(value)
            .Should().Be(CaptureType.Window);
    }

    [Fact]
    public void Desktop_Overview_Is_Explicit()
    {
        ComputerScreenshotTool.ParseCaptureScope("desktop_overview")
            .Should().Be(CaptureType.DesktopOverview);
    }

    [Fact]
    public void Monitor_Capture_Is_Explicit()
    {
        ComputerScreenshotTool.ParseCaptureScope("monitor")
            .Should().Be(CaptureType.Monitor);
    }
}

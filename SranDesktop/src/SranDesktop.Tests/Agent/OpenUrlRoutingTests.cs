using System.Text.Json;
using FluentAssertions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Routing fix: while the Chrome extension is connected, open_url must
/// open a NEW CHROME TAB through the bridge — never the OS default browser
/// (which may be Edge and is invisible to the browser_* tools). Fallback to the
/// default browser only when the bridge is down, and say so.</summary>
public sealed class OpenUrlRoutingTests
{
    private sealed class RecordingLauncher : IBrowserLauncher
    {
        public readonly List<string> Opened = new();
        public bool Open(string url)
        {
            Opened.Add(url);
            return true;
        }
    }

    private static JsonElement Args(string url) =>
        JsonSerializer.SerializeToElement(new { url });

    [Fact]
    public async Task Connected_Bridge_Opens_A_Chrome_Tab_Not_The_Default_Browser()
    {
        var bridge = new Mock<IBrowserBridgeService>();
        bridge.SetupGet(b => b.IsConnected).Returns(true);
        bridge.Setup(b => b.RequestAsync("tabs.new", It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ReturnsAsync(JsonSerializer.SerializeToElement(
                new { tab_id = 7, url = "https://youtube.com/", title = "YouTube" }));
        var launcher = new RecordingLauncher();

        var result = await new OpenUrlTool(bridge.Object, launcher).ExecuteAsync(
            Args("https://youtube.com/"), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("Chrome tab");
        launcher.Opened.Should().BeEmpty("the default browser must NOT be used while paired");
        bridge.Verify(b => b.RequestAsync("tabs.new", It.IsAny<object?>(),
            It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()), Times.Once);
    }

    [Fact]
    public async Task Disconnected_Bridge_Falls_Back_To_Default_Browser_And_Says_So()
    {
        var bridge = new Mock<IBrowserBridgeService>();
        bridge.SetupGet(b => b.IsConnected).Returns(false);
        var launcher = new RecordingLauncher();

        var result = await new OpenUrlTool(bridge.Object, launcher).ExecuteAsync(
            Args("https://example.com/"), CancellationToken.None);

        result.Success.Should().BeTrue();
        launcher.Opened.Should().ContainSingle().Which.Should().Be("https://example.com/");
        result.Content.Should().Contain("DEFAULT browser");
        result.Content.Should().Contain("cannot see", "the model must know it is blind here");
        bridge.Verify(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
            It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()), Times.Never);
    }

    [Fact]
    public async Task Bridge_Dropping_MidCall_Falls_Back_Gracefully()
    {
        var bridge = new Mock<IBrowserBridgeService>();
        bridge.SetupGet(b => b.IsConnected).Returns(true);
        bridge.Setup(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserNotConnectedException());
        var launcher = new RecordingLauncher();

        var result = await new OpenUrlTool(bridge.Object, launcher).ExecuteAsync(
            Args("https://example.com/"), CancellationToken.None);

        result.Success.Should().BeTrue();
        launcher.Opened.Should().ContainSingle();
        result.Content.Should().Contain("DEFAULT browser");
    }

    [Fact]
    public async Task Extension_Errors_Surface_Instead_Of_Silently_Escaping_To_Edge()
    {
        var bridge = new Mock<IBrowserBridgeService>();
        bridge.SetupGet(b => b.IsConnected).Returns(true);
        bridge.Setup(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserBridgeException("Cannot access this page"));
        var launcher = new RecordingLauncher();

        var result = await new OpenUrlTool(bridge.Object, launcher).ExecuteAsync(
            Args("https://example.com/"), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("Cannot access this page");
        launcher.Opened.Should().BeEmpty(
            "a real extension error must not quietly reroute to the default browser");
    }
}

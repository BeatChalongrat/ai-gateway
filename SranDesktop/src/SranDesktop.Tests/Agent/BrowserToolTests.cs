using System.Text.Json;
using FluentAssertions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>browser_* tools against a mocked bridge: happy paths, the soft
/// not-connected message, stale-ref passthrough, and the credential guard.</summary>
public class BrowserToolTests
{
    private readonly Mock<IBrowserBridgeService> _bridge = new();

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    private void SetupResult(string method, object result) =>
        _bridge.Setup(b => b.RequestAsync(method, It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ReturnsAsync(JsonSerializer.SerializeToElement(result));

    [Fact]
    public async Task Disconnected_Bridge_Yields_The_Install_Guidance()
    {
        _bridge.Setup(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserNotConnectedException());

        var result = await new BrowserReadPageTool(_bridge.Object)
            .ExecuteAsync(Args(new { }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("Settings → Browser");
    }

    [Fact]
    public async Task ListTabs_Formats_The_Indexed_Tab_Lines()
    {
        SetupResult("tabs.list", new
        {
            tabs = new[]
            {
                new { id = 3, title = "Example Domain", url = "https://example.com/", active = true },
                new { id = 7, title = "Docs", url = "https://docs.example.com/", active = false }
            }
        });

        var result = await new BrowserListTabsTool(_bridge.Object)
            .ExecuteAsync(Args(new { }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("[3] Example Domain — https://example.com/ (active)");
        result.Content.Should().Contain("[7] Docs — https://docs.example.com/");
    }

    [Fact]
    public async Task ReadPage_Passes_The_Element_Listing_Through()
    {
        SetupResult("page.read", new { text = "[0] <a> \"More information\" (enabled)" });

        var result = await new BrowserReadPageTool(_bridge.Object)
            .ExecuteAsync(Args(new { filter = "interactive" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Be("[0] <a> \"More information\" (enabled)");
    }

    [Fact]
    public async Task Click_Passes_A_Stale_Ref_Error_Through()
    {
        _bridge.Setup(b => b.RequestAsync("page.click", It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserBridgeException("refs are stale — call browser_read_page again"));

        var result = await new BrowserClickTool(_bridge.Object)
            .ExecuteAsync(Args(new { @ref = 12 }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("refs are stale");
    }

    [Fact]
    public async Task Click_Reports_Navigation_With_The_New_Page()
    {
        SetupResult("page.click", new { clicked = true, navigated = true, url = "https://www.iana.org/help/example-domains", title = "Example Domains" });

        var result = await new BrowserClickTool(_bridge.Object)
            .ExecuteAsync(Args(new { @ref = 0 }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("Example Domains");
        result.Content.Should().Contain("stale");
    }

    [Fact]
    public async Task Navigate_Surfaces_The_Real_Tab_Id_For_FollowUp_Calls()
    {
        // Chrome tab ids are arbitrary numbers; the model used to invent
        // "tab_id: 1" because navigate never told it the real one.
        SetupResult("page.navigate", new { tab_id = 1387, url = "https://youtube.com/", title = "YouTube" });

        var result = await new BrowserNavigateTool(_bridge.Object)
            .ExecuteAsync(Args(new { url = "https://youtube.com" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("tab 1387");
        result.Content.Should().Contain("tab_id 1387");
        result.Content.Should().Contain("YouTube");
    }

    [Fact]
    public async Task Navigate_Still_Works_When_An_Old_Extension_Omits_Tab_Id()
    {
        // A not-yet-reloaded extension returns only {url, title}.
        SetupResult("page.navigate", new { url = "https://example.com/", title = "Example" });

        var result = await new BrowserNavigateTool(_bridge.Object)
            .ExecuteAsync(Args(new { url = "https://example.com" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("the current tab");
        result.Content.Should().Contain("https://example.com/");
    }

    [Fact]
    public async Task Navigate_Rejects_Bad_Schemes_Without_Calling_The_Bridge()
    {
        var result = await new BrowserNavigateTool(_bridge.Object)
            .ExecuteAsync(Args(new { url = "javascript:alert(1)" }), CancellationToken.None);

        result.Success.Should().BeFalse();
        _bridge.Verify(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
            It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()), Times.Never);
    }

    [Fact]
    public async Task Type_Refuses_Credential_Text_Without_Calling_The_Bridge()
    {
        var result = await new BrowserTypeTool(_bridge.Object)
            .ExecuteAsync(Args(new { @ref = 4, text = "pass : hunter2" }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("prohibited");
        _bridge.Verify(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
            It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()), Times.Never);
    }

    [Fact]
    public async Task Screenshot_Returns_The_Image_Data_Uri()
    {
        SetupResult("page.screenshot", new { image = "data:image/jpeg;base64,abc123", title = "Example" });

        var result = await new BrowserScreenshotTool(_bridge.Object)
            .ExecuteAsync(Args(new { }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.ImageDataUri.Should().Be("data:image/jpeg;base64,abc123");
    }

    [Fact]
    public async Task Tabs_Tool_Validates_Its_Action_Arguments()
    {
        (await new BrowserTabsTool(_bridge.Object)
                .ExecuteAsync(Args(new { action = "close" }), CancellationToken.None))
            .Success.Should().BeFalse("close needs tab_id");
        (await new BrowserTabsTool(_bridge.Object)
                .ExecuteAsync(Args(new { action = "new", url = "notaurl" }), CancellationToken.None))
            .Success.Should().BeFalse("new needs a valid url");
        (await new BrowserTabsTool(_bridge.Object)
                .ExecuteAsync(Args(new { action = "explode" }), CancellationToken.None))
            .Success.Should().BeFalse("unknown action");
    }
}

/// <summary>Part C: the shared credential guard — extended labels catch what
/// the old password|passwd|pwd list missed, without new false positives.</summary>
public class CredentialTextGuardTests
{
    [Theory]
    [InlineData("pass : hunter2")]            // the gap that let ssh.txt content through
    [InlineData("secret=abc123")]
    [InlineData("token: xyz789")]
    [InlineData("api key = sk_val")]
    [InlineData("api-key: value")]
    [InlineData("apikey: value")]             // api[ _-]?key collapses to apikey too
    [InlineData("otp: 123456")]
    [InlineData("PIN = 9876")]
    [InlineData("password: hunter2")]
    [InlineData("pwd = s3cret!")]
    public void Extended_Labels_Are_Blocked(string text)
    {
        CredentialTextGuard.LooksLikeCredential(text, out _).Should().BeTrue(text);
    }

    [Theory]
    [InlineData("a passage from the book")]
    [InlineData("tokenize the input stream")]
    [InlineData("compass = north")]           // \bpass\b must not match inside compass
    [InlineData("the pinned message")]
    [InlineData("passport renewal form")]
    [InlineData("The password policy requires rotation")] // label without a value
    [InlineData("secrets management is a good practice")]
    [InlineData("spin = 0.5")]
    public void Innocent_Words_Are_Not_Blocked(string text)
    {
        CredentialTextGuard.LooksLikeCredential(text, out _).Should().BeFalse(text);
    }
}

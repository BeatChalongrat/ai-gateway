using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Security;

/// <summary>
/// The dedicated suite that earns <c>open_url</c> its shell launch (P3-5).
///
/// <para>Two properties are pinned. First, the scheme allowlist: only http and
/// https reach the launcher, because the shell would treat other schemes as
/// "run the local handler for this" rather than "show this page". Second, and
/// more important, honesty: the tool asks the browser to open a URL and cannot
/// see the result, so it must report a dispatch and never a verified page
/// state. A tool that claims verification it did not perform is worse than a
/// tool that does not work, because everything downstream believes it.</para>
/// </summary>
public sealed class OpenUrlToolSecurityTests
{
    /// <summary>Records what it was asked to open without touching a real browser.</summary>
    private sealed class RecordingLauncher : IBrowserLauncher
    {
        private readonly bool _result;
        public RecordingLauncher(bool result = true) => _result = result;
        public List<string> Opened { get; } = new();
        public bool Open(string url)
        {
            Opened.Add(url);
            return _result;
        }
    }

    private static JsonElement Args(string url) =>
        JsonSerializer.Deserialize<JsonElement>(
            JsonSerializer.Serialize(new { url }));

    [Theory]
    [InlineData("file:///C:/Windows/System32/calc.exe")]
    [InlineData("ms-settings:windowsupdate")]
    [InlineData("javascript:alert(1)")]
    [InlineData("ftp://example.com/payload")]
    [InlineData("C:\\Windows\\System32\\cmd.exe")]
    [InlineData("not a url at all")]
    [InlineData("")]
    public async Task Refuses_Every_Scheme_That_Is_Not_Http_Or_Https(string url)
    {
        var launcher = new RecordingLauncher();

        var result = await new OpenUrlTool(launcher).ExecuteAsync(
            Args(url), CancellationToken.None);

        result.Success.Should().BeFalse();
        launcher.Opened.Should().BeEmpty(
            "a refused URL must never reach the launcher — the shell decides what to "
            + "do with a scheme, and for anything but http(s) that means launching a "
            + "local program");
    }

    [Theory]
    [InlineData("http://example.com/")]
    [InlineData("https://example.com/docs?q=1")]
    public async Task Dispatches_Http_Urls_To_The_Launcher(string url)
    {
        var launcher = new RecordingLauncher();

        var result = await new OpenUrlTool(launcher).ExecuteAsync(
            Args(url), CancellationToken.None);

        result.Success.Should().BeTrue();
        launcher.Opened.Should().ContainSingle().Which.Should().Be(url);
    }

    /// <summary>
    /// The honesty invariant. <c>open_url</c> reports that it handed the URL over —
    /// it must not report a loaded page, because it never looked.
    /// </summary>
    [Fact]
    public async Task Reports_Dispatched_And_Never_Verified()
    {
        var result = await new OpenUrlTool(new RecordingLauncher()).ExecuteAsync(
            Args("https://example.com/"), CancellationToken.None);

        result.Success.Should().BeTrue("the dispatch itself did succeed");
        result.Effect.Should().NotBeNull(
            "a bare Ok would let the runtime treat native success as a verified effect");
        result.Effect!.Verified.Should().BeFalse(
            "the page state was never observed, so claiming verification would be a "
            + "false success");
        result.Effect.ExpectedEffectObserved.Should().BeFalse();
        result.Effect.FailureCode.Should().Be("NavigationNotObserved");
        result.Effect.Retryable.Should().BeFalse(
            "retrying cannot produce an observation this tool is incapable of making");
        result.Effect.Evidence.Should().Contain("dispatched_url=https://example.com/");
    }

    [Fact]
    public async Task Surfaces_A_Refused_Launch_As_A_Failed_Effect()
    {
        var result = await new OpenUrlTool(new RecordingLauncher(result: false))
            .ExecuteAsync(Args("https://example.com/"), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Effect!.FailureCode.Should().Be("BrowserLaunchFailed");
    }

    /// <summary>The launcher is handed the canonical form of the URL the scheme check
    /// approved, not the raw argument — otherwise the two could differ.</summary>
    [Fact]
    public async Task Passes_The_Canonicalized_Url_To_The_Launcher()
    {
        var launcher = new RecordingLauncher();

        await new OpenUrlTool(launcher).ExecuteAsync(
            Args("HTTPS://Example.COM/a/../b"), CancellationToken.None);

        launcher.Opened.Should().ContainSingle()
            .Which.Should().Be("https://example.com/b");
    }
}

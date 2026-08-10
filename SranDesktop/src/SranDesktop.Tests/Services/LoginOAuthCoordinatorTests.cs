using System.Net.Http;
using System.Text.RegularExpressions;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>SSO Phase 2 B2: Google sign-in hand-off — nonce-per-attempt,
/// system browser start, ticket deep link, redeem via the backend (the
/// redeem IS the re-confirm; the link itself is never trusted).</summary>
public class LoginOAuthCoordinatorTests
{
    private readonly Mock<IAuthService> _auth = new();
    private readonly Mock<IBrowserLauncher> _browser = new();
    private readonly Mock<ISettingsService> _settings = new();

    private const string Ticket = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    private static readonly string TicketLink = $"sran://oauth/callback?ticket={Ticket}";

    public LoginOAuthCoordinatorTests()
    {
        _settings.SetupGet(s => s.Current).Returns(new UserSettings
        {
            GatewayBaseUrl = "http://localhost:8080",
        });
    }

    private LoginOAuthCoordinator Create() => new(
        _auth.Object, _browser.Object, _settings.Object,
        NullLogger<LoginOAuthCoordinator>.Instance);

    private string StartAndCaptureNonce(LoginOAuthCoordinator sut)
    {
        string? url = null;
        _browser.Setup(b => b.Open(It.IsAny<string>()))
            .Callback<string>(u => url = u)
            .Returns(true);
        sut.StartSignIn().Should().BeTrue();
        var match = Regex.Match(url!, "[?&]nonce=([A-Za-z0-9_-]+)");
        match.Success.Should().BeTrue("start URL must carry the nonce");
        return match.Groups[1].Value;
    }

    // ── StartSignIn ─────────────────────────────────────────────────────────

    [Fact]
    public void StartSignIn_Opens_System_Browser_With_Desktop_Client_And_Nonce()
    {
        string? url = null;
        _browser.Setup(b => b.Open(It.IsAny<string>()))
            .Callback<string>(u => url = u)
            .Returns(true);
        var sut = Create();

        sut.StartSignIn().Should().BeTrue();

        url.Should().StartWith("http://localhost:8080/portal/auth/oauth/start?");
        url.Should().Contain("provider=google").And.Contain("client=desktop");
        Regex.IsMatch(url!, "[?&]nonce=[A-Za-z0-9_-]{22,128}(&|$)")
            .Should().BeTrue("nonce must satisfy the server's charset/length gate");
        sut.HasPendingAttempt.Should().BeTrue();
    }

    [Fact]
    public void StartSignIn_Mints_A_Fresh_Nonce_Per_Attempt()
    {
        var sut = Create();
        var first = StartAndCaptureNonce(sut);
        var second = StartAndCaptureNonce(sut);
        second.Should().NotBe(first);
    }

    [Fact]
    public void StartSignIn_Browser_Failure_Raises_Failed_And_Clears_Attempt()
    {
        _browser.Setup(b => b.Open(It.IsAny<string>())).Returns(false);
        var sut = Create();
        string? reason = null;
        sut.SignInFailed += (_, r) => reason = r;

        sut.StartSignIn().Should().BeFalse();

        reason.Should().Be("browser_failed");
        sut.HasPendingAttempt.Should().BeFalse();
    }

    // ── HandleDeepLink ──────────────────────────────────────────────────────

    [Fact]
    public async Task DeepLink_With_Pending_Attempt_Redeems_Ticket_With_Same_Nonce()
    {
        var sut = Create();
        var nonce = StartAndCaptureNonce(sut);
        var succeeded = new TaskCompletionSource<bool>();
        sut.SignInSucceeded += (_, _) => succeeded.TrySetResult(true);

        sut.HandleDeepLink(TicketLink);

        (await Task.WhenAny(succeeded.Task, Task.Delay(5000)))
            .Should().Be(succeeded.Task, "exchange should complete");
        _auth.Verify(
            a => a.SignInWithTicketAsync(Ticket, nonce, It.IsAny<CancellationToken>()),
            Times.Once);
        sut.HasPendingAttempt.Should().BeFalse("nonce is single-use");
    }

    [Fact]
    public void DeepLink_Without_Pending_Attempt_Is_Ignored()
    {
        var sut = Create();

        sut.HandleDeepLink(TicketLink);

        _auth.Verify(
            a => a.SignInWithTicketAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Never,
            "an unsolicited ticket is exactly the hijack shape the nonce kills");
    }

    [Fact]
    public async Task Second_DeepLink_For_Same_Attempt_Is_Ignored()
    {
        var sut = Create();
        StartAndCaptureNonce(sut);
        var succeeded = new TaskCompletionSource<bool>();
        sut.SignInSucceeded += (_, _) => succeeded.TrySetResult(true);

        sut.HandleDeepLink(TicketLink);
        sut.HandleDeepLink(TicketLink); // replay

        await Task.WhenAny(succeeded.Task, Task.Delay(5000));
        _auth.Verify(
            a => a.SignInWithTicketAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public void Connector_Variant_DeepLink_Is_Not_Claimed()
    {
        var sut = Create();
        StartAndCaptureNonce(sut);

        sut.HandleDeepLink("sran://oauth/callback?connector=slack&status=ok");

        _auth.Verify(
            a => a.SignInWithTicketAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Never);
        sut.HasPendingAttempt.Should().BeTrue("the attempt must survive foreign links");
    }

    [Fact]
    public async Task Exchange_Failure_Raises_Failed()
    {
        _auth.Setup(a => a.SignInWithTicketAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("401"));
        var sut = Create();
        StartAndCaptureNonce(sut);
        var failed = new TaskCompletionSource<string>();
        sut.SignInFailed += (_, r) => failed.TrySetResult(r);

        sut.HandleDeepLink(TicketLink);

        (await Task.WhenAny(failed.Task, Task.Delay(5000)))
            .Should().Be(failed.Task);
        (await failed.Task).Should().Be("exchange_failed");
    }
}

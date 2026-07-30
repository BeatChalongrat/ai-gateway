using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Credentials;
using Xunit;

namespace SranDesktop.Tests.Credentials;

/// <summary>
/// The interactive OAuth flow, driven end to end against a deterministic
/// provider and a real loopback listener.
///
/// The browser is replaced, because a test cannot log a human in. Everything
/// else is the production path: a real HttpListener on a real ephemeral port, a
/// real redirect, real PKCE, and the real state registry. The adversarial cases
/// are the point — a flow that works is easy, one that refuses a replayed state
/// and a mismatched verifier is the thing being built.
/// </summary>
/// <remarks>See ConnectorTrustAdversarialTests: AgentInputScope is
/// process-wide, so every class that resets it runs in one collection.</remarks>
[Collection("PathGuardGrants")]
public sealed class OAuthAuthorizationTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-oauth-{Guid.NewGuid():N}");
    private readonly InMemoryCredentialStore _secrets = new();
    private readonly FakeOAuthProvider _provider = new();
    private readonly DpapiConnectorCredentialStore _store;
    private readonly OAuthAttemptRegistry _attempts = new();
    private readonly ScriptedBrowser _browser;
    private readonly string _indexPath;

    private static readonly OAuthProviderConfig Provider = new()
    {
        ConnectorId = "mailer",
        AuthorizationEndpoint = "https://provider.test/authorize",
        TokenEndpoint = "https://provider.test/token",
        ClientId = "sran-desktop",
        AllowedScopes = ["mail.read", "mail.send"],
        DefaultScopes = ["mail.read"]
    };

    public OAuthAuthorizationTests()
    {
        Directory.CreateDirectory(_root);
        _indexPath = Path.Combine(_root, "connector-accounts.json");
        _store = new DpapiConnectorCredentialStore(_indexPath, _secrets, NullLogger.Instance);
        _browser = new ScriptedBrowser(_provider);
    }

    private OAuthAuthorizationService Service() =>
        new(_store, _provider, _browser, _attempts, NullLogger.Instance);

    private static UserApprovalEvidence Evidence()
    {
        AgentInputScope.ResetForTests();
        return UserApprovalEvidence.FromUserGesture("user:test", "connect account");
    }

    // ── Happy path ──────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Completed_Sign_In_Stores_An_Account_And_No_Token_In_The_Index()
    {
        _browser.Subject = "ana@example.test";

        var result = await Service().ConnectAsync(
            Provider, "local", ["mail.read"], Evidence());

        result.Success.Should().BeTrue(result.FailureReason);
        result.Descriptor!.Account.AccountId.Should().Be("ana@example.test");
        result.Descriptor.Scopes.Should().Contain("mail.read");
        result.Descriptor.HasRefreshToken.Should().BeTrue();

        var index = await File.ReadAllTextAsync(_indexPath);
        index.Should().NotContain("refresh-");
        index.Should().NotContain(".sig-",
            "the access token's own text must not appear beside the account record");
    }

    [Fact]
    public async Task The_Authorize_Url_Carries_The_Challenge_And_Never_The_Verifier()
    {
        _browser.Subject = "ana@example.test";

        await Service().ConnectAsync(Provider, "local", ["mail.read"], Evidence());

        _browser.LastUrl.Should().Contain("code_challenge=");
        _browser.LastUrl.Should().Contain("code_challenge_method=S256");
        _browser.LastUrl.Should().Contain("state=");
        _browser.LastUrl.Should().Contain("redirect_uri=http%3A%2F%2F127.0.0.1%3A");
        _browser.LastUrl.Should().NotContain("code_verifier",
            "the verifier is the secret half — it goes to the token endpoint only");
    }

    [Fact]
    public async Task The_Redirect_Is_Loopback_Only()
    {
        _browser.Subject = "ana@example.test";

        await Service().ConnectAsync(Provider, "local", ["mail.read"], Evidence());

        var redirect = new Uri(_browser.CapturedRedirectUri!);
        redirect.Host.Should().Be("127.0.0.1");
        redirect.IsLoopback.Should().BeTrue(
            "a redirect that leaves this machine hands the code to somebody else");
    }

    // ── Adversarial ─────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Callback_With_A_State_We_Never_Issued_Is_Refused()
    {
        _browser.Subject = "ana@example.test";
        _browser.StateOverride = "state-from-somewhere-else";

        var result = await Service().ConnectAsync(
            Provider, "local", ["mail.read"], Evidence());

        result.Success.Should().BeFalse();
        result.FailureCode.Should().Be("StateRejected");
        _provider.ExchangeCount.Should().Be(0,
            "the code must never reach the token endpoint when the state is wrong");
        _store.List().Should().BeEmpty();
    }

    [Fact]
    public async Task A_Replayed_State_Is_Refused_The_Second_Time()
    {
        _browser.Subject = "ana@example.test";
        var first = await Service().ConnectAsync(Provider, "local", ["mail.read"], Evidence());
        first.Success.Should().BeTrue(first.FailureReason);

        // Same state, delivered again — a captured callback being replayed.
        _browser.StateOverride = _browser.LastIssuedState;
        var second = await Service().ConnectAsync(Provider, "local", ["mail.read"], Evidence());

        second.Success.Should().BeFalse();
        second.FailureCode.Should().Be("StateRejected");
    }

    [Fact]
    public void An_Expired_State_Cannot_Be_Consumed()
    {
        var challenge = OAuthPkceChallenge.Create();
        // The challenge stamps itself from the real clock, so the fake clock has
        // to be anchored to it. A hard-coded date here reads as expiry but is
        // really a comparison against wall-clock now, and it silently inverts the
        // day the date goes past — which is exactly what happened on 2026-07-30.
        var now = challenge.CreatedUtc;
        var registry = new OAuthAttemptRegistry(() => now);
        registry.Register(challenge, "mailer", "local", "http://127.0.0.1:1/cb", []);

        now = now.Add(OAuthPkceChallenge.Lifetime).AddMinutes(1);

        registry.TryConsume(challenge.State, out _).Should().BeFalse(
            "a state left in a browser history must not still work tomorrow");
        registry.OpenCount.Should().Be(0);
    }

    [Fact]
    public async Task An_Authorization_Code_Cannot_Be_Redeemed_Twice()
    {
        _browser.Subject = "ana@example.test";
        await Service().ConnectAsync(Provider, "local", ["mail.read"], Evidence());
        var stolenCode = _browser.LastCode!;

        // The attacker has the code and a state we WILL accept, but the code
        // itself was consumed at the provider.
        var exchange = await _provider.ExchangeCodeAsync(
            Provider, stolenCode, "any-verifier", _browser.CapturedRedirectUri!);

        exchange.Success.Should().BeFalse();
        exchange.Error.Should().Contain("unknown or used code");
    }

    [Fact]
    public async Task A_Code_Redeemed_With_The_Wrong_Verifier_Is_Refused()
    {
        var challenge = OAuthPkceChallenge.Create();
        var code = _provider.IssueCode(
            challenge.Challenge, "http://127.0.0.1:9/cb", ["mail.read"], "ana@example.test");

        var exchange = await _provider.ExchangeCodeAsync(
            Provider, code, OAuthPkceChallenge.Create().Verifier, "http://127.0.0.1:9/cb");

        exchange.Success.Should().BeFalse();
        exchange.Error.Should().Contain("PKCE verifier mismatch");
    }

    [Fact]
    public async Task A_Code_Redeemed_Against_A_Different_Redirect_Is_Refused()
    {
        var challenge = OAuthPkceChallenge.Create();
        var code = _provider.IssueCode(
            challenge.Challenge, "http://127.0.0.1:9/cb", ["mail.read"], "ana@example.test");

        var exchange = await _provider.ExchangeCodeAsync(
            Provider, code, challenge.Verifier, "http://127.0.0.1:9/somewhere-else");

        exchange.Success.Should().BeFalse();
        exchange.Error.Should().Contain("redirect_uri mismatch");
    }

    [Fact]
    public async Task A_Scope_Outside_The_Policy_Is_Refused_Before_The_Browser_Opens()
    {
        var result = await Service().ConnectAsync(
            Provider, "local", ["mail.read", "admin.everything"], Evidence());

        result.Success.Should().BeFalse();
        result.FailureCode.Should().Be("ScopeNotAllowed");
        _browser.LastUrl.Should().BeNull("nothing should have been opened");
    }

    [Fact]
    public async Task A_Provider_Granting_More_Than_Policy_Allows_Stores_Nothing()
    {
        _browser.Subject = "ana@example.test";
        // A provider — compromised, or merely generous — hands back a scope this
        // connector is not permitted to hold.
        _provider.GrantedScopesOverride = ["mail.read", "admin.everything"];

        var result = await Service().ConnectAsync(
            Provider, "local", ["mail.read"], Evidence());

        result.Success.Should().BeFalse();
        result.FailureCode.Should().Be("ScopeEscalation");
        _store.List().Should().BeEmpty(
            "an over-broad grant is not something to keep and hope nobody uses");
    }

    [Fact]
    public async Task A_Provider_Error_Response_Does_Not_Complete_The_Connection()
    {
        _browser.Subject = "ana@example.test";
        _browser.ReturnError = "access_denied";

        var result = await Service().ConnectAsync(
            Provider, "local", ["mail.read"], Evidence());

        result.Success.Should().BeFalse();
        result.FailureCode.Should().Be("access_denied");
        _store.List().Should().BeEmpty();
    }

    [Fact]
    public async Task Connecting_An_Account_Requires_Evidence_Of_A_Person()
    {
        _browser.Subject = "ana@example.test";

        var connect = () => Service().ConnectAsync(Provider, "local", ["mail.read"], null!);

        await connect.Should().ThrowAsync<ArgumentNullException>(
            "connecting an account grants standing access to somebody else's data");
    }

    [Fact]
    public async Task Two_Accounts_Can_Be_Connected_On_One_Connector()
    {
        _browser.Subject = "ana@example.test";
        await Service().ConnectAsync(Provider, "local", ["mail.read"], Evidence());
        _browser.Subject = "bo@example.test";
        await Service().ConnectAsync(Provider, "local", ["mail.read"], Evidence());

        _store.List("mailer").Select(item => item.Account.AccountId)
            .Should().BeEquivalentTo("ana@example.test", "bo@example.test");
    }

    [Fact]
    public async Task The_Same_Person_In_Two_Tenants_Is_Two_Accounts()
    {
        _browser.Subject = "shared@example.test";
        await Service().ConnectAsync(Provider, "acme", ["mail.read"], Evidence());
        await Service().ConnectAsync(Provider, "globex", ["mail.read"], Evidence());

        _store.List("mailer", "acme").Should().ContainSingle();
        _store.List("mailer", "globex").Should().ContainSingle();
        _store.List("mailer").Should().HaveCount(2);
    }

    // ── Loopback listener ───────────────────────────────────────────────────

    [Fact]
    public async Task The_Listener_Ignores_Requests_That_Are_Not_The_Redirect()
    {
        using var listener = new LoopbackOAuthCallbackListener("/oauth/callback");
        using var http = new HttpClient();

        var waiting = listener.WaitAsync(TimeSpan.FromSeconds(10));
        var probe = await http.GetAsync($"http://127.0.0.1:{listener.Port}/admin");
        probe.StatusCode.Should().Be(System.Net.HttpStatusCode.NotFound);

        // The real callback still lands afterwards: an unrelated request must not
        // consume the one delivery.
        await http.GetAsync($"{listener.RedirectUri}?code=abc&state=xyz");
        var capture = await waiting;

        capture.Success.Should().BeTrue();
        capture.Code.Should().Be("abc");
        capture.State.Should().Be("xyz");
    }

    [Fact]
    public async Task The_Listener_Gives_Up_Rather_Than_Waiting_Forever()
    {
        using var listener = new LoopbackOAuthCallbackListener("/oauth/callback");

        var capture = await listener.WaitAsync(TimeSpan.FromMilliseconds(300));

        capture.Success.Should().BeFalse();
        capture.Error.Should().Be("timeout");
    }

    [Fact]
    public async Task The_Listener_Stops_Listening_After_It_Is_Disposed()
    {
        var listener = new LoopbackOAuthCallbackListener("/oauth/callback");
        var port = listener.Port;
        listener.Dispose();

        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
        var probe = async () => await http.GetAsync($"http://127.0.0.1:{port}/oauth/callback");

        await probe.Should().ThrowAsync<Exception>(
            "a listener left running is a second delivery nobody expects");
    }

    /// <summary>
    /// Stands in for the user's browser: reads the authorize URL, then delivers
    /// the callback to the loopback listener exactly as a provider redirect
    /// would. Everything it sends is taken from the real URL, so a flow that
    /// builds a wrong URL fails here rather than passing on a fixture's charity.
    /// </summary>
    private sealed class ScriptedBrowser(FakeOAuthProvider provider) : IBrowserLauncher
    {
        public string? LastUrl { get; private set; }
        public string? CapturedRedirectUri { get; private set; }
        public string? LastIssuedState { get; private set; }
        public string? LastCode { get; private set; }

        public string Subject { get; set; } = "ana@example.test";

        /// <summary>Delivers this state instead of the issued one.</summary>
        public string? StateOverride { get; set; }

        /// <summary>Delivers an error response instead of a code.</summary>
        public string? ReturnError { get; set; }

        public bool Open(string url)
        {
            LastUrl = url;
            var query = System.Web.HttpUtility.ParseQueryString(new Uri(url).Query);
            CapturedRedirectUri = query["redirect_uri"];
            LastIssuedState = query["state"];
            var challenge = query["code_challenge"]!;
            var scopes = (query["scope"] ?? "")
                .Split(' ', StringSplitOptions.RemoveEmptyEntries);

            var state = StateOverride ?? LastIssuedState;
            LastCode = ReturnError is null
                ? provider.IssueCode(challenge, CapturedRedirectUri!, scopes, Subject)
                : null;

            var callback = ReturnError is null
                ? $"{CapturedRedirectUri}?code={Uri.EscapeDataString(LastCode!)}"
                  + $"&state={Uri.EscapeDataString(state ?? "")}"
                : $"{CapturedRedirectUri}?error={Uri.EscapeDataString(ReturnError)}"
                  + $"&state={Uri.EscapeDataString(state ?? "")}";

            _ = Task.Run(async () =>
            {
                using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
                try
                {
                    await http.GetAsync(callback);
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                }
            });
            return true;
        }
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_root))
                Directory.Delete(_root, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
    }
}

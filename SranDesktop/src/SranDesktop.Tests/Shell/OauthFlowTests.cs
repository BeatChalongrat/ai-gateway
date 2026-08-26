using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Oauth;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// FIX7 — the client-native OAuth flow, exercised against a FAKE authorization
/// server built into these tests (a delegating handler that returns spec-shaped
/// RFC 9728 / 8414 / 7591 / 6749 responses). This is NOT the live proof the report
/// calls for (§5.3 needs a REAL service, which is not reachable in this
/// environment) — it is a labelled fixture that proves the flow LOGIC: PKCE S256,
/// the discovery chain, scope selection, the loopback capture, DCR, token exchange,
/// and refresh with rotation. A real end-to-end run is owner-gated.
/// </summary>
public class PkceTests
{
    [Fact]
    public void Challenge_is_unpadded_base64url_sha256_of_the_verifier()
    {
        var p = PkcePair.Create();
        p.Verifier.Length.Should().BeInRange(43, 128);           // RFC 7636 §4.1
        p.Verifier.Should().MatchRegex("^[A-Za-z0-9_-]+$");      // unreserved, base64url
        var expected = PkcePair.Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(p.Verifier)));
        p.Challenge.Should().Be(expected);
        p.Challenge.Should().NotContain("=").And.NotContain("+").And.NotContain("/");
        PkcePair.Method.Should().Be("S256");
    }

    [Fact]
    public void Two_pairs_differ_and_are_high_entropy()
    {
        PkcePair.Create().Verifier.Should().NotBe(PkcePair.Create().Verifier);
    }
}

public class OauthDiscoveryTests
{
    [Theory]
    [InlineData("Bearer resource_metadata=\"https://mcp.example.com/.well-known/oauth-protected-resource\"", "https://mcp.example.com/.well-known/oauth-protected-resource")]
    [InlineData("Bearer realm=\"x\", resource_metadata=\"https://a.test/prm\"", "https://a.test/prm")]
    [InlineData("Bearer resource_metadata=https://bare.test/prm", "https://bare.test/prm")]
    [InlineData("Bearer realm=\"x\"", null)]
    public void ResourceMetadataUrl_parses_the_www_authenticate_header(string header, string? expected) =>
        OauthDiscovery.ResourceMetadataUrl(header).Should().Be(expected);

    [Fact]
    public void PrimaryAuthorizationServer_uses_the_FIRST_entry_and_does_not_fall_back()
    {
        var prm = new ProtectedResourceMetadata("https://mcp", new() { "https://as-1.test", "https://as-2.test" }, null);
        OauthDiscovery.PrimaryAuthorizationServer(prm).Should().Be("https://as-1.test");
        OauthDiscovery.PrimaryAuthorizationServer(new ProtectedResourceMetadata("m", new(), null)).Should().BeNull();
    }

    [Fact]
    public void MetadataUrls_are_RFC8414_with_the_issuer_path_and_an_OIDC_fallback()
    {
        var (primary, oidc) = OauthDiscovery.MetadataUrls("https://as.test/tenant1");
        primary.Should().Be("https://as.test/.well-known/oauth-authorization-server/tenant1");
        oidc.Should().Be("https://as.test/.well-known/openid-configuration/tenant1");
        OauthDiscovery.MetadataUrls("https://as.test").primary.Should().Be("https://as.test/.well-known/oauth-authorization-server");
    }

    [Fact]
    public void SelectScopes_prefers_the_401_scope_then_PRM_and_appends_offline_access_only_when_the_AS_offers_it()
    {
        var prm = new ProtectedResourceMetadata("m", new() { "https://as" }, new() { "read", "write" });
        var asWithOffline = new AuthServerMetadata(null, "a", "t", "r", new() { "read", "write", "offline_access" }, new() { "S256" });
        var asNoOffline = asWithOffline with { ScopesSupported = new() { "read", "write" } };

        // 401 scope wins over PRM; offline_access appended because the AS lists it.
        OauthDiscovery.SelectScopes("read", prm, asWithOffline).Should().Be("read offline_access");
        // No 401 scope → PRM's scopes; AS offers offline_access → appended.
        OauthDiscovery.SelectScopes(null, prm, asWithOffline).Should().Be("read write offline_access");
        // AS does NOT offer offline_access → not appended (no phantom scope).
        OauthDiscovery.SelectScopes(null, prm, asNoOffline).Should().Be("read write");
    }

    [Fact]
    public void SupportsS256_gate()
    {
        OauthDiscovery.SupportsS256(new AuthServerMetadata(null, "a", "t", "r", null, new() { "S256" })).Should().BeTrue();
        OauthDiscovery.SupportsS256(new AuthServerMetadata(null, "a", "t", "r", null, new() { "plain" })).Should().BeFalse();
        OauthDiscovery.SupportsS256(new AuthServerMetadata(null, "a", "t", "r", null, null)).Should().BeFalse();
    }
}

public class LoopbackRedirectServerTests
{
    [Fact]
    public async Task Binds_loopback_on_an_ephemeral_port_and_captures_the_code_for_a_matching_state()
    {
        using var server = new LoopbackRedirectServer(NullLogger.Instance);
        server.RedirectUri.Should().MatchRegex(@"^http://127\.0\.0\.1:\d+/callback$");

        using var http = new HttpClient();
        var wait = server.WaitForCodeAsync("state123", CancellationToken.None);
        // The browser "redirects" to the loopback with the code + our state.
        _ = await http.GetAsync($"{server.RedirectUri}?code=the_code&state=state123");
        (await wait).Should().Be("the_code");
    }

    [Fact]
    public async Task Rejects_a_state_mismatch_CSRF()
    {
        using var server = new LoopbackRedirectServer(NullLogger.Instance);
        using var http = new HttpClient();
        var wait = server.WaitForCodeAsync("expected", CancellationToken.None);
        _ = await http.GetAsync($"{server.RedirectUri}?code=x&state=ATTACKER");
        await FluentActions.Awaiting(() => wait).Should().ThrowAsync<InvalidOperationException>()
            .Where(e => e.Message == "state_mismatch");
    }
}

public class ClientNativeOauthFlowTests
{
    private const string Mcp = "https://mcp.example.com/mcp";

    [Fact]
    public async Task Full_flow_discovers_registers_authorizes_and_exchanges_against_a_fake_AS()
    {
        var fake = new FakeAuthServer();
        var flow = new ClientNativeOauthFlow(new HttpClient(fake), fake.Browser, NullLogger.Instance);
        var now = DateTimeOffset.UnixEpoch;

        var www = $"Bearer resource_metadata=\"{FakeAuthServer.PrmUrl}\"";
        var disc = await flow.DiscoverAsync(Mcp, www, CancellationToken.None);
        disc.As.TokenEndpoint.Should().Be(FakeAuthServer.TokenEndpoint);
        disc.Scopes.Should().Contain("offline_access");           // AS advertises it → requested

        var reg = await flow.RegisterAsync(disc.As, CancellationToken.None);
        reg.ClientId.Should().Be(FakeAuthServer.ClientId);

        using var loopback = new LoopbackRedirectServer(NullLogger.Instance);
        fake.Browser.DriveLoopbackWithCode("auth_code_1");        // the "user" consents
        var (code, verifier) = await flow.AuthorizeAsync(disc.As, reg.ClientId!, disc.Scopes, loopback, CancellationToken.None);
        code.Should().Be("auth_code_1");

        var token = await flow.ExchangeAsync(disc.As, reg, loopback.RedirectUri, code, verifier, disc.Scopes, now, CancellationToken.None);
        token.AccessToken.Should().Be("access_1");
        token.RefreshToken.Should().Be("refresh_1");
        token.ExpiresAtUtc.Should().Be(now.AddSeconds(3600));
        // The AS received our PKCE verifier and it matched the challenge it was sent.
        fake.VerifierMatchedChallenge.Should().BeTrue();
    }

    [Fact]
    public async Task Refresh_rotates_the_refresh_token_and_keeps_it_when_the_server_does_not()
    {
        var fake = new FakeAuthServer();
        var flow = new ClientNativeOauthFlow(new HttpClient(fake), fake.Browser, NullLogger.Instance);
        var now = DateTimeOffset.UnixEpoch;
        var stored = new StoredConnectorToken("access_1", "refresh_1", FakeAuthServer.TokenEndpoint, FakeAuthServer.ClientId, null, "read", now.AddSeconds(3600));

        var refreshed = await flow.RefreshAsync(stored, now, CancellationToken.None);
        refreshed.AccessToken.Should().Be("access_2");
        refreshed.RefreshToken.Should().Be("refresh_2");          // rotated (public client)

        fake.RotateRefresh = false;                                // a server that does not rotate
        var again = await flow.RefreshAsync(refreshed, now, CancellationToken.None);
        again.RefreshToken.Should().Be("refresh_2");               // old one kept
    }

    [Fact]
    public async Task A_dead_refresh_token_surfaces_as_invalid_grant_not_a_generic_error()
    {
        var fake = new FakeAuthServer { InvalidGrant = true };
        var flow = new ClientNativeOauthFlow(new HttpClient(fake), fake.Browser, NullLogger.Instance);
        var stored = new StoredConnectorToken("a", "dead", FakeAuthServer.TokenEndpoint, FakeAuthServer.ClientId, null, null, null);
        await FluentActions.Awaiting(() => flow.RefreshAsync(stored, DateTimeOffset.UnixEpoch, CancellationToken.None))
            .Should().ThrowAsync<OauthFlowException>().Where(e => e.Code == "token_error:invalid_grant");
    }

    [Fact]
    public void NeedsProactiveRefresh_fires_within_five_minutes_of_expiry()
    {
        var t0 = DateTimeOffset.UnixEpoch;
        var tok = new StoredConnectorToken("a", "r", "t", "c", null, null, t0.AddMinutes(10));
        tok.NeedsProactiveRefresh(t0).Should().BeFalse();               // 10 min out
        tok.NeedsProactiveRefresh(t0.AddMinutes(5.5)).Should().BeTrue(); // inside 5-min window
        tok.NeedsProactiveRefresh(t0.AddMinutes(11)).Should().BeTrue();  // past expiry
        (tok with { ExpiresAtUtc = null }).NeedsProactiveRefresh(t0).Should().BeFalse(); // unknown expiry → reactive only
    }
}

using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Oauth;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// FIX7 §1.2 / §5.7 — the posture the whole round turns on: a client-native
/// (oauth_dcr) connector's tool calls send its OWN per-connector token, resolved
/// from the DPAPI store — NEVER the user's portal JWT. The legacy broker (oauth2)
/// mode still resolves the portal JWT, which is why the two must stay distinct: the
/// sidecar holds no portal JWT at all (Program.cs passes tokenSession: null), so an
/// oauth_dcr connector is the only way a shell connector authenticates.
/// </summary>
public class ConnectorTokenSourceTests
{
    private sealed class MemCreds : ICredentialStore
    {
        public readonly Dictionary<string, string> Store = new();
        public void Save(string key, string value) => Store[key] = value;
        public string? Load(string key) => Store.GetValueOrDefault(key);
        public void Delete(string key) => Store.Remove(key);
    }

    private sealed class PortalJwtSession : ITokenSession
    {
        public string? AccessToken => "PORTAL_JWT_MUST_NOT_LEAK";
        public string? RefreshToken => null;
        public UserInfo? User => null;
        public bool HasSession => true;
        public void SetSession(string a, string r, UserInfo? u) { }
        public void Clear() { }
        public event EventHandler? SessionChanged { add { } remove { } }
    }

    [Fact]
    public async Task OauthDcr_sends_the_per_connector_token_never_the_portal_JWT()
    {
        var tmp = Path.Combine(Path.GetTempPath(), "fix7-" + Guid.NewGuid().ToString("N"), "connectors.json");
        var creds = new MemCreds();
        // A tokenSession IS supplied here (as the WPF app would) to prove the
        // oauth_dcr path ignores it. In the real sidecar it is null anyway.
        var svc = new McpConnectorService(creds, NullLogger<McpConnectorService>.Instance,
            tokenSession: new PortalJwtSession(), stateFilePath: tmp);

        await svc.InstallAsync("dcr-conn", apiToken: null, authMode: InstalledConnector.AuthModeOAuthDcr);
        await svc.InstallAsync("broker-conn", apiToken: null, authMode: InstalledConnector.AuthModeOAuth2);

        var token = new StoredConnectorToken("per-connector-access", "refresh", "https://as.test/token",
            "client-id", null, "read", DateTimeOffset.UtcNow.AddHours(1));
        creds.Save("connector-oauth:dcr-conn", token.ToJson());

        // oauth_dcr → the per-connector access token, and NOT the portal JWT.
        svc.ResolveTokenProvider("dcr-conn")().Should().Be("per-connector-access");
        svc.ResolveTokenProvider("dcr-conn")().Should().NotBe("PORTAL_JWT_MUST_NOT_LEAK");
        // Legacy broker oauth2 → the portal JWT (WPF only) — proving the split is real.
        svc.ResolveTokenProvider("broker-conn")().Should().Be("PORTAL_JWT_MUST_NOT_LEAK");
    }

    [Fact]
    public async Task OauthDcr_with_no_stored_token_sends_NOTHING_rather_than_falling_back_to_the_JWT()
    {
        var tmp = Path.Combine(Path.GetTempPath(), "fix7-" + Guid.NewGuid().ToString("N"), "connectors.json");
        var svc = new McpConnectorService(new MemCreds(), NullLogger<McpConnectorService>.Instance,
            tokenSession: new PortalJwtSession(), stateFilePath: tmp);
        await svc.InstallAsync("dcr-conn", apiToken: null, authMode: InstalledConnector.AuthModeOAuthDcr);

        // No connector-oauth:{id} entry → null (a re-connect is needed), NOT the JWT.
        svc.ResolveTokenProvider("dcr-conn")().Should().BeNull();
    }
}

using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Credentials;
using Xunit;

namespace SranDesktop.Tests.Credentials;

/// <summary>
/// The credential broker: leasing, expiry, refresh, revocation, scopes, and the
/// tenant boundary.
///
/// The recurring assertion in this file is about where a token is NOT. A broker
/// that hands out working credentials is easy; one that never lets the secret
/// reach a log line, a durable record, or a caller that only needed to make one
/// request is the thing being built.
/// </summary>
public sealed class CredentialBrokerTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-cred-{Guid.NewGuid():N}");
    private readonly string _indexPath;
    private readonly InMemoryCredentialStore _secrets = new();
    private readonly FakeOAuthProvider _provider = new();
    private readonly DpapiConnectorCredentialStore _store;
    private readonly CredentialBroker _broker;
    private DateTimeOffset _now = new(2026, 7, 29, 12, 0, 0, TimeSpan.Zero);

    private static readonly OAuthProviderConfig Provider = new()
    {
        ConnectorId = "mailer",
        AuthorizationEndpoint = "https://provider.test/authorize",
        TokenEndpoint = "https://provider.test/token",
        RevocationEndpoint = "https://provider.test/revoke",
        ClientId = "sran-desktop",
        AllowedScopes = ["mail.read", "mail.send"],
        DefaultScopes = ["mail.read"]
    };

    public CredentialBrokerTests()
    {
        Directory.CreateDirectory(_root);
        _indexPath = Path.Combine(_root, "connector-accounts.json");
        _provider.Clock = () => _now;
        _store = new DpapiConnectorCredentialStore(_indexPath, _secrets, NullLogger.Instance);
        _broker = new CredentialBroker(
            _store, _provider, _ => Provider, NullLogger.Instance, () => _now);
    }

    // ── Storage ─────────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Token_Never_Reaches_The_Listable_Index()
    {
        await ConnectAsync("ana@example.test", accessToken: "at-secret-value",
            refreshToken: "rt-secret-value");

        var index = await File.ReadAllTextAsync(_indexPath);

        index.Should().NotContain("at-secret-value");
        index.Should().NotContain("rt-secret-value");
        index.Should().Contain("ana@example.test",
            "the account has to be listable — that is what the index is for");
    }

    [Fact]
    public async Task The_Descriptor_Says_Whether_A_Refresh_Token_Exists_Without_Holding_One()
    {
        await ConnectAsync("ana@example.test", "at", "rt");

        var descriptor = _store.Get(Account("ana@example.test"))!;

        descriptor.HasRefreshToken.Should().BeTrue();
        descriptor.GetType().GetProperties()
            .Select(property => property.GetValue(descriptor)?.ToString() ?? "")
            .Should().NotContain(value => value.Contains("rt", StringComparison.Ordinal)
                                          && value.Length > 8);
    }

    [Fact]
    public async Task Secrets_Live_Under_A_Per_Account_Key_In_Protected_Storage()
    {
        await ConnectAsync("ana@example.test", "at", "rt");
        await ConnectAsync("bo@example.test", "at2", "rt2");

        _secrets.Values.Keys.Should().BeEquivalentTo(
            "connector-account:local|mailer|ana@example.test",
            "connector-account:local|mailer|bo@example.test");
    }

    // ── Leasing ─────────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Lease_Carries_A_Header_Not_A_Token()
    {
        await ConnectAsync("ana@example.test", "at-1", "rt-1");

        var result = await _broker.LeaseAsync(Account("ana@example.test"), ["mail.read"]);

        result.Granted.Should().BeTrue(result.Reason);
        result.Lease!.HeaderName.Should().Be("Authorization");
        result.Lease.HeaderValue.Should().Be("Bearer at-1");
        result.Lease.ToString().Should().NotContain("at-1",
            "a lease interpolated into a log line must not print the credential");
    }

    [Fact]
    public async Task A_Released_Lease_Cannot_Be_Read_Again()
    {
        await ConnectAsync("ana@example.test", "at-1", "rt-1");
        var result = await _broker.LeaseAsync(Account("ana@example.test"), []);

        result.Lease!.Dispose();

        var read = () => result.Lease.HeaderValue;
        read.Should().Throw<ObjectDisposedException>(
            "a lease is borrowed for one operation, not kept");
    }

    [Fact]
    public async Task An_Unknown_Account_Is_Refused_Rather_Than_Guessed()
    {
        var result = await _broker.LeaseAsync(Account("nobody@example.test"), []);

        result.Granted.Should().BeFalse();
        result.Failure.Should().Be(CredentialLeaseFailure.NoAccount);
    }

    [Fact]
    public async Task A_Scope_That_Was_Never_Granted_Is_Refused_Before_The_Provider_Sees_It()
    {
        await ConnectAsync("ana@example.test", "at", "rt", scopes: ["mail.read"]);

        var result = await _broker.LeaseAsync(Account("ana@example.test"), ["mail.send"]);

        result.Granted.Should().BeFalse();
        result.Failure.Should().Be(CredentialLeaseFailure.ScopeNotGranted);
        result.Reason.Should().Contain("mail.send");
    }

    [Fact]
    public async Task A_Credential_From_Another_Tenant_Cannot_Be_Borrowed()
    {
        await ConnectAsync("ana@example.test", "at", "rt", tenant: "acme");

        var result = await _broker.LeaseAsync(
            new ConnectorAccountRef("mailer", "globex", "ana@example.test"), []);

        result.Granted.Should().BeFalse();
        result.Failure.Should().Be(CredentialLeaseFailure.NoAccount,
            "the tenant is part of the identity, so a different tenant is a different account");
    }

    // ── Expiry and refresh ──────────────────────────────────────────────────

    [Fact]
    public async Task An_Expired_Token_Is_Refreshed_Transparently()
    {
        var descriptor = await ConnectAsync("ana@example.test", "at-1", "rt-1");
        descriptor.AccessTokenExpiresUtc.Should().NotBeNull();
        _now = descriptor.AccessTokenExpiresUtc!.Value.AddMinutes(1);

        var result = await _broker.LeaseAsync(Account("ana@example.test"), ["mail.read"]);

        result.Granted.Should().BeTrue(result.Reason);
        result.Lease!.HeaderValue.Should().NotBe("Bearer at-1");
        _provider.RefreshCount.Should().Be(1);
    }

    [Fact]
    public async Task A_Rotated_Refresh_Token_Replaces_The_Old_One()
    {
        var descriptor = await ConnectAsync("ana@example.test", "at-1", "rt-1");
        _now = descriptor.AccessTokenExpiresUtc!.Value.AddMinutes(1);
        await _broker.LeaseAsync(Account("ana@example.test"), []);

        // A second expiry must refresh again, which only works if the rotated
        // token was stored: the old one is dead at the provider.
        _now = _now.AddHours(2);
        var second = await _broker.LeaseAsync(Account("ana@example.test"), []);

        second.Granted.Should().BeTrue(second.Reason);
        _provider.RefreshCount.Should().Be(2);
    }

    [Fact]
    public async Task An_Expired_Token_With_No_Refresh_Token_Asks_For_A_Reconnect()
    {
        var descriptor = await ConnectAsync("ana@example.test", "at-1", refreshToken: null);
        _now = descriptor.AccessTokenExpiresUtc!.Value.AddMinutes(1);

        var result = await _broker.LeaseAsync(Account("ana@example.test"), []);

        result.Granted.Should().BeFalse();
        result.Failure.Should().Be(CredentialLeaseFailure.Expired);
        _provider.RefreshCount.Should().Be(0);
    }

    [Fact]
    public async Task A_Refused_Refresh_Is_Recorded_And_Not_Retried()
    {
        var descriptor = await ConnectAsync("ana@example.test", "at-1", "rt-1");
        _now = descriptor.AccessTokenExpiresUtc!.Value.AddMinutes(1);
        _provider.RefreshFails = true;

        var first = await _broker.LeaseAsync(Account("ana@example.test"), []);
        var second = await _broker.LeaseAsync(Account("ana@example.test"), []);

        first.Failure.Should().Be(CredentialLeaseFailure.RefreshFailed);
        second.Failure.Should().Be(CredentialLeaseFailure.RefreshFailed);
        _provider.RefreshCount.Should().Be(1,
            "a provider that refused a refresh will refuse it again; asking twice only "
            + "burns the token and the rate limit");
    }

    [Fact]
    public async Task A_Provider_Error_Body_Does_Not_Carry_A_Token_Into_Stored_State()
    {
        var descriptor = await ConnectAsync("ana@example.test", "at-1", "rt-1");
        _now = descriptor.AccessTokenExpiresUtc!.Value.AddMinutes(1);
        _provider.RefreshFails = true;
        // A real provider echoing the offending credential back at us.
        _provider.RefreshErrorBody =
            "invalid_grant for token sk-live-51H8xQpZmNbVcXaSdFgHjKlPoIuYtReWq";

        await _broker.LeaseAsync(Account("ana@example.test"), []);

        var index = await File.ReadAllTextAsync(_indexPath);
        index.Should().NotContain("sk-live-51H8xQpZmNbVcXaSdFgHjKlPoIuYtReWq");
    }

    [Fact]
    public async Task A_Token_Revoked_At_The_Provider_Surfaces_As_Needing_A_Reconnect()
    {
        var descriptor = await ConnectAsync("ana@example.test", "at-1", "rt-1");
        var refreshToken = _secrets.Values.Values.First();
        // The user revoked from the provider's own settings page; nobody told us.
        _provider.RevokeServerSide(ExtractRefreshToken(refreshToken));
        _now = descriptor.AccessTokenExpiresUtc!.Value.AddMinutes(1);

        var result = await _broker.LeaseAsync(Account("ana@example.test"), []);

        result.Granted.Should().BeFalse();
        result.Failure.Should().Be(CredentialLeaseFailure.RefreshFailed);
    }

    [Fact]
    public async Task Concurrent_Operations_Refresh_Once_Between_Them()
    {
        var descriptor = await ConnectAsync("ana@example.test", "at-1", "rt-1");
        _now = descriptor.AccessTokenExpiresUtc!.Value.AddMinutes(1);

        var leases = await Task.WhenAll(Enumerable.Range(0, 8)
            .Select(_ => _broker.LeaseAsync(Account("ana@example.test"), [])));

        leases.Should().OnlyContain(item => item.Granted);
        _provider.RefreshCount.Should().Be(1,
            "a provider that rotates refresh tokens invalidates the loser of a race, "
            + "which would turn a working account into a broken one");
    }

    // ── Revocation ──────────────────────────────────────────────────────────

    [Fact]
    public async Task Revoking_Makes_The_Credential_Unusable_And_Drops_The_Secret()
    {
        await ConnectAsync("ana@example.test", "at-1", "rt-1");

        await _broker.RevokeAsync(Account("ana@example.test"));

        var result = await _broker.LeaseAsync(Account("ana@example.test"), []);
        result.Failure.Should().Be(CredentialLeaseFailure.Revoked);
        _secrets.Values.Should().NotContainKey(
            "connector-account:local|mailer|ana@example.test");
        _provider.RevokedTokens.Should().NotBeEmpty(
            "the provider is told too, best-effort");
    }

    [Fact]
    public async Task Revocation_Sticks_Even_When_The_Provider_Cannot_Be_Reached()
    {
        await ConnectAsync("ana@example.test", "at-1", "rt-1");
        var unreachable = new CredentialBroker(
            _store, new UnreachableProvider(), _ => Provider, NullLogger.Instance, () => _now);

        await unreachable.RevokeAsync(Account("ana@example.test"));

        var result = await unreachable.LeaseAsync(Account("ana@example.test"), []);
        result.Failure.Should().Be(CredentialLeaseFailure.Revoked,
            "a token that still works here because a network call failed is the "
            + "opposite of revoked");
    }

    [Fact]
    public async Task Disconnecting_Forgets_The_Account_Entirely()
    {
        await ConnectAsync("ana@example.test", "at-1", "rt-1");

        await _broker.DisconnectAsync(Account("ana@example.test"));

        _store.Get(Account("ana@example.test")).Should().BeNull();
        _secrets.Values.Should().BeEmpty();
    }

    // ── Multi-account ───────────────────────────────────────────────────────

    [Fact]
    public async Task Two_Accounts_On_One_Connector_Stay_Separate()
    {
        await ConnectAsync("ana@example.test", "at-ana", "rt-ana");
        await ConnectAsync("bo@example.test", "at-bo", "rt-bo");

        var ana = await _broker.LeaseAsync(Account("ana@example.test"), []);
        var bo = await _broker.LeaseAsync(Account("bo@example.test"), []);

        ana.Lease!.HeaderValue.Should().Be("Bearer at-ana");
        bo.Lease!.HeaderValue.Should().Be("Bearer at-bo");
        _broker.ListAccounts("mailer").Should().HaveCount(2);
    }

    [Fact]
    public async Task Revoking_One_Account_Leaves_The_Other_Working()
    {
        await ConnectAsync("ana@example.test", "at-ana", "rt-ana");
        await ConnectAsync("bo@example.test", "at-bo", "rt-bo");

        await _broker.RevokeAsync(Account("ana@example.test"));

        (await _broker.LeaseAsync(Account("ana@example.test"), [])).Granted.Should().BeFalse();
        (await _broker.LeaseAsync(Account("bo@example.test"), [])).Granted.Should().BeTrue();
    }

    [Fact]
    public async Task The_Same_Account_Id_In_Two_Tenants_Is_Two_Credentials()
    {
        await ConnectAsync("shared@example.test", "at-acme", "rt-acme", tenant: "acme");
        await ConnectAsync("shared@example.test", "at-globex", "rt-globex", tenant: "globex");

        var acme = await _broker.LeaseAsync(
            new ConnectorAccountRef("mailer", "acme", "shared@example.test"), []);
        var globex = await _broker.LeaseAsync(
            new ConnectorAccountRef("mailer", "globex", "shared@example.test"), []);

        acme.Lease!.HeaderValue.Should().Be("Bearer at-acme");
        globex.Lease!.HeaderValue.Should().Be("Bearer at-globex");
        _broker.ListAccounts("mailer", "acme").Should().ContainSingle();
    }

    [Fact]
    public async Task Credentials_Survive_A_Restart()
    {
        await ConnectAsync("ana@example.test", "at-1", "rt-1");

        var reopened = new DpapiConnectorCredentialStore(
            _indexPath, _secrets, NullLogger.Instance);
        var broker = new CredentialBroker(
            reopened, _provider, _ => Provider, NullLogger.Instance, () => _now);
        var result = await broker.LeaseAsync(Account("ana@example.test"), ["mail.read"]);

        result.Granted.Should().BeTrue(result.Reason);
        result.Lease!.HeaderValue.Should().Be("Bearer at-1");
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private static ConnectorAccountRef Account(string accountId, string tenant = "local") =>
        new("mailer", tenant, accountId);

    private async Task<ConnectorCredentialDescriptor> ConnectAsync(
        string accountId,
        string accessToken,
        string? refreshToken = null,
        IReadOnlyList<string>? scopes = null,
        string tenant = "local")
    {
        // The store is seeded directly here, so the provider has to be told about
        // the same token — otherwise a refresh would be rejected as unknown and
        // the test would be measuring its own setup.
        if (refreshToken is { Length: > 0 })
            _provider.Seed(refreshToken, accountId, scopes ?? ["mail.read", "mail.send"]);
        return await _store.SaveAsync(
            new ConnectorCredentialDescriptor
            {
                Account = Account(accountId, tenant),
                DisplayName = accountId,
                Kind = ConnectorCredentialKind.OAuth2,
                Scopes = scopes ?? ["mail.read", "mail.send"],
                AccessTokenExpiresUtc = _now.Add(_provider.AccessTokenLifetime),
                State = ConnectorCredentialState.Active
            },
            accessToken,
            refreshToken);
    }

    private static string ExtractRefreshToken(string storedJson)
    {
        using var document = System.Text.Json.JsonDocument.Parse(storedJson);
        return document.RootElement.GetProperty("RefreshToken").GetString()!;
    }

    private sealed class UnreachableProvider : IOAuthTokenClient
    {
        public Task<OAuthTokenResult> ExchangeCodeAsync(
            OAuthProviderConfig provider, string code, string codeVerifier,
            string redirectUri, CancellationToken ct = default) =>
            throw new HttpRequestException("offline");

        public Task<OAuthTokenResult> RefreshAsync(
            OAuthProviderConfig provider, string refreshToken, CancellationToken ct = default) =>
            throw new HttpRequestException("offline");

        public Task RevokeAsync(
            OAuthProviderConfig provider, string? token, CancellationToken ct = default) =>
            throw new HttpRequestException("offline");
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

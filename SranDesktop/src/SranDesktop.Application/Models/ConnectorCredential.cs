using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

public enum ConnectorCredentialKind
{
    /// <summary>A key the user pasted. No expiry, no refresh.</summary>
    ApiKey,

    /// <summary>An OAuth 2.0 authorization-code grant with PKCE.</summary>
    OAuth2,

    /// <summary>The provider token lives on the backend; this desktop sends the
    /// portal JWT instead and never holds the provider's secret at all.</summary>
    Brokered
}

public enum ConnectorCredentialState
{
    Active,

    /// <summary>The access token is past its expiry. Recoverable if a refresh
    /// token exists; otherwise the user has to reconnect.</summary>
    Expired,

    /// <summary>A refresh was attempted and the provider refused. Not retried
    /// automatically — a provider that says no does not change its mind because
    /// we asked again.</summary>
    RefreshFailed,

    Revoked
}

/// <summary>
/// Which account, at which organisation, on which connector.
///
/// All three are needed because none of them is unique on its own: one person
/// can hold two accounts on the same connector, and the same account id can
/// exist in two tenants. A credential resolved by connector id alone is a
/// credential that can be used against the wrong account.
/// </summary>
public sealed record ConnectorAccountRef(
    string ConnectorId,
    string TenantId,
    string AccountId)
{
    /// <summary>Tenant used when the deployment has no organisation concept.</summary>
    public const string DefaultTenant = "local";

    public static ConnectorAccountRef For(string connectorId, string accountId) =>
        new(connectorId, DefaultTenant, accountId);

    /// <summary>Storage key. Ordered tenant-first so a listing by tenant is a
    /// prefix scan and a tenant boundary is a string comparison, not a join.</summary>
    [JsonIgnore]
    public string Key => $"{TenantId}|{ConnectorId}|{AccountId}";

    public override string ToString() => Key;
}

/// <summary>
/// Everything ABOUT a credential that is not the credential.
///
/// This is the half that may be persisted in ordinary storage, written to logs,
/// and shown in the UI: which account, which scopes, when it expires, whether a
/// refresh token exists. The secret material itself never appears in this type,
/// which is what makes "no raw token in SQLite" a property of the type system
/// rather than of everyone's care.
/// </summary>
public sealed record ConnectorCredentialDescriptor
{
    public required ConnectorAccountRef Account { get; init; }

    /// <summary>What the user recognises the account by — an email or handle the
    /// provider returned. Never a token.</summary>
    public string DisplayName { get; init; } = "";

    public ConnectorCredentialKind Kind { get; init; } = ConnectorCredentialKind.OAuth2;

    /// <summary>Scopes the provider actually granted, which is not necessarily
    /// what was requested.</summary>
    public IReadOnlyList<string> Scopes { get; init; } = [];

    public DateTimeOffset? AccessTokenExpiresUtc { get; init; }

    /// <summary>Whether a refresh is possible at all. The token itself is not
    /// here and never will be.</summary>
    public bool HasRefreshToken { get; init; }

    public ConnectorCredentialState State { get; init; } = ConnectorCredentialState.Active;
    public DateTimeOffset CreatedUtc { get; init; } = DateTimeOffset.UtcNow;
    public DateTimeOffset UpdatedUtc { get; init; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? RevokedUtc { get; init; }

    /// <summary>Why the credential is not usable, in words a person can act on.
    /// Redacted on the way in — a provider error body can contain the token that
    /// caused it.</summary>
    public string? LastError { get; init; }

    /// <summary>Treats a token that expires in the next 60 seconds as already
    /// expired: a lease handed out at the boundary would be refused by the time
    /// the request arrived.</summary>
    public bool IsExpired(DateTimeOffset now) =>
        AccessTokenExpiresUtc is { } expiry && expiry <= now.AddSeconds(60);

    public bool CoversScopes(IReadOnlyList<string> required) =>
        required.Count == 0
        || required.All(scope => Scopes.Contains(scope, StringComparer.OrdinalIgnoreCase));
}

public enum CredentialLeaseFailure
{
    None,
    NoAccount,
    Expired,
    RefreshFailed,
    Revoked,
    ScopeNotGranted,
    TenantMismatch,
    StoreUnavailable
}

/// <summary>
/// A credential, borrowed for the duration of one operation.
///
/// The caller never sees the token: it gets a header name and a header value it
/// can put on one request. That is deliberate — a lease that exposed the secret
/// as a string would be a secret that can be logged, serialised into a durable
/// event, or handed to a worker, and every one of those has happened to somebody.
/// </summary>
public interface IScopedCredentialLease : IDisposable
{
    ConnectorAccountRef Account { get; }
    IReadOnlyList<string> Scopes { get; }
    DateTimeOffset? ExpiresUtc { get; }

    /// <summary>HTTP header carrying the credential, e.g. "Authorization".</summary>
    string HeaderName { get; }

    /// <summary>The header's value, e.g. "Bearer …". Valid until disposal.</summary>
    string HeaderValue { get; }
}

public sealed record CredentialLeaseResult(
    IScopedCredentialLease? Lease,
    CredentialLeaseFailure Failure,
    string? Reason,
    ConnectorCredentialDescriptor? Descriptor)
{
    public bool Granted => Lease is not null;

    public static CredentialLeaseResult Ok(
        IScopedCredentialLease lease, ConnectorCredentialDescriptor descriptor) =>
        new(lease, CredentialLeaseFailure.None, null, descriptor);

    public static CredentialLeaseResult Denied(
        CredentialLeaseFailure failure,
        string reason,
        ConnectorCredentialDescriptor? descriptor = null) =>
        new(null, failure, reason, descriptor);
}

/// <summary>Where an OAuth connector's endpoints live and what it is allowed to
/// ask for. Deployment configuration, never anything a server told us.</summary>
public sealed record OAuthProviderConfig
{
    public required string ConnectorId { get; init; }
    public required string AuthorizationEndpoint { get; init; }
    public required string TokenEndpoint { get; init; }
    public string? RevocationEndpoint { get; init; }
    public required string ClientId { get; init; }

    /// <summary>Scopes this connector may request. A scope outside this list is
    /// an escalation attempt, whoever asked for it.</summary>
    public IReadOnlyList<string> AllowedScopes { get; init; } = [];

    public IReadOnlyList<string> DefaultScopes { get; init; } = [];

    /// <summary>Loopback redirect path. The port is chosen per attempt and the
    /// host is always 127.0.0.1 — a redirect that leaves this machine is a
    /// redirect that hands the code to somebody else.</summary>
    public string RedirectPath { get; init; } = "/oauth/callback";

    public string HeaderName { get; init; } = "Authorization";
    public string TokenPrefix { get; init; } = "Bearer";
}

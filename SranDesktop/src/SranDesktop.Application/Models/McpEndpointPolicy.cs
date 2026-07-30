namespace SranDesktop.Application.Models;

public sealed record McpEndpointVerdict(bool Allowed, string? RejectionCode, string? Reason)
{
    public static McpEndpointVerdict Allow { get; } = new(true, null, null);

    public static McpEndpointVerdict Refuse(string code, string reason) =>
        new(false, code, reason);
}

/// <summary>
/// Where a remote connector is allowed to be dialled.
///
/// This runs at launch, on the endpoint that is about to be used, rather than
/// once at install. That matters because the URL can change underneath an
/// approval — a catalog that is refreshed, a custom definition that is edited —
/// and the check that decides whether a credential leaves this machine should be
/// looking at the address the request is actually going to.
///
/// The rules are deliberately blunt. Anything that is not plain https to a named
/// host is refused, because every softer variant has a well-known way of being
/// turned into "the token went somewhere else": userinfo in the authority,
/// a redirect to a different scheme, a host that resolves to the loopback
/// interface of something that is not this process.
/// </summary>
public sealed record McpEndpointPolicy
{
    /// <summary>Plain http is accepted for loopback only. A developer running a
    /// server on their own machine has no TLS to offer and no network to be
    /// intercepted on; the same allowance for a remote host would be a
    /// credential sent in the clear.</summary>
    public bool AllowLoopbackHttp { get; init; } = true;

    /// <summary>When non-empty, only these hosts may be dialled at all. Empty
    /// means "any host, subject to the other rules" — the right default for a
    /// product with a user-extensible connector list, and the right thing for a
    /// managed deployment to narrow.</summary>
    public IReadOnlyList<string> AllowedHosts { get; init; } = [];

    /// <summary>Largest response this build will read from a connector.</summary>
    public int MaximumResponseBytes { get; init; } = 8 * 1024 * 1024;

    public static McpEndpointPolicy Default { get; } = new();

    public McpEndpointVerdict Evaluate(string? url)
    {
        if (string.IsNullOrWhiteSpace(url))
            return McpEndpointVerdict.Refuse(
                "EndpointMissing", "The connector has no server address.");

        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri))
            return McpEndpointVerdict.Refuse(
                "EndpointMalformed", $"'{url}' is not an absolute URL.");

        if (uri.Scheme is not ("http" or "https"))
            return McpEndpointVerdict.Refuse(
                "EndpointSchemeNotAllowed",
                $"'{uri.Scheme}' is not a transport this build will dial. Only http "
                + "(loopback) and https are.");

        // Credentials in the authority are a way to make a URL look like one host
        // while authenticating as — or redirecting to — another, and they end up
        // in every log that records a URL.
        if (!string.IsNullOrEmpty(uri.UserInfo))
            return McpEndpointVerdict.Refuse(
                "EndpointCarriesCredentials",
                "The server address embeds credentials. Connect the account instead.");

        if (uri.Scheme == "http" && !(AllowLoopbackHttp && uri.IsLoopback))
            return McpEndpointVerdict.Refuse(
                "EndpointNotEncrypted",
                uri.IsLoopback
                    ? "Plain http is not permitted, even on this machine, by policy."
                    : $"'{uri.Host}' would be dialled without TLS. A connector "
                      + "credential must not travel in the clear.");

        if (AllowedHosts.Count > 0
            && !AllowedHosts.Contains(uri.Host, StringComparer.OrdinalIgnoreCase))
            return McpEndpointVerdict.Refuse(
                "EndpointHostNotAllowed",
                $"'{uri.Host}' is not in this deployment's list of permitted connector hosts.");

        return McpEndpointVerdict.Allow;
    }
}

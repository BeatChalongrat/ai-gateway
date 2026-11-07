using System.Text.RegularExpressions;

namespace SranDesktop.Application.Services;

/// <summary>Parsed ``sran://oauth/callback`` payload. The deep link carries
/// ONLY the connector id and a coarse status/reason — never tokens.</summary>
public sealed record OAuthCallbackResult(string ConnectorId, bool Success, string? Reason);

/// <summary>
/// Strict parser for the OAuth deep link (Phase 7 C2/C6). Any process can
/// launch ``sran://…`` at us (the URI scheme is registered system-wide), so
/// every field is validated against a tight shape and everything else is
/// rejected: wrong scheme/host/path, oversized input, malformed query,
/// hostile connector ids. Pure — unit-testable without WPF.
/// </summary>
public static class OAuthDeepLink
{
    public const int MaxUriLength = 2048;

    private static readonly Regex ConnectorIdRegex =
        new("^[a-z0-9][a-z0-9_-]{0,63}$", RegexOptions.Compiled);
    private static readonly Regex ReasonRegex =
        new("^[a-z0-9_]{1,64}$", RegexOptions.Compiled);

    public static bool TryParse(string? uri, out OAuthCallbackResult? result)
    {
        result = null;
        if (string.IsNullOrWhiteSpace(uri) || uri.Length > MaxUriLength)
            return false;
        if (!Uri.TryCreate(uri, UriKind.Absolute, out var parsed))
            return false;
        if (!parsed.Scheme.Equals("sran", StringComparison.OrdinalIgnoreCase))
            return false;
        if (!parsed.Authority.Equals("oauth", StringComparison.OrdinalIgnoreCase))
            return false;
        if (!parsed.AbsolutePath.Equals("/callback", StringComparison.OrdinalIgnoreCase))
            return false;

        var query = ParseQuery(parsed.Query);
        if (!query.TryGetValue("connector", out var connector)
            || !ConnectorIdRegex.IsMatch(connector))
            return false;
        if (!query.TryGetValue("status", out var status)
            || (status != "ok" && status != "error"))
            return false;

        string? reason = null;
        if (query.TryGetValue("reason", out var rawReason)
            && ReasonRegex.IsMatch(rawReason))
            reason = rawReason;

        result = new OAuthCallbackResult(connector, status == "ok", reason);
        return true;
    }

    private static readonly Regex LoginTicketRegex =
        new("^[a-f0-9]{32,128}$", RegexOptions.Compiled);

    /// <summary>
    /// SSO Phase 2: parse the LOGIN variant of the callback —
    /// ``sran://oauth/callback?ticket=&lt;hex&gt;``. Same strict shape rules as
    /// <see cref="TryParse"/>; the two variants are disjoint (a login link
    /// has ``ticket`` and no ``connector``), so both dispatchers can be
    /// offered every deep link and exactly one will claim it. The ticket
    /// is an opaque one-shot handle — the tokens never ride the URI.
    /// </summary>
    public static bool TryParseLoginTicket(string? uri, out string? ticket)
    {
        ticket = null;
        if (string.IsNullOrWhiteSpace(uri) || uri.Length > MaxUriLength)
            return false;
        if (!Uri.TryCreate(uri, UriKind.Absolute, out var parsed))
            return false;
        if (!parsed.Scheme.Equals("sran", StringComparison.OrdinalIgnoreCase))
            return false;
        if (!parsed.Authority.Equals("oauth", StringComparison.OrdinalIgnoreCase))
            return false;
        if (!parsed.AbsolutePath.Equals("/callback", StringComparison.OrdinalIgnoreCase))
            return false;

        var query = ParseQuery(parsed.Query);
        if (query.ContainsKey("connector"))
            return false; // connector variant — not ours
        if (!query.TryGetValue("ticket", out var raw)
            || !LoginTicketRegex.IsMatch(raw))
            return false;

        ticket = raw;
        return true;
    }

    private static Dictionary<string, string> ParseQuery(string query)
    {
        var map = new Dictionary<string, string>(StringComparer.Ordinal);
        if (string.IsNullOrEmpty(query))
            return map;
        foreach (var pair in query.TrimStart('?').Split('&'))
        {
            var idx = pair.IndexOf('=');
            if (idx <= 0)
                continue;
            var key = Uri.UnescapeDataString(pair[..idx]);
            var value = Uri.UnescapeDataString(pair[(idx + 1)..]);
            map.TryAdd(key, value); // first occurrence wins — no param smuggling
        }
        return map;
    }
}

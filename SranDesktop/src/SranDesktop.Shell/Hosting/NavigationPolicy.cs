namespace SranDesktop.Shell.Hosting;

/// <summary>
/// C-5 (DESKTOP_SHELL_PLAN.md §4): the allow-list every top-level and frame
/// navigation is checked against. Pure — no WebView2 types — so the decision
/// is unit-testable without a browser.
///
/// Origin match is exact: scheme + host + port, case-insensitive on host.
/// Frames additionally get <c>about:blank</c> / <c>about:srcdoc</c>, which is
/// what a sandboxed artifact iframe is (spike finding 6) — its <em>content</em>
/// is same-origin-served or inline, and its <em>reach</em> is bounded by C-1,
/// not by this list.
///
/// Phase 2 additions:
///   * Top-level <c>about:blank</c> is allowed. It is inert (no network, no
///     scriptable origin) and it is how the shell blanks the authenticated DOM
///     on sign-out. The page could navigate itself there too — a self-DoS the
///     shell recovers from with its own navigation, not a policy hole.
///   * The Portal origin is known here but stays OFF the allow-list — it is
///     recognised (<see cref="IsPortalNavigation"/>) so the window can turn a
///     refused portal navigation into a host re-auth signal (§2.2). The
///     constructor throws if the portal origin is ALSO allow-listed, because
///     that combination would let the WebView load the login web app, which is
///     exactly what host-owned sessions forbid.
/// </summary>
public sealed class NavigationPolicy
{
    private readonly HashSet<string> _origins;
    private readonly string? _portalOrigin;

    public NavigationPolicy(IEnumerable<string> allowedOrigins, string? portalOrigin = null)
    {
        _origins = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var raw in allowedOrigins)
        {
            if (string.IsNullOrWhiteSpace(raw))
                continue;
            _origins.Add(ParseOrigin(raw, "Shell:AllowedOrigins"));
        }
        if (_origins.Count == 0)
            throw new ArgumentException("Shell:AllowedOrigins must contain at least one origin.");

        if (!string.IsNullOrWhiteSpace(portalOrigin))
        {
            _portalOrigin = ParseOrigin(portalOrigin, "Shell:PortalOrigin");
            if (_origins.Contains(_portalOrigin))
                throw new ArgumentException(
                    $"Shell:PortalOrigin '{_portalOrigin}' must not also be in Shell:AllowedOrigins — " +
                    "the portal redirect is the re-auth signal, never a permitted navigation (plan §2.2).");
        }
    }

    private static string ParseOrigin(string raw, string settingName)
    {
        if (raw.Contains('*'))
            throw new ArgumentException($"Wildcards are not allowed in {settingName} (got '{raw}').");
        if (!Uri.TryCreate(raw.Trim(), UriKind.Absolute, out var uri))
            throw new ArgumentException($"{settingName} entry is not an absolute URI: '{raw}'.");
        if (uri.Scheme is not ("http" or "https") || string.IsNullOrWhiteSpace(uri.Host))
            throw new ArgumentException($"{settingName} entry must be an http(s) origin: '{raw}'.");
        if (uri.AbsolutePath != "/" || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment)
            || !string.IsNullOrEmpty(uri.UserInfo))
            throw new ArgumentException($"{settingName} entry must be an origin, not a URL: '{raw}'.");
        return OriginOf(uri);
    }

    public IReadOnlyCollection<string> Origins => _origins;

    public string? PortalOrigin => _portalOrigin;

    /// <summary>Top-level navigation: the allow-list, plus inert about:blank
    /// (the shell's signed-out blank — see class summary).</summary>
    public bool IsTopLevelAllowed(string uri)
    {
        if (uri is "about:blank")
            return true;
        return Uri.TryCreate(uri, UriKind.Absolute, out var parsed)
               && parsed.Scheme is "http" or "https"
               && _origins.Contains(OriginOf(parsed));
    }

    /// <summary>Frame navigation: the allow-list plus the two inert about: forms.</summary>
    public bool IsFrameAllowed(string uri)
    {
        if (uri is "about:blank" or "about:srcdoc")
            return true;
        return IsTopLevelAllowed(uri);
    }

    /// <summary>Phase 2 §2.2: is this (already-refused) navigation the page
    /// trying to reach the Portal — i.e. the re-auth signal? Never makes a
    /// navigation allowed; only classifies a refusal.</summary>
    public bool IsPortalNavigation(string uri) =>
        _portalOrigin is not null
        && Uri.TryCreate(uri, UriKind.Absolute, out var parsed)
        && parsed.Scheme is "http" or "https"
        && string.Equals(OriginOf(parsed), _portalOrigin, StringComparison.OrdinalIgnoreCase);

    public static string OriginOf(Uri uri) =>
        uri.IsDefaultPort
            ? $"{uri.Scheme}://{uri.Host}"
            : $"{uri.Scheme}://{uri.Host}:{uri.Port}";
}

namespace SranDesktop.Shell.Hosting;

/// <summary>
/// Bound from the <c>Shell</c> section of appsettings.json. The page URL and
/// the navigation allow-list are configuration — not constants — because
/// packaging option B (DESKTOP_SHELL_PLAN.md §2.3) changes both. A hard-coded
/// origin here would be a Phase-6 problem created in Phase 1.
/// </summary>
public sealed class ShellOptions
{
    public const string SectionName = "Shell";

    /// <summary>Where the shell navigates on startup.</summary>
    public string PageUrl { get; set; } = "";

    /// <summary>Exact origins (<c>scheme://host[:port]</c>) a top-level or
    /// frame navigation may target. No wildcards — pinned by ShellPinningTests.</summary>
    public List<string> AllowedOrigins { get; set; } = new();

    /// <summary>Phase 2 §2.2: the Portal origin. Deliberately NOT on the
    /// allow-list — a navigation towards it is the page saying "I have no
    /// session", and the shell answers it with a host-side re-auth instead of
    /// letting the WebView load a login web app. NavigationPolicy throws if
    /// this origin also appears in <see cref="AllowedOrigins"/>.</summary>
    public string PortalOrigin { get; set; } = "";

    /// <summary>Host refresh cadence, minutes. Must be strictly shorter than
    /// the portal access-token TTL (60 min in every current environment).
    /// Default 45: a 15-minute margin absorbs timer latency and short system
    /// sleeps, and it differs from the page's former 50-minute cadence so any
    /// 50-minute-spaced refresh in server logs would expose a page-side
    /// refresher that should not exist in hosted mode.</summary>
    public int SessionRefreshMinutes { get; set; } = 45;

    /// <summary>How long an interactive <c>sran://</c> sign-in may wait for
    /// its deep-link ticket before the shell falls back to the signed-out
    /// view. The browser flow involves a human; five minutes is generous
    /// without leaving a boot hung forever.</summary>
    public int SignInTimeoutSeconds { get; set; } = 300;

    /// <summary>Per-shell WebView2 user-data folder. Defaults to
    /// %LOCALAPPDATA%\SRAN Desktop\Shell\WebView2 — deliberately NOT the WPF
    /// app's shared four-surface jar (%LOCALAPPDATA%\SRAN Desktop\WebView2).</summary>
    public string? UserDataFolder { get; set; }

    public string ResolvedUserDataFolder =>
        Path.GetFullPath(string.IsNullOrWhiteSpace(UserDataFolder)
            ? Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "SRAN Desktop", "Shell", "WebView2")
            : UserDataFolder);

    public static string SharedWpfUserDataFolder => Path.GetFullPath(Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SRAN Desktop", "WebView2"));

    /// <summary>The shell and WPF profile directories must not contain one
    /// another. WebView2 owns the whole user-data root, not just one child.</summary>
    public bool UserDataFolderOverlapsWpfProfile()
    {
        var shell = ResolvedUserDataFolder.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var wpf = SharedWpfUserDataFolder.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return IsSameOrDescendant(shell, wpf) || IsSameOrDescendant(wpf, shell);
    }

    private static bool IsSameOrDescendant(string candidate, string parent)
    {
        if (string.Equals(candidate, parent, StringComparison.OrdinalIgnoreCase))
            return true;
        var relative = Path.GetRelativePath(parent, candidate);
        return relative != ".."
               && !relative.StartsWith($"..{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
               && !Path.IsPathRooted(relative);
    }
}

using System.Reflection;

namespace SranDesktop.Application.Services;

/// <summary>
/// Reads the build stamp (UTC build time + git short hash) that SranDesktop.App.csproj
/// embeds into AssemblyInformationalVersion (T5 PART B). Surfaced in the UI and logged at
/// startup so it is visible-by-eye which binary is actually running — a publish does NOT
/// restart the process, so a stale instance would otherwise look identical to a fresh one.
/// </summary>
public static class BuildStamp
{
    /// <summary>The running app's build stamp, e.g. "build 2026-07-23 15:50 · abc1234".
    /// Falls back gracefully when the informational version has no embedded stamp.</summary>
    public static string Current { get; } = Describe(
        (Assembly.GetEntryAssembly() ?? Assembly.GetExecutingAssembly())
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion);

    /// <summary>Parses a "&lt;version&gt;+build.&lt;yyyy-MM-dd HH:mm&gt;.&lt;hash&gt;"
    /// informational version into a compact human label. Pure + testable.</summary>
    public static string Describe(string? informationalVersion)
    {
        if (string.IsNullOrWhiteSpace(informationalVersion))
            return "build unknown";

        var plus = informationalVersion.IndexOf("+build.", StringComparison.Ordinal);
        if (plus < 0)
            return $"build {informationalVersion}";

        var stamp = informationalVersion[(plus + "+build.".Length)..];
        // stamp = "<yyyy-MM-dd HH:mm>.<hash>" — the timestamp itself contains no dot, so
        // the FIRST dot after it separates the git hash.
        var lastDot = stamp.LastIndexOf('.');
        if (lastDot <= 0)
            return $"build {stamp}";
        var timestamp = stamp[..lastDot];
        var hash = stamp[(lastDot + 1)..];
        return hash.Length == 0
            ? $"build {timestamp}"
            : $"build {timestamp} · {hash}";
    }
}

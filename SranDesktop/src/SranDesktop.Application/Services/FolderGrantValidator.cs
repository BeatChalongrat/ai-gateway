namespace SranDesktop.Application.Services;

public enum FolderGrantRejection { Ok, NotAbsolute, Invalid, NotADirectory, DriveRoot, SystemFolder }

/// <summary>
/// Pure rules for which folders the user may grant the agent's file tools
/// (Phase 6 Task 1). Shared by the Infrastructure PathGuard (defence in depth
/// against a hand-edited settings file) and the Settings UI (block at add
/// time), so the two can never disagree. A grant only widens WHERE tools may
/// operate — it never bypasses consent.
/// </summary>
public static class FolderGrantValidator
{
    /// <summary>The app's own roaming state dir (%APPDATA%\SRAN Desktop:
    /// data.db, credentials.dat, settings.json, connectors.json, projects.json,
    /// tasks.db, skills\, logs\) — the agent must never read or edit its own
    /// permissions/credentials, so it is never grantable and hard-denied by
    /// the Infrastructure PathGuard (Phase 10 D1).</summary>
    public static string AppStateDir { get; } = System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "SRAN Desktop");

    /// <summary>The app's local state dir (%LOCALAPPDATA%\SRAN Desktop:
    /// WebView2 browser profile, updates\) — same protection as
    /// <see cref="AppStateDir"/> (Phase 10 D1).</summary>
    public static string LocalAppStateDir { get; } = System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SRAN Desktop");

    /// <summary>Validates + canonicalizes a candidate folder. Refuses drive
    /// roots, the Windows/Program Files/System trees, and the app state dir;
    /// requires an existing, fully-qualified directory.</summary>
    public static FolderGrantRejection Validate(string? folder, out string? canonical)
    {
        canonical = null;
        if (string.IsNullOrWhiteSpace(folder) || !System.IO.Path.IsPathFullyQualified(folder))
            return FolderGrantRejection.NotAbsolute;

        string full;
        try
        {
            full = System.IO.Path.TrimEndingDirectorySeparator(System.IO.Path.GetFullPath(folder));
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return FolderGrantRejection.Invalid;
        }

        if (!System.IO.Directory.Exists(full))
            return FolderGrantRejection.NotADirectory;

        // A filesystem root (C:\, D:\) has no parent — granting exposes the whole drive.
        if (System.IO.Path.GetPathRoot(full) is { } root
            && full.Equals(System.IO.Path.TrimEndingDirectorySeparator(root), StringComparison.OrdinalIgnoreCase))
            return FolderGrantRejection.DriveRoot;

        foreach (var forbidden in ForbiddenRoots())
        {
            if (IsWithin(full, forbidden))
                return FolderGrantRejection.SystemFolder;
        }

        canonical = full;
        return FolderGrantRejection.Ok;
    }

    private static bool IsWithin(string fullPath, string root)
    {
        var normalizedRoot = System.IO.Path.TrimEndingDirectorySeparator(root);
        return fullPath.Equals(normalizedRoot, StringComparison.OrdinalIgnoreCase)
            || fullPath.StartsWith(normalizedRoot + System.IO.Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }

    private static IEnumerable<string> ForbiddenRoots()
    {
        yield return AppStateDir;
        yield return LocalAppStateDir;
        foreach (var special in new[]
                 {
                     Environment.SpecialFolder.Windows,
                     Environment.SpecialFolder.ProgramFiles,
                     Environment.SpecialFolder.ProgramFilesX86,
                     Environment.SpecialFolder.System,
                 })
        {
            var path = Environment.GetFolderPath(special);
            if (!string.IsNullOrEmpty(path))
                yield return System.IO.Path.TrimEndingDirectorySeparator(path);
        }
    }
}

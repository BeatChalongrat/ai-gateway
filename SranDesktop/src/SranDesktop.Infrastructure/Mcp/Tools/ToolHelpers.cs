using System.Text.Json;

namespace SranDesktop.Infrastructure.Mcp.Tools;

internal static class ToolHelpers
{
    /// <summary>Parse a JSON schema once; Clone() detaches from the document so it outlives disposal.</summary>
    public static JsonElement Schema(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    public static string? GetString(JsonElement args, string name) =>
        args.ValueKind == JsonValueKind.Object
        && args.TryGetProperty(name, out var el)
        && el.ValueKind == JsonValueKind.String
            ? el.GetString()
            : null;

    public static bool GetBool(JsonElement args, string name, bool fallback = false) =>
        args.ValueKind == JsonValueKind.Object
        && args.TryGetProperty(name, out var el)
        && el.ValueKind is JsonValueKind.True or JsonValueKind.False
            ? el.GetBoolean()
            : fallback;

    // FocusWindowForInputAsync moved verbatim to SranDesktop.Infrastructure.Windows
    // (Mcp/Tools/WindowFocus.cs) — the one Win32-bound method this class had.

    /// <summary>FIX-G: before a tool overwrites an EXISTING file, snapshot the
    /// previous content next to it as "&lt;file&gt;.sran.bak" (latest one only —
    /// enough to undo the last overwrite; not a version history). Returns the
    /// backup path, or null when nothing existed (new file = nothing to lose).
    /// The backup lands in the same directory, so it can never escape the
    /// PathGuard root that already admitted the write itself.</summary>
    /// <summary>Suffix of the internal undo snapshot — shared so list_directory
    /// can filter it out (it is not user content).</summary>
    public const string BackupSuffix = ".sran.bak";

    public static string? BackupBeforeOverwrite(string fullPath)
    {
        if (!File.Exists(fullPath))
            return null;
        var backup = fullPath + BackupSuffix;
        File.Copy(fullPath, backup, overwrite: true);
        // Hide it so it doesn't clutter Explorer / the user's folder view. It
        // stays on disk for one-step undo, and the tool result names the path.
        try { File.SetAttributes(backup, File.GetAttributes(backup) | FileAttributes.Hidden); }
        catch (Exception) { /* attribute set is best-effort — non-fatal */ }
        return backup;
    }

    public static string FormatSize(long bytes) => bytes switch
    {
        < 1024 => $"{bytes} B",
        < 1024 * 1024 => $"{bytes / 1024.0:0.#} KB",
        < 1024L * 1024 * 1024 => $"{bytes / (1024.0 * 1024):0.#} MB",
        _ => $"{bytes / (1024.0 * 1024 * 1024):0.##} GB"
    };
}

/// <summary>
/// Confines file-system tools to safe roots. Two built-in roots (the user
/// profile and the temp directory) are always allowed; the user can widen
/// reach by granting additional folders in Settings (Phase 6 Task 1) — those
/// are validated by <see cref="IsGrantableFolder"/> and applied via
/// <see cref="ConfigureGrantedRoots"/>. Paths are canonicalized first so ..\
/// traversal can't escape any root. A grant widens WHERE tools may operate; it
/// does NOT bypass consent — write_file / run_command still Ask per the
/// PermissionService scopes.
/// </summary>
public static class PathGuard
{
    private static readonly string[] BuiltInRoots =
    {
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        Path.GetTempPath()
    };

    // Phase 10 D1: the app's own state subtrees are HARD-DENIED. They live
    // UNDER the user-profile built-in root, and read_file is consent-free —
    // without this, model output could read data.db (every conversation),
    // settings.json, connectors.json and credentials.dat with zero prompts.
    // Checked BEFORE any allow-root (built-in or granted) so neither a grant
    // nor ..\ normalization can ever reach inside.
    private static readonly string[] DeniedStateRoots = BuildDeniedStateRoots();

    private static string[] BuildDeniedStateRoots()
    {
        var roots = new List<string>();
        foreach (var dir in new[]
                 {
                     Application.Services.FolderGrantValidator.AppStateDir,
                     Application.Services.FolderGrantValidator.LocalAppStateDir,
                 })
        {
            // Skip degenerate values (special folder resolved to "") so a
            // relative fragment can never be compared against real paths.
            if (!string.IsNullOrEmpty(dir) && Path.IsPathFullyQualified(dir))
                roots.Add(Path.TrimEndingDirectorySeparator(dir));
        }
        return roots.ToArray();
    }

    /// <summary>True when the (already canonical) path is inside the app's own
    /// state subtrees (%APPDATA%\SRAN Desktop, %LOCALAPPDATA%\SRAN Desktop).
    /// Used by <see cref="Resolve"/> as a hard deny, and by enumerating tools
    /// (list_directory / search_files) to skip protected entries a recursive
    /// walk of an allowed parent would otherwise surface.</summary>
    public static bool IsProtectedStatePath(string fullPath)
    {
        foreach (var root in DeniedStateRoots)
        {
            if (IsWithin(fullPath, root))
                return true;
        }
        foreach (var root in _extraDeniedRoots)
        {
            if (IsWithin(fullPath, root))
                return true;
        }
        return false;
    }

    // E4: a host that keeps its state somewhere other than the WPF app's
    // folders (the Electron sidecar's data dir) adds it here. ADDITIVE only —
    // nothing can ever remove the built-in denied roots.
    private static volatile string[] _extraDeniedRoots = Array.Empty<string>();

    public static void ConfigureDeniedRoots(IEnumerable<string> roots)
    {
        var applied = new List<string>();
        foreach (var root in roots)
        {
            if (!string.IsNullOrWhiteSpace(root) && Path.IsPathFullyQualified(root))
                applied.Add(Path.TrimEndingDirectorySeparator(Path.GetFullPath(root)));
        }
        _extraDeniedRoots = applied.ToArray();
    }

    // Replaced wholesale by ConfigureGrantedRoots; the reference swap is atomic
    // so concurrent Resolve calls always see a consistent snapshot.
    private static volatile string[] _grantedRoots = Array.Empty<string>();

    /// <summary>Validates + canonicalizes user-granted folders (via the shared
    /// Application-layer <see cref="Application.Services.FolderGrantValidator"/>)
    /// and swaps them in. Invalid/forbidden entries are skipped — defence in
    /// depth for a hand-edited settings file (the UI blocks them at add time).
    /// Returns the canonicalized roots actually applied.</summary>
    public static IReadOnlyList<string> ConfigureGrantedRoots(IReadOnlyList<string> folders)
    {
        var applied = new List<string>();
        foreach (var folder in folders)
        {
            if (Application.Services.FolderGrantValidator.Validate(folder, out var canonical)
                    == Application.Services.FolderGrantRejection.Ok)
                applied.Add(canonical!);
        }
        _grantedRoots = applied.ToArray();
        return applied;
    }

    /// <summary>Canonicalizes and validates. Returns the full path, or null with an error message.</summary>
    public static string? Resolve(string? path, out string? error)
    {
        error = null;
        if (string.IsNullOrWhiteSpace(path))
        {
            error = "Path is required.";
            return null;
        }

        string fullPath;
        try
        {
            // Bare relative paths resolve against the user profile, not the app's cwd.
            fullPath = Path.IsPathFullyQualified(path)
                ? Path.GetFullPath(path)
                : Path.GetFullPath(Path.Combine(BuiltInRoots[0], path));
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            error = $"Invalid path: {ex.Message}";
            return null;
        }

        // Hard deny BEFORE any allow-root: the app's own state is never
        // reachable, no matter which roots are configured (Phase 10 D1).
        if (IsProtectedStatePath(fullPath))
        {
            error = $"Access denied: '{fullPath}' is inside SRAN Desktop's own application data, " +
                    "which tools may never access.";
            return null;
        }

        foreach (var root in BuiltInRoots)
        {
            if (IsWithin(fullPath, root))
                return fullPath;
        }
        foreach (var root in _grantedRoots)
        {
            if (IsWithin(fullPath, root))
                return fullPath;
        }

        error = $"Access denied: '{fullPath}' is outside the allowed directories " +
                "(your user profile, the temp folder, and folders you've granted in Settings).";
        return null;
    }

    /// <summary>True when the path lies inside a folder the user EXPLICITLY
    /// chose (granted folders + the composer's working folder). The built-in
    /// user-profile/temp roots do NOT count — FIX-F uses this to decide
    /// whether a silent read (read_file / list_directory / search_files) has
    /// left the user's chosen scope and must ask first.</summary>
    public static bool IsWithinUserScope(string fullPath)
    {
        foreach (var root in _grantedRoots)
        {
            if (IsWithin(fullPath, Path.TrimEndingDirectorySeparator(root)))
                return true;
        }
        return false;
    }

    /// <summary>
    /// The allowed/granted root that contains <paramref name="fullPath"/>, most
    /// specific first (a granted project folder wins over the broad user-profile
    /// root). Used ONLY to key folder-scoped consent (PART 4) — it makes no
    /// access decision; <see cref="Resolve"/> remains the gate. Returns null
    /// when the path is inside no configured root.
    /// </summary>
    public static string? ContainingRoot(string fullPath)
    {
        string? best = null;
        foreach (var root in _grantedRoots.Concat(BuiltInRoots))
        {
            var normalized = Path.TrimEndingDirectorySeparator(root);
            if (IsWithin(fullPath, normalized) && (best is null || normalized.Length > best.Length))
                best = normalized;
        }
        return best;
    }

    /// <summary>True when <paramref name="fullPath"/> is the root itself or a descendant.</summary>
    private static bool IsWithin(string fullPath, string root)
    {
        var normalizedRoot = Path.TrimEndingDirectorySeparator(root);
        return fullPath.Equals(normalizedRoot, StringComparison.OrdinalIgnoreCase)
            || fullPath.StartsWith(normalizedRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }
}

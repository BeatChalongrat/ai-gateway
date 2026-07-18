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

    /// <summary>Brings a window to the foreground before keyboard injection and
    /// VERIFIES it — typing into the wrong window is the worst computer-use
    /// failure mode. Returns an error message, or null when focused.</summary>
    public static async Task<string?> FocusWindowForInputAsync(string windowTitle, CancellationToken ct)
    {
        var hWnd = Native.Win32Input.FindWindowByTitle(windowTitle);
        if (hWnd == IntPtr.Zero)
            return $"No visible window with a title containing '{windowTitle}'.";

        var focused = await Task.Run(() => Native.Win32Input.ForceForegroundWindow(hWnd), ct);
        await Task.Delay(150, ct); // let the activation settle before keystrokes
        if (!focused || Native.Win32Input.GetForegroundWindow() != hWnd)
            return $"Could not bring '{windowTitle}' to the foreground — it may belong to an " +
                   "elevated (administrator) process, which cannot receive our input. " +
                   "Input was NOT sent.";
        return null;
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
        return false;
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

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

    /// <summary>FIX29 §3.3: backups no longer sit beside the user's file. They
    /// live in one hidden, bounded folder under the app's local data
    /// (%LOCALAPPDATA%\SranDesktop\backups), named by a hash of the original
    /// path plus the file name, and the folder is pruned to the newest
    /// <see cref="BackupKeep"/> entries on every write. The undo path is still
    /// returned in the tool result.</summary>
    public const int BackupKeep = 50;

    public static string BackupRoot => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SranDesktop", "backups");

    public static string BackupPathFor(string fullPath)
    {
        var hash = Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(
            System.Text.Encoding.UTF8.GetBytes(fullPath.ToLowerInvariant())))[..12].ToLowerInvariant();
        return Path.Combine(BackupRoot, hash + "_" + Path.GetFileName(fullPath) + BackupSuffix);
    }

    private static void PruneBackups()
    {
        try
        {
            var files = new DirectoryInfo(BackupRoot).GetFiles("*" + BackupSuffix)
                .OrderByDescending(f => f.LastWriteTimeUtc).Skip(BackupKeep);
            foreach (var f in files) { try { f.Delete(); } catch (Exception) { /* best-effort */ } }
        }
        catch (Exception) { /* best-effort */ }
    }

    public static string? BackupBeforeOverwrite(string fullPath)
    {
        if (!File.Exists(fullPath))
            return null;
        Directory.CreateDirectory(BackupRoot);
        try { File.SetAttributes(BackupRoot, File.GetAttributes(BackupRoot) | FileAttributes.Hidden); } catch (Exception) { /* best-effort */ }
        var backup = BackupPathFor(fullPath);
        // FIX26 (found by the Part 3 test run): File.Copy(overwrite: true) onto
        // a HIDDEN file throws UnauthorizedAccessException on Windows, so the
        // SECOND overwrite of the same file failed once the first backup had
        // been hidden below. Drop the previous snapshot first.
        if (File.Exists(backup))
        {
            try { File.SetAttributes(backup, FileAttributes.Normal); } catch (Exception) { /* best-effort */ }
            File.Delete(backup);
        }
        File.Copy(fullPath, backup, overwrite: true);
        // Hide it so it doesn't clutter Explorer / the user's folder view. It
        // stays on disk for one-step undo, and the tool result names the path.
        try { File.SetAttributes(backup, File.GetAttributes(backup) | FileAttributes.Hidden); }
        catch (Exception) { /* attribute set is best-effort — non-fatal */ }
        PruneBackups();
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
    // FIX26 Part 3 — the fence is the GRANTED folders. The user profile is no
    // longer an implicit root: with it, every file tool treated
    // C:\Users\<user> as "the granted folder" (FIX24 B2/B3/B5 wrote there,
    // G2 wrote into Documents\MuMuSharedFolder) and list_directory printed
    // .ssh/.gnupg/.docker into the chat. The ONE built-in root that remains
    // is the temp folder, which the app itself uses to stage files (uploads,
    // generated documents) — a deliberate, named exception, not a grant.
    private static readonly string[] BuiltInRoots =
    {
        Path.GetTempPath()
    };

    private static readonly string UserProfileRoot =
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

    /// <summary>The folder an unqualified / relative path resolves into and the
    /// default working directory for shell commands: the FIRST granted folder
    /// (grants are applied in the order the user granted them). Null when
    /// nothing is granted — callers must refuse, never fall back to the
    /// profile.</summary>
    public static string? DefaultRoot()
    {
        var roots = _grantedRoots;
        return roots.Length > 0 ? roots[0] : null;
    }

    /// <summary>The current granted roots (canonical), for messages and prompts.</summary>
    public static IReadOnlyList<string> GrantedRoots() => _grantedRoots;

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
            if (Path.IsPathFullyQualified(path))
            {
                fullPath = Path.GetFullPath(path);
            }
            else
            {
                // FIX26 Part 3: a bare relative path resolves INTO a granted
                // folder (the first one), never the profile or the app's cwd.
                var root = DefaultRoot();
                if (root is null)
                {
                    error = NoGrantMessage(path);
                    return null;
                }
                fullPath = Path.GetFullPath(Path.Combine(root, path));
            }
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

        error = OutsideFenceMessage(fullPath);
        return null;
    }

    // ── FIX26 Part 3: refusals that say what WOULD allow the action ─────

    private static string GrantedList()
    {
        var roots = _grantedRoots;
        return roots.Length == 0
            ? "(no folder is granted yet)"
            : string.Join("; ", roots);
    }

    private static string NoGrantMessage(string requested)
    {
        return $"Access denied: '{requested}' is a relative path and no folder has been granted yet, " +
               "so there is nowhere to resolve it into. Ask the user to add a folder from the folder " +
               "chip in the composer (or Settings → Folder access), then use a path inside it.";
    }

    /// <summary>The message for a path outside every granted root. Names the
    /// folder the user would need to grant — the nearest existing directory of
    /// the path — and NEVER suggests granting a system folder (Windows, Program
    /// Files, ProgramData, the app's own data): those say so and stop.</summary>
    public static string OutsideFenceMessage(string fullPath)
    {
        var candidate = Directory.Exists(fullPath) ? fullPath : Path.GetDirectoryName(fullPath);
        while (!string.IsNullOrEmpty(candidate) && !Directory.Exists(candidate))
            candidate = Path.GetDirectoryName(candidate);

        var grantedList = GrantedList();
        if (string.IsNullOrEmpty(candidate))
        {
            return $"Access denied: '{fullPath}' is outside every granted folder. Granted: {grantedList}. " +
                   "Use a path inside a granted folder, or ask the user to grant one from the composer's folder chip.";
        }

        var verdict = Application.Services.FolderGrantValidator.Validate(candidate, out var canonical);
        switch (verdict)
        {
            case Application.Services.FolderGrantRejection.SystemFolder:
                return $"Access denied: '{fullPath}' is inside a system folder ({candidate}), which can never be granted. " +
                       $"Do not ask the user to grant it. Granted: {grantedList}. Use a path inside a granted folder instead.";
            case Application.Services.FolderGrantRejection.DriveRoot:
                return $"Access denied: '{fullPath}' sits directly under a drive root, which cannot be granted. " +
                       $"Granted: {grantedList}. Use a path inside a granted folder, or ask the user to grant a specific folder.";
            case Application.Services.FolderGrantRejection.Ok:
                if (IsWithin(canonical!, UserProfileRoot) && canonical!.Equals(Path.TrimEndingDirectorySeparator(UserProfileRoot), StringComparison.OrdinalIgnoreCase))
                {
                    return $"Access denied: '{fullPath}' is in the user's home folder, which is not granted as a whole. " +
                           $"Granted: {grantedList}. Use a path inside a granted folder, or ask the user to grant a specific " +
                           "sub-folder (for example a project folder) from the composer's folder chip — not the whole home folder.";
                }
                return $"Access denied: '{fullPath}' is outside every granted folder. Granted: {grantedList}. " +
                       $"To allow this, the user would need to grant '{canonical}' from the folder chip in the composer " +
                       "(or Settings → Folder access); otherwise use a path inside a granted folder.";
            default:
                return $"Access denied: '{fullPath}' is outside every granted folder. Granted: {grantedList}. " +
                       "Use a path inside a granted folder, or ask the user to grant one from the composer's folder chip.";
        }
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

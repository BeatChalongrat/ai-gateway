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
    private static readonly string UserProfile =
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

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

    // The composer's working folder (when set). Bare relative paths resolve against
    // this instead of the user profile, so "save a.txt" lands in the folder the user
    // pointed the agent at — not silently in the home directory (the real-run bug).
    // Null means relative paths fail closed.
    private static volatile string? _workingDirectory;

    // The words users and models say for their standard shell folders, mapped to the
    // real special-folder location (which may be redirected, e.g. OneNote/OneDrive,
    // away from %USERPROFILE%\Desktop). Applied only to a LEADING path segment.
    private static readonly Dictionary<string, Environment.SpecialFolder> KnownUserFolders =
        new(StringComparer.OrdinalIgnoreCase)
        {
            ["desktop"] = Environment.SpecialFolder.Desktop,
            ["documents"] = Environment.SpecialFolder.MyDocuments,
            ["my documents"] = Environment.SpecialFolder.MyDocuments,
            ["pictures"] = Environment.SpecialFolder.MyPictures,
        };

    /// <summary>Sets the working folder that bare relative paths resolve against.
    /// Called from app startup and on every settings change, mirroring
    /// <see cref="ConfigureGrantedRoots"/>. An empty/invalid value clears it (relative
    /// paths then fail closed).</summary>
    public static void ConfigureWorkingDirectory(string? folder)
    {
        if (string.IsNullOrWhiteSpace(folder))
        {
            _workingDirectory = null;
            WorkspaceFileBroker.Shared.SetWorkingDirectory(null);
            return;
        }
        try
        {
            _workingDirectory = Path.IsPathFullyQualified(folder)
                ? Path.TrimEndingDirectorySeparator(Path.GetFullPath(folder))
                : null;
            WorkspaceFileBroker.Shared.SetWorkingDirectory(_workingDirectory);
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            _workingDirectory = null;
            WorkspaceFileBroker.Shared.SetWorkingDirectory(null);
        }
    }

    /// <summary>Expands a leading "Desktop"/"Documents"/"Pictures" segment, or the
    /// literal token "working folder"/"working directory", to a real folder path.
    /// Fully-qualified paths and unrecognized inputs pass through unchanged. Pure —
    /// makes no access decision; <see cref="Resolve"/> still gates the result.</summary>
    public static string ExpandUserFolders(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || Path.IsPathFullyQualified(path))
            return path;
        var trimmed = path.Trim();

        // The user's/model's phrase for "put it in the working folder".
        if (trimmed.Equals("working folder", StringComparison.OrdinalIgnoreCase)
            || trimmed.Equals("working directory", StringComparison.OrdinalIgnoreCase)
            || trimmed == ".")
            return _workingDirectory ?? UserProfile;

        var separator = trimmed.IndexOfAny(new[] { '/', '\\' });
        var head = separator < 0 ? trimmed : trimmed[..separator];
        var rest = separator < 0 ? string.Empty : trimmed[(separator + 1)..].TrimStart('/', '\\');
        if (!KnownUserFolders.TryGetValue(head, out var special))
            return path;
        var baseDir = Environment.GetFolderPath(special);
        if (string.IsNullOrEmpty(baseDir))
            return path;
        return rest.Length == 0 ? baseDir : Path.Combine(baseDir, rest);
    }

    /// <summary>
    /// Validates + canonicalizes user-granted folders (via the shared
    /// Application-layer <see cref="Application.Services.FolderGrantValidator"/>)
    /// and swaps them in. Invalid/forbidden entries are skipped — defence in
    /// depth for a hand-edited settings file (the UI blocks them at add time).
    /// Returns the canonicalized roots actually applied.
    ///
    /// <para><b>Access levels differ by role (P3-2).</b> The working folder is
    /// the one directory the user pointed the agent at for this session, through
    /// the composer, on purpose. It gets <see cref="WorkspaceMountAccess.ReadWrite"/>.
    /// Other granted folders stay <see cref="WorkspaceMountAccess.ReadWriteNoDelete"/>:
    /// they are standing grants that may have been added long ago for one task,
    /// and a rename there is worth refusing.</para>
    ///
    /// <para>Why the working folder had to change: <c>NoDelete</c> also blocks
    /// rename, because a rename consumes the source name. That made
    /// <c>move_file</c> impossible in every folder the agent could see — the tool
    /// was registered but could never succeed. Worse, the sandbox lane's
    /// write-back step deletes files the command removed, so any sandboxed
    /// command that touched a temporary file ended in
    /// <c>SandboxWorkspaceSyncFailed</c> after running correctly. Granting
    /// "no delete" over a folder the agent can already overwrite byte-for-byte
    /// prevented neither data loss nor escape; it only broke two features.</para>
    ///
    /// <para><paramref name="workingFolder"/> is passed explicitly rather than
    /// read from previously-stored state so the result does not depend on
    /// whether <see cref="ConfigureWorkingDirectory"/> happened to run first.
    /// Omitting it keeps whatever working folder is already configured.</para>
    /// </summary>
    public static IReadOnlyList<string> ConfigureGrantedRoots(
        IReadOnlyList<string> folders,
        string? workingFolder = null)
    {
        if (workingFolder is not null)
            ConfigureWorkingDirectory(workingFolder);

        // Canonicalized through the same validator as the grants so the identity
        // comparison below cannot be defeated by casing, a trailing separator,
        // or a short (8.3) path spelling of the same directory.
        string? canonicalWorking = null;
        if (!string.IsNullOrWhiteSpace(workingFolder)
            && Application.Services.FolderGrantValidator.Validate(
                   workingFolder, out var validatedWorking)
               == Application.Services.FolderGrantRejection.Ok)
            canonicalWorking = validatedWorking;

        var applied = new List<string>();
        foreach (var folder in folders)
        {
            if (Application.Services.FolderGrantValidator.Validate(folder, out var canonical)
                    == Application.Services.FolderGrantRejection.Ok)
                applied.Add(canonical!);
        }
        _grantedRoots = applied.ToArray();
        WorkspaceFileBroker.Shared.Configure(
            applied.Select(root => new WorkspaceMount(root, AccessFor(root, canonicalWorking))),
            _workingDirectory);
        return applied;
    }

    /// <summary>The working folder is writable including rename/delete; every
    /// other granted root is not. Kept separate so the rule reads as one line at
    /// the call site.</summary>
    private static WorkspaceMountAccess AccessFor(string root, string? canonicalWorkingFolder) =>
        canonicalWorkingFolder is not null
        && Path.TrimEndingDirectorySeparator(root).Equals(
               Path.TrimEndingDirectorySeparator(canonicalWorkingFolder),
               StringComparison.OrdinalIgnoreCase)
            ? WorkspaceMountAccess.ReadWrite
            : WorkspaceMountAccess.ReadWriteNoDelete;

    /// <summary>Configures explicit mount capabilities without adding implicit
    /// host roots. Invalid mounts are omitted and an empty set fails closed.</summary>
    public static IReadOnlyList<WorkspaceMount> ConfigureMounts(
        IReadOnlyList<WorkspaceMount> mounts,
        string? workingDirectory = null)
    {
        _workingDirectory = workingDirectory;
        var configured = WorkspaceFileBroker.Shared.Configure(mounts, workingDirectory);
        _grantedRoots = configured.Select(static mount => mount.RootPath).ToArray();
        return configured;
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

        // Map "Desktop"/"Documents"/"working folder" tokens to real folders first, so
        // the model can name a destination the way a user says it.
        path = ExpandUserFolders(path);

        string fullPath;
        try
        {
            // Bare relative paths resolve against the working folder when one is set
            // (else the user profile) — not the app's cwd.
            fullPath = Path.IsPathFullyQualified(path)
                ? Path.GetFullPath(path)
                : _workingDirectory is null
                    ? throw new ArgumentException(
                        "Relative paths require a working folder. " + NoWorkspaceRemedy)
                    : Path.GetFullPath(Path.Combine(_workingDirectory, path));
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

        try
        {
            return WorkspaceFileBroker.Shared.ResolveForScope(
                fullPath,
                WorkspaceFileOperation.Read);
        }
        catch (Exception ex) when (ex is IOException
                                   or UnauthorizedAccessException
                                   or ArgumentException
                                   or NotSupportedException)
        {
            // "Outside the allowed directories" is misleading when there are no
            // allowed directories at all — it reads as "wrong folder" when the
            // real state is "none configured yet", which sends the caller
            // retrying other paths instead of asking the user to pick a folder.
            error = _grantedRoots.Length == 0
                ? $"No working folder is configured, so '{fullPath}' cannot be reached. "
                  + NoWorkspaceRemedy
                : $"Access denied: '{fullPath}' is outside the allowed directories. {ex.Message}";
            return null;
        }
    }

    /// <summary>The one sentence that turns "this failed" into "here is what to
    /// do about it". Shared by every no-workspace error so the agent relays the
    /// same instruction however it hit the wall.</summary>
    internal const string NoWorkspaceRemedy =
        "Ask the user to pick a working folder using the folder button next to the "
        + "message box (it is labelled with the folder name once chosen). File tools "
        + "reach only folders the user has granted, so none are available until then. "
        + "Do not retry other paths — no path will work.";

    /// <summary>
    /// Canonicalizes a directory that a HOST-LANE shell command will merely start
    /// in. Enforces the app's own state as off-limits and requires the directory
    /// to exist, but does NOT require it to be inside an authorized mount.
    ///
    /// <para><b>Only for the host lane, and only for the starting directory.</b>
    /// Every file tool, and the sandbox lane, must keep using
    /// <see cref="Resolve"/>, where the mount IS the boundary.</para>
    ///
    /// <para>What replaces the mount check here: in the host lane the command runs
    /// with the signed-in user's full rights, so the process can reach the whole
    /// machine no matter which directory it starts in. Refusing to start it in an
    /// unlisted folder therefore constrains nothing — it only rejected commands
    /// that touch no files at all (<c>docker ps</c>, <c>git --version</c>,
    /// <c>ipconfig</c>). The gates that do constrain this lane sit upstream and
    /// still apply: the user must have opted into host mode, the blocklist runs,
    /// the consent dialog shows the exact command line, and every dispatch is
    /// written to the security audit log.</para>
    ///
    /// <para>This method exists to close a seam (P3-3): the host lane already
    /// defaulted to <c>%USERPROFILE%</c> when no directory was given, while naming
    /// that very same directory explicitly was rejected. One directory, two
    /// answers depending on whether the caller mentioned it — a defect, not a
    /// policy. Both paths now run through here.</para>
    /// </summary>
    public static string? ResolveStartingDirectory(string? path, out string? error)
    {
        error = null;
        if (string.IsNullOrWhiteSpace(path))
        {
            error = "Path is required.";
            return null;
        }

        path = ExpandUserFolders(path);

        string fullPath;
        try
        {
            // Relative starting directories resolve against the working folder if
            // there is one, else the profile. Unlike Resolve, this cannot fail
            // closed on a missing working folder: the profile is always a valid
            // place for a host command to start.
            fullPath = Path.IsPathFullyQualified(path)
                ? Path.GetFullPath(path)
                : Path.GetFullPath(Path.Combine(_workingDirectory ?? UserProfile, path));
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            error = $"Invalid path: {ex.Message}";
            return null;
        }

        // Unchanged from Resolve, and deliberately: the app's own state stays
        // unreachable in every lane. A shell that starts inside credentials.dat's
        // directory is not a starting directory, it is an exfiltration setup.
        if (IsProtectedStatePath(fullPath))
        {
            error = $"Access denied: '{fullPath}' is inside SRAN Desktop's own application data, " +
                    "which tools may never access.";
            return null;
        }

        if (!Directory.Exists(fullPath))
        {
            error = $"Working directory not found: {fullPath}";
            return null;
        }

        return fullPath;
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
        return WorkspaceFileBroker.Shared.ContainingRoot(fullPath);
    }

    /// <summary>True when <paramref name="fullPath"/> is the root itself or a descendant.</summary>
    private static bool IsWithin(string fullPath, string root)
    {
        var relative = Path.GetRelativePath(
            Path.TrimEndingDirectorySeparator(root),
            Path.GetFullPath(fullPath));
        if (relative == ".")
            return true;
        if (Path.IsPathRooted(relative))
            return false;
        var first = relative.Split(
            [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar],
            StringSplitOptions.RemoveEmptyEntries).FirstOrDefault();
        return !string.Equals(first, "..", StringComparison.Ordinal);
    }
}

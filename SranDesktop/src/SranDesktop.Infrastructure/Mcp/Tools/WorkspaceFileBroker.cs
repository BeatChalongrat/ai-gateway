using System.ComponentModel;
using System.IO.Enumeration;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public enum WorkspaceMountAccess
{
    ReadOnly,
    ReadWrite,
    ReadWriteNoDelete
}

public enum WorkspaceFileOperation
{
    Read,
    Enumerate,
    Write,
    Delete
}

public sealed record WorkspaceMount(string RootPath, WorkspaceMountAccess Access);

public sealed record WorkspaceFileSnapshot(
    string FullPath,
    string Name,
    byte[] Bytes,
    string Sha256);

public sealed record WorkspaceWriteReceipt(
    string FullPath,
    long Length,
    string Sha256);

public sealed record WorkspaceEntry(
    string FullPath,
    string RelativePath,
    bool IsDirectory,
    long Length);

public sealed record WorkspaceSearchMatch(
    string FullPath,
    int? LineNumber,
    string? Line);

public sealed class WorkspaceAccessDeniedException(string message) : IOException(message);

/// <summary>
/// The only filesystem authority used by agent file tools.
///
/// Security invariants:
/// 1. No tool may access a filesystem target outside an authorized mount.
/// 2. Paths are resolved through Windows reparse points before authorization.
/// 3. An authorization failure never falls back to unrestricted host I/O.
///
/// Existing targets are authorized from their final handle path. New targets
/// are authorized while the nearest existing parent is held without delete
/// sharing, then the opened target handle is checked again before data is
/// written. This removes the check-then-open gap for file reads and writes and
/// substantially narrows it for directory creation and rename operations.
/// </summary>
public interface IWorkspaceFileBroker
{
    Task<WorkspaceFileSnapshot> ReadFileAsync(string requestedPath, long maxBytes, CancellationToken ct);
    Task<WorkspaceWriteReceipt> WriteTextAsync(string requestedPath, string content, CancellationToken ct);
    Task<WorkspaceWriteReceipt> WriteBytesAsync(string requestedPath, byte[] content, CancellationToken ct);
    Task<WorkspaceWriteReceipt> ReplaceTextAsync(
        string requestedPath,
        string oldText,
        string newText,
        bool replaceAll,
        long maxBytes,
        CancellationToken ct);
    Task CreateDirectoryAsync(string requestedPath, CancellationToken ct);
    Task DeleteAsync(string requestedPath, CancellationToken ct);
    Task MoveAsync(string sourcePath, string destinationPath, bool overwrite, CancellationToken ct);
    Task<IReadOnlyList<WorkspaceEntry>> ListAsync(
        string requestedDirectory,
        bool recursive,
        int maxEntries,
        CancellationToken ct);
    Task<IReadOnlyList<WorkspaceSearchMatch>> SearchAsync(
        string requestedDirectory,
        string pattern,
        Regex? contentPattern,
        int maxMatches,
        long maxGrepFileBytes,
        CancellationToken ct);
    string ResolveForScope(string requestedPath, WorkspaceFileOperation operation);
    string? ContainingRoot(string fullPath);
}

public sealed class WorkspaceFileBroker : IWorkspaceFileBroker
{
    private const uint FileReadAttributes = 0x0080;
    private const uint GenericRead = 0x80000000;
    private const uint GenericWrite = 0x40000000;
    private const uint OpenExisting = 3;
    private const uint FileFlagBackupSemantics = 0x02000000;
    private const uint FileShareRead = 0x00000001;
    private const uint FileShareWrite = 0x00000002;
    private const int FinalPathNameNormalized = 0x0;
    private const int MaxReparseDepth = 63;

    private readonly SemaphoreSlim _operationGate = new(1, 1);
    private readonly bool _sandboxOwned;
    private volatile MountState[] _mounts = Array.Empty<MountState>();
    private volatile string? _workingDirectory;

    public static WorkspaceFileBroker Shared { get; } = new();

    public WorkspaceFileBroker()
    {
    }

    public WorkspaceFileBroker(IEnumerable<WorkspaceMount> mounts, string? workingDirectory = null)
    {
        Configure(mounts, workingDirectory);
    }

    /// <summary>
    /// A broker over a directory the SANDBOX owns rather than a folder the user
    /// granted.
    ///
    /// The distinction matters because <see cref="Configure"/> drops any mount
    /// inside the app's own state tree, which is right for user grants — the
    /// agent must not be handed the trust file or the credential index — and
    /// wrong for the per-connector scratch directory the launcher just created
    /// under the connector root. With the default connector root living at
    /// %LOCALAPPDATA%\SRAN Desktop\connectors, that filter silently removed the
    /// only mount, and every local connector failed to launch with "the working
    /// directory is not inside an authorized workspace mount". Tests never saw
    /// it because they all pointed the connector root at a temp directory.
    ///
    /// This constructor waives ONLY the state-tree filter, and only for a
    /// caller that supplies the directory itself. Existence and the handle
    /// checks still apply, the AppContainer still sees nothing else, and the
    /// agent-facing <see cref="Shared"/> broker is untouched.
    /// </summary>
    public static WorkspaceFileBroker ForSandboxWorkspace(
        string workspace, WorkspaceMountAccess access = WorkspaceMountAccess.ReadWrite)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(workspace);
        var broker = new WorkspaceFileBroker(sandboxOwned: true);
        broker.Configure([new WorkspaceMount(workspace, access)], workspace);
        return broker;
    }

    private WorkspaceFileBroker(bool sandboxOwned) => _sandboxOwned = sandboxOwned;

    public IReadOnlyList<WorkspaceMount> Configure(
        IEnumerable<WorkspaceMount> mounts,
        string? workingDirectory = null)
    {
        EnsureWindows();
        var configured = new List<MountState>();
        foreach (var mount in mounts)
        {
            try
            {
                var lexical = NormalizeAbsolutePath(mount.RootPath);
                if (!Directory.Exists(lexical))
                    continue;
                // The state-tree filter guards USER grants. A sandbox-owned
                // workspace is the launcher's own scratch directory and is
                // exempt — see ForSandboxWorkspace.
                if (!_sandboxOwned && PathGuard.IsProtectedStatePath(lexical))
                    continue;

                using var handle = OpenDirectoryHandle(lexical);
                var finalPath = GetFinalPath(handle);
                var identity = GetIdentity(handle);
                configured.Add(new MountState(
                    new WorkspaceMount(lexical, mount.Access),
                    finalPath,
                    identity));
            }
            catch (Exception ex) when (ex is IOException
                                       or UnauthorizedAccessException
                                       or ArgumentException
                                       or NotSupportedException
                                       or Win32Exception)
            {
                // Invalid mounts are omitted. An empty result is fail closed.
            }
        }

        _workingDirectory = string.IsNullOrWhiteSpace(workingDirectory)
            ? null
            : NormalizeAbsolutePath(workingDirectory);
        _mounts = configured
            .OrderByDescending(static mount => mount.FinalRoot.Length)
            .ToArray();
        return configured.Select(static state => state.Mount).ToArray();
    }

    public void SetWorkingDirectory(string? workingDirectory)
    {
        _workingDirectory = string.IsNullOrWhiteSpace(workingDirectory)
            ? null
            : NormalizeAbsolutePath(workingDirectory);
    }

    public string ResolveForScope(string requestedPath, WorkspaceFileOperation operation)
    {
        using var authorization = Authorize(requestedPath, operation, allowMissingTarget: true);
        return authorization.LexicalPath;
    }

    public string? ContainingRoot(string fullPath)
    {
        string lexical;
        try
        {
            lexical = NormalizeAbsolutePath(fullPath);
        }
        catch
        {
            return null;
        }

        return _mounts
            .Where(mount => IsWithin(lexical, mount.Mount.RootPath))
            .OrderByDescending(mount => mount.Mount.RootPath.Length)
            .Select(mount => mount.Mount.RootPath)
            .FirstOrDefault();
    }

    public async Task<WorkspaceFileSnapshot> ReadFileAsync(
        string requestedPath,
        long maxBytes,
        CancellationToken ct)
    {
        await _operationGate.WaitAsync(ct);
        try
        {
            var lexical = NormalizeRequestedPath(requestedPath);
            await using var stream = new FileStream(
                lexical,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read,
                64 * 1024,
                FileOptions.Asynchronous | FileOptions.SequentialScan);
            AuthorizeOpenedHandle(lexical, stream.SafeFileHandle, WorkspaceFileOperation.Read);
            if (stream.Length > maxBytes)
                throw new IOException($"File too large: {ToolHelpers.FormatSize(stream.Length)} " +
                                      $"(max {ToolHelpers.FormatSize(maxBytes)}).");

            var bytes = new byte[stream.Length];
            await stream.ReadExactlyAsync(bytes, ct);
            return new WorkspaceFileSnapshot(
                lexical,
                Path.GetFileName(lexical),
                bytes,
                Convert.ToHexString(SHA256.HashData(bytes)));
        }
        finally
        {
            _operationGate.Release();
        }
    }

    public async Task<WorkspaceWriteReceipt> WriteTextAsync(
        string requestedPath,
        string content,
        CancellationToken ct) =>
        await WriteBytesAsync(
            requestedPath,
            new UTF8Encoding(encoderShouldEmitUTF8Identifier: false).GetBytes(content),
            ct);

    public async Task<WorkspaceWriteReceipt> WriteBytesAsync(
        string requestedPath,
        byte[] content,
        CancellationToken ct)
    {
        await _operationGate.WaitAsync(ct);
        try
        {
            var lexical = NormalizeRequestedPath(requestedPath);
            using var parentAuthorization = AuthorizeParentForCreate(
                lexical,
                WorkspaceFileOperation.Write);
            Directory.CreateDirectory(Path.GetDirectoryName(lexical)!);

            await using var stream = new FileStream(
                lexical,
                FileMode.OpenOrCreate,
                FileAccess.ReadWrite,
                FileShare.Read,
                64 * 1024,
                FileOptions.Asynchronous | FileOptions.WriteThrough);
            AuthorizeOpenedHandle(lexical, stream.SafeFileHandle, WorkspaceFileOperation.Write);

            stream.SetLength(0);
            await stream.WriteAsync(content, ct);
            await stream.FlushAsync(ct);

            stream.Position = 0;
            var readBack = new byte[stream.Length];
            await stream.ReadExactlyAsync(readBack, ct);
            if (!content.AsSpan().SequenceEqual(readBack))
                throw new IOException("File write read-back verification failed.");

            return new WorkspaceWriteReceipt(
                lexical,
                readBack.LongLength,
                Convert.ToHexString(SHA256.HashData(readBack)));
        }
        finally
        {
            _operationGate.Release();
        }
    }

    public async Task<WorkspaceWriteReceipt> ReplaceTextAsync(
        string requestedPath,
        string oldText,
        string newText,
        bool replaceAll,
        long maxBytes,
        CancellationToken ct)
    {
        var snapshot = await ReadFileAsync(requestedPath, maxBytes, ct);
        var content = Encoding.UTF8.GetString(snapshot.Bytes);
        var occurrences = CountOccurrences(content, oldText);
        if (occurrences == 0)
            throw new InvalidOperationException("The requested text was not found.");
        if (occurrences > 1 && !replaceAll)
            throw new InvalidOperationException(
                $"The requested text matched {occurrences} locations and is ambiguous.");

        var updated = replaceAll
            ? content.Replace(oldText, newText, StringComparison.Ordinal)
            : ReplaceFirst(content, oldText, newText);
        return await WriteTextAsync(requestedPath, updated, ct);
    }

    public async Task CreateDirectoryAsync(string requestedPath, CancellationToken ct)
    {
        await _operationGate.WaitAsync(ct);
        try
        {
            var lexical = NormalizeRequestedPath(requestedPath);
            using var parentAuthorization = AuthorizeParentForCreate(
                lexical,
                WorkspaceFileOperation.Write);
            if (File.Exists(lexical))
                throw new IOException($"A file already exists at {lexical}.");
            Directory.CreateDirectory(lexical);
            using var createdHandle = OpenDirectoryHandle(lexical);
            AuthorizeOpenedHandle(lexical, createdHandle, WorkspaceFileOperation.Write);
        }
        finally
        {
            _operationGate.Release();
        }
    }

    public async Task DeleteAsync(string requestedPath, CancellationToken ct)
    {
        await _operationGate.WaitAsync(ct);
        try
        {
            var lexical = NormalizeRequestedPath(requestedPath);
            using (Authorize(lexical, WorkspaceFileOperation.Delete, allowMissingTarget: false))
            {
            }

            if (Directory.Exists(lexical))
                Directory.Delete(lexical, recursive: true);
            else if (File.Exists(lexical))
                File.Delete(lexical);
            else
                throw new FileNotFoundException($"Path not found: {lexical}");
        }
        finally
        {
            _operationGate.Release();
        }
    }

    public async Task MoveAsync(
        string sourcePath,
        string destinationPath,
        bool overwrite,
        CancellationToken ct)
    {
        await _operationGate.WaitAsync(ct);
        try
        {
            ct.ThrowIfCancellationRequested();
            var source = NormalizeRequestedPath(sourcePath);
            var destination = NormalizeRequestedPath(destinationPath);

            // Rename consumes the source name, so both mounts must permit delete.
            using (Authorize(source, WorkspaceFileOperation.Delete, allowMissingTarget: false))
            using (AuthorizeParentForCreate(destination, WorkspaceFileOperation.Write))
            {
                var parent = Path.GetDirectoryName(destination)!;
                Directory.CreateDirectory(parent);
                if (Directory.Exists(source))
                {
                    if (Directory.Exists(destination))
                        throw new IOException($"Destination directory already exists: {destination}");
                    Directory.Move(source, destination);
                }
                else if (File.Exists(source))
                {
                    File.Move(source, destination, overwrite);
                }
                else
                {
                    throw new FileNotFoundException($"Source not found: {source}");
                }
            }

            using var destinationAuthorization = Authorize(
                destination,
                WorkspaceFileOperation.Read,
                allowMissingTarget: false);
        }
        finally
        {
            _operationGate.Release();
        }
    }

    public async Task<IReadOnlyList<WorkspaceEntry>> ListAsync(
        string requestedDirectory,
        bool recursive,
        int maxEntries,
        CancellationToken ct)
    {
        await _operationGate.WaitAsync(ct);
        try
        {
            var root = NormalizeRequestedPath(requestedDirectory);
            using var rootAuthorization = Authorize(
                root,
                WorkspaceFileOperation.Enumerate,
                allowMissingTarget: false);
            if (!Directory.Exists(root))
                throw new DirectoryNotFoundException($"Directory not found: {root}");

            var results = new List<WorkspaceEntry>();
            var pending = new Stack<string>();
            pending.Push(root);
            while (pending.Count > 0 && results.Count < maxEntries)
            {
                ct.ThrowIfCancellationRequested();
                var current = pending.Pop();
                using var currentAuthorization = Authorize(
                    current,
                    WorkspaceFileOperation.Enumerate,
                    allowMissingTarget: false);

                foreach (var entry in Directory.EnumerateFileSystemEntries(current))
                {
                    ct.ThrowIfCancellationRequested();
                    if (PathGuard.IsProtectedStatePath(entry) || IsReparsePoint(entry))
                        continue;

                    using var entryAuthorization = Authorize(
                        entry,
                        WorkspaceFileOperation.Read,
                        allowMissingTarget: false);
                    var isDirectory = Directory.Exists(entry);
                    results.Add(new WorkspaceEntry(
                        entry,
                        Path.GetRelativePath(root, entry),
                        isDirectory,
                        isDirectory ? 0 : new FileInfo(entry).Length));
                    if (results.Count >= maxEntries)
                        break;
                    if (recursive && isDirectory)
                        pending.Push(entry);
                }
            }

            return results;
        }
        finally
        {
            _operationGate.Release();
        }
    }

    public async Task<IReadOnlyList<WorkspaceSearchMatch>> SearchAsync(
        string requestedDirectory,
        string pattern,
        Regex? contentPattern,
        int maxMatches,
        long maxGrepFileBytes,
        CancellationToken ct)
    {
        var entries = await ListAsync(
            requestedDirectory,
            recursive: true,
            maxEntries: Math.Max(maxMatches * 100, 10_000),
            ct);
        var matches = new List<WorkspaceSearchMatch>();
        foreach (var entry in entries.Where(static entry => !entry.IsDirectory))
        {
            ct.ThrowIfCancellationRequested();
            if (!FileSystemName.MatchesSimpleExpression(
                    pattern,
                    Path.GetFileName(entry.FullPath),
                    ignoreCase: true))
                continue;

            if (contentPattern is null)
            {
                matches.Add(new WorkspaceSearchMatch(entry.FullPath, null, null));
            }
            else if (entry.Length <= maxGrepFileBytes)
            {
                var snapshot = await ReadFileAsync(entry.FullPath, maxGrepFileBytes, ct);
                using var reader = new StringReader(Encoding.UTF8.GetString(snapshot.Bytes));
                var lineNumber = 0;
                while (await reader.ReadLineAsync(ct) is { } line)
                {
                    lineNumber++;
                    bool isMatch;
                    try
                    {
                        isMatch = contentPattern.IsMatch(line);
                    }
                    catch (RegexMatchTimeoutException)
                    {
                        continue;
                    }

                    if (isMatch)
                        matches.Add(new WorkspaceSearchMatch(entry.FullPath, lineNumber, line));
                    if (matches.Count >= maxMatches)
                        break;
                }
            }

            if (matches.Count >= maxMatches)
                break;
        }

        return matches;
    }

    private AuthorizationLease AuthorizeParentForCreate(
        string targetPath,
        WorkspaceFileOperation operation)
    {
        var existing = Path.GetDirectoryName(targetPath)
                       ?? throw new WorkspaceAccessDeniedException(
                           "Unable to determine the target parent.");
        var hops = 0;
        while (!File.Exists(existing) && !Directory.Exists(existing))
        {
            if (++hops > MaxReparseDepth)
                throw new WorkspaceAccessDeniedException("Path depth exceeds the security limit.");
            existing = Path.GetDirectoryName(existing)
                       ?? throw new WorkspaceAccessDeniedException(
                           "Unable to prove the target parent is inside an authorized mount.");
        }

        var lease = Authorize(existing, operation, allowMissingTarget: false);
        if (!Directory.Exists(existing))
        {
            lease.Dispose();
            throw new WorkspaceAccessDeniedException(
                "Unable to prove the target parent is a directory inside an authorized mount.");
        }

        return lease;
    }

    private AuthorizationLease Authorize(
        string requestedPath,
        WorkspaceFileOperation operation,
        bool allowMissingTarget)
    {
        EnsureWindows();
        var lexical = NormalizeRequestedPath(requestedPath);
        // Sandbox-owned brokers waive this: their whole mount is inside the app
        // state tree by construction (see ForSandboxWorkspace), so the mount
        // containment check below is what confines them, not this one.
        if (!_sandboxOwned && PathGuard.IsProtectedStatePath(lexical))
            throw new WorkspaceAccessDeniedException(
                "Access denied: the target is inside SRAN Desktop application data.");

        if (File.Exists(lexical))
        {
            var handle = OpenPathHandle(lexical, isDirectory: false);
            try
            {
                var mount = AuthorizeOpenedHandle(lexical, handle, operation);
                return new AuthorizationLease(lexical, GetFinalPath(handle), mount, handle);
            }
            catch
            {
                handle.Dispose();
                throw;
            }
        }

        if (Directory.Exists(lexical))
        {
            var handle = OpenDirectoryHandle(lexical);
            try
            {
                var mount = AuthorizeOpenedHandle(lexical, handle, operation);
                return new AuthorizationLease(lexical, GetFinalPath(handle), mount, handle);
            }
            catch
            {
                handle.Dispose();
                throw;
            }
        }

        if (!allowMissingTarget)
            throw new FileNotFoundException($"Path not found: {lexical}");

        using var parent = AuthorizeParentForCreate(lexical, operation);
        return new AuthorizationLease(lexical, lexical, parent.Mount, handle: null);
    }

    private MountState AuthorizeOpenedHandle(
        string lexicalPath,
        SafeFileHandle handle,
        WorkspaceFileOperation operation)
    {
        var finalPath = GetFinalPath(handle);
        foreach (var mount in _mounts)
        {
            if (!IsWithin(finalPath, mount.FinalRoot))
                continue;
            VerifyMountIdentity(mount);
            EnforceMountPermission(mount, operation);
            // Post-resolution check, so a symlink out of the workspace and into
            // app state is still refused. A sandbox-owned mount lives there
            // legitimately, and IsWithin above has already proved the resolved
            // target is inside that one workspace.
            if (!_sandboxOwned && PathGuard.IsProtectedStatePath(finalPath))
                throw new WorkspaceAccessDeniedException(
                    "Access denied: the final target is inside SRAN Desktop application data.");
            return mount;
        }

        throw new WorkspaceAccessDeniedException(
            $"Access denied: final target '{finalPath}' for '{lexicalPath}' is outside authorized workspace mounts.");
    }

    private static void EnforceMountPermission(MountState mount, WorkspaceFileOperation operation)
    {
        if (operation is WorkspaceFileOperation.Read or WorkspaceFileOperation.Enumerate)
            return;
        if (mount.Mount.Access == WorkspaceMountAccess.ReadOnly)
            throw new WorkspaceAccessDeniedException(
                $"Mount '{mount.Mount.RootPath}' is read-only.");
        if (operation == WorkspaceFileOperation.Delete
            && mount.Mount.Access == WorkspaceMountAccess.ReadWriteNoDelete)
            throw new WorkspaceAccessDeniedException(
                $"Mount '{mount.Mount.RootPath}' does not permit delete or rename operations.");
    }

    private static void VerifyMountIdentity(MountState mount)
    {
        using var current = OpenDirectoryHandle(mount.Mount.RootPath);
        var currentFinal = GetFinalPath(current);
        var currentIdentity = GetIdentity(current);
        if (!currentIdentity.Equals(mount.Identity)
            || !currentFinal.Equals(mount.FinalRoot, StringComparison.OrdinalIgnoreCase))
            throw new WorkspaceAccessDeniedException(
                $"Workspace mount '{mount.Mount.RootPath}' changed after authorization.");
    }

    private string NormalizeRequestedPath(string path)
    {
        if (string.IsNullOrWhiteSpace(path))
            throw new WorkspaceAccessDeniedException("Path is required.");
        var expanded = PathGuard.ExpandUserFolders(path);
        if (!Path.IsPathFullyQualified(expanded))
        {
            if (_workingDirectory is null)
                throw new WorkspaceAccessDeniedException(
                    "Relative paths are denied because no authorized working directory is configured.");
            expanded = Path.Combine(_workingDirectory, expanded);
        }

        return NormalizeAbsolutePath(expanded);
    }

    private static string NormalizeAbsolutePath(string path)
    {
        EnsureWindows();
        var trimmed = path.Trim();
        if (trimmed.StartsWith(@"\\", StringComparison.Ordinal)
            || trimmed.StartsWith(@"\\?\", StringComparison.Ordinal)
            || trimmed.StartsWith(@"\\.\", StringComparison.Ordinal)
            || trimmed.StartsWith(@"\??\", StringComparison.Ordinal))
            throw new WorkspaceAccessDeniedException(
                "UNC, network, device, and extended-syntax paths are not allowed.");

        if (!Path.IsPathFullyQualified(trimmed))
            throw new WorkspaceAccessDeniedException("An absolute local path is required.");
        if (trimmed.Length > 2 && trimmed.IndexOf(':', 2) >= 0)
            throw new WorkspaceAccessDeniedException(
                "NTFS alternate data streams are not allowed.");

        var full = Path.TrimEndingDirectorySeparator(Path.GetFullPath(trimmed));
        if (full.Length < 3 || full[1] != ':' || full[2] != Path.DirectorySeparatorChar)
            throw new WorkspaceAccessDeniedException("Only local drive paths are allowed.");
        return full;
    }

    private static SafeFileHandle OpenPathHandle(string path, bool isDirectory)
    {
        var flags = isDirectory ? FileFlagBackupSemantics : 0u;
        var handle = CreateFileW(
            path,
            FileReadAttributes,
            FileShareRead | FileShareWrite,
            IntPtr.Zero,
            OpenExisting,
            flags,
            IntPtr.Zero);
        if (handle.IsInvalid)
        {
            var error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error, $"Unable to open '{path}' for security validation.");
        }
        return handle;
    }

    private static SafeFileHandle OpenDirectoryHandle(string path) =>
        OpenPathHandle(path, isDirectory: true);

    private static string GetFinalPath(SafeFileHandle handle)
    {
        var capacity = 512;
        while (capacity <= 32_768)
        {
            var buffer = new StringBuilder(capacity);
            var length = GetFinalPathNameByHandleW(
                handle,
                buffer,
                (uint)buffer.Capacity,
                FinalPathNameNormalized);
            if (length == 0)
                throw new Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "Windows could not resolve the final path for an opened handle.");
            if (length < buffer.Capacity)
                return StripExtendedPrefix(buffer.ToString());
            capacity = checked((int)length + 1);
        }

        throw new WorkspaceAccessDeniedException("Resolved path exceeds the security limit.");
    }

    private static FileIdentity GetIdentity(SafeFileHandle handle)
    {
        if (!GetFileInformationByHandle(handle, out var info))
            throw new Win32Exception(
                Marshal.GetLastWin32Error(),
                "Windows could not read workspace file identity.");
        return new FileIdentity(
            info.VolumeSerialNumber,
            ((ulong)info.FileIndexHigh << 32) | info.FileIndexLow);
    }

    private static string StripExtendedPrefix(string path)
    {
        if (path.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
            return @"\\" + path[8..];
        return path.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)
            ? path[4..]
            : path;
    }

    private static bool IsWithin(string path, string root)
    {
        var relative = Path.GetRelativePath(
            Path.TrimEndingDirectorySeparator(root),
            Path.GetFullPath(path));
        if (relative == ".")
            return true;
        if (Path.IsPathRooted(relative))
            return false;
        var first = relative.Split(
            [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar],
            StringSplitOptions.RemoveEmptyEntries).FirstOrDefault();
        return !string.Equals(first, "..", StringComparison.Ordinal);
    }

    private static bool IsReparsePoint(string path) =>
        (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;

    private static int CountOccurrences(string haystack, string needle)
    {
        var count = 0;
        for (var index = 0;
             (index = haystack.IndexOf(needle, index, StringComparison.Ordinal)) >= 0;
             index += needle.Length)
            count++;
        return count;
    }

    private static string ReplaceFirst(string content, string oldText, string newText)
    {
        var index = content.IndexOf(oldText, StringComparison.Ordinal);
        return string.Concat(
            content.AsSpan(0, index),
            newText,
            content.AsSpan(index + oldText.Length));
    }

    private static void EnsureWindows()
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException(
                "The workspace security broker requires Windows handle-based path validation.");
    }

    private sealed record MountState(
        WorkspaceMount Mount,
        string FinalRoot,
        FileIdentity Identity);

    private readonly record struct FileIdentity(uint VolumeSerial, ulong FileIndex);

    private sealed class AuthorizationLease(
        string lexicalPath,
        string finalPath,
        MountState mount,
        SafeFileHandle? handle) : IDisposable
    {
        public string LexicalPath { get; } = lexicalPath;
        public string FinalPath { get; } = finalPath;
        public MountState Mount { get; } = mount;
        public void Dispose() => handle?.Dispose();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ByHandleFileInformation
    {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string lpFileName,
        uint dwDesiredAccess,
        uint dwShareMode,
        IntPtr lpSecurityAttributes,
        uint dwCreationDisposition,
        uint dwFlagsAndAttributes,
        IntPtr hTemplateFile);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandleW(
        SafeFileHandle hFile,
        StringBuilder lpszFilePath,
        uint cchFilePath,
        uint dwFlags);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle hFile,
        out ByHandleFileInformation lpFileInformation);
}

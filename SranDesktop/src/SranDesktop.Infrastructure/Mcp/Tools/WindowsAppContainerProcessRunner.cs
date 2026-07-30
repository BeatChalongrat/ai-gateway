using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public class SandboxExecutionException(string message, Exception? inner = null)
    : InvalidOperationException(message, inner);

public sealed class SandboxUnavailableException(string message, Exception? inner = null)
    : SandboxExecutionException(message, inner);

public sealed class SandboxOutputLimitException(string message)
    : SandboxExecutionException(message);

public sealed class SandboxWorkspaceSyncException(string message, Exception? inner = null)
    : SandboxExecutionException(message, inner);

internal sealed record SandboxJobPolicy(
    uint CpuControlFlags,
    uint CpuRate,
    uint LimitFlags,
    uint ActiveProcessLimit,
    ulong ProcessMemoryLimit,
    ulong JobMemoryLimit);

/// <summary>
/// Executes a deterministic system shell in a capability-free Windows
/// AppContainer and a kill-on-close Job Object. The worker receives a staged
/// copy of one authorized workspace mount, never the host workspace path.
///
/// Security invariants:
/// - No command executes in the UI/agent security context.
/// - Sandbox initialization failure never causes host execution fallback.
/// - The environment is allowlisted; host credentials are never inherited.
/// - Network is denied by the AppContainer's empty capability set.
/// - Every descendant belongs to a supervised Job Object and is terminated
///   when the execution ends, times out, or is cancelled.
/// </summary>
public sealed class WindowsAppContainerProcessRunner : IProcessRunner
{
    private const string ProfileName = "SRAN.Desktop.CommandSandbox.v1";
    private const int MaxWorkspaceFiles = 10_000;
    private const long MaxWorkspaceBytes = 100 * 1024 * 1024;
    private const long MaxSingleFileBytes = 25 * 1024 * 1024;
    private const long MaxOutputBytes = 2 * 1024 * 1024;
    private const uint CpuHardCapRate = 2_000; // 20.00 percent, expressed in 1/100 percent.
    private static readonly TimeSpan CpuControllerWarmup = TimeSpan.FromSeconds(2);
    private static readonly TimeSpan OrphanedJobGrace = TimeSpan.FromMinutes(1);
    private const string LiveMarkerName = ".sran-job-live";
    private static long _failedTemporaryCleanups;

    /// <summary>Job directories this process could not reclaim on the first
    /// pass. The soak gate asserts it stays at zero.</summary>
    public static long FailedTemporaryCleanups => Interlocked.Read(ref _failedTemporaryCleanups);

    private readonly IWorkspaceFileBroker _files;

    public WindowsAppContainerProcessRunner(IWorkspaceFileBroker? files = null) =>
        _files = files ?? WorkspaceFileBroker.Shared;

    public Task<int> RunAsync(
        ProcessRunSpec spec,
        Action<string> onLine,
        CancellationToken ct) =>
        RunCoreAsync(spec, onLine, worker: null, ct);

    /// <summary>
    /// Runs a first-party isolated worker (document, MCP) under the same
    /// AppContainer, Job Object, quota, and cleanup policy as run_command.
    /// The worker image is chosen by CAPABILITY, never by a caller- or
    /// model-supplied path, and its binaries are staged into the job's private
    /// folder — outside the workspace, so they are never synchronized back into
    /// the user's files.
    /// </summary>
    public Task<int> RunWorkerAsync(
        IsolatedWorkerLaunch worker,
        string workingDirectory,
        Action<string> onLine,
        CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(worker);
        return RunCoreAsync(
            new ProcessRunSpec(DotnetHostPath(), string.Empty, workingDirectory),
            onLine,
            worker,
            ct);
    }

    /// <summary>
    /// Starts a LONG-LIVED worker under the same AppContainer, Job Object and
    /// quota policy as <see cref="RunWorkerAsync"/>, but hands the caller an open
    /// stdin and live stdout/stderr streams instead of running to completion.
    ///
    /// This is what a request/response protocol (MCP stdio) needs: one process
    /// serving many correlated messages. The isolation is identical — the only
    /// difference is who closes the pipes and when.
    /// </summary>
    public async Task<InteractiveIsolatedProcess> StartInteractiveWorkerAsync(
        IsolatedWorkerLaunch worker,
        string workingDirectory,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(worker);
        if (!OperatingSystem.IsWindows())
            throw new SandboxUnavailableException("The Windows AppContainer sandbox is unavailable.");
        ct.ThrowIfCancellationRequested();

        var executable = ValidateDotnetHost(DotnetHostPath());
        if (_files.ContainingRoot(workingDirectory) is null)
            throw new SandboxUnavailableException(
                "The working directory is not inside an authorized workspace mount.");

        var profile = AcquireProfile();
        var profileFolder = GetProfileFolder(profile);
        var jobsRoot = Path.Combine(profileFolder, "LocalState", "SranJobs");
        var jobRoot = Path.Combine(jobsRoot, Guid.NewGuid().ToString("N"));
        SweepOrphanedJobDirectories(
            jobsRoot,
            Path.TrimEndingDirectorySeparator(Path.GetFullPath(profileFolder)));
        var stagedWorkspace = Path.Combine(jobRoot, "workspace");
        Directory.CreateDirectory(stagedWorkspace);
        var liveMarker = new FileStream(
            Path.Combine(jobRoot, LiveMarkerName),
            FileMode.Create,
            FileAccess.ReadWrite,
            FileShare.None,
            bufferSize: 1,
            FileOptions.DeleteOnClose);

        try
        {
            var stagedWorker = Path.Combine(jobRoot, "worker");
            StageWorkerImage(worker, stagedWorker);
            // The authorized mount is staged IN so the worker can read what it was
            // explicitly given. Nothing is synchronized back: a long-lived server
            // must not be able to rewrite the user's files as a side effect of
            // serving a message.
            await StageWorkspaceAsync(
                _files.ContainingRoot(workingDirectory)!, stagedWorkspace, ct);
            return LaunchInteractive(
                profile,
                executable,
                $"\"{Path.Combine(stagedWorker, worker.EntryAssemblyName)}\"",
                stagedWorkspace,
                jobRoot,
                profileFolder,
                liveMarker);
        }
        catch
        {
            liveMarker.Dispose();
            TryDeleteJobDirectory(jobRoot, profileFolder);
            profile.Dispose();
            throw;
        }
    }

    private static InteractiveIsolatedProcess LaunchInteractive(
        SafeSidHandle profile,
        string executable,
        string arguments,
        string workingDirectory,
        string jobRoot,
        string profileFolder,
        FileStream liveMarker)
    {
        var job = CreateConstrainedJob();
        var stdoutRead = CreateChildOutputPipe(out var stdoutWrite);
        var stderrRead = CreateChildOutputPipe(out var stderrWrite);
        var stdinWrite = CreateChildInputPipe(out var stdinRead);
        var startup = new StartupInfoEx();
        var securityCapabilitiesPtr = IntPtr.Zero;
        var environmentPtr = IntPtr.Zero;
        try
        {
            startup.StartupInfo.cb = Marshal.SizeOf<StartupInfoEx>();
            startup.StartupInfo.dwFlags = StartfUseStdHandles;
            startup.StartupInfo.hStdInput = stdinRead.DangerousGetHandle();
            startup.StartupInfo.hStdOutput = stdoutWrite.DangerousGetHandle();
            startup.StartupInfo.hStdError = stderrWrite.DangerousGetHandle();

            var attributeSize = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref attributeSize);
            startup.AttributeList = Marshal.AllocHGlobal(attributeSize);
            if (!InitializeProcThreadAttributeList(startup.AttributeList, 1, 0, ref attributeSize))
                throw LastWin32("Unable to initialize sandbox process attributes.");

            var capabilities = new SecurityCapabilities
            {
                AppContainerSid = profile.DangerousGetHandle()
            };
            securityCapabilitiesPtr = Marshal.AllocHGlobal(Marshal.SizeOf<SecurityCapabilities>());
            Marshal.StructureToPtr(capabilities, securityCapabilitiesPtr, false);
            if (!UpdateProcThreadAttribute(
                    startup.AttributeList,
                    0,
                    ProcThreadAttributeSecurityCapabilities,
                    securityCapabilitiesPtr,
                    (IntPtr)Marshal.SizeOf<SecurityCapabilities>(),
                    IntPtr.Zero,
                    IntPtr.Zero))
                throw LastWin32("Unable to apply AppContainer capabilities.");

            environmentPtr = Marshal.StringToHGlobalUni(BuildEnvironment(jobRoot));
            var commandLine = new StringBuilder(
                $"\"{executable}\" {arguments}",
                executable.Length + arguments.Length + 8);
            if (!CreateProcessW(
                    executable,
                    commandLine,
                    IntPtr.Zero,
                    IntPtr.Zero,
                    true,
                    ExtendedStartupInfoPresent
                    | CreateUnicodeEnvironment
                    | CreateSuspended
                    | CreateNoWindow,
                    environmentPtr,
                    workingDirectory,
                    ref startup,
                    out var processInfo))
                throw LastWin32("Unable to start the AppContainer worker.");

            var processHandle = new SafeProcessHandle(processInfo.Process, true);
            using var threadHandle = new SafeKernelHandle(processInfo.Thread, true);
            if (!AssignProcessToJobObject(job, processHandle))
            {
                TerminateProcess(processHandle, 0xE001);
                processHandle.Dispose();
                throw LastWin32("Unable to attach the worker to its Job Object.");
            }

            // Only the CHILD ends are closed here. stdinWrite stays open for the
            // life of the session — that is the whole point of this path.
            stdoutWrite.Dispose();
            stderrWrite.Dispose();
            stdinRead.Dispose();

            if (ResumeThread(threadHandle) == uint.MaxValue)
            {
                TerminateJobObject(job, 0xE002);
                processHandle.Dispose();
                throw LastWin32("Unable to resume the sandbox worker.");
            }

            return new InteractiveIsolatedProcess(
                (int)processInfo.ProcessId,
                job,
                profile,
                processHandle,
                stdinWrite,
                stdoutRead,
                stderrRead,
                jobRoot,
                profileFolder,
                liveMarker,
                ApplyCpuRateLimit);
        }
        catch
        {
            job.Dispose();
            stdoutRead.Dispose();
            stdoutWrite.Dispose();
            stderrRead.Dispose();
            stderrWrite.Dispose();
            stdinWrite.Dispose();
            stdinRead.Dispose();
            throw;
        }
        finally
        {
            if (startup.AttributeList != IntPtr.Zero)
            {
                DeleteProcThreadAttributeList(startup.AttributeList);
                Marshal.FreeHGlobal(startup.AttributeList);
            }
            if (securityCapabilitiesPtr != IntPtr.Zero)
                Marshal.FreeHGlobal(securityCapabilitiesPtr);
            if (environmentPtr != IntPtr.Zero)
                Marshal.FreeHGlobal(environmentPtr);
        }
    }

    /// <summary>Job/CPU/handle plumbing an interactive session owns. Exposed as
    /// delegates so the session type does not need the P/Invoke surface.</summary>
    internal static void TerminateJob(object job, uint exitCode) =>
        TerminateJobObject((SafeJobHandle)job, exitCode);

    internal static void ReclaimJobDirectory(string jobRoot, string profileFolder) =>
        TryDeleteJobDirectory(jobRoot, profileFolder);

    private async Task<int> RunCoreAsync(
        ProcessRunSpec spec,
        Action<string> onLine,
        IsolatedWorkerLaunch? worker,
        CancellationToken ct)
    {
        if (!OperatingSystem.IsWindows())
            throw new SandboxUnavailableException("The Windows AppContainer sandbox is unavailable.");

        var executable = worker is null
            ? ValidateExecutable(spec.FileName)
            : ValidateDotnetHost(spec.FileName);
        var workspaceRoot = _files.ContainingRoot(spec.WorkingDirectory)
                            ?? throw new SandboxUnavailableException(
                                "The working directory is not inside an authorized workspace mount.");

        using var profile = AcquireProfile();
        var profileFolder = GetProfileFolder(profile);
        var jobsRoot = Path.Combine(profileFolder, "LocalState", "SranJobs");
        var jobRoot = Path.Combine(jobsRoot, Guid.NewGuid().ToString("N"));
        var stagedWorkspace = Path.Combine(jobRoot, "workspace");
        // A hard crash (or a killed host) skips the finally-block delete, so
        // reclaim anything an earlier run stranded before staging a new one.
        SweepOrphanedJobDirectories(
            jobsRoot,
            Path.TrimEndingDirectorySeparator(Path.GetFullPath(profileFolder)));
        Directory.CreateDirectory(stagedWorkspace);
        // Held exclusively for the run's lifetime so a concurrent instance's
        // sweep can tell "live" from "stranded" without guessing on age.
        var liveMarker = new FileStream(
            Path.Combine(jobRoot, LiveMarkerName),
            FileMode.Create,
            FileAccess.ReadWrite,
            FileShare.None,
            bufferSize: 1,
            FileOptions.DeleteOnClose);

        try
        {
            var baseline = await StageWorkspaceAsync(workspaceRoot, stagedWorkspace, ct);
            var relativeWorkingDirectory = Path.GetRelativePath(
                workspaceRoot,
                Path.GetFullPath(spec.WorkingDirectory));
            if (!IsContainedRelativePath(relativeWorkingDirectory))
                throw new SandboxUnavailableException(
                    "The working directory escaped the authorized workspace.");
            var stagedWorkingDirectory = Path.Combine(stagedWorkspace, relativeWorkingDirectory);
            Directory.CreateDirectory(stagedWorkingDirectory);

            var arguments = spec.Arguments;
            if (worker is not null)
            {
                // Staged as a sibling of "workspace" so the container can read it
                // while SyncWorkspaceAsync never sees it as user content.
                var stagedWorker = Path.Combine(jobRoot, "worker");
                StageWorkerImage(worker, stagedWorker);
                arguments = $"\"{Path.Combine(stagedWorker, worker.EntryAssemblyName)}\"";
            }

            var exitCode = await LaunchLowboxAsync(
                profile,
                executable,
                arguments,
                stagedWorkingDirectory,
                jobRoot,
                onLine,
                ct);
            await SyncWorkspaceAsync(
                workspaceRoot,
                stagedWorkspace,
                baseline,
                CancellationToken.None);
            return exitCode;
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (SandboxExecutionException)
        {
            throw;
        }
        catch (Exception ex) when (ex is IOException
                                   or UnauthorizedAccessException
                                   or Win32Exception
                                   or ArgumentException
                                   or NotSupportedException)
        {
            throw new SandboxUnavailableException(
                "The isolated execution environment could not be initialized.",
                ex);
        }
        finally
        {
            // Release the marker before deleting: it lives inside the very
            // directory being reclaimed.
            liveMarker.Dispose();
            TryDeleteJobDirectory(jobRoot, profileFolder);
        }
    }

    private async Task<Dictionary<string, string>> StageWorkspaceAsync(
        string workspaceRoot,
        string stagedWorkspace,
        CancellationToken ct)
    {
        var baseline = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var entries = await _files.ListAsync(
            workspaceRoot,
            recursive: true,
            MaxWorkspaceFiles,
            ct);
        if (entries.Count >= MaxWorkspaceFiles)
            throw new SandboxUnavailableException(
                $"Workspace staging exceeded the {MaxWorkspaceFiles} entry quota.");

        long total = 0;
        foreach (var entry in entries)
        {
            ct.ThrowIfCancellationRequested();
            var stagedPath = SafeStagePath(stagedWorkspace, entry.RelativePath);
            if (entry.IsDirectory)
            {
                Directory.CreateDirectory(stagedPath);
                continue;
            }

            if (entry.Length > MaxSingleFileBytes
                || (total += entry.Length) > MaxWorkspaceBytes)
                throw new SandboxUnavailableException(
                    "Workspace staging exceeded the file-size quota.");
            var snapshot = await _files.ReadFileAsync(entry.FullPath, MaxSingleFileBytes, ct);
            Directory.CreateDirectory(Path.GetDirectoryName(stagedPath)!);
            await File.WriteAllBytesAsync(stagedPath, snapshot.Bytes, ct);
            baseline[entry.RelativePath] = snapshot.Sha256;
        }

        return baseline;
    }

    private async Task SyncWorkspaceAsync(
        string workspaceRoot,
        string stagedWorkspace,
        IReadOnlyDictionary<string, string> baseline,
        CancellationToken ct)
    {
        try
        {
            var current = EnumerateStagedFiles(stagedWorkspace);
            long total = 0;
            foreach (var (relative, stagedPath) in current)
            {
                var info = new FileInfo(stagedPath);
                if (info.Length > MaxSingleFileBytes
                    || (total += info.Length) > MaxWorkspaceBytes)
                    throw new SandboxWorkspaceSyncException(
                        "Sandbox output exceeded the workspace synchronization quota.");

                var bytes = await File.ReadAllBytesAsync(stagedPath, ct);
                var hash = Convert.ToHexString(SHA256.HashData(bytes));
                if (baseline.TryGetValue(relative, out var previous)
                    && previous.Equals(hash, StringComparison.Ordinal))
                    continue;

                await _files.WriteBytesAsync(Path.Combine(workspaceRoot, relative), bytes, ct);
            }

            foreach (var removed in baseline.Keys.Except(current.Keys, StringComparer.OrdinalIgnoreCase))
                await _files.DeleteAsync(Path.Combine(workspaceRoot, removed), ct);
        }
        catch (SandboxWorkspaceSyncException)
        {
            throw;
        }
        catch (Exception ex) when (ex is IOException
                                   or UnauthorizedAccessException
                                   or ArgumentException
                                   or NotSupportedException)
        {
            throw new SandboxWorkspaceSyncException(
                "The sandbox completed, but workspace changes could not be reconciled.",
                ex);
        }
    }

    private static Dictionary<string, string> EnumerateStagedFiles(string stagedWorkspace)
    {
        var files = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var pending = new Stack<string>();
        pending.Push(stagedWorkspace);
        while (pending.Count > 0)
        {
            var current = pending.Pop();
            foreach (var entry in Directory.EnumerateFileSystemEntries(current))
            {
                var attributes = File.GetAttributes(entry);
                if ((attributes & FileAttributes.ReparsePoint) != 0)
                    throw new SandboxWorkspaceSyncException(
                        "A sandbox process created a reparse point; no changes were synchronized.");
                if ((attributes & FileAttributes.Directory) != 0)
                {
                    pending.Push(entry);
                    continue;
                }

                var relative = Path.GetRelativePath(stagedWorkspace, entry);
                files[relative] = entry;
                if (files.Count > MaxWorkspaceFiles)
                    throw new SandboxWorkspaceSyncException(
                        "Sandbox output exceeded the file-count quota.");
            }
        }
        return files;
    }

    private static string SafeStagePath(string root, string relative)
    {
        var combined = Path.GetFullPath(Path.Combine(root, relative));
        var fromRoot = Path.GetRelativePath(
            Path.TrimEndingDirectorySeparator(Path.GetFullPath(root)),
            combined);
        if (!IsContainedRelativePath(fromRoot))
            throw new SandboxUnavailableException("Workspace staging escaped its private root.");
        return combined;
    }

    private static bool IsContainedRelativePath(string relative)
    {
        if (relative == ".")
            return true;
        if (Path.IsPathRooted(relative))
            return false;
        var first = relative.Split(
            [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar],
            StringSplitOptions.RemoveEmptyEntries).FirstOrDefault();
        return !string.Equals(first, "..", StringComparison.Ordinal);
    }

    private static async Task<int> LaunchLowboxAsync(
        SafeSidHandle profileSid,
        string executable,
        string arguments,
        string workingDirectory,
        string tempDirectory,
        Action<string> onLine,
        CancellationToken ct)
    {
        using var job = CreateConstrainedJob();
        using var stdoutRead = CreateChildOutputPipe(out var stdoutWrite);
        using var stderrRead = CreateChildOutputPipe(out var stderrWrite);
        using var stdinWrite = CreateChildInputPipe(out var stdinRead);
        using (stdoutWrite)
        using (stderrWrite)
        using (stdinRead)
        {
            var startup = new StartupInfoEx();
            startup.StartupInfo.cb = Marshal.SizeOf<StartupInfoEx>();
            startup.StartupInfo.dwFlags = StartfUseStdHandles;
            startup.StartupInfo.hStdInput = stdinRead.DangerousGetHandle();
            startup.StartupInfo.hStdOutput = stdoutWrite.DangerousGetHandle();
            startup.StartupInfo.hStdError = stderrWrite.DangerousGetHandle();

            var attributeSize = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref attributeSize);
            startup.AttributeList = Marshal.AllocHGlobal(attributeSize);
            var securityCapabilitiesPtr = IntPtr.Zero;
            var environmentPtr = IntPtr.Zero;
            try
            {
                if (!InitializeProcThreadAttributeList(
                        startup.AttributeList,
                        1,
                        0,
                        ref attributeSize))
                    throw LastWin32("Unable to initialize sandbox process attributes.");

                var capabilities = new SecurityCapabilities
                {
                    AppContainerSid = profileSid.DangerousGetHandle()
                };
                securityCapabilitiesPtr = Marshal.AllocHGlobal(
                    Marshal.SizeOf<SecurityCapabilities>());
                Marshal.StructureToPtr(capabilities, securityCapabilitiesPtr, false);
                if (!UpdateProcThreadAttribute(
                        startup.AttributeList,
                        0,
                        ProcThreadAttributeSecurityCapabilities,
                        securityCapabilitiesPtr,
                        (IntPtr)Marshal.SizeOf<SecurityCapabilities>(),
                        IntPtr.Zero,
                        IntPtr.Zero))
                    throw LastWin32("Unable to apply AppContainer capabilities.");

                environmentPtr = Marshal.StringToHGlobalUni(BuildEnvironment(tempDirectory));
                var commandLine = new StringBuilder(
                    $"\"{executable}\" {arguments}",
                    executable.Length + arguments.Length + 8);
                if (!CreateProcessW(
                        executable,
                        commandLine,
                        IntPtr.Zero,
                        IntPtr.Zero,
                        true,
                        ExtendedStartupInfoPresent
                        | CreateUnicodeEnvironment
                        | CreateSuspended
                        | CreateNoWindow,
                        environmentPtr,
                        workingDirectory,
                        ref startup,
                        out var processInfo))
                    throw LastWin32("Unable to start the AppContainer worker.");

                using var processHandle = new SafeProcessHandle(processInfo.Process, true);
                using var threadHandle = new SafeKernelHandle(processInfo.Thread, true);
                if (!AssignProcessToJobObject(job, processHandle))
                {
                    TerminateProcess(processHandle, 0xE001);
                    throw LastWin32("Unable to attach the worker to its Job Object.");
                }
                stdoutWrite.Dispose();
                stderrWrite.Dispose();
                stdinRead.Dispose();
                stdinWrite.Dispose();

                if (ResumeThread(threadHandle) == uint.MaxValue)
                {
                    TerminateJobObject(job, 0xE002);
                    throw LastWin32("Unable to resume the sandbox worker.");
                }

                using var process = Process.GetProcessById((int)processInfo.ProcessId);
                // Legacy Windows PowerShell initializes the CLR inside the
                // lowbox before accepting a Job CPU-rate controller. Applying
                // it while the primary thread is suspended causes
                // STATUS_DLL_INIT_FAILED on supported Windows builds. The
                // process is already lowbox-isolated and under memory/process/
                // kill-on-close quotas during this bounded warmup.
                var exitTask = process.WaitForExitAsync(ct);
                var cpuWarmupTask = Task.Delay(CpuControllerWarmup, ct);
                if (await Task.WhenAny(exitTask, cpuWarmupTask) == cpuWarmupTask)
                {
                    await cpuWarmupTask;
                    ApplyCpuRateLimit(job);
                }
                var outputBytes = 0L;
                var outputExceeded = 0;
                async Task DrainAsync(SafeFileHandle readHandle)
                {
                    await using var stream = new FileStream(
                        readHandle,
                        FileAccess.Read,
                        16 * 1024,
                        isAsync: false);
                    using var reader = new StreamReader(
                        stream,
                        new UTF8Encoding(false, false),
                        true);
                    while (await reader.ReadLineAsync(CancellationToken.None) is { } line)
                    {
                        if (Interlocked.Add(
                                ref outputBytes,
                                Encoding.UTF8.GetByteCount(line) + 1) > MaxOutputBytes)
                        {
                            Interlocked.Exchange(ref outputExceeded, 1);
                            TerminateJobObject(job, 0xE003);
                            break;
                        }
                        onLine(line);
                    }
                }

                var stdoutTask = DrainAsync(stdoutRead);
                var stderrTask = DrainAsync(stderrRead);
                try
                {
                    await exitTask;
                }
                catch (OperationCanceledException)
                {
                    TerminateJobObject(job, 0xC000013A);
                    using var cleanupCts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                    try
                    {
                        await process.WaitForExitAsync(cleanupCts.Token);
                    }
                    catch (OperationCanceledException)
                    {
                    }
                    throw;
                }

                await Task.WhenAll(stdoutTask, stderrTask);
                if (Volatile.Read(ref outputExceeded) != 0)
                    throw new SandboxOutputLimitException(
                        $"Sandbox output exceeded {MaxOutputBytes} bytes.");
                if (!GetExitCodeProcess(processHandle, out var exitCode))
                    throw LastWin32("Unable to read the sandbox worker exit code.");
                return unchecked((int)exitCode);
            }
            finally
            {
                if (startup.AttributeList != IntPtr.Zero)
                {
                    DeleteProcThreadAttributeList(startup.AttributeList);
                    Marshal.FreeHGlobal(startup.AttributeList);
                }
                if (securityCapabilitiesPtr != IntPtr.Zero)
                    Marshal.FreeHGlobal(securityCapabilitiesPtr);
                if (environmentPtr != IntPtr.Zero)
                    Marshal.FreeHGlobal(environmentPtr);
            }
        }
    }

    private static string ValidateExecutable(string fileName)
    {
        var system = Environment.GetFolderPath(Environment.SpecialFolder.System);
        var windowsPowerShell = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.Windows),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe");
        var allowed = new[] { Path.Combine(system, "cmd.exe"), windowsPowerShell };
        var full = Path.GetFullPath(fileName);
        return allowed.FirstOrDefault(path =>
                   path.Equals(full, StringComparison.OrdinalIgnoreCase))
               ?? throw new SandboxUnavailableException(
                   $"Executable '{fileName}' is not an approved sandbox entry point.");
    }

    internal static string DotnetHostPath() =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "dotnet",
            "dotnet.exe");

    /// <summary>The .NET host is an approved entry point ONLY for first-party
    /// worker images. run_command's allowlist is untouched, so a shell request
    /// can never reach it and hand it an arbitrary assembly.</summary>
    private static string ValidateDotnetHost(string fileName)
    {
        var expected = DotnetHostPath();
        if (!expected.Equals(Path.GetFullPath(fileName), StringComparison.OrdinalIgnoreCase)
            || !File.Exists(expected))
            throw new SandboxUnavailableException(
                "The .NET worker host is unavailable; isolated workers fail closed.");
        return expected;
    }

    /// <summary>Copies the verified worker image into the job's private folder.
    /// Identity was checked by the caller against the manifest hash — this step
    /// must not consult anything a request could influence.</summary>
    private static void StageWorkerImage(IsolatedWorkerLaunch worker, string destination)
    {
        Directory.CreateDirectory(destination);
        var source = new DirectoryInfo(worker.ImageDirectory);
        if (!source.Exists)
            throw new SandboxUnavailableException(
                $"Worker image '{worker.Capability}' is not installed; the capability fails closed.");
        foreach (var file in source.EnumerateFiles("*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(source.FullName, file.FullName);
            var target = Path.Combine(destination, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            file.CopyTo(target, overwrite: true);
        }
        if (!File.Exists(Path.Combine(destination, worker.EntryAssemblyName)))
            throw new SandboxUnavailableException(
                $"Worker image '{worker.Capability}' has no entry assembly; the capability fails closed.");
    }

    private static string BuildEnvironment(string tempDirectory)
    {
        var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        var system = Environment.GetFolderPath(Environment.SpecialFolder.System);
        var values = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["APPDATA"] = tempDirectory,
            ["COMSPEC"] = Path.Combine(system, "cmd.exe"),
            ["LOCALAPPDATA"] = tempDirectory,
            ["PATH"] = system,
            ["PATHEXT"] = ".COM;.EXE;.BAT;.CMD",
            ["SRAN_SANDBOX"] = "1",
            ["SYSTEMROOT"] = windows,
            ["TEMP"] = tempDirectory,
            ["TMP"] = tempDirectory,
            ["USERPROFILE"] = tempDirectory,
            ["WINDIR"] = windows
        };
        return string.Join('\0', values.Select(pair => $"{pair.Key}={pair.Value}")) + "\0\0";
    }

    private static SafeSidHandle AcquireProfile()
    {
        var hr = CreateAppContainerProfile(
            ProfileName,
            "SRAN Desktop command sandbox",
            "Capability-free local execution worker",
            IntPtr.Zero,
            0,
            out var sid);
        if (hr == HResultAlreadyExists)
            hr = DeriveAppContainerSidFromAppContainerName(ProfileName, out sid);
        if (hr < 0 || sid == IntPtr.Zero)
            throw new SandboxUnavailableException(
                $"Windows could not load the AppContainer profile (HRESULT 0x{hr:X8}).");
        return new SafeSidHandle(sid, true);
    }

    private static string GetProfileFolder(SafeSidHandle sid)
    {
        if (!ConvertSidToStringSidW(sid, out var sidStringPtr))
            throw LastWin32("Windows could not serialize the AppContainer SID.");
        string sidString;
        try
        {
            sidString = Marshal.PtrToStringUni(sidStringPtr)
                        ?? throw new SandboxUnavailableException(
                            "Windows returned an empty AppContainer SID.");
        }
        finally
        {
            LocalFree(sidStringPtr);
        }

        var hr = GetAppContainerFolderPath(sidString, out var pathPtr);
        if (hr < 0 || pathPtr == IntPtr.Zero)
            throw new SandboxUnavailableException(
                $"Windows could not resolve the AppContainer folder (HRESULT 0x{hr:X8}).");
        try
        {
            return Marshal.PtrToStringUni(pathPtr)
                   ?? throw new SandboxUnavailableException(
                       "Windows returned an empty AppContainer folder path.");
        }
        finally
        {
            Marshal.FreeCoTaskMem(pathPtr);
        }
    }

    private static SafeJobHandle CreateConstrainedJob()
    {
        var job = CreateJobObjectW(IntPtr.Zero, null);
        if (job.IsInvalid)
            throw LastWin32("Unable to create the sandbox Job Object.");

        var limits = new JobObjectExtendedLimitInformation
        {
            BasicLimitInformation = new JobObjectBasicLimitInformation
            {
                LimitFlags = JobObjectLimitKillOnJobClose
                             | JobObjectLimitActiveProcess
                             | JobObjectLimitProcessMemory
                             | JobObjectLimitJobMemory,
                ActiveProcessLimit = 16
            },
            ProcessMemoryLimit = (UIntPtr)(512UL * 1024 * 1024),
            JobMemoryLimit = (UIntPtr)(768UL * 1024 * 1024)
        };
        var size = Marshal.SizeOf<JobObjectExtendedLimitInformation>();
        var ptr = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, ptr, false);
            if (!SetInformationJobObject(
                    job,
                    JobObjectInfoClass.ExtendedLimitInformation,
                    ptr,
                    (uint)size))
                throw LastWin32("Unable to set sandbox quotas.");
        }
        catch
        {
            job.Dispose();
            throw;
        }
        finally
        {
            Marshal.FreeHGlobal(ptr);
        }

        return job;
    }

    private static void ApplyCpuRateLimit(SafeJobHandle job)
    {
        var cpuLimits = new JobObjectCpuRateControlInformation
        {
            ControlFlags = JobObjectCpuRateControlEnable | JobObjectCpuRateControlHardCap,
            CpuRate = CpuHardCapRate
        };
        var cpuSize = Marshal.SizeOf<JobObjectCpuRateControlInformation>();
        var cpuPtr = Marshal.AllocHGlobal(cpuSize);
        try
        {
            Marshal.StructureToPtr(cpuLimits, cpuPtr, false);
            if (!SetInformationJobObject(
                    job,
                    JobObjectInfoClass.CpuRateControlInformation,
                    cpuPtr,
                    (uint)cpuSize))
                throw LastWin32("Unable to set the sandbox CPU-rate quota.");
        }
        catch
        {
            job.Dispose();
            throw;
        }
        finally
        {
            Marshal.FreeHGlobal(cpuPtr);
        }
    }

    internal static SandboxJobPolicy InspectAppliedJobPolicy()
    {
        using var job = CreateConstrainedJob();
        ApplyCpuRateLimit(job);
        var cpuSize = Marshal.SizeOf<JobObjectCpuRateControlInformation>();
        var cpuPtr = Marshal.AllocHGlobal(cpuSize);
        var limitsSize = Marshal.SizeOf<JobObjectExtendedLimitInformation>();
        var limitsPtr = Marshal.AllocHGlobal(limitsSize);
        try
        {
            if (!QueryInformationJobObject(
                    job,
                    JobObjectInfoClass.CpuRateControlInformation,
                    cpuPtr,
                    (uint)cpuSize,
                    out _))
                throw LastWin32("Unable to inspect the sandbox CPU-rate quota.");
            if (!QueryInformationJobObject(
                    job,
                    JobObjectInfoClass.ExtendedLimitInformation,
                    limitsPtr,
                    (uint)limitsSize,
                    out _))
                throw LastWin32("Unable to inspect the sandbox resource quotas.");
            var cpu = Marshal.PtrToStructure<JobObjectCpuRateControlInformation>(cpuPtr);
            var limits = Marshal.PtrToStructure<JobObjectExtendedLimitInformation>(limitsPtr);
            return new SandboxJobPolicy(
                cpu.ControlFlags,
                cpu.CpuRate,
                limits.BasicLimitInformation.LimitFlags,
                limits.BasicLimitInformation.ActiveProcessLimit,
                limits.ProcessMemoryLimit.ToUInt64(),
                limits.JobMemoryLimit.ToUInt64());
        }
        finally
        {
            Marshal.FreeHGlobal(cpuPtr);
            Marshal.FreeHGlobal(limitsPtr);
        }
    }

    private static SafeFileHandle CreateChildOutputPipe(out SafeFileHandle childWrite)
    {
        var attributes = InheritableSecurityAttributes();
        if (!CreatePipe(out var parentRead, out childWrite, ref attributes, 0))
            throw LastWin32("Unable to create a sandbox output pipe.");
        if (!SetHandleInformation(parentRead, HandleFlagInherit, 0))
        {
            parentRead.Dispose();
            childWrite.Dispose();
            throw LastWin32("Unable to protect a sandbox output handle.");
        }
        return parentRead;
    }

    private static SafeFileHandle CreateChildInputPipe(out SafeFileHandle childRead)
    {
        var attributes = InheritableSecurityAttributes();
        if (!CreatePipe(out childRead, out var parentWrite, ref attributes, 0))
            throw LastWin32("Unable to create a sandbox input pipe.");
        if (!SetHandleInformation(parentWrite, HandleFlagInherit, 0))
        {
            childRead.Dispose();
            parentWrite.Dispose();
            throw LastWin32("Unable to protect a sandbox input handle.");
        }
        return parentWrite;
    }

    private static SecurityAttributes InheritableSecurityAttributes() => new()
    {
        Length = Marshal.SizeOf<SecurityAttributes>(),
        InheritHandle = true
    };

    private static Win32Exception LastWin32(string message) =>
        new(Marshal.GetLastWin32Error(), message);

    private static void TryDeleteJobDirectory(string jobRoot, string profileFolder)
    {
        var fullProfile = Path.TrimEndingDirectorySeparator(Path.GetFullPath(profileFolder));
        var fullJob = Path.GetFullPath(jobRoot);
        if (!fullJob.StartsWith(
                fullProfile + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase))
            return;

        // Killing the Job Object returns before every descendant has released
        // its handles, so the first recursive delete can lose the race and
        // strand a staged workspace (measured: cancellation/output-quota runs
        // leaked one directory each). Retry briefly, then fall back to the
        // orphan sweep on the next run rather than leaking silently.
        if (DeleteWithRetry(fullJob))
            return;
        Interlocked.Increment(ref _failedTemporaryCleanups);
        SweepOrphanedJobDirectories(Path.GetDirectoryName(fullJob), fullProfile);
    }

    private static bool DeleteWithRetry(string directory)
    {
        for (var attempt = 0; attempt < 20; attempt++)
        {
            try
            {
                if (!Directory.Exists(directory))
                    return true;
                Directory.Delete(directory, recursive: true);
                return true;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                Thread.Sleep(50);
            }
        }
        return !Directory.Exists(directory);
    }

    /// <summary>Best-effort reclaim of job directories a previous crash or lost
    /// delete race stranded. Only touches this runner's private job root, and
    /// never a directory whose live-marker is still held open by a concurrent
    /// run — a long command must not have its staged workspace pulled away.</summary>
    private static void SweepOrphanedJobDirectories(string? jobsRoot, string profileFolder)
    {
        if (jobsRoot is null
            || !Path.GetFullPath(jobsRoot).StartsWith(
                profileFolder + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase))
            return;
        try
        {
            foreach (var candidate in Directory.EnumerateDirectories(jobsRoot))
            {
                if (IsJobDirectoryLive(candidate))
                    continue;
                try
                {
                    Directory.Delete(candidate, recursive: true);
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                {
                }
            }
        }
        catch (Exception ex) when (ex is IOException
                                   or UnauthorizedAccessException
                                   or DirectoryNotFoundException)
        {
        }
    }

    private static bool IsJobDirectoryLive(string jobDirectory)
    {
        var marker = Path.Combine(jobDirectory, LiveMarkerName);
        if (!File.Exists(marker))
            // Pre-marker or partially created directory: fall back to age so a
            // directory being staged right now is never swept.
            return DateTime.UtcNow - Directory.GetCreationTimeUtc(jobDirectory)
                   < OrphanedJobGrace;
        try
        {
            using var probe = new FileStream(
                marker,
                FileMode.Open,
                FileAccess.ReadWrite,
                FileShare.None);
            return false;
        }
        catch (IOException)
        {
            return true; // another run still holds the marker
        }
        catch (UnauthorizedAccessException)
        {
            return true;
        }
    }

    private const int HResultAlreadyExists = unchecked((int)0x800700B7);
    private static readonly IntPtr ProcThreadAttributeSecurityCapabilities = (IntPtr)0x00020009;
    private const uint ExtendedStartupInfoPresent = 0x00080000;
    private const uint CreateUnicodeEnvironment = 0x00000400;
    private const uint CreateSuspended = 0x00000004;
    private const uint CreateNoWindow = 0x08000000;
    private const uint StartfUseStdHandles = 0x00000100;
    private const uint HandleFlagInherit = 0x00000001;
    private const uint JobObjectLimitActiveProcess = 0x00000008;
    private const uint JobObjectLimitProcessMemory = 0x00000100;
    private const uint JobObjectLimitJobMemory = 0x00000200;
    private const uint JobObjectLimitKillOnJobClose = 0x00002000;
    private const uint JobObjectCpuRateControlEnable = 0x00000001;
    private const uint JobObjectCpuRateControlHardCap = 0x00000004;

    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityCapabilities
    {
        public IntPtr AppContainerSid;
        public IntPtr Capabilities;
        public uint CapabilityCount;
        public uint Reserved;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct StartupInfo
    {
        public int cb;
        public string? lpReserved;
        public string? lpDesktop;
        public string? lpTitle;
        public uint dwX;
        public uint dwY;
        public uint dwXSize;
        public uint dwYSize;
        public uint dwXCountChars;
        public uint dwYCountChars;
        public uint dwFillAttribute;
        public uint dwFlags;
        public ushort wShowWindow;
        public ushort cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct StartupInfoEx
    {
        public StartupInfo StartupInfo;
        public IntPtr AttributeList;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInformation
    {
        public IntPtr Process;
        public IntPtr Thread;
        public uint ProcessId;
        public uint ThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityAttributes
    {
        public int Length;
        public IntPtr SecurityDescriptor;
        [MarshalAs(UnmanagedType.Bool)] public bool InheritHandle;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectBasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectExtendedLimitInformation
    {
        public JobObjectBasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectCpuRateControlInformation
    {
        public uint ControlFlags;
        public uint CpuRate;
    }

    private enum JobObjectInfoClass
    {
        ExtendedLimitInformation = 9,
        CpuRateControlInformation = 15
    }

    /// <summary>
    /// A live sandboxed worker process with its pipes still open. Disposing it
    /// terminates the whole Job Object — every descendant dies with it — and
    /// reclaims the staged directory, so a session can never outlive its owner.
    /// </summary>
    public sealed class InteractiveIsolatedProcess : IAsyncDisposable
    {
        private readonly SafeJobHandle _job;
        private readonly SafeSidHandle _profile;
        private readonly SafeProcessHandle _process;
        private readonly SafeFileHandle _stdinWrite;
        private readonly string _jobRoot;
        private readonly string _profileFolder;
        private readonly FileStream _liveMarker;
        private readonly Action<SafeJobHandle> _applyCpuLimit;
        private readonly Process _processView;
        private int _disposed;

        internal InteractiveIsolatedProcess(
            int processId,
            SafeJobHandle job,
            SafeSidHandle profile,
            SafeProcessHandle process,
            SafeFileHandle stdinWrite,
            SafeFileHandle stdoutRead,
            SafeFileHandle stderrRead,
            string jobRoot,
            string profileFolder,
            FileStream liveMarker,
            Action<SafeJobHandle> applyCpuLimit)
        {
            ProcessId = processId;
            _job = job;
            _profile = profile;
            _process = process;
            _stdinWrite = stdinWrite;
            _jobRoot = jobRoot;
            _profileFolder = profileFolder;
            _liveMarker = liveMarker;
            _applyCpuLimit = applyCpuLimit;
            _processView = Process.GetProcessById(processId);
            StandardInput = new FileStream(stdinWrite, FileAccess.Write, 4096, isAsync: false);
            StandardOutput = new FileStream(stdoutRead, FileAccess.Read, 4096, isAsync: false);
            StandardError = new FileStream(stderrRead, FileAccess.Read, 4096, isAsync: false);
        }

        public int ProcessId { get; }
        public Stream StandardInput { get; }
        public Stream StandardOutput { get; }
        public Stream StandardError { get; }
        public bool HasExited => _processView.HasExited;

        /// <summary>Applied after the runtime has initialized: a CPU-rate cap set
        /// while the primary thread is suspended fails CLR startup inside the
        /// lowbox (learned in the batch path).</summary>
        public void ApplyCpuQuota() => _applyCpuLimit(_job);

        public Task WaitForExitAsync(CancellationToken ct = default) =>
            _processView.WaitForExitAsync(ct);

        /// <summary>Kills the process tree immediately. Used for force-termination
        /// and whenever protocol integrity is lost.</summary>
        public void Terminate(uint exitCode = 0xE100)
        {
            try
            {
                TerminateJobObject(_job, exitCode);
            }
            catch (ObjectDisposedException)
            {
            }
        }

        public async ValueTask DisposeAsync()
        {
            if (Interlocked.Exchange(ref _disposed, 1) != 0)
                return;
            Terminate();
            try
            {
                using var grace = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await _processView.WaitForExitAsync(grace.Token);
            }
            catch (Exception ex) when (ex is OperationCanceledException or InvalidOperationException)
            {
            }

            foreach (var stream in new[] { StandardInput, StandardOutput, StandardError })
            {
                try
                {
                    await stream.DisposeAsync();
                }
                catch (Exception ex) when (ex is IOException or ObjectDisposedException)
                {
                }
            }
            _stdinWrite.Dispose();
            _process.Dispose();
            _job.Dispose();
            _processView.Dispose();
            // The marker lives inside the directory being reclaimed, so it has to
            // go first or the delete loses to our own open handle.
            _liveMarker.Dispose();
            TryDeleteJobDirectory(_jobRoot, _profileFolder);
            _profile.Dispose();
        }
    }

    internal sealed class SafeSidHandle : SafeHandleZeroOrMinusOneIsInvalid
    {
        public SafeSidHandle(IntPtr handle, bool ownsHandle) : base(ownsHandle) =>
            SetHandle(handle);
        protected override bool ReleaseHandle() => FreeSid(handle) == IntPtr.Zero;
    }

    internal sealed class SafeJobHandle : SafeHandleZeroOrMinusOneIsInvalid
    {
        private SafeJobHandle() : base(true)
        {
        }
        protected override bool ReleaseHandle() => CloseHandle(handle);
    }

    private sealed class SafeKernelHandle : SafeHandleZeroOrMinusOneIsInvalid
    {
        public SafeKernelHandle(IntPtr handle, bool ownsHandle) : base(ownsHandle) =>
            SetHandle(handle);
        protected override bool ReleaseHandle() => CloseHandle(handle);
    }

    [DllImport("userenv.dll", CharSet = CharSet.Unicode)]
    private static extern int CreateAppContainerProfile(
        string pszAppContainerName,
        string pszDisplayName,
        string pszDescription,
        IntPtr pCapabilities,
        uint dwCapabilityCount,
        out IntPtr ppSidAppContainerSid);

    [DllImport("userenv.dll", CharSet = CharSet.Unicode)]
    private static extern int DeriveAppContainerSidFromAppContainerName(
        string pszAppContainerName,
        out IntPtr ppsidAppContainerSid);

    [DllImport("userenv.dll", CharSet = CharSet.Unicode)]
    private static extern int GetAppContainerFolderPath(
        string pszAppContainerSid,
        out IntPtr ppszPath);

    [DllImport("advapi32.dll")]
    private static extern IntPtr FreeSid(IntPtr pSid);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ConvertSidToStringSidW(
        SafeSidHandle sid,
        out IntPtr stringSid);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr hMem);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool InitializeProcThreadAttributeList(
        IntPtr lpAttributeList,
        int dwAttributeCount,
        int dwFlags,
        ref IntPtr lpSize);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UpdateProcThreadAttribute(
        IntPtr lpAttributeList,
        uint dwFlags,
        IntPtr attribute,
        IntPtr lpValue,
        IntPtr cbSize,
        IntPtr lpPreviousValue,
        IntPtr lpReturnSize);

    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(IntPtr lpAttributeList);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CreateProcessW(
        string lpApplicationName,
        StringBuilder lpCommandLine,
        IntPtr lpProcessAttributes,
        IntPtr lpThreadAttributes,
        [MarshalAs(UnmanagedType.Bool)] bool bInheritHandles,
        uint dwCreationFlags,
        IntPtr lpEnvironment,
        string lpCurrentDirectory,
        ref StartupInfoEx lpStartupInfo,
        out ProcessInformation lpProcessInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CreatePipe(
        out SafeFileHandle hReadPipe,
        out SafeFileHandle hWritePipe,
        ref SecurityAttributes lpPipeAttributes,
        uint nSize);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetHandleInformation(
        SafeFileHandle hObject,
        uint dwMask,
        uint dwFlags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeJobHandle CreateJobObjectW(
        IntPtr lpJobAttributes,
        string? lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(
        SafeJobHandle hJob,
        JobObjectInfoClass jobObjectInfoClass,
        IntPtr lpJobObjectInfo,
        uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool QueryInformationJobObject(
        SafeJobHandle hJob,
        JobObjectInfoClass jobObjectInfoClass,
        IntPtr lpJobObjectInfo,
        uint cbJobObjectInfoLength,
        out uint lpReturnLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AssignProcessToJobObject(
        SafeJobHandle hJob,
        SafeProcessHandle hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool TerminateJobObject(SafeJobHandle hJob, uint uExitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool TerminateProcess(SafeProcessHandle hProcess, uint uExitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetExitCodeProcess(SafeProcessHandle hProcess, out uint lpExitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint ResumeThread(SafeKernelHandle hThread);

    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr hObject);
}

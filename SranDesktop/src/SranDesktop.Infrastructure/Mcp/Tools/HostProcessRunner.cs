using System.Diagnostics;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Executes a command on the real machine, with the real environment, so host
/// tooling (docker, git, node, python) behaves the way it does in a terminal.
///
/// <para>This is the second of two shell lanes. It exists because the
/// AppContainer lane cannot host these tools even in principle: a CLI like
/// <c>docker</c> is a client of a host service reached over
/// <c>\\.\pipe\docker_engine</c>, and the sandbox's whole purpose is to sever
/// access to host services. No amount of PATH tuning changes that.</para>
///
/// <para><b>This lane is opt-in and is never a fallback.</b> It runs only when
/// the user has set <c>shell_execution_mode = "host"</c>. A sandbox failure
/// must surface as a failure — routing it here would turn an isolation defect
/// into a silent privilege escalation, which is the single failure mode the
/// sandbox design was built to prevent.</para>
///
/// <para>What still applies in this lane: the working directory must resolve
/// inside an authorized root, the caller's blocklist still runs, the consent
/// gate still runs, output is still bounded and still wrapped as an untrusted
/// observation, and every dispatch is written to the security audit log.</para>
///
/// <para>What no longer applies: filesystem confinement, the empty capability
/// set, the sanitized environment, and the Job Object limits. A command in this
/// lane can do anything the signed-in user can do.</para>
/// </summary>
public sealed class HostProcessRunner : IProcessRunner
{
    /// <summary>Grace period for a cancelled process to exit on its own before
    /// the tree is killed. Keeps Ctrl-C-style cleanup working for well-behaved
    /// tools without letting a wedged build linger.</summary>
    private static readonly TimeSpan TerminationGrace = TimeSpan.FromSeconds(3);

    public async Task<int> RunAsync(
        ProcessRunSpec spec, Action<string> onLine, CancellationToken ct)
    {
        // No workspace-containment check here, deliberately. An earlier version
        // of this runner copied one over from the sandbox lane, where it is the
        // security boundary. In this lane it is not: the command already runs
        // with the signed-in user's full rights, so refusing to *start* it in an
        // unlisted directory restrains nothing. All it did was reject commands
        // that touch no files at all — `docker ps`, `git --version`, `ipconfig`
        // — which made the app unable to answer questions about the machine
        // unless the user first nominated an unrelated folder.
        //
        // The gates that do constrain this lane are upstream and still apply:
        // the user must have opted into host mode, the blocklist runs, and the
        // consent dialog shows the exact command line before anything starts.
        if (!Directory.Exists(spec.WorkingDirectory))
            throw new SandboxExecutionException(
                $"Working directory not found: {spec.WorkingDirectory}");

        var startInfo = new ProcessStartInfo
        {
            FileName = spec.FileName,
            Arguments = spec.Arguments,
            WorkingDirectory = spec.WorkingDirectory,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
            // Required for redirection, and it keeps a console window from
            // flashing over the user's desktop on every command.
            UseShellExecute = false,
            CreateNoWindow = true
        };

        using var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };

        // stdout and stderr are interleaved into one stream, matching what the
        // user would see in a terminal and what the sandbox lane already does.
        var gate = new object();
        void Forward(object _, DataReceivedEventArgs e)
        {
            if (e.Data is null) return;
            lock (gate) onLine(e.Data);
        }

        process.OutputDataReceived += Forward;
        process.ErrorDataReceived += Forward;

        try
        {
            process.Start();
        }
        catch (Exception ex)
        {
            throw new SandboxExecutionException(
                $"Could not start '{Path.GetFileName(spec.FileName)}': {ex.Message}");
        }

        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        // Nothing on stdin. Without this, a tool that prompts (a package
        // manager asking to confirm, git asking for credentials) blocks until
        // the timeout instead of failing fast with its own error text.
        try
        {
            process.StandardInput.Close();
        }
        catch (IOException)
        {
            // The process may have exited already; its exit code is the answer.
        }

        try
        {
            await process.WaitForExitAsync(ct);
        }
        catch (OperationCanceledException)
        {
            TerminateTree(process);
            throw;
        }

        // Flush the async readers so the final lines are not lost between the
        // process exiting and the handlers draining.
        process.WaitForExit();
        return process.ExitCode;
    }

    /// <summary>Kills the whole tree, not just the shell. cmd.exe exits happily
    /// while the build it spawned keeps running, which is how orphaned workers
    /// are created — the exact defect §6 of the capability audit documents.</summary>
    private static void TerminateTree(Process process)
    {
        try
        {
            if (process.HasExited) return;
            process.Kill(entireProcessTree: true);
            process.WaitForExit(TerminationGrace);
        }
        catch (InvalidOperationException)
        {
            // Race with normal exit — nothing left to kill.
        }
        catch (System.ComponentModel.Win32Exception)
        {
            // Access denied on a child we do not own; the parent is gone and
            // the caller already treats this run as cancelled.
        }
    }
}

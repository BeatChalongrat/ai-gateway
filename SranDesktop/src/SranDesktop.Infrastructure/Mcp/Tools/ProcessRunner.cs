using System.Diagnostics;
using System.Text;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>How to launch a child process (Phase B PART 1).</summary>
public sealed record ProcessRunSpec(string FileName, string Arguments, string WorkingDirectory);

/// <summary>
/// Abstracts process spawning so run_command's streaming can be tested
/// deterministically with a fake that emits lines over time (no real shell,
/// CI-safe). <paramref name="onLine"/> is invoked for each stdout/stderr line AS
/// IT ARRIVES — that is what makes the output stream live.
/// </summary>
public interface IProcessRunner
{
    /// <summary>Runs the process, calling <paramref name="onLine"/> per output
    /// line while it runs, and returns the exit code. Throws
    /// <see cref="OperationCanceledException"/> when <paramref name="ct"/> fires
    /// (and kills the process tree).</summary>
    Task<int> RunAsync(ProcessRunSpec spec, Action<string> onLine, CancellationToken ct);
}

/// <summary>Real runner over <see cref="Process"/> — async line-buffered reads
/// via OutputDataReceived/ErrorDataReceived so lines flow while the process
/// runs, not only after it exits.</summary>
public sealed class SystemProcessRunner : IProcessRunner
{
    public async Task<int> RunAsync(ProcessRunSpec spec, Action<string> onLine, CancellationToken ct)
    {
        using var process = new Process();
        process.StartInfo = new ProcessStartInfo
        {
            FileName = spec.FileName,
            Arguments = spec.Arguments,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = spec.WorkingDirectory,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };

        // Serialize interleaved stdout/stderr callbacks — onLine mutates shared
        // buffers and raises a UI event, so it must not run concurrently.
        var gate = new object();
        void Emit(string? data) { if (data is not null) lock (gate) onLine(data); }
        process.OutputDataReceived += (_, e) => Emit(e.Data);
        process.ErrorDataReceived += (_, e) => Emit(e.Data);

        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        try
        {
            await process.WaitForExitAsync(ct);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(entireProcessTree: true); } catch (Exception) { /* already gone */ }
            throw;
        }

        return process.ExitCode;
    }
}

/// <summary>
/// Accumulates output while a command runs, bounded so a flooding build can't
/// blow memory or the model's context (Phase B PART 1). Keeps the HEAD and the
/// TAIL and drops the middle — the head shows how the run started, the tail
/// shows how it ended (where errors usually are).
/// </summary>
public sealed class BoundedOutputBuffer
{
    private readonly int _headLimit;
    private readonly int _tailLimit;
    private readonly StringBuilder _head = new();
    private readonly LinkedList<string> _tail = new();
    private int _tailChars;
    private int _droppedLines;

    public BoundedOutputBuffer(int headLimit = 25_000, int tailLimit = 25_000)
    {
        _headLimit = headLimit;
        _tailLimit = tailLimit;
    }

    public void Append(string line)
    {
        if (_head.Length < _headLimit && _droppedLines == 0 && _tail.Count == 0)
        {
            _head.Append(line).Append('\n');
            return;
        }

        // Head is full — ring-buffer the tail, evicting oldest past the limit.
        _tail.AddLast(line);
        _tailChars += line.Length + 1;
        while (_tailChars > _tailLimit && _tail.Count > 1)
        {
            _tailChars -= _tail.First!.Value.Length + 1;
            _tail.RemoveFirst();
            _droppedLines++;
        }
    }

    public override string ToString()
    {
        var head = _head.ToString().TrimEnd('\n');
        if (_tail.Count == 0)
            return head;
        var tail = string.Join('\n', _tail);
        var marker = $"\n[... {_droppedLines} line(s) of output truncated ...]\n";
        return head.Length == 0 ? tail : head + marker + tail;
    }
}

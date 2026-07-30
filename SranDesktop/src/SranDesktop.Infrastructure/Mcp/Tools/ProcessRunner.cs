using System.Text;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed record ProcessRunSpec(string FileName, string Arguments, string WorkingDirectory);

/// <summary>
/// A first-party worker image resolved by CAPABILITY. The image directory comes
/// from the installed application layout, never from a request payload — that is
/// what keeps "launch an arbitrary executable" off the table.
/// </summary>
public sealed record IsolatedWorkerLaunch(
    string Capability,
    string ImageDirectory,
    string EntryAssemblyName);

public interface IProcessRunner
{
    Task<int> RunAsync(ProcessRunSpec spec, Action<string> onLine, CancellationToken ct);
}

/// <summary>
/// Binary-compatibility guard for older composition roots. Direct host process
/// execution is disabled and can never act as a sandbox fallback.
/// </summary>
[Obsolete("Direct host execution is disabled. Use WindowsAppContainerProcessRunner.")]
public sealed class SystemProcessRunner : IProcessRunner
{
    public Task<int> RunAsync(ProcessRunSpec spec, Action<string> onLine, CancellationToken ct) =>
        throw new SandboxUnavailableException(
            "Direct host process execution is disabled and has no fallback.");
}

/// <summary>
/// Bounded command output: retain the beginning and end while dropping the
/// middle so command output cannot exhaust memory or model context.
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

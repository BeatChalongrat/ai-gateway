using System.Diagnostics;
using Microsoft.Extensions.Logging;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// Fail-closed local MCP transport.
///
/// A local MCP server is arbitrary executable code and cannot inherit the UI
/// process token, environment, filesystem, handles, or network access. The
/// previous implementation used Process.Start directly. Until an interactive
/// AppContainer transport can provide bidirectional stdio under the same
/// workspace, environment, network, quota, and Job Object policy as
/// run_command, local stdio connectors remain unavailable. There is
/// deliberately no host fallback.
/// </summary>
public sealed class StdioMcpTransport : IMcpTransport
{
    private readonly string _command;
    private readonly ILogger? _logger;
    private bool _disposed;

    public StdioMcpTransport(
        string command,
        IReadOnlyList<string>? args = null,
        IReadOnlyDictionary<string, string>? env = null,
        ILogger? logger = null)
    {
        _command = command;
        _logger = logger;
        _ = args;
        _ = env;
    }

    public event EventHandler? Exited
    {
        add { }
        remove { }
    }

    internal int? ProcessId => null;

    public Task EnsureStartedAsync(CancellationToken ct = default)
    {
        ct.ThrowIfCancellationRequested();
        ObjectDisposedException.ThrowIf(_disposed, this);
        _logger?.LogWarning(
            "Blocked local MCP stdio launch for executable identity '{Executable}': " +
            "isolated interactive transport is unavailable",
            Path.GetFileName(_command));
        throw Blocked();
    }

    public async Task<JsonRpcResponse> SendRequestAsync(
        string method,
        object? @params,
        CancellationToken ct = default)
    {
        _ = method;
        _ = @params;
        await EnsureStartedAsync(ct);
        throw new UnreachableException();
    }

    public async Task SendNotificationAsync(
        string method,
        object? @params,
        CancellationToken ct = default)
    {
        _ = method;
        _ = @params;
        await EnsureStartedAsync(ct);
    }

    private McpProtocolException Blocked() =>
        new(
            $"Local MCP stdio server '{Path.GetFileName(_command)}' is blocked: " +
            "no deterministically isolated interactive transport is configured. " +
            "Host execution has no fallback.");

    public ValueTask DisposeAsync()
    {
        _disposed = true;
        return ValueTask.CompletedTask;
    }
}

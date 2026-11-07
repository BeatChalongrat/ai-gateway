using System.Text.Json;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>One parsed JSON-RPC 2.0 response: result XOR error.</summary>
public sealed class JsonRpcResponse
{
    public JsonElement? Result { get; init; }
    public JsonElement? Error { get; init; }
}

/// <summary>
/// Phase 12 D: transport seam under <see cref="McpRemoteClient"/>. The MCP
/// protocol layer (initialize / tools/list / tools/call) is transport-agnostic;
/// implementations move JSON-RPC 2.0 messages over streamable HTTP
/// (<see cref="HttpMcpTransport"/> — the behavior formerly inline in
/// McpRemoteClient) or over a local child process's stdin/stdout
/// (<see cref="StdioMcpTransport"/> — newline-delimited JSON per the MCP
/// stdio spec).
/// </summary>
public interface IMcpTransport : IAsyncDisposable
{
    /// <summary>Bring the transport up: spawn the server process for stdio,
    /// no-op for HTTP. Idempotent; called lazily on first send too.</summary>
    Task EnsureStartedAsync(CancellationToken ct = default);

    /// <summary>Send a JSON-RPC request and await its id-matched response.
    /// Transport failures surface as <see cref="McpProtocolException"/> (or
    /// HttpRequestException for the HTTP impl, unchanged from before).</summary>
    Task<JsonRpcResponse> SendRequestAsync(string method, object? @params, CancellationToken ct = default);

    /// <summary>Send a JSON-RPC notification (no id — no response expected).</summary>
    Task SendNotificationAsync(string method, object? @params, CancellationToken ct = default);

    /// <summary>Raised when the transport dies underneath us (stdio server
    /// process crash/exit). NOT raised on graceful disposal. Never raised by
    /// the HTTP transport — its failures surface per call.</summary>
    event EventHandler? Exited;
}

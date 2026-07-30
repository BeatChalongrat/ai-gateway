using System.Text;
using System.Text.Json;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// MCP protocol client (R10 Fix 1). Phase 12 D: the JSON-RPC plumbing moved
/// behind <see cref="IMcpTransport"/> so the same protocol layer runs over
/// streamable HTTP (<see cref="HttpMcpTransport"/>) or a local stdio server
/// process (<see cref="StdioMcpTransport"/>). The HTTP-flavored constructors
/// are preserved verbatim for existing call sites and tests.
/// </summary>
public sealed class McpRemoteClient : IAsyncDisposable
{
    private readonly IMcpTransport _transport;

    /// <summary>Exposed for tests / reuse of a mocked handler.</summary>
    /// <summary>
    /// Dials a URL directly. Internal since Phase 4.1C: a public constructor that
    /// turns a string into a live connection is the shortest possible route
    /// around the durable gateway, and the only reason it survives at all is the
    /// protocol-level tests that need a client without an install layout.
    /// </summary>
    internal McpRemoteClient(string baseUrl, string? authToken = null, HttpClient? http = null)
        : this(baseUrl, authToken is null ? null : new Func<string?>(() => authToken), http)
    {
    }

    /// <summary>Phase 7 C4: the Bearer is resolved PER REQUEST so an oauth2
    /// connector always sends the CURRENT portal access token — a mid-session
    /// JWT refresh is picked up without re-dialing the client.</summary>
    internal McpRemoteClient(string baseUrl, Func<string?>? tokenProvider, HttpClient? http = null)
        : this(new HttpMcpTransport(baseUrl, tokenProvider, http))
    {
    }

    /// <summary>Phase 12 D: protocol layer over an explicit transport.</summary>
    public McpRemoteClient(IMcpTransport transport) => _transport = transport;

    /// <summary>The underlying transport — test seam.</summary>
    internal IMcpTransport Transport => _transport;

    /// <summary>HTTP session id, when the transport has one (stdio has none).</summary>
    public string? SessionId => (_transport as HttpMcpTransport)?.SessionId;

    /// <summary>Handshake: initialize + notifications/initialized. Call once.</summary>
    public async Task<McpInitResult> InitializeAsync(CancellationToken ct = default)
    {
        await _transport.EnsureStartedAsync(ct);

        var response = await _transport.SendRequestAsync("initialize", new
        {
            protocolVersion = "2025-06-18",
            capabilities = new { },
            clientInfo = new { name = "SranDesktop", version = "1.0.0" }
        }, ct);

        if (response.Error is not null)
            throw new McpProtocolException($"initialize failed: {response.Error}");
        if (response.Result is not { } result)
            throw new McpProtocolException("initialize returned no result.");

        // The negotiated version decides what this session may attempt. A server
        // answering with something we do not implement is refused rather than
        // guessed at.
        var negotiated = result.TryGetProperty("protocolVersion", out var version)
            ? version.GetString() ?? ""
            : "";
        if (negotiated.Length > 0 && !SupportedProtocolVersions.Contains(negotiated))
            throw new McpProtocolException(
                $"VersionMismatch: the server negotiated '{negotiated}', which this client "
                + $"does not implement (supported: {string.Join(", ", SupportedProtocolVersions)}).");

        var capabilities = McpServerCapabilities.Parse(
            result.TryGetProperty("capabilities", out var caps) ? caps : default);
        var serverName = "Unknown";
        var serverVersion = "";
        if (result.TryGetProperty("serverInfo", out var info))
        {
            if (info.TryGetProperty("name", out var name))
                serverName = name.GetString() ?? serverName;
            if (info.TryGetProperty("version", out var infoVersion))
                serverVersion = infoVersion.GetString() ?? "";
        }

        await _transport.SendNotificationAsync("notifications/initialized", null, ct);
        Capabilities = capabilities;
        Initialized = true;
        NegotiatedProtocolVersion = negotiated;
        ServerIdentity = $"{serverName}/{serverVersion}";
        return new McpInitResult
        {
            ServerName = serverName,
            ServerVersion = serverVersion,
            ProtocolVersion = negotiated,
            Capabilities = capabilities
        };
    }

    /// <summary>Protocol versions this client actually implements. Claiming more
    /// than this would be claiming completeness we have not built.</summary>
    public static IReadOnlySet<string> SupportedProtocolVersions { get; } =
        new HashSet<string>(StringComparer.Ordinal) { "2025-06-18", "2025-03-26" };

    /// <summary>Capabilities the server declared during initialize.</summary>
    public McpServerCapabilities Capabilities { get; private set; } = McpServerCapabilities.None;

    public bool Initialized { get; private set; }

    /// <summary>The version the server actually agreed to. Recorded so a
    /// reconnect that lands on a differently-versioned server is visible rather
    /// than assumed away.</summary>
    public string NegotiatedProtocolVersion { get; private set; } = "";

    /// <summary>name/version the server reported. Used as the provenance check
    /// when deciding whether a repeat after a disconnect is going to the same
    /// place.</summary>
    public string ServerIdentity { get; private set; } = "";

    private const int MaxResourceChars = 200_000;

    private void RequireCapability(bool declared, string capability)
    {
        if (!Initialized)
            throw new McpProtocolException(
                "CapabilityUnavailable: initialize must complete before any operation.");
        if (!declared)
            throw new McpProtocolException(
                $"CapabilityUnavailable: the server did not declare '{capability}'.");
    }

    /// <summary>resources/list — the server must have declared the capability.</summary>
    public async Task<List<McpResourceDescriptor>> ListResourcesAsync(
        CancellationToken ct = default)
    {
        RequireCapability(Capabilities.Resources, "resources");
        var response = await _transport.SendRequestAsync("resources/list", null, ct);
        if (response.Error is not null)
            throw new McpProtocolException($"resources/list failed: {response.Error}");

        var resources = new List<McpResourceDescriptor>();
        if (response.Result is { } result
            && result.TryGetProperty("resources", out var array)
            && array.ValueKind == JsonValueKind.Array)
        {
            foreach (var resource in array.EnumerateArray())
            {
                resources.Add(new McpResourceDescriptor
                {
                    Uri = resource.TryGetProperty("uri", out var uri) ? uri.GetString() ?? "" : "",
                    Name = resource.TryGetProperty("name", out var name) ? name.GetString() : null,
                    MimeType = resource.TryGetProperty("mimeType", out var mime)
                        ? mime.GetString()
                        : null
                });
            }
        }
        return resources;
    }

    /// <summary>resources/read. Content comes back as an untrusted observation —
    /// the caller must not treat it as instructions.</summary>
    public async Task<McpResourceContent> ReadResourceAsync(
        string uri,
        CancellationToken ct = default)
    {
        RequireCapability(Capabilities.Resources, "resources");
        var response = await _transport.SendRequestAsync("resources/read", new { uri }, ct);
        if (response.Error is not null)
            throw new McpProtocolException($"resources/read failed: {response.Error}");

        var text = new StringBuilder();
        var mimeType = "text/plain";
        var truncated = false;
        if (response.Result is { } result
            && result.TryGetProperty("contents", out var contents)
            && contents.ValueKind == JsonValueKind.Array)
        {
            foreach (var block in contents.EnumerateArray())
            {
                if (block.TryGetProperty("mimeType", out var mime))
                    mimeType = mime.GetString() ?? mimeType;
                if (block.TryGetProperty("text", out var value))
                    text.AppendLine(value.GetString());
                else if (block.TryGetProperty("blob", out var blob))
                    text.AppendLine($"[binary content, {blob.GetString()?.Length ?? 0} base64 chars]");
                if (text.Length > MaxResourceChars)
                {
                    truncated = true;
                    break;
                }
            }
        }
        return new McpResourceContent
        {
            Uri = uri,
            MimeType = mimeType,
            Text = text.ToString().TrimEnd(),
            Truncated = truncated
        };
    }

    /// <summary>prompts/list.</summary>
    public async Task<List<McpPromptDescriptor>> ListPromptsAsync(CancellationToken ct = default)
    {
        RequireCapability(Capabilities.Prompts, "prompts");
        var response = await _transport.SendRequestAsync("prompts/list", null, ct);
        if (response.Error is not null)
            throw new McpProtocolException($"prompts/list failed: {response.Error}");

        var prompts = new List<McpPromptDescriptor>();
        if (response.Result is { } result
            && result.TryGetProperty("prompts", out var array)
            && array.ValueKind == JsonValueKind.Array)
        {
            foreach (var prompt in array.EnumerateArray())
            {
                prompts.Add(new McpPromptDescriptor
                {
                    Name = prompt.TryGetProperty("name", out var name) ? name.GetString() ?? "" : "",
                    Description = prompt.TryGetProperty("description", out var description)
                        ? description.GetString()
                        : null
                });
            }
        }
        return prompts;
    }

    /// <summary>prompts/get. The returned text is CONTENT from a connector, not a
    /// system instruction — the boundary is enforced by the caller.</summary>
    public async Task<McpPromptContent> GetPromptAsync(
        string name,
        JsonElement? arguments = null,
        CancellationToken ct = default)
    {
        RequireCapability(Capabilities.Prompts, "prompts");
        var response = await _transport.SendRequestAsync(
            "prompts/get",
            arguments is { } args
                ? new { name, arguments = (object)args }
                : new { name, arguments = (object)new { } },
            ct);
        if (response.Error is not null)
            throw new McpProtocolException($"prompts/get failed: {response.Error}");

        var text = new StringBuilder();
        if (response.Result is { } result
            && result.TryGetProperty("messages", out var messages)
            && messages.ValueKind == JsonValueKind.Array)
        {
            foreach (var message in messages.EnumerateArray())
            {
                if (message.TryGetProperty("content", out var content)
                    && content.TryGetProperty("text", out var value))
                    text.AppendLine(value.GetString());
            }
        }
        return new McpPromptContent
        {
            Name = name,
            Text = text.ToString().TrimEnd()
        };
    }

    /// <summary>tools/list → tool definitions with JSON-schema inputs.</summary>
    public async Task<List<McpToolDefinition>> ListToolsAsync(CancellationToken ct = default)
    {
        var response = await _transport.SendRequestAsync("tools/list", null, ct);
        if (response.Error is not null)
            throw new McpProtocolException($"tools/list failed: {response.Error}");

        var tools = new List<McpToolDefinition>();
        if (response.Result is { } result
            && result.TryGetProperty("tools", out var array)
            && array.ValueKind == JsonValueKind.Array)
        {
            foreach (var tool in array.EnumerateArray())
            {
                tools.Add(new McpToolDefinition
                {
                    Name = tool.GetProperty("name").GetString() ?? "",
                    Description = tool.TryGetProperty("description", out var d) ? d.GetString() : null,
                    InputSchema = tool.TryGetProperty("inputSchema", out var s)
                        ? s.GetRawText() : "{\"type\":\"object\"}"
                });
            }
        }
        return tools;
    }

    /// <summary>tools/call — arguments as raw JSON element (passed verbatim).</summary>
    public Task<McpToolResult> CallToolAsync(
        string toolName, JsonElement arguments, CancellationToken ct = default) =>
        CallToolAsync(toolName, arguments, null, ct);

    /// <summary>
    /// tools/call with an idempotency key.
    ///
    /// The key travels in the protocol's reserved <c>_meta</c>, never inside the
    /// tool's own arguments: injecting a field into <c>arguments</c> would send
    /// the server a payload its declared schema never described, and would change
    /// the fingerprint of the very call we are trying to identify.
    /// </summary>
    public async Task<McpToolResult> CallToolAsync(
        string toolName,
        JsonElement arguments,
        string? idempotencyKey,
        CancellationToken ct = default)
    {
        var response = await _transport.SendRequestAsync("tools/call",
            idempotencyKey is { Length: > 0 } key
                ? new
                {
                    name = toolName,
                    arguments,
                    _meta = new { idempotencyKey = key }
                }
                : (object)new { name = toolName, arguments }, ct);

        if (response.Error is not null)
            return new McpToolResult { Content = response.Error.ToString()!, IsError = true };

        var isError = false;
        var text = new StringBuilder();
        if (response.Result is { } result)
        {
            if (result.TryGetProperty("isError", out var err) && err.ValueKind == JsonValueKind.True)
                isError = true;
            // MCP content: [{type:"text", text:"..."}, ...]
            if (result.TryGetProperty("content", out var content)
                && content.ValueKind == JsonValueKind.Array)
            {
                foreach (var block in content.EnumerateArray())
                {
                    if (block.TryGetProperty("text", out var t))
                        text.AppendLine(t.GetString());
                    else
                        text.AppendLine(block.GetRawText());
                }
            }
            else
            {
                text.Append(result.GetRawText());
            }
        }
        return new McpToolResult { Content = text.ToString().TrimEnd(), IsError = isError };
    }

    /// <summary>Kept here for existing tests; implementation lives with the
    /// HTTP transport it belongs to.</summary>
    internal static string ExtractSseData(string sseBody) =>
        HttpMcpTransport.ExtractSseData(sseBody);

    public ValueTask DisposeAsync() => _transport.DisposeAsync();
}

public sealed class McpInitResult
{
    public string ServerName { get; set; } = "";
    public string ServerVersion { get; set; } = "";
    public string ProtocolVersion { get; set; } = "";
    public McpServerCapabilities Capabilities { get; set; } = McpServerCapabilities.None;
}

/// <summary>What the server said it can do. Anything not declared here is
/// refused with CapabilityUnavailable rather than attempted.</summary>
public sealed record McpServerCapabilities(
    bool Tools,
    bool Resources,
    bool ResourceSubscriptions,
    bool Prompts,
    bool Logging,
    bool Completions)
{
    public static McpServerCapabilities None { get; } =
        new(false, false, false, false, false, false);

    public static McpServerCapabilities Parse(JsonElement capabilities)
    {
        if (capabilities.ValueKind != JsonValueKind.Object)
            return None;
        var resources = capabilities.TryGetProperty("resources", out var resourceCaps)
                        && resourceCaps.ValueKind == JsonValueKind.Object;
        return new McpServerCapabilities(
            capabilities.TryGetProperty("tools", out var tools)
            && tools.ValueKind == JsonValueKind.Object,
            resources,
            resources
            && resourceCaps.TryGetProperty("subscribe", out var subscribe)
            && subscribe.ValueKind == JsonValueKind.True,
            capabilities.TryGetProperty("prompts", out var prompts)
            && prompts.ValueKind == JsonValueKind.Object,
            capabilities.TryGetProperty("logging", out var logging)
            && logging.ValueKind == JsonValueKind.Object,
            capabilities.TryGetProperty("completions", out var completions)
            && completions.ValueKind == JsonValueKind.Object);
    }
}

public sealed class McpResourceDescriptor
{
    public string Uri { get; set; } = "";
    public string? Name { get; set; }
    public string? MimeType { get; set; }
}

public sealed class McpResourceContent
{
    public string Uri { get; set; } = "";
    public string MimeType { get; set; } = "text/plain";
    public string Text { get; set; } = "";
    public bool Truncated { get; set; }
}

public sealed class McpPromptDescriptor
{
    public string Name { get; set; } = "";
    public string? Description { get; set; }
}

public sealed class McpPromptContent
{
    public string Name { get; set; } = "";
    public string Text { get; set; } = "";
}

public sealed class McpToolDefinition
{
    public string Name { get; set; } = "";
    public string? Description { get; set; }
    public string InputSchema { get; set; } = "{}";
}

public sealed class McpToolResult
{
    public string Content { get; set; } = "";
    public bool IsError { get; set; }
}

public sealed class McpProtocolException : Exception
{
    public McpProtocolException(string message) : base(message)
    {
    }
}

/// <summary>
/// The connector rejected the credential.
///
/// Distinct from a protocol fault because the remedy is: reconnect the account.
/// Collapsing it into "the server is unreachable" tells the user to wait for
/// something that will never get better on its own. The provider's response body
/// is deliberately not carried — a server refusing a token routinely echoes it.
/// </summary>
public sealed class McpAuthorizationException : Exception
{
    public McpAuthorizationException(string message) : base(message)
    {
    }
}

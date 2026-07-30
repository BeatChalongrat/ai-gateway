using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>
/// Every MCP operation the runtime knows how to dispatch. An operation that is
/// not on this list cannot reach a server: the durable gateway switches on it,
/// so adding a protocol method is a deliberate act with a matching AgentStep,
/// not an incidental new call site.
/// </summary>
public enum McpOperationKind
{
    Initialize,
    ToolsList,
    ToolsCall,
    ResourcesList,
    ResourcesRead,
    PromptsList,
    PromptsGet,
    Cancel,
    Reconnect,
    Reconcile,
    Shutdown,

    // Declared so an unsupported feature is a NAMED operation that returns
    // CapabilityUnavailable, rather than something we quietly never mention.
    ToolsListPaginated,
    ResourcesSubscribe,
    ResourcesUnsubscribe,
    CompletionComplete
}

/// <summary>
/// Side-effect class of an MCP operation. The distinction that matters is not
/// "read vs write" but WHAT A DISCONNECT MEANS: a read that never answered can
/// be asked again, while a mutation that never answered may already have
/// happened.
/// </summary>
public enum McpRiskClass
{
    /// <summary>Proven to have no side effect by the connector's approved
    /// policy. Only a policy statement can put a tool here — a server saying so
    /// about itself cannot.</summary>
    ReadOnly,

    /// <summary>Read-shaped and safe to repeat, but not policy-proven. Repeating
    /// it is harmless; believing a lost answer is not.</summary>
    IdempotentRead,

    /// <summary>Changes state inside the workspace/sandbox only.</summary>
    LocalMutation,

    /// <summary>Changes state somebody else can see — a sent message, a created
    /// ticket, an uploaded file.</summary>
    ExternalMutation,

    /// <summary>Removes or overwrites state, or moves money.</summary>
    Destructive,

    /// <summary>Nothing established its class. Treated as consequential.</summary>
    Unknown
}

/// <summary>
/// What the runtime concluded about one MCP operation. Deliberately distinct
/// from "the call threw": a throw can mean the server never saw the request or
/// that it acted and the answer was lost, and those are not the same fact.
/// </summary>
public enum McpOperationStatus
{
    Succeeded,

    /// <summary>Provably did not reach the server. Safe to retry.</summary>
    FailedNoEffect,

    /// <summary>Reached the server; whether it acted is unknown. Never retried
    /// automatically.</summary>
    UnknownOutcome,

    /// <summary>Unknown and not resolvable by machine. A person must decide.</summary>
    NeedsUser,

    /// <summary>The server, the protocol version, or this build does not offer
    /// the feature. Structured, never silent.</summary>
    CapabilityUnavailable,

    /// <summary>Refused before dispatch by trust, permission, or durable policy.</summary>
    Blocked
}

/// <summary>How far an MCP request got. Mirrors the transport's dispatch stage in
/// terms the durable journal can persist.</summary>
public enum McpDispatchPhase
{
    Prepared,
    TrustValidated,
    IntentRecorded,
    Dispatched,
    Observed,
    Reconciled,
    Recorded
}

/// <summary>
/// The record written BEFORE any byte leaves for the server, so a process that
/// dies mid-dispatch still leaves behind everything needed to ask the server
/// afterwards what actually happened.
/// </summary>
public sealed record McpDispatchIntent
{
    public required string IntentId { get; init; }
    public required string RunId { get; init; }
    public required string ActionId { get; init; }
    public required string ToolCallId { get; init; }
    public required string ConnectorId { get; init; }
    public required McpOperationKind Operation { get; init; }
    public required string ToolName { get; init; }
    public required McpRiskClass Risk { get; init; }

    /// <summary>Stable hash of connector + tool + arguments. The server-side
    /// lookup key when the answer is lost.</summary>
    public required string ArgumentsFingerprint { get; init; }

    /// <summary>Client-generated key sent WITH the mutation so the server can
    /// recognise a repeat as the same act.</summary>
    public required string IdempotencyKey { get; init; }

    public required DateTimeOffset CreatedUtc { get; init; }
    public string? VerificationTool { get; init; }
    public string? ExpectedStateValue { get; init; }

    /// <summary>Set once a response names the object. Absent after a lost
    /// answer, which is exactly why fingerprint lookup exists.</summary>
    public string? ReturnedObjectId { get; init; }

    public McpDispatchPhase Phase { get; init; } = McpDispatchPhase.Prepared;

    /// <summary>Null while the intent is open. An open intent for a mutation is
    /// what stops a restart from dispatching it a second time.</summary>
    public McpOperationStatus? Resolution { get; init; }

    public string? ResolutionReason { get; init; }
    public DateTimeOffset? ResolvedUtc { get; init; }

    [JsonIgnore]
    public bool IsOpen => Resolution is null;

    public static string Fingerprint(string connectorId, string toolName, string argumentsJson) =>
        Convert.ToHexString(SHA256.HashData(
            Encoding.UTF8.GetBytes($"{connectorId}|{toolName}|{argumentsJson}")))[..32];
}

/// <summary>One MCP operation, as asked for by a run.</summary>
public sealed record McpOperationRequest
{
    public required string ConnectorId { get; init; }
    public required McpOperationKind Operation { get; init; }

    /// <summary>tools/call only.</summary>
    public string? ToolName { get; init; }

    /// <summary>resources/read only.</summary>
    public string? ResourceUri { get; init; }

    /// <summary>prompts/get only.</summary>
    public string? PromptName { get; init; }

    public string ArgumentsJson { get; init; } = "{}";

    /// <summary>Correlates the operation with the model's tool call when there is
    /// one; generated otherwise.</summary>
    public string? ToolCallId { get; init; }

    /// <summary>Name the durable runtime sees for this operation.</summary>
    public string CapabilityName => Operation switch
    {
        McpOperationKind.ToolsCall => $"mcp.{ConnectorId}.{ToolName}",
        _ => $"mcp.{ConnectorId}.{Method}"
    };

    public string Method => Operation switch
    {
        McpOperationKind.Initialize => "initialize",
        McpOperationKind.ToolsList => "tools/list",
        McpOperationKind.ToolsCall => "tools/call",
        McpOperationKind.ResourcesList => "resources/list",
        McpOperationKind.ResourcesRead => "resources/read",
        McpOperationKind.PromptsList => "prompts/list",
        McpOperationKind.PromptsGet => "prompts/get",
        McpOperationKind.Cancel => "notifications/cancelled",
        McpOperationKind.Reconnect => "reconnect",
        McpOperationKind.Reconcile => "reconcile",
        McpOperationKind.Shutdown => "shutdown",
        McpOperationKind.ToolsListPaginated => "tools/list#cursor",
        McpOperationKind.ResourcesSubscribe => "resources/subscribe",
        McpOperationKind.ResourcesUnsubscribe => "resources/unsubscribe",
        McpOperationKind.CompletionComplete => "completion/complete",
        _ => "unknown"
    };
}

/// <summary>Outcome of one MCP operation, with the durable identifiers that prove
/// it went through the runtime rather than around it.</summary>
public sealed record McpOperationResult
{
    public required McpOperationStatus Status { get; init; }
    public required string ConnectorId { get; init; }
    public required McpOperationKind Operation { get; init; }

    /// <summary>Content is ALWAYS an untrusted observation — it is wrapped before
    /// it gets here.</summary>
    public string Content { get; init; } = "";

    public string? FailureCode { get; init; }
    public string? FailureReason { get; init; }

    /// <summary>The durable run that owns this operation.</summary>
    public string? RunId { get; init; }

    /// <summary>The AgentStep (TaskAction) this operation was recorded under.
    /// Its absence means the operation did not go through the runtime, which is
    /// itself a failure.</summary>
    public string? ActionId { get; init; }

    public string? IntentId { get; init; }
    public McpRiskClass Risk { get; init; } = McpRiskClass.Unknown;
    public McpDispatchPhase Phase { get; init; } = McpDispatchPhase.Prepared;
    public bool Retryable { get; init; }
    public bool RequiresUser => Status == McpOperationStatus.NeedsUser;
    public IReadOnlyList<string> Evidence { get; init; } = [];

    /// <summary>True when the answer was established by reading the server's
    /// state back rather than by the original call succeeding.</summary>
    public bool ReconciledWithoutRedispatch { get; init; }
}

/// <summary>
/// Features this build implements, per negotiated protocol version. Anything
/// absent here is answered with CapabilityUnavailable — the point is that the
/// matrix is data the tests can read, not prose in a report.
/// </summary>
public static class McpProtocolFeatureMatrix
{
    public const string PreferredVersion = "2025-06-18";

    public static IReadOnlySet<string> SupportedVersions { get; } =
        new HashSet<string>(StringComparer.Ordinal) { "2025-06-18", "2025-03-26" };

    /// <summary>Implemented and exercised end to end.</summary>
    public static IReadOnlySet<McpOperationKind> Implemented { get; } =
        new HashSet<McpOperationKind>
        {
            McpOperationKind.Initialize,
            McpOperationKind.ToolsList,
            McpOperationKind.ToolsCall,
            McpOperationKind.ResourcesList,
            McpOperationKind.ResourcesRead,
            McpOperationKind.PromptsList,
            McpOperationKind.PromptsGet,
            McpOperationKind.Cancel,
            McpOperationKind.Reconnect,
            McpOperationKind.Reconcile,
            McpOperationKind.Shutdown
        };

    /// <summary>Named, refused, and reported — never silently ignored.</summary>
    public static IReadOnlyDictionary<McpOperationKind, string> Unsupported { get; } =
        new Dictionary<McpOperationKind, string>
        {
            [McpOperationKind.ToolsListPaginated] =
                "Cursor pagination for tools/list is not implemented; this build reads a "
                + "single page and reports the cursor rather than silently dropping the rest.",
            [McpOperationKind.ResourcesSubscribe] =
                "Resource subscriptions are not implemented; a server declaring "
                + "subscribe:true is still only read on demand.",
            [McpOperationKind.ResourcesUnsubscribe] =
                "Resource subscriptions are not implemented, so there is nothing to "
                + "unsubscribe from.",
            [McpOperationKind.CompletionComplete] =
                "Argument completions are not implemented."
        };

    public static bool IsImplemented(McpOperationKind kind) => Implemented.Contains(kind);

    public static string UnsupportedReason(McpOperationKind kind) =>
        Unsupported.TryGetValue(kind, out var reason)
            ? reason
            : $"'{kind}' is not implemented by this build.";
}

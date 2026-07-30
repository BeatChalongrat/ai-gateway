using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

/// <summary>
/// What a server said about its own tools. MCP lets a server annotate a tool as
/// read-only or destructive; those annotations are a HINT FROM THE THING BEING
/// GATED, so they are carried here explicitly rather than folded into the
/// classification, and they may only ever make the verdict stricter.
/// </summary>
public sealed record McpToolAnnotations(
    bool? ReadOnlyHint = null,
    bool? DestructiveHint = null,
    bool? IdempotentHint = null,
    bool? OpenWorldHint = null);

/// <summary>
/// The per-connector classification the user approved when they trusted the
/// connector. This is the only source that can establish <see
/// cref="McpRiskClass.ReadOnly"/>.
/// </summary>
public sealed record ConnectorRiskPolicy
{
    public required string ConnectorId { get; init; }

    /// <summary>False for a connector that was installed without a reviewed
    /// policy. Everything then falls back to heuristics and Unknown, and no
    /// server hint can rescue it.</summary>
    public bool PolicyDeclared { get; init; }

    public IReadOnlyList<string> ReadOnlyTools { get; init; } = [];
    public IReadOnlyList<string> LocalMutationTools { get; init; } = [];
    public IReadOnlyList<string> ExternalMutationTools { get; init; } = [];
    public IReadOnlyList<string> DestructiveTools { get; init; } = [];

    /// <summary>mutation tool → tool that can read the result back. Without an
    /// entry, a lost answer for that mutation can only end as NeedsUser.</summary>
    public IReadOnlyDictionary<string, string> VerificationTools { get; init; } =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

    public static ConnectorRiskPolicy Undeclared(string connectorId) =>
        new() { ConnectorId = connectorId, PolicyDeclared = false };

    public string? VerificationToolFor(string toolName) =>
        VerificationTools.TryGetValue(toolName, out var verification) ? verification : null;
}

/// <summary>
/// The classification plus what may be done with it. Retry rules live here and
/// not at the call sites so a new call site cannot invent a laxer one.
/// </summary>
public sealed record McpRiskAssessment(
    McpRiskClass Risk,
    bool PolicyValidated,
    bool MayRetryBeforeDispatch,
    bool MayRetryAfterDispatch,
    bool RequiresProvenanceValidation,
    bool RequiresReconciliation,
    bool RequiresExplicitApproval,
    string Reason,
    IReadOnlyList<string> Evidence)
{
    public SideEffectClass SideEffectClass => Risk switch
    {
        McpRiskClass.ReadOnly or McpRiskClass.IdempotentRead => SideEffectClass.ReadOnly,
        McpRiskClass.LocalMutation => SideEffectClass.ReversibleMediumRisk,
        McpRiskClass.ExternalMutation => SideEffectClass.Consequential,
        McpRiskClass.Destructive => SideEffectClass.Irreversible,
        _ => SideEffectClass.Consequential
    };
}

/// <summary>
/// Decides what an MCP tool may do to the world, and therefore what a lost
/// answer permits.
///
/// The rule that shapes everything: a server's own annotation never lowers the
/// verdict. A compromised or merely optimistic server that labels
/// <c>delete_everything</c> as read-only would otherwise buy itself a free
/// automatic retry after a disconnect. Only the connector policy the user
/// approved can establish that a tool is harmless; without one, read-shaped
/// names get <see cref="McpRiskClass.IdempotentRead"/> at best and everything
/// else is <see cref="McpRiskClass.Unknown"/>.
/// </summary>
public static class McpRiskPolicy
{
    // Verbs, not nouns. "invoice" as a noun appears in get_invoice as readily as
    // in charge_invoice, so a noun here would make every reader destructive.
    private static readonly string[] DestructiveHints =
        ["delete", "remove", "drop", "purge", "destroy", "revoke", "wipe", "truncate",
         "pay", "charge", "refund", "purchase", "transfer"];

    private static readonly string[] ExternalHints =
        ["send", "post", "publish", "email", "message", "upload", "notify", "invite",
         "share", "tweet", "comment", "deploy"];

    private static readonly string[] LocalMutationHints =
        ["write", "save", "create", "update", "set", "edit", "rename", "move", "patch"];

    private static readonly string[] ReadPrefixes =
        ["get", "list", "read", "search", "find", "query", "fetch", "describe",
         "show", "ping", "lookup", "count", "resolve", "status"];

    public static McpRiskAssessment Classify(
        ConnectorRiskPolicy policy,
        string toolName,
        string? description = null,
        McpToolAnnotations? serverAnnotations = null)
    {
        ArgumentNullException.ThrowIfNull(policy);
        var evidence = new List<string>
        {
            $"connector={policy.ConnectorId}",
            $"tool={toolName}",
            $"connector_policy={(policy.PolicyDeclared ? "declared" : "absent")}"
        };

        var (declared, declaredReason) = FromConnectorPolicy(policy, toolName);
        var (heuristic, heuristicReason) = FromHeuristics(toolName, description);

        McpRiskClass risk;
        bool policyValidated;
        string reason;
        if (declared is { } fromPolicy)
        {
            // The approved policy is authoritative, but it cannot make a tool
            // safer than the name plainly says it is: a policy that lists
            // "delete_workspace" as read-only is a mistake, not a permission.
            // Unknown is not such a contradiction — it is the absence of an
            // opinion, and must not override one that exists.
            risk = heuristic == McpRiskClass.Unknown
                ? fromPolicy
                : Stricter(fromPolicy, heuristic);
            policyValidated = risk == fromPolicy;
            reason = risk == fromPolicy
                ? declaredReason
                : $"{heuristicReason} The connector policy claimed {fromPolicy}, which is "
                  + "less strict than the name implies, so the stricter class stands.";
            evidence.Add($"policy_class={fromPolicy}");
        }
        else
        {
            // No declaration: a read-shaped name is at best repeat-safe, never
            // proven side-effect-free.
            risk = heuristic == McpRiskClass.ReadOnly ? McpRiskClass.IdempotentRead : heuristic;
            policyValidated = false;
            reason = heuristicReason;
        }
        evidence.Add($"heuristic_class={heuristic}");

        if (serverAnnotations is { } annotations)
        {
            if (annotations.DestructiveHint == true)
            {
                risk = Stricter(risk, McpRiskClass.Destructive);
                evidence.Add("server_hint=destructive_accepted");
            }
            if (annotations.ReadOnlyHint == true)
                // Accepted only as corroboration of a policy statement we already
                // have. On its own it changes nothing.
                evidence.Add(
                    policyValidated && risk == McpRiskClass.ReadOnly
                        ? "server_hint=read_only_corroborated"
                        : "server_hint=read_only_rejected_unvalidated");
            if (annotations.IdempotentHint == true && risk is not McpRiskClass.ReadOnly)
                evidence.Add("server_hint=idempotent_noted_not_trusted");
        }

        var isRead = risk is McpRiskClass.ReadOnly or McpRiskClass.IdempotentRead;
        return new McpRiskAssessment(
            Risk: risk,
            PolicyValidated: policyValidated,
            // A request that never left the queue provably did nothing, whatever
            // its class. That is the one retry that is always safe.
            MayRetryBeforeDispatch: true,
            // After dispatch only a read may be repeated, and only once its
            // provenance is checked — a repeat must be answered by the same
            // connector and generation we asked, not by whatever is there now.
            MayRetryAfterDispatch: isRead,
            RequiresProvenanceValidation: isRead,
            RequiresReconciliation: !isRead,
            // Unknown is treated as consequential: conservative permission, and
            // no automatic mutation retry.
            RequiresExplicitApproval: !isRead,
            Reason: reason,
            Evidence: evidence);
    }

    /// <summary>
    /// Classifies a durable capability name of the form
    /// <c>mcp.&lt;connector&gt;.&lt;tool-or-method&gt;</c>, for the places that only
    /// have the recorded name — notably the durable runtime's own approval gate.
    ///
    /// Protocol methods are reads by construction. Everything else is a tool call
    /// and gets the conservative treatment: with no connector policy in hand here,
    /// the best a read-shaped name can earn is IdempotentRead, and an unrecognised
    /// one stays Unknown, which the runtime treats as needing approval.
    /// </summary>
    public static McpRiskAssessment ClassifyCapabilityName(string capabilityName)
    {
        var target = capabilityName.StartsWith("mcp.", StringComparison.Ordinal)
            ? capabilityName[4..] is var rest
              && rest.IndexOf('.', StringComparison.Ordinal) is var separator
              && separator >= 0
                ? rest[(separator + 1)..]
                : rest
            : capabilityName;

        if (ProtocolReadMethods.Contains(target))
            return new McpRiskAssessment(
                McpRiskClass.IdempotentRead,
                PolicyValidated: false,
                MayRetryBeforeDispatch: true,
                MayRetryAfterDispatch: true,
                RequiresProvenanceValidation: true,
                RequiresReconciliation: false,
                RequiresExplicitApproval: false,
                Reason: "MCP protocol discovery methods do not change server state.",
                Evidence: [$"method={target}", "class=protocol_read"]);

        return Classify(ConnectorRiskPolicy.Undeclared("unknown"), target);
    }

    private static readonly HashSet<string> ProtocolReadMethods =
        new(StringComparer.Ordinal)
        {
            "initialize", "tools/list", "resources/list", "resources/read",
            "prompts/list", "prompts/get", "notifications/cancelled",
            "reconnect", "reconcile", "shutdown",
            // Not implemented, but still reads: they must reach the gateway and
            // come back as CapabilityUnavailable rather than being stopped at
            // the approval gate, which would say nothing about why.
            "tools/list#cursor", "resources/subscribe", "resources/unsubscribe",
            "completion/complete"
        };

    /// <summary>Verification tool for a mutation, from the approved policy only.
    /// A server naming its own verifier would let it point us at a tool that
    /// always says "yes".</summary>
    public static string? ResolveVerificationTool(ConnectorRiskPolicy policy, string toolName) =>
        policy.PolicyDeclared ? policy.VerificationToolFor(toolName) : null;

    private static (McpRiskClass? Class, string Reason) FromConnectorPolicy(
        ConnectorRiskPolicy policy,
        string toolName)
    {
        if (!policy.PolicyDeclared)
            return (null,
                "The connector has no approved risk policy, so nothing about this tool is "
                + "established.");
        if (Contains(policy.DestructiveTools, toolName))
            return (McpRiskClass.Destructive, "The connector policy classifies this tool as destructive.");
        if (Contains(policy.ExternalMutationTools, toolName))
            return (McpRiskClass.ExternalMutation,
                "The connector policy classifies this tool as an externally visible mutation.");
        if (Contains(policy.LocalMutationTools, toolName))
            return (McpRiskClass.LocalMutation,
                "The connector policy classifies this tool as a local mutation.");
        if (Contains(policy.ReadOnlyTools, toolName))
            return (McpRiskClass.ReadOnly,
                "The approved connector policy establishes this tool as read-only.");
        return (null,
            "The connector has an approved policy but does not classify this tool.");
    }

    private static (McpRiskClass Class, string Reason) FromHeuristics(
        string toolName,
        string? description)
    {
        var name = toolName.ToLowerInvariant();
        var text = $"{toolName} {description}".ToLowerInvariant();
        // The read check comes first, but only for a name that is read-shaped AND
        // carries no mutating verb anywhere in it — "search_and_delete" must not
        // ride in on its prefix.
        if (ReadPrefixes.Any(prefix => name.StartsWith(prefix, StringComparison.Ordinal))
            && !DestructiveHints.Any(name.Contains)
            && !ExternalHints.Any(name.Contains))
            return (McpRiskClass.ReadOnly, "The tool's name is read-shaped.");
        if (DestructiveHints.Any(text.Contains))
            return (McpRiskClass.Destructive,
                "The tool's name or description describes removing, overwriting, or paying.");
        if (ExternalHints.Any(text.Contains))
            return (McpRiskClass.ExternalMutation,
                "The tool's name or description describes an externally visible action.");
        if (LocalMutationHints.Any(text.Contains))
            return (McpRiskClass.LocalMutation,
                "The tool's name or description describes changing stored state.");
        return (McpRiskClass.Unknown,
            "Nothing establishes what this tool does, so it is treated as consequential.");
    }

    private static McpRiskClass Stricter(McpRiskClass left, McpRiskClass right) =>
        Severity(left) >= Severity(right) ? left : right;

    private static int Severity(McpRiskClass risk) => risk switch
    {
        McpRiskClass.ReadOnly => 0,
        McpRiskClass.IdempotentRead => 1,
        McpRiskClass.LocalMutation => 2,
        McpRiskClass.Unknown => 3,
        McpRiskClass.ExternalMutation => 4,
        McpRiskClass.Destructive => 5,
        _ => 3
    };

    private static bool Contains(IReadOnlyList<string> names, string toolName) =>
        names.Any(name => string.Equals(name, toolName, StringComparison.OrdinalIgnoreCase));
}

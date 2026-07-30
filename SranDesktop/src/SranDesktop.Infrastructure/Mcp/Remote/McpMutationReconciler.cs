using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Infrastructure.Mcp.Remote;

public enum McpMutationVerdict
{
    /// <summary>The change is confirmed to exist.</summary>
    Succeeded,

    /// <summary>Confirmed NOT to have happened — safe to retry.</summary>
    FailedNoEffect,

    /// <summary>Cannot be determined. Only a human resolves this.</summary>
    NeedsUser
}

/// <summary>
/// Everything needed to ask a server, after the fact, "did this actually
/// happen?". Persisted BEFORE dispatch so a reconnect after a crash still knows
/// what to look for.
/// </summary>
public sealed record McpMutationIntent
{
    public required string ConnectorId { get; init; }
    public required string ToolName { get; init; }
    public required string ArgumentsFingerprint { get; init; }
    public required string IdempotencyKey { get; init; }
    public string? Account { get; init; }
    public required DateTimeOffset DispatchedUtc { get; init; }

    /// <summary>Tool that can read the state back, when the connector has one.
    /// Its absence is why some mutations can only end as NeedsUser.</summary>
    public string? VerificationTool { get; init; }

    /// <summary>Field in the mutation result that identifies the object.</summary>
    public string? ObjectIdField { get; init; }

    /// <summary>Object id, once a response has been seen.</summary>
    public string? ReturnedObjectId { get; init; }

    /// <summary>Expected value used to confirm the object really is ours.</summary>
    public string? ExpectedStateValue { get; init; }

    public static string Fingerprint(string connectorId, string toolName, string argumentsJson) =>
        Convert.ToHexString(SHA256.HashData(
            Encoding.UTF8.GetBytes($"{connectorId}|{toolName}|{argumentsJson}")))[..32];
}

public sealed record McpMutationOutcome(
    McpMutationVerdict Verdict,
    string? ObjectId,
    string Reason,
    IReadOnlyList<string> Evidence);

public interface IMcpMutationReconciler
{
    Task<McpMutationOutcome> ReconcileAsync(
        McpRemoteClient client,
        McpMutationIntent intent,
        CancellationToken ct);
}

/// <summary>
/// Answers "did the mutation land?" by READING THE SERVER'S STATE BACK, never by
/// trusting the original call.
///
/// The conservative default is deliberate: when a connector exposes no way to
/// verify, the answer is <see cref="McpMutationVerdict.NeedsUser"/>. Guessing
/// "probably failed" would invite a retry that duplicates a sent message or a
/// created record; guessing "probably succeeded" would report work that may
/// never have happened.
/// </summary>
public sealed class McpMutationReconciler(ILogger? logger = null) : IMcpMutationReconciler
{
    public async Task<McpMutationOutcome> ReconcileAsync(
        McpRemoteClient client,
        McpMutationIntent intent,
        CancellationToken ct)
    {
        var evidence = new List<string>
        {
            $"connector={intent.ConnectorId}",
            $"tool={intent.ToolName}",
            $"idempotency_key={intent.IdempotencyKey}",
            $"arguments_fingerprint={intent.ArgumentsFingerprint}"
        };

        if (intent.VerificationTool is not { Length: > 0 } verificationTool)
            return new McpMutationOutcome(
                McpMutationVerdict.NeedsUser,
                intent.ReturnedObjectId,
                "This connector exposes no way to read the state back, so whether the "
                + "change happened cannot be established. A person must confirm it.",
                [.. evidence, "verification_capability=absent"]);

        if (intent.ReturnedObjectId is not { Length: > 0 } objectId)
            // The answer was lost before it named the object, so there is nothing
            // to look up. Retrying blind could create a second one.
            return new McpMutationOutcome(
                McpMutationVerdict.NeedsUser,
                null,
                "The mutation was dispatched but no object id came back, so the created "
                + "or modified object cannot be located. A person must confirm it.",
                [.. evidence, "object_id=unknown"]);

        try
        {
            var arguments = JsonSerializer.SerializeToElement(
                new Dictionary<string, object?> { ["id"] = objectId });
            var result = await client.CallToolAsync(verificationTool, arguments, ct);
            evidence.Add($"verification_tool={verificationTool}");

            if (result.IsError)
                return new McpMutationOutcome(
                    McpMutationVerdict.NeedsUser,
                    objectId,
                    "The verification read itself failed, so the mutation's outcome is "
                    + "still unknown.",
                    [.. evidence, "verification_read=error"]);

            var found = LooksFound(result.Content);
            if (found == false)
                return new McpMutationOutcome(
                    McpMutationVerdict.FailedNoEffect,
                    objectId,
                    "The server reports the object does not exist, so the mutation did "
                    + "not take effect.",
                    [.. evidence, "verification_read=absent"]);
            if (found == true && MatchesExpectedState(result.Content, intent))
                return new McpMutationOutcome(
                    McpMutationVerdict.Succeeded,
                    objectId,
                    "The object was read back and matches what was requested.",
                    [.. evidence, "verification_read=confirmed"]);

            return new McpMutationOutcome(
                McpMutationVerdict.NeedsUser,
                objectId,
                "The object exists but does not match the requested state; a person must "
                + "decide what happened.",
                [.. evidence, "verification_read=mismatch"]);
        }
        catch (Exception ex) when (
            ex is McpProtocolException or McpDispatchException or OperationCanceledException)
        {
            logger?.LogWarning(
                "MCP reconciliation for {Tool} could not complete: {Message}",
                intent.ToolName,
                ex.Message);
            return new McpMutationOutcome(
                McpMutationVerdict.NeedsUser,
                objectId,
                $"Reconciliation could not reach the server ({ex.GetType().Name}); the "
                + "mutation's outcome remains unknown.",
                [.. evidence, "verification_read=unreachable"]);
        }
    }

    /// <summary>Null when the answer does not clearly say either way — which is
    /// itself a reason to escalate rather than assume.</summary>
    private static bool? LooksFound(string content)
    {
        if (content.Contains("\"found\":false", StringComparison.OrdinalIgnoreCase))
            return false;
        if (content.Contains("\"found\":true", StringComparison.OrdinalIgnoreCase))
            return true;
        return null;
    }

    private static bool MatchesExpectedState(string content, McpMutationIntent intent) =>
        intent.ExpectedStateValue is not { Length: > 0 } expected
        || content.Contains(expected, StringComparison.Ordinal);
}

/// <summary>Side-effect class of an MCP tool. Anything not proven read-only is
/// treated as consequential, because an unknown tool that turns out to send a
/// message must never have been auto-retried.</summary>
public enum McpToolRisk
{
    ReadOnly,
    LocalMutation,
    ExternalMutation,
    Destructive,
    FinancialOrHighImpact,
    CredentialSensitive,
    Unknown
}

public static class McpToolRiskPolicy
{
    private static readonly string[] DestructiveHints =
        ["delete", "remove", "drop", "purge", "destroy", "revoke", "wipe"];

    private static readonly string[] ExternalHints =
        ["send", "post", "publish", "email", "message", "upload", "notify", "invite", "share"];

    private static readonly string[] FinancialHints =
        ["pay", "charge", "invoice", "refund", "order", "purchase", "transfer", "subscribe"];

    private static readonly string[] CredentialHints =
        ["token", "secret", "credential", "password", "apikey", "api_key", "auth"];

    private static readonly string[] ReadOnlyHints =
        ["get", "list", "read", "search", "find", "query", "fetch", "describe", "show", "ping"];

    public static McpToolRisk Classify(string toolName, string? description = null)
    {
        var text = $"{toolName} {description}".ToLowerInvariant();
        if (CredentialHints.Any(text.Contains))
            return McpToolRisk.CredentialSensitive;
        if (FinancialHints.Any(text.Contains))
            return McpToolRisk.FinancialOrHighImpact;
        if (DestructiveHints.Any(text.Contains))
            return McpToolRisk.Destructive;
        if (ExternalHints.Any(text.Contains))
            return McpToolRisk.ExternalMutation;
        if (ReadOnlyHints.Any(hint =>
                toolName.StartsWith(hint, StringComparison.OrdinalIgnoreCase)))
            return McpToolRisk.ReadOnly;
        // Everything else is unknown, NOT read-only.
        return McpToolRisk.Unknown;
    }

    /// <summary>Only a provably read-only tool may be retried without asking the
    /// server what happened first.</summary>
    public static bool MayRetryWithoutReconciliation(McpToolRisk risk) =>
        risk == McpToolRisk.ReadOnly;

    public static SideEffectClass ToSideEffectClass(McpToolRisk risk) => risk switch
    {
        McpToolRisk.ReadOnly => SideEffectClass.ReadOnly,
        McpToolRisk.LocalMutation => SideEffectClass.ReversibleMediumRisk,
        McpToolRisk.Destructive or McpToolRisk.FinancialOrHighImpact =>
            SideEffectClass.Irreversible,
        McpToolRisk.CredentialSensitive => SideEffectClass.CredentialSensitive,
        _ => SideEffectClass.Consequential
    };
}

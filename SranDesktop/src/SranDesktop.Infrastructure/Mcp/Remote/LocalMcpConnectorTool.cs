using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// A tool on a LOCAL (sandboxed stdio) MCP connector, exposed to the agent.
///
/// It carries no protocol logic of its own on purpose: everything — trust, risk
/// classification, the recorded intent, the isolated dispatch, reconciliation —
/// happens inside <see cref="IMcpDurableGateway"/>. A tool class that dialled the
/// server itself is exactly the bypass this phase removes, so this one only
/// translates between the agent's tool contract and an
/// <see cref="McpOperationRequest"/>.
/// </summary>
public sealed class LocalMcpConnectorTool : IMcpTool
{
    private readonly IMcpDurableGateway _gateway;
    private readonly string _connectorId;
    private readonly string _remoteToolName;

    public LocalMcpConnectorTool(
        IMcpDurableGateway gateway,
        string connectorId,
        string remoteToolName,
        string description,
        JsonElement schema)
    {
        _gateway = gateway;
        _connectorId = connectorId;
        _remoteToolName = remoteToolName;
        Name = McpSchemaValidator.Namespaced(connectorId, remoteToolName);
        Description = description;
        ParametersSchema = schema;
    }

    public string Name { get; }
    public string Description { get; }
    public JsonElement ParametersSchema { get; }
    public ToolCategory Category => ToolCategory.Network;

    /// <summary>A connector's side effects are outside this machine's control, so
    /// the answer is always to ask first.</summary>
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var result = await _gateway.ExecuteAttachedAsync(
            new McpOperationRequest
            {
                ConnectorId = _connectorId,
                Operation = McpOperationKind.ToolsCall,
                ToolName = _remoteToolName,
                ArgumentsJson = arguments.GetRawText()
            },
            $"connector:{_connectorId}",
            ct);

        return result.Status switch
        {
            McpOperationStatus.Succeeded => ToolResult.Verified(
                result.Content, result.Evidence),

            // Not a failure the model should paper over: the operation reached the
            // connector, nobody can say what it did, and a person has to look.
            McpOperationStatus.NeedsUser => ToolResult.FailedEffect(
                result.FailureCode ?? "NeedsUser",
                result.FailureReason
                ?? "The connector operation's outcome could not be established. "
                + "Do not repeat it — it needs a person to confirm what happened.",
                retryable: false),

            McpOperationStatus.UnknownOutcome => ToolResult.FailedEffect(
                "UnknownOutcome",
                result.FailureReason
                ?? "The request reached the connector and its effect is unknown.",
                retryable: false),

            McpOperationStatus.CapabilityUnavailable => ToolResult.FailedEffect(
                "CapabilityUnavailable",
                result.FailureReason ?? "The connector does not offer this.",
                retryable: false),

            McpOperationStatus.Blocked => ToolResult.FailedEffect(
                result.FailureCode ?? "ConnectorBlocked",
                result.FailureReason ?? "The connector may not be used.",
                retryable: false),

            _ => ToolResult.FailedEffect(
                result.FailureCode ?? "McpFailedNoEffect",
                result.FailureReason ?? "The operation did not take effect.",
                result.Retryable)
        };
    }
}

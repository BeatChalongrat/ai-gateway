using Microsoft.Extensions.DependencyInjection;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Tests.Mcp;

namespace SranDesktop.Tests.Benchmark;

/// <summary>Durable runs for the benchmark, created the way the scheduler
/// creates them so the measurements are of the real path.</summary>
internal static class Runs
{
    public static async Task<DurableTurnContext> ScheduledAsync(
        ProductionConnectorHost host, string intent)
    {
        var conversationId = await NewConversationAsync(host, "bench");
        return await host.Runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Scheduler,
            intent,
            $"conversation:{conversationId}",
            SecurityContextJson: """{"unattended":true,"consent_fallback":"deny"}""",
            PermissionContextJson: """{"unattended":true,"allow_stored_scope_only":true}""",
            OperationIsFullySpecified: true));
    }

    public static Task ApproveAsync(
        ProductionConnectorHost host,
        DurableTurnContext context,
        string connectorId,
        string toolName,
        string argumentsJson) =>
        host.Runtime.RecordApprovalAsync(
            context,
            $"approval-{Guid.NewGuid():N}",
            $"mcp.{connectorId}.{toolName}",
            argumentsJson,
            $"connector:{connectorId}",
            granted: true,
            DateTimeOffset.UtcNow.AddMinutes(10));

    private static async Task<string> NewConversationAsync(
        ProductionConnectorHost host, string prefix)
    {
        var conversationId = $"{prefix}-{Guid.NewGuid():N}";
        await host.Services.GetRequiredService<IConversationStore>()
            .UpsertConversationAsync(new Conversation
            {
                Id = conversationId,
                Title = prefix,
                ModelId = "none",
                CreatedAt = DateTimeOffset.UtcNow,
                UpdatedAt = DateTimeOffset.UtcNow,
                Source = ConversationSource.Local
            });
        return conversationId;
    }
}

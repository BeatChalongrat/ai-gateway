using System.Text.Json;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Contract for state-mutating desktop and browser tools. The orchestrator
/// obtains immutable observations on both sides of execution and delegates
/// verification to the tool without allowing model text to supply evidence.
/// </summary>
public interface IPerceptualActionTool
{
    InteractionActionDescriptor DescribeAction(JsonElement arguments);

    Task<InteractionObservationSnapshot> ObserveBeforeActionAsync(
        JsonElement arguments,
        CancellationToken ct);

    Task<InteractionObservationSnapshot> ObserveAfterActionAsync(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        CancellationToken ct);

    ToolContractVerification VerifyAction(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after);
}

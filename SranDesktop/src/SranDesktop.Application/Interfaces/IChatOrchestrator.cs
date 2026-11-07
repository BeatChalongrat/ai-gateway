using SranDesktop.Application.Events;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

public interface IChatOrchestrator
{
    /// <summary>
    /// UI hook: invoked when a tool call needs user consent. If unsubscribed,
    /// consent-requiring calls are denied (safe default).
    /// </summary>
    event Func<ToolConsentRequest, Task<ToolConsentResponse>>? ConsentRequested;

    /// <summary>
    /// The consent handler currently subscribed to <see cref="ConsentRequested"/>
    /// (null when none). Lets a nested orchestrator (run_subagent, Phase 12 F)
    /// forward its own consent requests to the SAME app handler the parent turn
    /// uses, so delegated tool calls keep the exact same consent gate.
    /// </summary>
    Func<ToolConsentRequest, Task<ToolConsentResponse>>? ConsentHandler { get; }

    /// <summary>
    /// Runs the tool-calling loop: stream completion → execute tool calls →
    /// append results → repeat until a final answer, the iteration cap, or timeout.
    /// Mutates <paramref name="messages"/> in place (assistant + tool messages
    /// appended; old screenshots and over-budget history trimmed).
    /// <paramref name="contextWindow"/> is the model's context window in tokens;
    /// null falls back to a conservative default.
    /// </summary>
    IAsyncEnumerable<AgentStreamEvent> RunAsync(
        string model,
        List<GatewayMessage> messages,
        string conversationId,
        int? contextWindow = null,
        CancellationToken ct = default);
}

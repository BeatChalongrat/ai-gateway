using SranDesktop.Application.Events;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Opt-in surface (Phase 16 A) for a tool that runs a NESTED agent loop
/// (run_subagent) and wants its nested tool activity surfaced live inside the
/// parent tool card. ONLY SpawnAgentTool implements it; the ChatViewModel
/// consumer checks <c>is ISupportsNestedProgress</c> and subscribes — no other
/// <see cref="IMcpTool"/> implementation's signature changes.
///
/// The event carries the nested run's own <see cref="AgentStreamEvent"/>s so the
/// consumer can reuse the exact same pending→ok/failed tool-card lifecycle it
/// already applies to top-level tool calls. Depth cap 1 is preserved by the
/// publisher: nested events never mention run_subagent itself.
/// </summary>
public interface ISupportsNestedProgress
{
    /// <summary>
    /// Raised (on the executing thread) for each nested tool-call and
    /// tool-result event while a run_subagent execution is in flight. Only
    /// <see cref="AgentToolCallsEvent"/> and <see cref="AgentToolResultEvent"/>
    /// are published, and never for run_subagent itself.
    /// </summary>
    event Action<AgentStreamEvent>? NestedProgress;
}

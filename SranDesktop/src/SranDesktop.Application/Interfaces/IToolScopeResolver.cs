namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Resolves the consent scope key for a tool call (Phase A PART 4). File writes
/// resolve to their containing granted/allowed FOLDER (so "Always allow" covers
/// a whole project, not one file); shell commands resolve to their working
/// directory (per-cwd trust instead of a blanket "*"). Lives behind an
/// abstraction because the folder logic depends on the Infrastructure PathGuard,
/// which the Application-layer orchestrator must not reference directly.
/// </summary>
public interface IToolScopeResolver
{
    string Resolve(IMcpTool tool, string argumentsJson, string conversationId);
}

using System.Collections.Concurrent;

namespace SranDesktop.Application.Services;

/// <summary>
/// Per-conversation shell state (Phase A PART 3): a working directory the agent
/// can set once and RETAIN across run_command calls, so a multi-step build in a
/// project folder doesn't reset to the user profile on every call.
///
/// The active conversation for the tool call currently executing is carried in
/// an <see cref="AsyncLocal{T}"/> that flows down the orchestrator's async call
/// chain into the tool — the tool never receives a conversation id directly.
/// Nested sub-agent runs set their own id, so their cwd stays isolated. The
/// stored directory is only ever a PathGuard-validated path (the tool validates
/// before storing); this class holds no security decision of its own.
/// </summary>
public sealed class ShellSession
{
    private static readonly AsyncLocal<string?> _current = new();
    private readonly ConcurrentDictionary<string, string> _workingDirectories = new(StringComparer.Ordinal);

    /// <summary>The conversation whose turn is executing a tool right now.
    /// Set by the orchestrator immediately before each tool call.</summary>
    public string? CurrentConversationId
    {
        get => _current.Value;
        set => _current.Value = value;
    }

    public string? GetWorkingDirectory(string conversationId) =>
        _workingDirectories.TryGetValue(conversationId, out var dir) ? dir : null;

    public void SetWorkingDirectory(string conversationId, string path) =>
        _workingDirectories[conversationId] = path;
}

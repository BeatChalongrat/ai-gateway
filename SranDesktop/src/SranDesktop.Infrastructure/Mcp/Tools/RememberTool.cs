using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Cross-session agent memory (Phase B PART 3). Persists a short, durable,
/// model-authored note scoped to the CURRENT conversation — recalled in later
/// turns and after the app restarts via the system-prompt <c>## Notes</c> block.
/// No machine side effect (a local note), so AllowAlways. The active
/// conversation is read from the ambient <see cref="ShellSession"/> the
/// orchestrator sets before every tool call.
/// </summary>
public sealed class RememberTool : IMcpTool
{
    private readonly IConversationStore _store;
    private readonly ShellSession? _session;

    public RememberTool(IConversationStore store, ShellSession? session = null)
    {
        _store = store;
        _session = session;
    }

    public string Name => "remember";
    public string Description =>
        "Save a short durable note about this task or project — e.g. the build command " +
        "(\"builds with dotnet build src/X.sln\"), a discovered file path, or a user preference. " +
        "Notes are recalled in later turns AND after the app restarts, scoped to THIS conversation. " +
        "Use it to avoid re-discovering the same facts. Keep each note to one factual sentence.";
    public ToolCategory Category => ToolCategory.Network; // no machine side effect
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "note": { "type": "string", "description": "A short, factual note to remember for this conversation." }
            },
            "required": ["note"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var note = ToolHelpers.GetString(arguments, "note");
        if (string.IsNullOrWhiteSpace(note))
            return ToolResult.Fail("'note' is required.");

        var conversationId = _session?.CurrentConversationId;
        if (string.IsNullOrEmpty(conversationId))
            return ToolResult.Fail("No active conversation to attach the note to.");

        await _store.AddNoteAsync(conversationId, note.Trim(), ct);
        return ToolResult.Ok("Saved a note for this conversation.");
    }
}

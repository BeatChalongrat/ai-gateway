using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Cross-session agent memory (Phase B PART 3). Persists a short, durable,
/// model-authored note — recalled in later turns and after the app restarts
/// via the system-prompt <c>## Notes</c> / <c>## Memory (global)</c> blocks.
/// scope=conversation (default) attaches to the CURRENT conversation (read
/// from the ambient <see cref="ShellSession"/> the orchestrator sets before
/// every tool call); scope=global stores a user-level fact that carries into
/// EVERY conversation. No machine side effect (a local note), so AllowAlways.
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
        "Save a short durable note. scope=\"conversation\" (default) for task-specific facts — " +
        "the build command of this repo (\"builds with dotnet build src/X.sln\"), a discovered " +
        "file path. scope=\"global\" for durable user-level facts that should carry into every " +
        "future conversation — the user's name or team, preferences like \"ชอบรายงานแบบสั้น\", " +
        "recurring workflows. Notes are recalled in later turns AND after the app restarts. " +
        "Keep each note to one factual sentence. Never store secrets or credentials.";
    public ToolCategory Category => ToolCategory.Network; // no machine side effect
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "note": { "type": "string", "description": "A short, factual note to remember." },
                "scope": {
                    "type": "string",
                    "enum": ["conversation", "global"],
                    "description": "conversation (default) = this task only; global = durable user-level fact recalled in ALL conversations."
                }
            },
            "required": ["note"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var note = ToolHelpers.GetString(arguments, "note");
        if (string.IsNullOrWhiteSpace(note))
            return ToolResult.Fail("'note' is required.");

        var scope = ToolHelpers.GetString(arguments, "scope") ?? "conversation";
        if (scope is not ("conversation" or "global"))
            return ToolResult.Fail("'scope' must be \"conversation\" or \"global\".");

        if (scope == "global")
        {
            await _store.AddNoteAsync(IConversationStore.GlobalNotesId, note.Trim(), ct);
            return ToolResult.Ok("Saved a global memory — it will be recalled in every conversation.");
        }

        var conversationId = _session?.CurrentConversationId;
        if (string.IsNullOrEmpty(conversationId))
            return ToolResult.Fail("No active conversation to attach the note to.");

        await _store.AddNoteAsync(conversationId, note.Trim(), ct);
        return ToolResult.Ok("Saved a note for this conversation.");
    }
}

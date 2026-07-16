using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Model-maintained plan / todo (Phase A PART 5). The model writes and updates a
/// structured task list as it works; the app renders it as a live checklist. The
/// tool has NO side effect — it validates the plan and returns a short summary —
/// so it is AllowAlways. The plan itself rides the assistant tool-call chain, so
/// PART 1 persistence carries it across turns and restarts for free.
/// </summary>
public sealed class UpdatePlanTool : IMcpTool
{
    public string Name => "update_plan";
    public string Description =>
        "Maintain a short task list for a multi-step task. Call this to lay out the steps and again to " +
        "mark progress: each step has a 'status' of pending, in_progress, or done. Keep exactly one step " +
        "in_progress at a time. Skip this for a simple one-shot answer.";
    public ToolCategory Category => ToolCategory.Network; // no local side effect
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "The full task list, in order. Re-send the whole list each time with updated statuses.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": { "type": "string", "description": "Short description of the step" },
                            "status": { "type": "string", "enum": ["pending", "in_progress", "done"] }
                        },
                        "required": ["step", "status"]
                    }
                }
            },
            "required": ["steps"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var steps = PlanParser.Parse(arguments);
        return Task.FromResult(ToolResult.Ok(PlanParser.Summarize(steps)));
    }
}

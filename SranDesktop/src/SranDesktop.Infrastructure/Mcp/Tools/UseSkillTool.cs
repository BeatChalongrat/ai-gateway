using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Lets the model load a skill's SKILL.md on demand (Phase 6 Task 2) — the
/// path that fixes Thai/paraphrase requests the exact-substring trigger
/// fast-path misses. The model sees the skill catalog in the system prompt
/// and calls this tool to fetch full instructions.
///
/// DefaultPermission is AllowAlways: reading bundled, app-authored instruction
/// text has no side effects (no file writes, no network, no shell) — it only
/// adds guidance to the model's context, exactly like the trigger fast-path
/// already does without asking. Consent still gates every ACTION the skill
/// then leads the model to take (write_file, run_command, …).
/// </summary>
public sealed class UseSkillTool : IMcpTool
{
    private readonly ISkillManager _skills;

    public UseSkillTool(ISkillManager skills) => _skills = skills;

    public string Name => "use_skill";
    public string Description =>
        "Load a skill's detailed instructions by name. Call this when a task matches a skill " +
        "listed in the Skills catalog, then follow the returned instructions.";
    public ToolCategory Category => ToolCategory.Network; // no local side effects; not a file/shell op
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "name": { "type": "string", "description": "The skill name from the Skills catalog" }
            },
            "required": ["name"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var name = ToolHelpers.GetString(arguments, "name");
        if (string.IsNullOrWhiteSpace(name))
            return ToolResult.Fail("'name' is required.");

        var skills = await _skills.GetSkillsAsync(ct);
        var skill = skills.FirstOrDefault(s =>
            string.Equals(s.Manifest.Name, name, StringComparison.OrdinalIgnoreCase));

        if (skill is null)
        {
            var available = string.Join(", ", skills.Where(s => s.IsEnabled).Select(s => s.Manifest.Name));
            return ToolResult.Fail(
                $"No skill named '{name}'. Available skills: {(available.Length > 0 ? available : "none")}.");
        }
        if (!skill.IsEnabled)
            return ToolResult.Fail($"The skill '{name}' is disabled. Ask the user to enable it in Skills settings.");

        return ToolResult.Ok(
            $"--- Skill: {skill.Manifest.DisplayName} ---\n{skill.Instructions}");
    }
}

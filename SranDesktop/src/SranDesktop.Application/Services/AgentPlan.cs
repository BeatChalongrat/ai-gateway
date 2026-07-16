using System.Text.Json;

namespace SranDesktop.Application.Services;

/// <summary>One item in the model-maintained task list (Phase A PART 5).</summary>
public sealed record PlanStep(string Step, string Status)
{
    public const string Pending = "pending";
    public const string InProgress = "in_progress";
    public const string Done = "done";

    public static string Normalize(string? status) => status?.Trim().ToLowerInvariant() switch
    {
        "in_progress" or "in-progress" or "active" or "doing" => InProgress,
        "done" or "complete" or "completed" => Done,
        _ => Pending,
    };
}

/// <summary>
/// Parses the update_plan tool's arguments into a plan. Shared by the live tool
/// path and the history-reload path so a plan re-renders identically after a
/// restart (the plan rides the durable tool chain — PART 1).
/// </summary>
public static class PlanParser
{
    public static IReadOnlyList<PlanStep> Parse(string? argumentsJson)
    {
        if (string.IsNullOrWhiteSpace(argumentsJson))
            return Array.Empty<PlanStep>();
        try
        {
            using var doc = JsonDocument.Parse(argumentsJson);
            return Parse(doc.RootElement);
        }
        catch (JsonException)
        {
            return Array.Empty<PlanStep>();
        }
    }

    public static IReadOnlyList<PlanStep> Parse(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("steps", out var steps)
            || steps.ValueKind != JsonValueKind.Array)
            return Array.Empty<PlanStep>();

        var result = new List<PlanStep>();
        foreach (var item in steps.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object)
                continue;
            var text = item.TryGetProperty("step", out var s) && s.ValueKind == JsonValueKind.String
                ? s.GetString()
                : null;
            if (string.IsNullOrWhiteSpace(text))
                continue;
            var status = item.TryGetProperty("status", out var st) && st.ValueKind == JsonValueKind.String
                ? st.GetString()
                : null;
            result.Add(new PlanStep(text!, PlanStep.Normalize(status)));
        }
        return result;
    }

    /// <summary>Short, context-cheap confirmation returned to the model.</summary>
    public static string Summarize(IReadOnlyList<PlanStep> steps)
    {
        if (steps.Count == 0)
            return "Plan cleared.";
        var done = steps.Count(s => s.Status == PlanStep.Done);
        var active = steps.Count(s => s.Status == PlanStep.InProgress);
        return $"Plan updated: {steps.Count} steps ({done} done, {active} in progress).";
    }
}

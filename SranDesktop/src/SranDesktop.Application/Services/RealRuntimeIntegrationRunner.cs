using System.Text.Json;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

public enum RuntimeIntegrationMode { DeterministicOrchestration, LiveModel }

public sealed record RuntimeAttemptReport(
    int Attempt,
    string ConversationId,
    string? TaskId,
    string? PlanId,
    int PlanVersion,
    bool GoalCoverage,
    int RequiredOutcomes,
    int VerifiedOutcomes,
    int Retries,
    int UnrelatedActions,
    bool Completed,
    IReadOnlyList<string> Failures,
    IReadOnlyList<string> ActionIds);

public sealed record RuntimeIntegrationReport(
    string Prompt,
    string Model,
    RuntimeIntegrationMode Mode,
    DateTimeOffset StartedAtUtc,
    DateTimeOffset CompletedAtUtc,
    IReadOnlyList<RuntimeAttemptReport> Attempts,
    string JsonPath,
    string CsvPath);

/// <summary>
/// Executes regression prompts through the same ChatOrchestrator and durable SQLite path as
/// the chat UI. Tool effects remain graded by runtime evidence, never by model output.
/// </summary>
public sealed class RealRuntimeIntegrationRunner
{
    private readonly IChatOrchestrator _orchestrator;
    private readonly IAgentTaskStore _tasks;

    public RealRuntimeIntegrationRunner(
        IChatOrchestrator orchestrator,
        IAgentTaskStore tasks)
    {
        _orchestrator = orchestrator;
        _tasks = tasks;
    }

    public async Task<RuntimeIntegrationReport> RunAsync(
        string prompt,
        string model,
        RuntimeIntegrationMode mode,
        int attempts,
        string outputDirectory,
        Func<int, CancellationToken, Task>? resetAttempt = null,
        CancellationToken ct = default)
    {
        if (attempts < 1) throw new ArgumentOutOfRangeException(nameof(attempts));
        var root = Path.GetFullPath(outputDirectory);
        Directory.CreateDirectory(root);
        var started = DateTimeOffset.UtcNow;
        var reports = new List<RuntimeAttemptReport>();
        for (var attempt = 1; attempt <= attempts; attempt++)
        {
            ct.ThrowIfCancellationRequested();
            if (resetAttempt is not null) await resetAttempt(attempt, ct);
            var conversationId = $"runtime-regression-{Guid.NewGuid():N}";
            var messages = new List<GatewayMessage>
            {
                new() { Role = "user", Content = prompt }
            };
            var failures = new List<string>();
            var actionIds = new List<string>();
            await foreach (var evt in _orchestrator.RunAsync(
                               model, messages, conversationId, ct: ct))
            {
                if (evt is AgentErrorEvent error)
                    failures.Add($"{error.Code ?? "error"}:{error.Message}");
                if (evt is AgentToolResultEvent
                    {
                        UnifiedResult: { } result
                    })
                {
                    actionIds.Add(result.ActionId);
                    if (!result.Verified && result.FailureCode is { Length: > 0 } code)
                        failures.Add($"{result.ActionId}:{code}");
                }
            }

            var snapshot = await _tasks.GetLatestByConversationAsync(conversationId, ct);
            var outcomes = snapshot?.Plan.Outcomes ?? [];
            var required = outcomes.Where(item => item.Required && !item.Prohibited).ToList();
            var events = snapshot is null
                ? []
                : await _tasks.GetEventsAsync(snapshot.Task.TaskId, ct);
            reports.Add(new RuntimeAttemptReport(
                attempt,
                conversationId,
                snapshot?.Task.TaskId,
                snapshot?.Plan.PlanId,
                snapshot?.Plan.Version ?? 0,
                required.Count > 0 && required.All(item =>
                    snapshot!.Plan.Milestones.SelectMany(milestone => milestone.Steps)
                        .Any(step => step.OutcomeId == item.OutcomeId)),
                required.Count,
                required.Count(item => item.Status == Domain.Entities.OutcomeStatus.Verified),
                events.Count(item => item.EventType == "RetryDispatching"),
                events.Count(item => item.EventType == "ActionRejected"
                                     && item.DataJson.Contains("GoalIrrelevantAction",
                                         StringComparison.Ordinal)),
                snapshot?.Task.Status == Domain.Entities.AgentTaskStatus.Completed
                && required.All(item => item.Status == Domain.Entities.OutcomeStatus.Verified),
                failures,
                actionIds));
        }

        var completed = DateTimeOffset.UtcNow;
        var stem = $"runtime-{mode.ToString().ToLowerInvariant()}-{started:yyyyMMdd-HHmmss}";
        var jsonPath = Path.Combine(root, stem + ".json");
        var csvPath = Path.Combine(root, stem + ".csv");
        var report = new RuntimeIntegrationReport(
            prompt, model, mode, started, completed, reports, jsonPath, csvPath);
        await File.WriteAllTextAsync(jsonPath,
            JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }), ct);
        var csv = new List<string>
        {
            "attempt,conversation_id,task_id,plan_id,plan_version,goal_coverage,required_outcomes,verified_outcomes,retries,unrelated_actions,completed,failures"
        };
        csv.AddRange(reports.Select(item => string.Join(",",
            item.Attempt,
            Csv(item.ConversationId), Csv(item.TaskId), Csv(item.PlanId), item.PlanVersion,
            item.GoalCoverage, item.RequiredOutcomes, item.VerifiedOutcomes, item.Retries,
            item.UnrelatedActions, item.Completed, Csv(string.Join(";", item.Failures)))));
        await File.WriteAllLinesAsync(csvPath, csv, ct);
        return report;
    }

    private static string Csv(string? value) =>
        $"\"{(value ?? string.Empty).Replace("\"", "\"\"", StringComparison.Ordinal)}\"";
}

using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Services;

public sealed record MockHarnessResult(
    int TotalSteps,
    int VerifiedSteps,
    int Retries,
    int PlanRevisions,
    int ResumeCount,
    bool Completed,
    bool FalseSuccess,
    TimeSpan Duration);

public sealed class MockLongHorizonHarness
{
    private readonly IDurableTaskRuntime _runtime;

    public MockLongHorizonHarness(IDurableTaskRuntime runtime) => _runtime = runtime;

    public async Task<MockHarnessResult> RunAsync(
        DurableTurnContext context,
        int stepCount,
        bool injectFailures,
        CancellationToken ct = default)
    {
        if (stepCount < 1) throw new ArgumentOutOfRangeException(nameof(stepCount));
        var started = DateTimeOffset.UtcNow;
        var descriptions = Enumerable.Range(1, stepCount)
            .Select(i => $"Verify mock installer phase {i:00}")
            .ToList();
        context = await _runtime.ApplyPlanUpdateAsync(
            context, PlanJson(descriptions), ct);

        var verified = 0;
        var retries = 0;
        var revisions = 0;
        var resumes = 0;
        for (var i = 0; i < stepCount; i++)
        {
            var args = JsonSerializer.Serialize(new
            {
                harness = "mock-installer",
                phase = i + 1,
                expected_state = descriptions[i]
            });
            var preparation = await _runtime.PrepareActionAsync(
                context, $"mock_call_{i + 1}_1", "mock_state_transition",
                ToolCategory.Network, args, ct);
            if (!preparation.Proceed)
                throw new InvalidOperationException(
                    $"Harness step {i + 1} was rejected: {preparation.FailureCode}");

            if (injectFailures && i is 6 or 21)
            {
                var failed = await _runtime.RecordResultAsync(
                    context, preparation,
                    ToolResult.FailedEffect(
                        i == 6 ? "NetworkFailure" : "VerificationFailed",
                        "Injected deterministic transient failure.",
                        retryable: true),
                    durationMs: 1, ct);
                context = failed.Context;
                retries++;

                if (i == 21)
                {
                    var observe = await _runtime.PrepareActionAsync(
                        context, $"mock_observe_{i + 1}", "get_computer_state",
                        ToolCategory.Screen, "{}", ct);
                    if (!observe.Proceed)
                        throw new InvalidOperationException("Fresh observation was rejected.");
                    context = (await _runtime.RecordResultAsync(
                        context, observe,
                        ToolResult.Verified("Fresh mock state observed.",
                            new[] { "fresh_observation=true" }),
                        1, ct)).Context;
                }

                preparation = await _runtime.PrepareActionAsync(
                    context, $"mock_call_{i + 1}_2", "mock_state_transition",
                    ToolCategory.Network, JsonSerializer.Serialize(new
                    {
                        harness = "mock-installer",
                        phase = i + 1,
                        expected_state = descriptions[i],
                        recovery_strategy = i == 6
                            ? "retry after classified transient network failure"
                            : "retry after fresh state observation"
                    }), ct);
                if (!preparation.Proceed)
                    throw new InvalidOperationException(
                        $"Harness retry {i + 1} was rejected: {preparation.FailureCode}");
            }

            var outcome = await _runtime.RecordResultAsync(
                context, preparation,
                ToolResult.Verified(
                    $"Mock phase {i + 1} reached its ground-truth state.",
                    new[]
                    {
                        $"ground_truth_phase={i + 1}",
                        "process_exit_code=0"
                    },
                    new[] { $"mock_state={i + 1}" }),
                durationMs: 1, ct);
            context = outcome.Context;
            if (outcome.Result.StepCompleted) verified++;

            if (injectFailures && i == 24)
            {
                context = await _runtime.ApplyPlanUpdateAsync(
                    context, PlanJson(descriptions), ct);
                revisions++;
            }
            if (injectFailures && i == 31)
            {
                await _runtime.PauseAsync(context, "Injected provider interruption.", ct);
                context = await _runtime.StartOrResumeAsync(
                              context.ConversationId,
                              Array.Empty<GatewayMessage>(),
                              $"conversation:{context.ConversationId}",
                              ct)
                          ?? throw new InvalidOperationException("Harness resume failed.");
                resumes++;
            }
        }

        var completion = await _runtime.EvaluateCompletionAsync(context, ct);
        return new MockHarnessResult(
            stepCount, verified, retries, revisions, resumes,
            completion.Completed,
            completion.Completed && verified != stepCount,
            DateTimeOffset.UtcNow - started);
    }

    private static string PlanJson(IReadOnlyList<string> steps) =>
        JsonSerializer.Serialize(new
        {
            steps = steps.Select(step => new { step, status = "pending" })
        });
}

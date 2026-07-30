using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Workers;

/// <summary>
/// Runs a document plan fence in the isolated worker and hands back a file the
/// user can save. The staging directory is the worker's own scratch, so the plan
/// output never lands anywhere the user's files live until they choose to save it.
/// </summary>
public sealed class IsolatedPlanExecutor : IIsolatedPlanExecutor
{
    private readonly IIsolatedWorkerHost _workers;
    private readonly IWorkerImageResolver _images;
    private readonly string _outputRoot;

    public IsolatedPlanExecutor(
        IIsolatedWorkerHost workers,
        IWorkerImageResolver images,
        string? outputRoot = null)
    {
        _workers = workers;
        _images = images;
        _outputRoot = outputRoot
                      ?? Path.Combine(Path.GetTempPath(), "SranDesktop", "plans");
    }

    public bool IsAvailable
    {
        get
        {
            try
            {
                _images.Resolve("document");
                return true;
            }
            catch (SandboxUnavailableException)
            {
                return false;
            }
        }
    }

    public async Task<IsolatedPlanExecution> ExecuteAsync(
        string planType,
        JsonElement plan,
        string outputFileName,
        CancellationToken ct = default)
    {
        var safeName = Path.GetFileName(outputFileName);
        if (string.IsNullOrWhiteSpace(safeName))
            return IsolatedPlanExecution.Failed(
                "InvalidArgument", "A plan needs an output file name.");

        var workspace = Path.Combine(_outputRoot, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(workspace);
        var attemptId = $"attempt_{Guid.NewGuid():N}";
        var payload = JsonSerializer.Serialize(new
        {
            plan_type = planType,
            plan,
            output_file = safeName
        });

        var outcome = await _workers.DispatchAsync(
            new IsolatedWorkerRequest
            {
                RunId = $"plan_{Guid.NewGuid():N}",
                StepId = "plan_execute",
                AttemptId = attemptId,
                IdempotencyKey = attemptId,
                Capability = "document",
                Operation = "plan.execute",
                PayloadJson = payload,
                WorkingDirectory = workspace,
                ExpectedOutputFile = safeName
            },
            ct);

        var produced = Path.Combine(workspace, safeName);
        if (outcome.Class != WorkerOutcomeClass.SucceededUnverified || !File.Exists(produced))
        {
            TryCleanup(workspace);
            return IsolatedPlanExecution.Failed(
                outcome.FailureCode ?? "PlanExecutionFailed",
                outcome.FailureReason
                ?? "The isolated worker produced no verified plan output.");
        }

        var validation = ReadValidation(outcome);
        if (validation is null || !validation.Passed)
        {
            TryCleanup(workspace);
            return IsolatedPlanExecution.Failed(
                "ValidationFailed",
                validation is null
                    ? "The worker returned no validation evidence."
                    : string.Join(
                        "; ",
                        validation.Findings.Select(item => $"{item.Code}: {item.Detail}")));
        }

        return new IsolatedPlanExecution(
            true,
            produced,
            new FileInfo(produced).Length,
            outcome.Response?.Observations
                .Where(item => item.Kind == "plan_changes")
                .SelectMany(item => item.Detail.Split(" | ", StringSplitOptions.RemoveEmptyEntries))
                .ToList() ?? [],
            validation,
            null,
            null);
    }

    private static DocumentValidationReport? ReadValidation(IsolatedWorkerOutcome outcome)
    {
        if (outcome.Response?.ResultJson is not { Length: > 0 } json)
            return null;
        try
        {
            using var document = JsonDocument.Parse(json);
            var root = document.RootElement;
            return new DocumentValidationReport
            {
                Reopened = root.TryGetProperty("reopened", out var reopened)
                           && reopened.ValueKind == JsonValueKind.True,
                StructureValid = root.TryGetProperty("structure_valid", out var structure)
                                 && structure.ValueKind == JsonValueKind.True,
                Findings = root.TryGetProperty("findings", out var findings)
                           && findings.ValueKind == JsonValueKind.Array
                    ? findings.EnumerateArray().Select(item => new DocumentValidationFinding(
                        item.GetProperty("code").GetString() ?? "Unknown",
                        item.GetProperty("detail").GetString() ?? "",
                        item.TryGetProperty("blocking", out var blocking)
                        && blocking.ValueKind == JsonValueKind.True)).ToList()
                    : [],
                PageOrSlideOrSheetCount =
                    root.TryGetProperty("count", out var count) && count.TryGetInt32(out var value)
                        ? value
                        : null,
                Evidence = outcome.Response.Observations
                    .Select(item => $"{item.Kind}={item.Detail}").ToList()
            };
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static void TryCleanup(string directory)
    {
        try
        {
            if (Directory.Exists(directory))
                Directory.Delete(directory, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
        }
    }
}

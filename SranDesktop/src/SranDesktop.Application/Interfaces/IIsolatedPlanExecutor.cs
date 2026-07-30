using System.Text.Json;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Executes an on-device document plan in an ISOLATED worker.
///
/// This replaces the old in-UI-process <see cref="ILocalPlanExecutor"/> call
/// path. Same executors, same on-device privacy guarantee, but the Office code
/// no longer runs inside the privileged desktop process, and the produced file
/// is reopened and validated before it is reported as a result — the previous
/// path reported validity by construction.
/// </summary>
public interface IIsolatedPlanExecutor
{
    /// <summary>False when no isolated worker is available. Callers must then use
    /// the server path or surface the failure — never run the plan in-process.</summary>
    bool IsAvailable { get; }

    Task<IsolatedPlanExecution> ExecuteAsync(
        string planType,
        JsonElement plan,
        string outputFileName,
        CancellationToken ct = default);
}

public sealed record IsolatedPlanExecution(
    bool Succeeded,
    string? OutputPath,
    long FileSizeBytes,
    IReadOnlyList<string> Changes,
    DocumentValidationReport? Validation,
    string? FailureCode,
    string? FailureReason)
{
    public static IsolatedPlanExecution Failed(string code, string reason) =>
        new(false, null, 0, [], null, code, reason);
}

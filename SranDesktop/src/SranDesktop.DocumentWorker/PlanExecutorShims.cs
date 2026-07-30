// The three plan executors are source-linked from Infrastructure so a deck or
// workbook generated out of process is identical to one generated in-app. They
// depend on exactly two Application types, redeclared here rather than
// referencing the assembly — the worker must not pull in the WPF/credential
// graph it is isolated from.

namespace SranDesktop.Application.Models
{
    public sealed record LocalPlanExecutionResult(
        string OutputPath,
        long FileSizeBytes,
        List<string> Changes,
        Dictionary<string, int> Stats);
}

namespace SranDesktop.Application.Interfaces
{
    using System.Text.Json;
    using SranDesktop.Application.Models;

    public interface ILocalPlanExecutor
    {
        string PlanType { get; }
        bool CanExecute(JsonElement plan);
        Task<LocalPlanExecutionResult> ExecuteAsync(
            JsonElement plan, string outputPath, CancellationToken ct = default);
    }
}

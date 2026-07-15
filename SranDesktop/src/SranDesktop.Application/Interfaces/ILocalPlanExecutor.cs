using System.Text.Json;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// On-device executor for one :::excel-plan / :::doc-plan fence format
/// (Phase 12 E). Consumes the SAME plan JSON the server executors read
/// (scripts/excel_executor.py / docx_generator.py) so a plan can run either
/// place. Executors are conservative: <see cref="CanExecute"/> returns true
/// only for plans they can reproduce with full fidelity; everything else
/// falls back to the chat-edge server path.
///
/// Privacy note: local execution keeps generated document content on-device
/// — the plan never transits chat-edge. Tradeoff: local files skip the
/// server-side validator gate and version lineage (files table); the change
/// log is produced locally instead.
/// </summary>
public interface ILocalPlanExecutor
{
    /// <summary>Plan fence family this executor handles: "excel" | "doc".</summary>
    string PlanType { get; }

    /// <summary>
    /// True only when every operation in the plan is supported locally.
    /// Plans that edit an existing server file (source_file_id /
    /// modify_workbook), request an unsupported format (pdf/pptx) or contain
    /// any unknown or unsupported action must return false.
    /// </summary>
    bool CanExecute(JsonElement plan);

    /// <summary>
    /// Applies the plan and writes the finished file to
    /// <paramref name="outputPath"/> (parent directories are created).
    /// Implementations must not leave a partial file behind on failure:
    /// generate to a temp sibling and move into place only on success.
    /// </summary>
    Task<LocalPlanExecutionResult> ExecuteAsync(
        JsonElement plan, string outputPath, CancellationToken ct = default);
}

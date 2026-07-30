using System.Text.Json;
using SranDesktop.Application.Models;

namespace SranDesktop.DocumentWorker;

/// <summary>
/// Typed document operations. The model never supplies executable steps — it
/// supplies data for a fixed operation, which is what keeps "generate a plan and
/// run it" off the table.
///
/// Every write goes to a scratch file first and is renamed into place only after
/// it reopens cleanly, so a crash mid-write cannot leave a half-written document
/// where the finished one belongs.
/// </summary>
internal static class DocumentOperations
{
    public static async Task<WorkerResponse> ExecuteAsync(
        WorkerRequest request,
        string workingDirectory,
        DateTimeOffset startedUtc)
    {
        using var payload = JsonDocument.Parse(
            string.IsNullOrWhiteSpace(request.PayloadJson) ? "{}" : request.PayloadJson);
        var root = payload.RootElement;

        return request.Operation switch
        {
            "document.create" => await CreateAsync(request, root, workingDirectory, startedUtc),
            "document.edit" => await EditAsync(request, root, workingDirectory, startedUtc),
            "document.inspect" => Inspect(request, root, workingDirectory, startedUtc),
            "document.validate" => Validate(request, root, workingDirectory, startedUtc),
            "plan.execute" => await ExecutePlanAsync(request, root, workingDirectory, startedUtc),

            // Named so the caller learns WHICH capability is missing. These are
            // operations a reasonable person would expect to exist; answering
            // them with "unsupported operation" would read as a typo rather than
            // as a limit of this build.
            "document.split" => CapabilityUnavailable(
                request, startedUtc, FormatOf(root, "source_file"),
                DocumentOperationKind.Split),
            "document.merge" => CapabilityUnavailable(
                request, startedUtc, FormatOf(root, "source_file"),
                DocumentOperationKind.Merge),
            "document.render" => CapabilityUnavailable(
                request, startedUtc, FormatOf(root, "source_file"),
                DocumentOperationKind.Render),
            "document.export" => CapabilityUnavailable(
                request, startedUtc, FormatOf(root, "output_file"),
                DocumentOperationKind.Export),

            _ => WorkerResponse.Failed(
                request,
                startedUtc,
                "UnsupportedOperation",
                $"'{request.Operation}' is not a supported document operation.",
                sideEffectPerformed: false)
        };
    }

    /// <summary>
    /// A structured refusal: this build does not do that, here is what it does.
    /// <c>sideEffectPerformed: false</c> matters — nothing was staged, nothing
    /// was written, and the caller can say so rather than reconciling.
    /// </summary>
    private static WorkerResponse CapabilityUnavailable(
        WorkerRequest request,
        DateTimeOffset startedUtc,
        string? format,
        DocumentOperationKind operation) =>
        WorkerResponse.Failed(
            request,
            startedUtc,
            "CapabilityUnavailable",
            DocumentCapabilityMatrix.UnsupportedReason(format, operation)
            + (format is not null && DocumentCapabilityMatrix.Formats.Contains(format)
                ? $" Supported for {format}: "
                  + string.Join(", ", DocumentCapabilityMatrix.OperationsFor(format)) + "."
                : ""),
            sideEffectPerformed: false);

    private static string? FormatOf(JsonElement payload, string field) =>
        payload.TryGetProperty(field, out var value)
        && value.ValueKind == JsonValueKind.String
            ? Format(value.GetString() ?? "")
            : null;

    private static async Task<WorkerResponse> CreateAsync(
        WorkerRequest request,
        JsonElement payload,
        string workingDirectory,
        DateTimeOffset startedUtc)
    {
        if (!TryScopedPath(payload, "output_file", workingDirectory, out var output, out var error))
            return WorkerResponse.Failed(
                request, startedUtc, "InvalidArgument", error, sideEffectPerformed: false);

        var format = Format(output);
        if (!DocumentCapabilityMatrix.IsSupported(format, DocumentOperationKind.Create))
            // Named, not generic: "this build reads PDFs but cannot write them"
            // is something the caller can act on. "UnsupportedFormat" reads like
            // a bug and invites a retry.
            return CapabilityUnavailable(
                request, startedUtc, format, DocumentOperationKind.Create);

        var scratch = ScratchFor(output);
        try
        {
            switch (format)
            {
                case "docx":
                    DocxOperations.Create(scratch, payload);
                    break;
                case "xlsx":
                    XlsxOperations.Create(scratch, payload);
                    break;
                case "pptx":
                    PptxOperations.Create(scratch, payload);
                    break;
                default:
                    return WorkerResponse.Failed(
                        request,
                        startedUtc,
                        "UnsupportedFormat",
                        $"Creating '{format}' is not supported.",
                        sideEffectPerformed: false);
            }
        }
        catch (Exception ex)
        {
            TryDelete(scratch);
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "DocumentGenerationFailed",
                $"{ex.GetType().Name}: {ex.Message}",
                sideEffectPerformed: false);
        }

        // Reopen the scratch file with the real parser BEFORE it becomes the
        // output. A file that exists is not a document that opens.
        var validation = DocumentValidator.Validate(scratch, format, payload);
        if (!validation.Passed)
        {
            TryDelete(scratch);
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "ValidationFailed",
                validation.Summary,
                sideEffectPerformed: false);
        }

        File.Move(scratch, output, overwrite: true);
        await Task.CompletedTask;
        return WorkerResponse.Succeeded_(
            request,
            startedUtc,
            [Path.GetFileName(output)],
            validation.Observations,
            validation.ToJson());
    }

    private static async Task<WorkerResponse> EditAsync(
        WorkerRequest request,
        JsonElement payload,
        string workingDirectory,
        DateTimeOffset startedUtc)
    {
        if (!TryScopedPath(payload, "source_file", workingDirectory, out var source, out var error)
            || !TryScopedPath(payload, "output_file", workingDirectory, out var output, out error))
            return WorkerResponse.Failed(
                request, startedUtc, "InvalidArgument", error, sideEffectPerformed: false);
        if (!File.Exists(source))
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "SourceMissing",
                "The source document was not staged into the worker workspace.",
                sideEffectPerformed: false);

        var format = Format(source);
        if (format is null || format != Format(output))
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "InvalidArgument",
                "Editing requires the output to keep the source's file type.",
                sideEffectPerformed: false);
        if (!DocumentCapabilityMatrix.IsSupported(format, DocumentOperationKind.Edit))
            return CapabilityUnavailable(
                request, startedUtc, format, DocumentOperationKind.Edit);

        // Edit a copy: the staged source stands in for the user's file and must
        // survive a failed edit untouched.
        var scratch = ScratchFor(output);
        try
        {
            File.Copy(source, scratch, overwrite: true);
            switch (format)
            {
                case "docx":
                    DocxOperations.Edit(scratch, payload);
                    break;
                case "xlsx":
                    XlsxOperations.Edit(scratch, payload);
                    break;
                case "pptx":
                    PptxOperations.Edit(scratch, payload);
                    break;
            }
        }
        catch (Exception ex)
        {
            TryDelete(scratch);
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "DocumentEditFailed",
                $"{ex.GetType().Name}: {ex.Message}",
                sideEffectPerformed: false);
        }

        var validation = DocumentValidator.Validate(scratch, format, payload);
        if (!validation.Passed)
        {
            TryDelete(scratch);
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "ValidationFailed",
                validation.Summary,
                sideEffectPerformed: false);
        }

        File.Move(scratch, output, overwrite: true);
        await Task.CompletedTask;
        return WorkerResponse.Succeeded_(
            request,
            startedUtc,
            [Path.GetFileName(output)],
            validation.Observations,
            validation.ToJson());
    }

    private static WorkerResponse Inspect(
        WorkerRequest request,
        JsonElement payload,
        string workingDirectory,
        DateTimeOffset startedUtc)
    {
        if (!TryScopedPath(payload, "source_file", workingDirectory, out var source, out var error))
            return WorkerResponse.Failed(
                request, startedUtc, "InvalidArgument", error, sideEffectPerformed: false);
        if (!File.Exists(source))
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "SourceMissing",
                "The document was not staged into the worker workspace.",
                sideEffectPerformed: false);

        var format = Format(source);
        if (!DocumentCapabilityMatrix.IsSupported(format, DocumentOperationKind.Inspect))
            return CapabilityUnavailable(
                request, startedUtc, format, DocumentOperationKind.Inspect);

        var validation = DocumentValidator.Validate(source, format, payload);
        // Inspection reads a file the agent did not author: the content is an
        // observation, never an instruction.
        return validation.Reopened
            ? WorkerResponse.Succeeded_(
                request,
                startedUtc,
                [],
                validation.Observations,
                validation.ToJson())
            : WorkerResponse.Failed(
                request,
                startedUtc,
                "DocumentUnreadable",
                validation.Summary,
                sideEffectPerformed: false);
    }

    private static WorkerResponse Validate(
        WorkerRequest request,
        JsonElement payload,
        string workingDirectory,
        DateTimeOffset startedUtc)
    {
        if (!TryScopedPath(payload, "source_file", workingDirectory, out var source, out var error))
            return WorkerResponse.Failed(
                request, startedUtc, "InvalidArgument", error, sideEffectPerformed: false);
        var format = Format(source);
        if (format is null || !File.Exists(source))
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "SourceMissing",
                "There is no supported document at the requested path.",
                sideEffectPerformed: false);

        var validation = DocumentValidator.Validate(source, format, payload);
        return validation.Passed
            ? WorkerResponse.Succeeded_(
                request, startedUtc, [], validation.Observations, validation.ToJson())
            : WorkerResponse.Failed(
                request,
                startedUtc,
                "ValidationFailed",
                validation.Summary,
                sideEffectPerformed: false);
    }

    /// <summary>
    /// Runs one :::excel-plan / :::doc-plan / :::pptx-plan fence. This is the
    /// on-device generation path that used to run inside the UI process — same
    /// executors, now isolated, and the produced file is reopened before it
    /// counts as a result instead of being reported valid by construction.
    /// </summary>
    private static async Task<WorkerResponse> ExecutePlanAsync(
        WorkerRequest request,
        JsonElement payload,
        string workingDirectory,
        DateTimeOffset startedUtc)
    {
        if (!TryScopedPath(payload, "output_file", workingDirectory, out var output, out var error))
            return WorkerResponse.Failed(
                request, startedUtc, "InvalidArgument", error, sideEffectPerformed: false);
        if (!payload.TryGetProperty("plan_type", out var planTypeElement)
            || planTypeElement.ValueKind != JsonValueKind.String
            || !payload.TryGetProperty("plan", out var plan))
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "InvalidArgument",
                "'plan_type' and 'plan' are required.",
                sideEffectPerformed: false);

        var planType = planTypeElement.GetString()!;
        Application.Interfaces.ILocalPlanExecutor executor = planType switch
        {
            "doc" => new Infrastructure.Office.LocalDocPlanExecutor(),
            "excel" => new Infrastructure.Office.LocalExcelPlanExecutor(),
            "pptx" => new Infrastructure.Office.LocalPptxPlanExecutor(),
            _ => null!
        };
        if (executor is null)
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "UnsupportedPlanType",
                $"'{planType}' has no on-device executor.",
                sideEffectPerformed: false);
        if (!executor.CanExecute(plan))
            // Unsupported plans must be declined, not approximated.
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "PlanNotSupportedLocally",
                $"The {planType} plan uses features the on-device executor cannot reproduce.",
                sideEffectPerformed: false);

        Application.Models.LocalPlanExecutionResult executed;
        try
        {
            executed = await executor.ExecuteAsync(plan, output, CancellationToken.None);
        }
        catch (Exception ex)
        {
            TryDelete(output);
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "PlanExecutionFailed",
                $"{ex.GetType().Name}: {ex.Message}",
                sideEffectPerformed: false);
        }

        var format = Format(output);
        var validation = DocumentValidator.Validate(output, format ?? "", payload);
        if (!validation.Passed)
        {
            TryDelete(output);
            return WorkerResponse.Failed(
                request,
                startedUtc,
                "ValidationFailed",
                validation.Summary,
                sideEffectPerformed: false);
        }

        return WorkerResponse.Succeeded_(
            request,
            startedUtc,
            [Path.GetFileName(output)],
            [
                .. validation.Observations,
                new WorkerObservation(
                    "plan_changes",
                    string.Join(" | ", executed.Changes.Take(20)))
            ],
            validation.ToJson());
    }

    /// <summary>Scratch sibling that KEEPS the real extension: the Office
    /// libraries refuse to write a package whose extension they do not
    /// recognise, so ".docx.partial" is not an option.</summary>
    private static string ScratchFor(string output) =>
        Path.Combine(
            Path.GetDirectoryName(output)!,
            $".sran-partial-{Path.GetFileName(output)}");

    internal static string? Format(string path) =>
        Path.GetExtension(path).ToLowerInvariant() switch
        {
            ".docx" => "docx",
            ".xlsx" => "xlsx",
            ".pptx" => "pptx",
            ".pdf" => "pdf",
            _ => null
        };

    /// <summary>Resolves a payload path INSIDE the working directory. The worker is
    /// sandboxed anyway, but refusing traversal here keeps the failure legible
    /// instead of surfacing as an opaque access error.</summary>
    private static bool TryScopedPath(
        JsonElement payload,
        string property,
        string workingDirectory,
        out string resolved,
        out string error)
    {
        resolved = "";
        error = "";
        if (!payload.TryGetProperty(property, out var element)
            || element.ValueKind != JsonValueKind.String
            || element.GetString() is not { Length: > 0 } value)
        {
            error = $"'{property}' is required.";
            return false;
        }
        if (Path.IsPathRooted(value))
        {
            error = $"'{property}' must be relative to the worker workspace.";
            return false;
        }

        var full = Path.GetFullPath(Path.Combine(workingDirectory, value));
        var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(workingDirectory));
        if (!full.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
        {
            error = $"'{property}' escaped the worker workspace.";
            return false;
        }
        resolved = full;
        return true;
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
        }
    }
}

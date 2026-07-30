using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Workers;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// The single document entry point. Every create/edit/inspect goes out of process
/// to the isolated document worker — there is no in-process generation path and
/// no host fallback, so an unavailable worker fails closed rather than quietly
/// running Office code inside the privileged UI process.
/// </summary>
public sealed class DocumentTool : IMcpTool
{
    private static readonly HashSet<string> MutatingOperations = new(StringComparer.Ordinal)
    {
        "document.create", "document.edit"
    };

    private readonly DocumentWorkerService _documents;

    public DocumentTool(DocumentWorkerService documents) => _documents = documents;

    public string Name => "document";
    public string Description =>
        "Create, edit, inspect or validate a .docx/.xlsx/.pptx/.pdf file in an isolated "
        + "worker. The result is verified by reopening the produced file before it is published.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["document.create", "document.edit", "document.inspect", "document.validate"],
                    "description": "Typed document operation"
                },
                "source_file": { "type": "string", "description": "Existing document to read or edit" },
                "output_file": { "type": "string", "description": "Destination for create/edit" },
                "blocks": { "type": "array", "description": "DOCX content blocks for document.create" },
                "sheets": { "type": "array", "description": "XLSX sheets for document.create" },
                "slides": { "type": "array", "description": "PPTX slides for document.create" },
                "edits": { "type": "array", "description": "Typed edits for document.edit" },
                "expect": {
                    "type": "object",
                    "description": "Checks the reopened document must satisfy before publishing"
                }
            },
            "required": ["operation"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var operation = ToolHelpers.GetString(arguments, "operation");
        if (string.IsNullOrWhiteSpace(operation))
            return ToolResult.Fail("InvalidArgument: 'operation' is required.");

        var runId = AgentRunExecutionContext.CurrentRunId;
        var stepId = AgentRunExecutionContext.CurrentStepId;
        if (MutatingOperations.Contains(operation)
            && (string.IsNullOrWhiteSpace(runId) || string.IsNullOrWhiteSpace(stepId)))
            return ToolResult.Fail(
                "RuntimeContextRequired: a document mutation must execute inside an "
                + "AgentRun/AgentStep.");

        var result = await _documents.ExecuteAsync(
            new DocumentOperationRequest
            {
                RunId = runId ?? "adhoc",
                StepId = stepId ?? "adhoc",
                AttemptId = $"attempt_{Guid.NewGuid():N}",
                IdempotencyKey = Fingerprint(
                    $"{runId}|{stepId}|{operation}|{arguments.GetRawText()}"),
                Operation = operation,
                SourcePath = ToolHelpers.GetString(arguments, "source_file"),
                OutputPath = ToolHelpers.GetString(arguments, "output_file"),
                PayloadJson = arguments.GetRawText()
            },
            ct);

        return result.Class switch
        {
            WorkerOutcomeClass.SucceededVerified => ToolResult.Verified(
                Describe(result),
                [.. result.Evidence, $"artifact_id={result.Artifact?.ArtifactId}"],
                result.Artifact?.State == ArtifactState.Published
                    ? [$"document_published={result.Artifact.OutputPath}"]
                    : null),

            // Unknown means the document may already have been written. The runtime
            // must reconcile against the real file before anything is retried.
            WorkerOutcomeClass.PostDispatchUnknownOutcome
                or WorkerOutcomeClass.FailedPartialEffect => ToolResult.FailedEffect(
                    "UnknownOutcome",
                    $"{result.FailureCode}: {result.FailureReason} "
                    + "Inspect the destination file before retrying — the write may have landed.",
                    retryable: false),

            WorkerOutcomeClass.PreDispatchFailure when
                result.FailureCode == "CapabilityUnavailable" => ToolResult.FailedEffect(
                    "CapabilityUnavailable",
                    result.FailureReason
                    ?? "The isolated document worker is unavailable and has no host fallback.",
                    retryable: false),

            _ => ToolResult.Fail($"{result.FailureCode}: {result.FailureReason}")
        };
    }

    private static string Fingerprint(string value) =>
        Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
            System.Text.Encoding.UTF8.GetBytes(value)));

    private static string Describe(DocumentOperationResult result)
    {
        var artifact = result.Artifact;
        if (artifact is null)
            return "The document operation completed.";
        var validation = artifact.Validation;
        var detail = validation?.Evidence.Count > 0
            ? " " + string.Join("; ", validation.Evidence)
            : "";
        return artifact.State == ArtifactState.Published
            ? $"Published {Path.GetFileName(artifact.OutputPath)} "
              + $"({artifact.FileSize} bytes), verified by reopening it.{detail}"
            : $"Inspected {Path.GetFileName(artifact.SourcePath ?? artifact.OutputPath)}.{detail}";
    }
}

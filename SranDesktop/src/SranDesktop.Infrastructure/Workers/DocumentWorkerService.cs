using System.Security.Cryptography;
using System.Text.Json;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Workers;

/// <summary>
/// Host side of a document operation: stages the user's file into a private
/// worker workspace, dispatches the isolated worker, and only then decides
/// whether anything is published.
///
/// The user's original file is never opened for writing. It is copied in, worked
/// on out of process, verified by reopening the RESULT, and finally swapped in
/// atomically with a backup retained — so a crash, a bad edit, or a killed worker
/// leaves the original exactly as it was.
/// </summary>
public sealed class DocumentWorkerService
{
    private const long MaxDocumentBytes = 64 * 1024 * 1024;

    private readonly IIsolatedWorkerHost _workers;
    private readonly IWorkspaceFileBroker _files;
    private readonly string _scratchRoot;

    public DocumentWorkerService(
        IIsolatedWorkerHost workers,
        IWorkspaceFileBroker files,
        string? scratchRoot = null)
    {
        _workers = workers;
        _files = files;
        _scratchRoot = scratchRoot
                       ?? Path.Combine(Path.GetTempPath(), "sran-document-work");
    }

    public async Task<DocumentOperationResult> ExecuteAsync(
        DocumentOperationRequest request,
        CancellationToken ct)
    {
        var artifactId = $"artifact_{Guid.NewGuid():N}";
        var artifactType = DocumentArtifact.TypeFor(request.OutputPath ?? request.SourcePath ?? "");
        if (artifactType == ArtifactType.Unknown)
            return DocumentOperationResult.Rejected(
                "UnsupportedFormat",
                "Only .docx, .xlsx, .pptx and .pdf are supported document artifacts.");

        // Resolve BOTH paths through the broker before anything else: an
        // unauthorized destination must fail before a worker is ever started.
        string? resolvedSource = null;
        string? resolvedOutput = null;
        try
        {
            if (request.SourcePath is { Length: > 0 } source)
                resolvedSource = _files.ResolveForScope(source, WorkspaceFileOperation.Read);
            if (request.OutputPath is { Length: > 0 } output)
                resolvedOutput = _files.ResolveForScope(output, WorkspaceFileOperation.Write);
        }
        catch (WorkspaceAccessDeniedException ex)
        {
            return DocumentOperationResult.Rejected("WorkspaceAccessDenied", ex.Message);
        }

        var workspace = Path.Combine(
            _scratchRoot,
            request.RunId,
            request.StepId,
            request.AttemptId);
        var scratchBroker = new WorkspaceFileBroker(
            [new WorkspaceMount(workspace, WorkspaceMountAccess.ReadWrite)],
            workspace);
        Directory.CreateDirectory(workspace);

        string? originalHash = null;
        try
        {
            const string stagedSourceName = "source";
            const string stagedOutputName = "output";
            string? stagedSource = null;
            if (resolvedSource is not null)
            {
                if (!File.Exists(resolvedSource))
                    return DocumentOperationResult.Rejected(
                        "SourceMissing", $"'{request.SourcePath}' does not exist.");
                var snapshot = await _files.ReadFileAsync(
                    resolvedSource, MaxDocumentBytes, ct);
                originalHash = snapshot.Sha256;
                stagedSource = stagedSourceName + Path.GetExtension(resolvedSource);
                await File.WriteAllBytesAsync(
                    Path.Combine(workspace, stagedSource), snapshot.Bytes, ct);
            }

            // Whether an operation writes is a property of the OPERATION, not of
            // whether the caller happened to pass an output path. Deciding it the
            // other way round means an inspect called with one is judged by a
            // file it was never going to produce, and can therefore never verify.
            var stagedOutput = resolvedOutput is null || IsReadOnly(request.Operation)
                ? null
                : stagedOutputName + Path.GetExtension(resolvedOutput);
            var payload = BuildPayload(request, stagedSource, stagedOutput);

            var outcome = await _workers.DispatchAsync(
                new IsolatedWorkerRequest
                {
                    RunId = request.RunId,
                    StepId = request.StepId,
                    AttemptId = request.AttemptId,
                    IdempotencyKey = request.IdempotencyKey,
                    Capability = "document",
                    Operation = request.Operation,
                    PayloadJson = payload,
                    WorkingDirectory = workspace,
                    Sensitivity = request.Sensitivity,
                    ExpectedOutputFile = stagedOutput,
                    Quotas = request.Quotas ?? WorkerResourceQuotas.Default
                },
                ct);

            // A read-only operation publishes nothing; its verification is simply
            // that the worker reopened the document and reported structure.
            if (stagedOutput is null || resolvedOutput is null
                || IsReadOnly(request.Operation))
                return ReadOnlyResult(outcome, artifactId, request, artifactType);

            if (outcome.Class != WorkerOutcomeClass.SucceededUnverified)
                return DocumentOperationResult.FromFailedWorker(
                    outcome, artifactId, request, artifactType);

            var producedPath = Path.Combine(workspace, stagedOutput);
            if (!File.Exists(producedPath))
                // Claimed success with nothing to show: not a clean failure,
                // because we cannot prove it never touched anything.
                return DocumentOperationResult.Unknown(
                    outcome,
                    artifactId,
                    request,
                    artifactType,
                    "WorkerOutputMissing",
                    "The worker reported success but produced no output file.");

            return await PublishAsync(
                outcome,
                artifactId,
                request,
                artifactType,
                producedPath,
                resolvedOutput,
                originalHash,
                ct);
        }
        catch (WorkspaceAccessDeniedException ex)
        {
            return DocumentOperationResult.Rejected("WorkspaceAccessDenied", ex.Message);
        }
        finally
        {
            TryDeleteDirectory(workspace);
            // Prune the run/step/attempt shells too: leaving empty directories
            // behind is the same slow leak the sandbox job root had.
            PruneEmptyParents(Path.GetDirectoryName(workspace), _scratchRoot);
            _ = scratchBroker;
        }
    }

    /// <summary>
    /// Swap the verified result in. The original is backed up first and the swap
    /// is a single rename, so an interruption leaves either the old file or the
    /// new one — never a half-written document.
    /// </summary>
    private async Task<DocumentOperationResult> PublishAsync(
        IsolatedWorkerOutcome outcome,
        string artifactId,
        DocumentOperationRequest request,
        ArtifactType artifactType,
        string producedPath,
        string destination,
        string? originalHash,
        CancellationToken ct)
    {
        var validation = ReadValidation(outcome);
        if (validation is null || !validation.Passed)
            return DocumentOperationResult.FromFailedWorker(
                outcome with
                {
                    FailureCode = "ValidationEvidenceMissing",
                    FailureReason = "The worker returned no passing validation evidence, "
                                    + "so the result was not published."
                },
                artifactId,
                request,
                artifactType);

        var bytes = await File.ReadAllBytesAsync(producedPath, ct);
        var finalHash = Convert.ToHexString(SHA256.HashData(bytes));
        string? backupPath = null;
        var replacing = File.Exists(destination);
        try
        {
            if (replacing)
            {
                backupPath = destination + $".sran-backup-{DateTime.UtcNow:yyyyMMddHHmmss}";
                File.Copy(destination, backupPath, overwrite: true);
            }
            // Broker write keeps the destination inside an authorized mount and
            // performs the same containment checks as any other file mutation.
            await _files.WriteBytesAsync(destination, bytes, ct);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            var restored = TryRestore(backupPath, destination);
            return DocumentOperationResult.Unknown(
                outcome,
                artifactId,
                request,
                artifactType,
                "PublishFailed",
                $"{ex.Message} (backup restored: {restored})");
        }

        var artifact = new DocumentArtifact
        {
            ArtifactId = artifactId,
            RunId = request.RunId,
            StepId = request.StepId,
            ArtifactType = artifactType,
            SourcePath = request.SourcePath,
            WorkingPath = null,
            OutputPath = destination,
            OriginalHash = originalHash,
            FinalHash = finalHash,
            MimeType = DocumentArtifact.MimeFor(artifactType),
            FileSize = bytes.LongLength,
            ModifiedUtc = DateTimeOffset.UtcNow,
            Provenance = new ArtifactProvenance(
                ArtifactProvenance.AgentGenerated,
                request.RunId,
                request.StepId,
                request.SourcePath,
                DateTimeOffset.UtcNow),
            Sensitivity = request.Sensitivity,
            State = ArtifactState.Published,
            Validation = validation,
            BackupPath = backupPath,
            TemporaryFilesRemoved = outcome.TemporaryArtifactsRemoved
        };

        return new DocumentOperationResult(
            WorkerOutcomeClass.SucceededVerified,
            artifact,
            null,
            null,
            [
                .. outcome.Evidence,
                $"artifact_reopened={validation.Reopened.ToString().ToLowerInvariant()}",
                $"artifact_validated={validation.Passed.ToString().ToLowerInvariant()}",
                $"final_sha256={finalHash}",
                $"original_preserved={(backupPath is not null).ToString().ToLowerInvariant()}"
            ],
            outcome.DurationMs);
    }

    /// <summary>Operations that read a document and publish nothing.</summary>
    private static bool IsReadOnly(string operation) =>
        operation is "document.inspect" or "document.validate";

    private static DocumentOperationResult ReadOnlyResult(
        IsolatedWorkerOutcome outcome,
        string artifactId,
        DocumentOperationRequest request,
        ArtifactType artifactType)
    {
        var validation = ReadValidation(outcome);
        if (outcome.Class != WorkerOutcomeClass.SucceededUnverified || validation is null)
            return DocumentOperationResult.FromFailedWorker(
                outcome, artifactId, request, artifactType);

        var artifact = new DocumentArtifact
        {
            ArtifactId = artifactId,
            RunId = request.RunId,
            StepId = request.StepId,
            ArtifactType = artifactType,
            SourcePath = request.SourcePath,
            OutputPath = request.SourcePath ?? "",
            MimeType = DocumentArtifact.MimeFor(artifactType),
            Provenance = new ArtifactProvenance(
                // Read back from a file the agent did not author.
                ArtifactProvenance.ExternalUntrusted,
                request.RunId,
                request.StepId,
                request.SourcePath,
                DateTimeOffset.UtcNow),
            TrustLevel = ArtifactProvenance.ExternalUntrusted,
            Sensitivity = request.Sensitivity,
            State = Readable(validation) ? ArtifactState.Inspected : ArtifactState.Invalid,
            Validation = validation,
            TemporaryFilesRemoved = outcome.TemporaryArtifactsRemoved
        };

        // Reopening is not the same as understanding. An encrypted PDF parses far
        // enough to be recognised and then stops; a damaged cross-reference table
        // reopens and describes nothing. Reporting either as a verified
        // inspection would hand the caller an empty reading as if it were a real
        // one.
        var blocking = validation.Findings
            .Where(finding => finding.Blocking)
            .ToList();
        var readable = Readable(validation);

        return new DocumentOperationResult(
            readable ? WorkerOutcomeClass.SucceededVerified : WorkerOutcomeClass.FailedNoEffect,
            artifact,
            readable ? null : blocking.FirstOrDefault()?.Code ?? "DocumentUnreadable",
            readable
                ? null
                : string.Join("; ", blocking.Select(finding => finding.Detail)),
            [
                .. outcome.Evidence,
                $"document_reopened={validation.Reopened.ToString().ToLowerInvariant()}",
                // The validator's own findings travel with the result. Without
                // them the caller can see THAT a PDF declares JavaScript only by
                // opening it again, which is the thing the sandbox exists to
                // avoid doing twice.
                .. validation.Evidence,
                .. blocking.Select(finding => $"blocking={finding.Code}: {finding.Detail}"),
                "trust=untrusted_document_content"
            ],
            outcome.DurationMs);
    }

    /// <summary>A document that both reopened and produced no blocking finding.
    /// Either alone is not enough to call an inspection successful.</summary>
    private static bool Readable(DocumentValidationReport validation) => validation.Passed;

    private static DocumentValidationReport? ReadValidation(IsolatedWorkerOutcome outcome)
    {
        if (outcome.Response?.ResultJson is not { Length: > 0 } json)
            return null;
        try
        {
            using var document = JsonDocument.Parse(json);
            var root = document.RootElement;
            var findings = root.TryGetProperty("findings", out var list)
                           && list.ValueKind == JsonValueKind.Array
                ? list.EnumerateArray().Select(item => new DocumentValidationFinding(
                    item.GetProperty("code").GetString() ?? "Unknown",
                    item.GetProperty("detail").GetString() ?? "",
                    item.TryGetProperty("blocking", out var blocking)
                    && blocking.ValueKind == JsonValueKind.True)).ToList()
                : [];
            return new DocumentValidationReport
            {
                Reopened = root.TryGetProperty("reopened", out var reopened)
                           && reopened.ValueKind == JsonValueKind.True,
                StructureValid = root.TryGetProperty("structure_valid", out var structure)
                                 && structure.ValueKind == JsonValueKind.True,
                Findings = findings,
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

    private static string BuildPayload(
        DocumentOperationRequest request,
        string? stagedSource,
        string? stagedOutput)
    {
        using var document = JsonDocument.Parse(
            string.IsNullOrWhiteSpace(request.PayloadJson) ? "{}" : request.PayloadJson);
        var buffer = new MemoryStream();
        using (var writer = new Utf8JsonWriter(buffer))
        {
            writer.WriteStartObject();
            foreach (var property in document.RootElement.EnumerateObject())
            {
                // The worker's file names are assigned by the host; a payload
                // must not be able to redirect them.
                if (property.NameEquals("source_file") || property.NameEquals("output_file"))
                    continue;
                property.WriteTo(writer);
            }
            if (stagedSource is not null)
                writer.WriteString("source_file", stagedSource);
            if (stagedOutput is not null)
                writer.WriteString("output_file", stagedOutput);
            writer.WriteEndObject();
        }
        return System.Text.Encoding.UTF8.GetString(buffer.ToArray());
    }

    private static bool TryRestore(string? backupPath, string destination)
    {
        if (backupPath is null || !File.Exists(backupPath))
            return false;
        try
        {
            File.Copy(backupPath, destination, overwrite: true);
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    private static void PruneEmptyParents(string? directory, string stopAt)
    {
        var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(stopAt));
        var current = directory;
        while (current is not null)
        {
            var full = Path.TrimEndingDirectorySeparator(Path.GetFullPath(current));
            if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase)
                || !Directory.Exists(full))
                return;
            try
            {
                if (Directory.EnumerateFileSystemEntries(full).Any())
                    return;
                Directory.Delete(full);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                return;
            }
            if (string.Equals(full, root, StringComparison.OrdinalIgnoreCase))
                return;
            current = Path.GetDirectoryName(full);
        }
    }

    private static void TryDeleteDirectory(string path)
    {
        try
        {
            if (Directory.Exists(path))
                Directory.Delete(path, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
        }
    }
}

public sealed record DocumentOperationRequest
{
    public required string RunId { get; init; }
    public required string StepId { get; init; }
    public required string AttemptId { get; init; }
    public required string IdempotencyKey { get; init; }
    public required string Operation { get; init; }
    public string? SourcePath { get; init; }
    public string? OutputPath { get; init; }
    public string PayloadJson { get; init; } = "{}";
    public string Sensitivity { get; init; } = "internal";
    public WorkerResourceQuotas? Quotas { get; init; }
}

public sealed record DocumentOperationResult(
    WorkerOutcomeClass Class,
    DocumentArtifact? Artifact,
    string? FailureCode,
    string? FailureReason,
    IReadOnlyList<string> Evidence,
    long DurationMs)
{
    public bool Verified => Class == WorkerOutcomeClass.SucceededVerified;

    public bool RequiresReconciliation =>
        Class is WorkerOutcomeClass.PostDispatchUnknownOutcome
            or WorkerOutcomeClass.FailedPartialEffect;

    public static DocumentOperationResult Rejected(string code, string reason) =>
        new(WorkerOutcomeClass.PreDispatchFailure, null, code, reason,
            ["dispatched=false"], 0);

    public static DocumentOperationResult FromFailedWorker(
        IsolatedWorkerOutcome outcome,
        string artifactId,
        DocumentOperationRequest request,
        ArtifactType artifactType) =>
        new(
            outcome.Class,
            new DocumentArtifact
            {
                ArtifactId = artifactId,
                RunId = request.RunId,
                StepId = request.StepId,
                ArtifactType = artifactType,
                SourcePath = request.SourcePath,
                OutputPath = request.OutputPath ?? "",
                MimeType = DocumentArtifact.MimeFor(artifactType),
                Provenance = new ArtifactProvenance(
                    ArtifactProvenance.AgentGenerated,
                    request.RunId,
                    request.StepId,
                    request.SourcePath,
                    DateTimeOffset.UtcNow),
                State = outcome.Class switch
                {
                    WorkerOutcomeClass.PostDispatchUnknownOutcome =>
                        ArtifactState.UnknownOutcome,
                    WorkerOutcomeClass.FailedPartialEffect => ArtifactState.PartiallyWritten,
                    _ => ArtifactState.Invalid
                },
                TemporaryFilesRemoved = outcome.TemporaryArtifactsRemoved
            },
            outcome.FailureCode,
            outcome.FailureReason,
            outcome.Evidence,
            outcome.DurationMs);

    public static DocumentOperationResult Unknown(
        IsolatedWorkerOutcome outcome,
        string artifactId,
        DocumentOperationRequest request,
        ArtifactType artifactType,
        string code,
        string reason) =>
        FromFailedWorker(
            outcome with
            {
                Class = WorkerOutcomeClass.PostDispatchUnknownOutcome,
                FailureCode = code,
                FailureReason = reason
            },
            artifactId,
            request,
            artifactType);
}

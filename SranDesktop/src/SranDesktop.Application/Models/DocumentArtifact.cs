namespace SranDesktop.Application.Models;

public enum ArtifactType { Docx, Xlsx, Pptx, Pdf, Unknown }

/// <summary>
/// Lifecycle of one document the agent touches. The ordering matters: an artifact
/// is only <see cref="Verified"/> after it was written, REOPENED by a real parser,
/// and validated — never because a worker exited zero or a file appeared on disk.
/// </summary>
public enum ArtifactState
{
    Discovered,
    Inspected,
    WorkingCopyCreated,
    Modified,
    Saved,
    Reopened,
    Validated,
    Rendered,
    Verified,
    Published,

    // Terminal problem states.
    Invalid,
    Corrupted,
    PartiallyWritten,
    UnknownOutcome,
    NeedsUser,
    RolledBack
}

/// <summary>Where an artifact came from — carried through to the model so
/// generated content is never confused with content the user authored.</summary>
public sealed record ArtifactProvenance(
    string Origin,
    string RunId,
    string StepId,
    string? SourcePath,
    DateTimeOffset CreatedUtc)
{
    public const string AgentGenerated = "agent-generated";
    public const string UserSupplied = "user-supplied";
    public const string ExternalUntrusted = "external-untrusted";
}

public sealed record DocumentArtifact
{
    public required string ArtifactId { get; init; }
    public required string RunId { get; init; }
    public required string StepId { get; init; }
    public required ArtifactType ArtifactType { get; init; }

    /// <summary>The user's file. Untouched until a validated publish.</summary>
    public string? SourcePath { get; init; }

    /// <summary>Private scratch copy the worker edits.</summary>
    public string? WorkingPath { get; init; }

    /// <summary>Final destination once validation passes.</summary>
    public required string OutputPath { get; init; }

    public string? OriginalHash { get; init; }
    public string? WorkingHash { get; init; }
    public string? FinalHash { get; init; }
    public string MimeType { get; init; } = "application/octet-stream";
    public long FileSize { get; init; }
    public DateTimeOffset CreatedUtc { get; init; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? ModifiedUtc { get; init; }
    public required ArtifactProvenance Provenance { get; init; }
    public string Sensitivity { get; init; } = "internal";
    public string TrustLevel { get; init; } = ArtifactProvenance.AgentGenerated;
    public int Version { get; init; } = 1;
    public ArtifactState State { get; init; } = ArtifactState.Discovered;
    public DocumentValidationReport? Validation { get; init; }

    /// <summary>Backup of the original, kept until the publish is confirmed so a
    /// failed edit can be rolled back.</summary>
    public string? BackupPath { get; init; }

    public bool TemporaryFilesRemoved { get; init; }

    public static ArtifactType TypeFor(string path) =>
        Path.GetExtension(path).ToLowerInvariant() switch
        {
            ".docx" => ArtifactType.Docx,
            ".xlsx" => ArtifactType.Xlsx,
            ".pptx" => ArtifactType.Pptx,
            ".pdf" => ArtifactType.Pdf,
            _ => ArtifactType.Unknown
        };

    public static string MimeFor(ArtifactType type) => type switch
    {
        ArtifactType.Docx =>
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ArtifactType.Xlsx =>
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ArtifactType.Pptx =>
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ArtifactType.Pdf => "application/pdf",
        _ => "application/octet-stream"
    };
}

public sealed record DocumentValidationFinding(
    string Code,
    string Detail,
    bool Blocking);

/// <summary>
/// Deterministic checks performed by REOPENING the produced file. Model vision may
/// add findings later but can never be the sole acceptance gate.
/// </summary>
public sealed record DocumentValidationReport
{
    public required bool Reopened { get; init; }
    public required bool StructureValid { get; init; }
    public IReadOnlyList<DocumentValidationFinding> Findings { get; init; } = [];
    public int? PageOrSlideOrSheetCount { get; init; }
    public IReadOnlyList<string> Evidence { get; init; } = [];

    public bool Passed =>
        Reopened && StructureValid && Findings.All(finding => !finding.Blocking);

    public static DocumentValidationReport Failure(string code, string detail) =>
        new()
        {
            Reopened = false,
            StructureValid = false,
            Findings = [new DocumentValidationFinding(code, detail, Blocking: true)]
        };
}

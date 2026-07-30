namespace SranDesktop.Application.Models;

public enum DocumentOperationKind
{
    Inspect,
    Create,
    Edit,
    Validate,
    Split,
    Merge,
    Render,
    Export
}

/// <summary>
/// What this build can actually do to each document format.
///
/// It is data rather than prose so a test can read it, for the same reason the
/// MCP feature matrix is: a report claiming PDF support and a worker that
/// refuses to create one are only distinguishable if the claim is executable.
/// Anything absent here is answered with a structured refusal naming the format
/// and the operation, never with a generic failure that reads like a bug.
/// </summary>
public static class DocumentCapabilityMatrix
{
    public static IReadOnlySet<string> Formats { get; } =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "docx", "xlsx", "pptx", "pdf" };

    private static readonly Dictionary<string, HashSet<DocumentOperationKind>> Supported =
        new(StringComparer.OrdinalIgnoreCase)
        {
            ["docx"] =
            [
                DocumentOperationKind.Inspect, DocumentOperationKind.Create,
                DocumentOperationKind.Edit, DocumentOperationKind.Validate
            ],
            ["xlsx"] =
            [
                DocumentOperationKind.Inspect, DocumentOperationKind.Create,
                DocumentOperationKind.Edit, DocumentOperationKind.Validate
            ],
            ["pptx"] =
            [
                DocumentOperationKind.Inspect, DocumentOperationKind.Create,
                DocumentOperationKind.Edit, DocumentOperationKind.Validate
            ],
            // PDF is read-only in this build. The structural parser reads the
            // signature, the cross-reference table, the page count and the
            // declared active content; it does not write, render, split or merge,
            // and it never executes what it finds.
            ["pdf"] = [DocumentOperationKind.Inspect, DocumentOperationKind.Validate]
        };

    private static readonly Dictionary<(string Format, DocumentOperationKind Operation), string>
        Reasons = new()
        {
            [("pdf", DocumentOperationKind.Create)] =
                "This build reads PDFs but cannot write them. Create the document as DOCX "
                + "and convert it outside the agent.",
            [("pdf", DocumentOperationKind.Edit)] =
                "This build reads PDFs but cannot modify them.",
            [("pdf", DocumentOperationKind.Split)] =
                "Splitting a PDF is not implemented.",
            [("pdf", DocumentOperationKind.Merge)] =
                "Merging PDFs is not implemented.",
            [("pdf", DocumentOperationKind.Render)] =
                "Rendering a PDF page to an image is not implemented, so visual "
                + "verification of a PDF is not available.",
            [("pdf", DocumentOperationKind.Export)] =
                "Exporting to PDF is not implemented."
        };

    public static bool IsSupported(string? format, DocumentOperationKind operation) =>
        format is not null
        && Supported.TryGetValue(format, out var operations)
        && operations.Contains(operation);

    /// <summary>Why not, in terms of the format and the operation rather than a
    /// generic failure that reads like something went wrong.</summary>
    public static string UnsupportedReason(string? format, DocumentOperationKind operation)
    {
        if (format is null)
            return "The file's type is not one this build understands.";
        if (!Formats.Contains(format))
            return $"'{format}' documents are not supported by this build.";
        return Reasons.TryGetValue((format, operation), out var reason)
            ? reason
            : $"{operation} is not implemented for {format} documents.";
    }

    /// <summary>Operations this build implements for a format. Empty for a format
    /// it does not handle at all.</summary>
    public static IReadOnlySet<DocumentOperationKind> OperationsFor(string? format) =>
        format is not null && Supported.TryGetValue(format, out var operations)
            ? operations
            : new HashSet<DocumentOperationKind>();
}

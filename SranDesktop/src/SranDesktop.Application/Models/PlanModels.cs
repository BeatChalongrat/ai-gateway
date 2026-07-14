using System.Text.Json;
using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

// Wire shapes of chat-edge plan execution (internal/handler/excel.go / document.go).
// The plan JSON emitted by the AI inside :::excel-plan / :::doc-plan / :::pptx-plan
// fences is sent back verbatim, wrapped under the "plan" key.

public sealed record PlanExecutionResult(
    [property: JsonPropertyName("file")] PlanFileDto? File,
    [property: JsonPropertyName("validation")] PlanValidationDto? Validation,
    [property: JsonPropertyName("changes")] List<string>? Changes);

public sealed record PlanFileDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("content_type")] string? ContentType,
    [property: JsonPropertyName("file_size_bytes")] long FileSizeBytes,
    [property: JsonPropertyName("download_url")] string? DownloadUrl,
    [property: JsonPropertyName("version")] int Version);

public sealed record PlanValidationDto(
    [property: JsonPropertyName("valid")] bool Valid,
    [property: JsonPropertyName("issues")] List<string>? Issues,
    [property: JsonPropertyName("errors")] List<string>? Errors,
    [property: JsonPropertyName("warnings")] List<string>? Warnings,
    // excel: sheets / charts; doc: page_count / word_count / ...
    [property: JsonPropertyName("stats")] Dictionary<string, int>? Stats);

/// <summary>POST .../excel/execute request.</summary>
public sealed record ExecuteExcelPlanRequest(
    [property: JsonPropertyName("source_file_id")] string? SourceFileId,
    [property: JsonPropertyName("output_filename")] string? OutputFilename,
    [property: JsonPropertyName("plan")] JsonElement Plan);

/// <summary>POST .../document/execute request (format: docx | pdf | pptx).</summary>
public sealed record ExecuteDocumentPlanRequest(
    [property: JsonPropertyName("source_file_id")] string? SourceFileId,
    [property: JsonPropertyName("format")] string Format,
    [property: JsonPropertyName("output_filename")] string? OutputFilename,
    [property: JsonPropertyName("plan")] JsonElement Plan);

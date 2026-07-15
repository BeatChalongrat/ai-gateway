using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>
/// Result of a server-side run_python execution (Phase 13 Part B). chat-edge
/// runs the code in its hardened sandbox and returns stdout/stderr plus any
/// artifacts the code wrote to /job/out — images inline as base64 data URIs,
/// other files summarised.
/// </summary>
public sealed record CodeExecutionResult(
    [property: JsonPropertyName("exit")] int Exit,
    [property: JsonPropertyName("stdout")] string? Stdout,
    [property: JsonPropertyName("stderr")] string? Stderr,
    [property: JsonPropertyName("timed_out")] bool TimedOut,
    [property: JsonPropertyName("truncated")] bool Truncated,
    [property: JsonPropertyName("images")] List<CodeExecutionImage>? Images,
    [property: JsonPropertyName("files")] List<CodeExecutionFile>? Files);

public sealed record CodeExecutionImage(
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("data_uri")] string DataUri);

public sealed record CodeExecutionFile(
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("content_type")] string ContentType,
    [property: JsonPropertyName("size_bytes")] int SizeBytes);

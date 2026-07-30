using System.Text.Json;
using System.Text.Json.Serialization;

namespace SranDesktop.DocumentWorker;

internal static class WorkerJson
{
    public static JsonSerializerOptions Options { get; } = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true
    };
}

internal sealed record WorkerRequest
{
    [JsonPropertyName("protocol_version")] public int ProtocolVersion { get; init; }
    [JsonPropertyName("run_id")] public string RunId { get; init; } = "";
    [JsonPropertyName("step_id")] public string StepId { get; init; } = "";
    [JsonPropertyName("attempt_id")] public string AttemptId { get; init; } = "";
    [JsonPropertyName("idempotency_key")] public string IdempotencyKey { get; init; } = "";
    [JsonPropertyName("operation")] public string Operation { get; init; } = "";
    [JsonPropertyName("payload_json")] public string PayloadJson { get; init; } = "{}";
    [JsonPropertyName("sensitivity")] public string Sensitivity { get; init; } = "internal";
    [JsonPropertyName("deadline_unix_ms")] public long DeadlineUnixMs { get; init; }
}

internal sealed record WorkerObservation(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("detail")] string Detail);

internal sealed record WorkerResponse
{
    [JsonPropertyName("protocol_version")] public int ProtocolVersion { get; init; } = 1;
    [JsonPropertyName("run_id")] public string RunId { get; init; } = "";
    [JsonPropertyName("step_id")] public string StepId { get; init; } = "";
    [JsonPropertyName("attempt_id")] public string AttemptId { get; init; } = "";
    [JsonPropertyName("idempotency_key")] public string IdempotencyKey { get; init; } = "";
    [JsonPropertyName("operation")] public string Operation { get; init; } = "";
    [JsonPropertyName("succeeded")] public bool Succeeded { get; init; }
    [JsonPropertyName("failure_code")] public string? FailureCode { get; init; }
    [JsonPropertyName("failure_reason")] public string? FailureReason { get; init; }
    [JsonPropertyName("side_effect_performed")] public bool SideEffectPerformed { get; init; }
    [JsonPropertyName("started_utc")] public DateTimeOffset StartedUtc { get; init; }
    [JsonPropertyName("completed_utc")] public DateTimeOffset CompletedUtc { get; init; }
    [JsonPropertyName("output_files")] public IReadOnlyList<string> OutputFiles { get; init; } = [];
    [JsonPropertyName("observations")]
    public IReadOnlyList<WorkerObservation> Observations { get; init; } = [];
    [JsonPropertyName("result_json")] public string? ResultJson { get; init; }
    [JsonPropertyName("temporary_artifacts")]
    public IReadOnlyList<string> TemporaryArtifacts { get; init; } = [];

    public static WorkerResponse Failed(
        WorkerRequest request,
        DateTimeOffset startedUtc,
        string code,
        string reason,
        bool sideEffectPerformed) =>
        new()
        {
            RunId = request.RunId,
            StepId = request.StepId,
            AttemptId = request.AttemptId,
            IdempotencyKey = request.IdempotencyKey,
            Operation = request.Operation,
            Succeeded = false,
            FailureCode = code,
            FailureReason = reason,
            SideEffectPerformed = sideEffectPerformed,
            StartedUtc = startedUtc,
            CompletedUtc = DateTimeOffset.UtcNow
        };

    public static WorkerResponse Succeeded_(
        WorkerRequest request,
        DateTimeOffset startedUtc,
        IReadOnlyList<string> outputFiles,
        IReadOnlyList<WorkerObservation> observations,
        string? resultJson,
        IReadOnlyList<string>? temporaryArtifacts = null) =>
        new()
        {
            RunId = request.RunId,
            StepId = request.StepId,
            AttemptId = request.AttemptId,
            IdempotencyKey = request.IdempotencyKey,
            Operation = request.Operation,
            Succeeded = true,
            SideEffectPerformed = outputFiles.Count > 0,
            StartedUtc = startedUtc,
            CompletedUtc = DateTimeOffset.UtcNow,
            OutputFiles = outputFiles,
            Observations = observations,
            ResultJson = resultJson,
            TemporaryArtifacts = temporaryArtifacts ?? []
        };
}

namespace SranDesktop.Application.Models;

/// <summary>
/// Payload-free security audit record. Callers may supply categories,
/// identifiers and counts, but never raw arguments, content, command output,
/// clipboard data, screenshots, tokens or headers.
/// </summary>
public sealed record SecurityAuditEvent(
    string EventType,
    string PolicyDecision,
    string? ToolName = null,
    string? Action = null,
    string? RiskClass = null,
    string? TargetCategory = null,
    string? DestinationCategory = null,
    bool? ExternalTransmission = null,
    bool? Success = null,
    string? ErrorCode = null,
    long? DurationMs = null,
    long? ByteCount = null,
    string? CorrelationId = null);

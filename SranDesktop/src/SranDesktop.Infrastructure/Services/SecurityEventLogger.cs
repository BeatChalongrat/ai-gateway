using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Services;

public sealed class SecurityEventLogger(ILogger<SecurityEventLogger> logger)
    : ISecurityEventLogger
{
    public void Write(SecurityAuditEvent auditEvent)
    {
        ArgumentNullException.ThrowIfNull(auditEvent);
        logger.LogInformation(
            "security_event type={EventType} decision={PolicyDecision} tool={ToolName} " +
            "action={Action} risk={RiskClass} target={TargetCategory} " +
            "destination={DestinationCategory} external={ExternalTransmission} " +
            "success={Success} error={ErrorCode} durationMs={DurationMs} " +
            "byteCount={ByteCount} correlationId={CorrelationId}",
            auditEvent.EventType,
            auditEvent.PolicyDecision,
            auditEvent.ToolName,
            auditEvent.Action,
            auditEvent.RiskClass,
            auditEvent.TargetCategory,
            auditEvent.DestinationCategory,
            auditEvent.ExternalTransmission,
            auditEvent.Success,
            auditEvent.ErrorCode,
            auditEvent.DurationMs,
            auditEvent.ByteCount,
            auditEvent.CorrelationId);
    }
}

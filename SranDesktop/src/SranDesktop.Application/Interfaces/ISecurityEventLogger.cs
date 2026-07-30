using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

public interface ISecurityEventLogger
{
    void Write(SecurityAuditEvent auditEvent);
}

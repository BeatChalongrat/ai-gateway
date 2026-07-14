using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Interfaces;

public enum PermissionDecision
{
    /// <summary>Execute without asking.</summary>
    Auto,

    /// <summary>Show the consent dialog first.</summary>
    NeedsConsent,

    /// <summary>Blocked by a persisted or session deny.</summary>
    Denied
}

public interface IPermissionService
{
    /// <summary>Resolve the decision for a tool + scope (persisted → session → tool default).</summary>
    Task<PermissionDecision> CheckAsync(IMcpTool tool, string scope, CancellationToken ct = default);

    /// <summary>Cache an Allow-Once / Deny decision for the rest of this app session.</summary>
    void RecordSessionDecision(string toolName, string scope, PermissionLevel level);

    /// <summary>Persist allow_always / deny_always to the tool_permissions table.</summary>
    Task PersistDecisionAsync(string toolName, string scope, string permission, CancellationToken ct = default);
}

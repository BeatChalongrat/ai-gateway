using System.Collections.Concurrent;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp;

/// <summary>
/// Three-tier permission resolution: persisted overrides (tool_permissions
/// table) → in-memory session decisions → the tool's declared default.
/// </summary>
public sealed class PermissionService : IPermissionService
{
    private readonly IConversationStore _store;
    private readonly ISecurityEventLogger? _securityEvents;
    private readonly ConcurrentDictionary<string, PermissionLevel> _sessionCache = new();

    public PermissionService(
        IConversationStore store,
        ISecurityEventLogger? securityEvents = null)
    {
        _store = store;
        _securityEvents = securityEvents;
    }

    public async Task<PermissionDecision> CheckAsync(
        IMcpTool tool, string scope, CancellationToken ct = default)
    {
        // 1. Persistent overrides
        var persisted = await _store.GetToolPermissionAsync(tool.Name, scope, ct);
        if (persisted == "allow_always")
            return Record(tool, scope, PermissionDecision.Auto, "stored_allow");
        if (persisted == "deny_always")
            return Record(tool, scope, PermissionDecision.Denied, "stored_deny");

        // 2. Session decisions
        if (_sessionCache.TryGetValue(CacheKey(tool.Name, scope), out var cached))
        {
            if (cached is PermissionLevel.AllowOnce or PermissionLevel.AllowAlways)
                return Record(tool, scope, PermissionDecision.Auto, "task_allow");
            if (cached == PermissionLevel.Deny)
                return Record(tool, scope, PermissionDecision.Denied, "task_deny");
        }

        // 3. Tool default
        var decision = tool.DefaultPermission switch
        {
            PermissionLevel.AllowAlways => PermissionDecision.Auto,
            PermissionLevel.Deny => PermissionDecision.Denied,
            _ => PermissionDecision.NeedsConsent
        };
        return Record(tool, scope, decision, "tool_default");
    }

    public void RecordSessionDecision(string toolName, string scope, PermissionLevel level) =>
        _sessionCache[CacheKey(toolName, scope)] = level;

    public Task PersistDecisionAsync(
        string toolName, string scope, string permission, CancellationToken ct = default) =>
        _store.SetToolPermissionAsync(toolName, scope, permission, ct);

    private static string CacheKey(string toolName, string scope) => $"{toolName}::{scope}";

    private PermissionDecision Record(
        IMcpTool tool,
        string scope,
        PermissionDecision decision,
        string source)
    {
        _securityEvents?.Write(new SecurityAuditEvent(
            "permission.evaluated",
            $"{source}:{decision}",
            tool.Name,
            TargetCategory: scope == "*" ? "global" : "scoped"));
        return decision;
    }
}

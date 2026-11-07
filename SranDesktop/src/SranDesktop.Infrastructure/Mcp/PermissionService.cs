using System.Collections.Concurrent;
using SranDesktop.Application.Interfaces;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp;

/// <summary>
/// Three-tier permission resolution: persisted overrides (tool_permissions
/// table) → in-memory session decisions → the tool's declared default.
/// </summary>
public sealed class PermissionService : IPermissionService
{
    private readonly IConversationStore _store;
    private readonly ConcurrentDictionary<string, PermissionLevel> _sessionCache = new();

    public PermissionService(IConversationStore store) => _store = store;

    public async Task<PermissionDecision> CheckAsync(
        IMcpTool tool, string scope, CancellationToken ct = default)
    {
        // 1. Persistent overrides
        var persisted = await _store.GetToolPermissionAsync(tool.Name, scope, ct);
        if (persisted == "allow_always") return PermissionDecision.Auto;
        if (persisted == "deny_always") return PermissionDecision.Denied;

        // 2. Session decisions
        if (_sessionCache.TryGetValue(CacheKey(tool.Name, scope), out var cached))
        {
            if (cached is PermissionLevel.AllowOnce or PermissionLevel.AllowAlways)
                return PermissionDecision.Auto;
            if (cached == PermissionLevel.Deny)
                return PermissionDecision.Denied;
        }

        // 3. Tool default
        return tool.DefaultPermission switch
        {
            PermissionLevel.AllowAlways => PermissionDecision.Auto,
            PermissionLevel.Deny => PermissionDecision.Denied,
            _ => PermissionDecision.NeedsConsent
        };
    }

    public void RecordSessionDecision(string toolName, string scope, PermissionLevel level) =>
        _sessionCache[CacheKey(toolName, scope)] = level;

    public async Task<bool> HasStoredDecisionAsync(
        string toolName, string scope, CancellationToken ct = default)
    {
        var persisted = await _store.GetToolPermissionAsync(toolName, scope, ct);
        if (persisted is "allow_always" or "deny_always")
            return true;
        return _sessionCache.ContainsKey(CacheKey(toolName, scope));
    }

    public Task PersistDecisionAsync(
        string toolName, string scope, string permission, CancellationToken ct = default) =>
        _store.SetToolPermissionAsync(toolName, scope, permission, ct);

    private static string CacheKey(string toolName, string scope) => $"{toolName}::{scope}";
}

using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Shell.Hosting;

/// <summary>
/// Outcome of a one-time local→server conversation reconciliation.
/// <paramref name="Total"/> = local conversations examined;
/// <paramref name="OnServer"/> = how many are on the server AFTER the push (safe
/// at cut-over); <paramref name="MigratedUp"/> = local-only ones that gained a
/// server home during this run; <paramref name="LocalOnlyRemaining"/> = still
/// local-only and therefore EXPORTED so nothing is lost silently.
/// </summary>
public sealed record ConversationMigrationResult(
    int Total, int OnServer, int MigratedUp, int LocalOnlyRemaining, string? ExportPath);

/// <summary>
/// Phase 5 §4.1 — the local conversation migration. The hosted page reads
/// conversations from the SERVER; a conversation that lives ONLY in this
/// machine's SQLite store would vanish from the sidebar at cut-over. This
/// migrates such conversations UP by reusing the existing push-sync
/// (<see cref="IAgentConversationSyncService"/>, the FIX 9 one-way backup, useful
/// to web on its own — abandonment rule 3), then EXPORTS anything that is STILL
/// local-only so a user's history is never lost silently.
///
/// CORRECTNESS NOTE (the Phase-5-live bug this fixes): "on the server" is NOT
/// "ServerId != null". Conversations created through the server API
/// (<see cref="ConversationSource.Server"/>, the normal Chat-Mode case) ARE
/// server conversations — their local id is the server id — and the push-sync
/// DELIBERATELY keeps their local <c>server_id</c> column NULL (so the sidebar
/// pull does not mistake them for Phase-3 mirrors). Only genuinely local-only
/// conversations are <see cref="ConversationSource.Local"/> with no ServerId.
/// The first live run measured 0/32 "migrated" purely because it read ServerId;
/// in fact all 32 were already on the server and nothing was at risk.
///
/// Host-side and one-shot: not a bridge op. The page has no vocabulary for local
/// conversations and does not gain one.
/// </summary>
public sealed class ShellConversationMigrator
{
    private readonly IConversationStore _store;
    private readonly IAgentConversationSyncService _sync;
    private readonly ILogger<ShellConversationMigrator> _logger;

    public ShellConversationMigrator(
        IConversationStore store, IAgentConversationSyncService sync, ILogger<ShellConversationMigrator> logger)
    {
        _store = store;
        _sync = sync;
        _logger = logger;
    }

    /// <summary>A conversation is safe at cut-over (readable from the server by
    /// the hosted page) when it was created on the server OR has been mirrored
    /// there. Only <see cref="ConversationSource.Local"/> rows with no ServerId
    /// are genuinely local-only.</summary>
    private static bool IsOnServer(Conversation c) =>
        c.Source == ConversationSource.Server || !string.IsNullOrEmpty(c.ServerId);

    /// <summary>
    /// Reconcile the local store with the server: push local-only conversations
    /// up, then export whatever is still local-only. Returns before/after counts
    /// classified correctly (Source + ServerId, never ServerId alone).
    /// </summary>
    public async Task<ConversationMigrationResult> MigrateAsync(string exportDir, CancellationToken ct = default)
    {
        var before = await _store.GetConversationsAsync(ct);
        var total = before.Count;
        var onServerBefore = before.Count(IsOnServer);
        var localOnlyBefore = total - onServerBefore;
        _logger.LogInformation(
            "Conversation migration: {Total} local conversation(s); {OnServer} already on the server, {LocalOnly} local-only",
            total, onServerBefore, localOnlyBefore);

        // Migrate up via the existing push-sync ONLY if there is something at
        // risk. A store that is entirely server-backed needs no network at all.
        if (localOnlyBefore > 0)
        {
            try
            {
                await _sync.SyncAllAsync(ct);
            }
            catch (Exception ex)
            {
                // SyncAllAsync is documented never to throw for transport
                // failures; a surprise must not crash the shell — the export
                // fallback below still preserves everything.
                _logger.LogWarning(ex, "Push-sync during migration failed; local-only conversations will be exported");
            }
        }

        var after = await _store.GetConversationsAsync(ct);
        var onServerAfter = after.Count(IsOnServer);
        var migratedUp = Math.Max(0, onServerAfter - onServerBefore);
        var stillLocalOnly = after.Where(c => !IsOnServer(c)).ToList();

        string? exportPath = null;
        if (stillLocalOnly.Count > 0)
        {
            exportPath = await ExportAsync(stillLocalOnly, exportDir, ct);
            // Visible, not silent (§1.4): the caller MUST surface this to the
            // user at cut-over and offer the export — never skip quietly.
            _logger.LogWarning(
                "Conversation migration: {Count} conversation(s) could NOT be migrated to the server and were exported to {Path}. " +
                "This must be shown to the user before the shell becomes default — their history is only in that file.",
                stillLocalOnly.Count, exportPath);
        }

        _logger.LogInformation(
            "Conversation migration done: total={Total} onServer={OnServer} migratedUp={Migrated} localOnlyRemaining={Remaining}",
            total, onServerAfter, migratedUp, stillLocalOnly.Count);
        return new ConversationMigrationResult(total, onServerAfter, migratedUp, stillLocalOnly.Count, exportPath);
    }

    private async Task<string> ExportAsync(
        IReadOnlyList<Conversation> conversations, string exportDir, CancellationToken ct)
    {
        Directory.CreateDirectory(exportDir);
        var path = Path.Combine(exportDir, "local-conversations-export.json");

        var payload = new List<object>();
        foreach (var c in conversations)
        {
            var messages = await _store.GetMessagesAsync(c.Id, ct);
            payload.Add(new
            {
                id = c.Id,
                title = c.Title,
                createdAt = c.CreatedAt,
                updatedAt = c.UpdatedAt,
                messages = messages.Select(m => new
                {
                    role = m.Role.ToString(),
                    content = m.Content,
                    createdAt = m.CreatedAt,
                    sortOrder = m.SortOrder,
                }).ToArray(),
            });
        }

        var json = JsonSerializer.Serialize(
            new { exportedConversations = payload.Count, conversations = payload },
            new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(path, json, ct);
        return path;
    }
}

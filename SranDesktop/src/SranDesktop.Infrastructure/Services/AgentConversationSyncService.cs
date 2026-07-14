using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// One-way push sync of local agent-mode conversations to chat-edge's import
/// endpoints (Phase 3 D). Only <c>source='local'</c> conversations and their
/// user/assistant text messages sync — tool executions, screenshots and
/// tool-call metadata stay local-only (chat-edge cannot represent them; the
/// transcript is the valuable part). Sync state lives in SQLite:
/// conversations.server_id maps to the mirror; messages.server_id stores the
/// SERVER CONVERSATION id as the synced marker (the server dedupes by our
/// message id as its client_id, so no per-message server id exists).
/// </summary>
public sealed class AgentConversationSyncService : IAgentConversationSyncService, IDisposable
{
    private const int BatchSize = 200;
    private static readonly TimeSpan QueueDebounce = TimeSpan.FromSeconds(5);
    /// <summary>Slower re-arm after an offline drain (Phase 10 D3) — retries
    /// keep coming without hammering an unreachable server.</summary>
    private static readonly TimeSpan OfflineRetryDebounce = TimeSpan.FromSeconds(30);

    private readonly IChatEdgeApiClient _api;
    private readonly IConversationStore _store;
    private readonly ISettingsService _settings;
    private readonly ILogger<AgentConversationSyncService> _logger;

    private readonly SemaphoreSlim _syncLock = new(1, 1);
    private readonly object _queueLock = new();
    private readonly HashSet<string> _pending = new(StringComparer.Ordinal);
    private Timer? _debounce;

    // Phase 8 Task 1: escalate Warning-level per-conversation failures to ONE
    // toast per session (the sweeps keep retrying silently after that).
    private readonly IToastService? _toasts;
    private int _failureToastShown;

    public AgentConversationSyncService(
        IChatEdgeApiClient api,
        IConversationStore store,
        ISettingsService settings,
        ILogger<AgentConversationSyncService> logger,
        IToastService? toasts = null)
    {
        _api = api;
        _store = store;
        _settings = settings;
        _logger = logger;
        _toasts = toasts;
    }

    private void NotifySyncFailureOnce()
    {
        if (Interlocked.Exchange(ref _failureToastShown, 1) == 0)
            _toasts?.Show(ToastKind.Error, "toast.syncFailed");
    }

    public async Task SyncAllAsync(CancellationToken ct = default)
    {
        if (!_settings.Current.SyncAgentConversations)
            return;
        if (!await _syncLock.WaitAsync(0, ct))
            return; // a sync is already running — the next trigger catches up

        try
        {
            var conversations = await _store.GetConversationsAsync(ct);
            foreach (var conversation in conversations)
            {
                if (conversation.Source != ConversationSource.Local)
                    continue;
                ct.ThrowIfCancellationRequested();
                try
                {
                    await PushConversationAsync(conversation, ct);
                }
                catch (HttpRequestException ex) when (ex.StatusCode is not null)
                {
                    // The server ANSWERED — this one conversation is poisoned
                    // (404 after a failed recovery, 422, 500…). It must never
                    // starve the rest of the sweep.
                    _logger.LogWarning(ex, "Agent sync failed for {Id} (HTTP {Status})",
                        conversation.Id, (int)ex.StatusCode);
                    NotifySyncFailureOnce();
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                    // No response at all — offline / server down. Hammering the
                    // remaining conversations is pointless; retried next trigger.
                    _logger.LogDebug(ex, "Agent sync paused (server unreachable)");
                    return;
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "Agent sync failed for {Id}", conversation.Id);
                    NotifySyncFailureOnce();
                }
            }
        }
        finally
        {
            _syncLock.Release();
        }
    }

    public void QueueConversationSync(string conversationId)
    {
        if (!_settings.Current.SyncAgentConversations)
            return;
        lock (_queueLock)
        {
            _pending.Add(conversationId);
            _debounce ??= new Timer(_ => _ = DrainQueueAsync(), null,
                Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
            _debounce.Change(QueueDebounce, Timeout.InfiniteTimeSpan);
        }
    }

    // Internal for tests: the timer path is `_ = DrainQueueAsync()`.
    internal async Task DrainQueueAsync()
    {
        List<string> ids;
        lock (_queueLock)
        {
            ids = _pending.ToList();
            _pending.Clear();
        }

        await _syncLock.WaitAsync();
        try
        {
            for (var i = 0; i < ids.Count; i++)
            {
                var id = ids[i];
                try
                {
                    var conversation = await _store.GetConversationAsync(id);
                    if (conversation is { Source: ConversationSource.Local })
                        await PushConversationAsync(conversation, CancellationToken.None);
                }
                catch (HttpRequestException ex) when (ex.StatusCode is not null)
                {
                    // Server answered with an error — poisoned conversation;
                    // keep draining the rest of the queue.
                    _logger.LogWarning(ex, "Queued agent sync failed for {Id} (HTTP {Status})",
                        id, (int)ex.StatusCode);
                    NotifySyncFailureOnce();
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                    // Server unreachable — put THIS id and the undrained rest
                    // back so the re-armed debounce retries them (Phase 10 D3;
                    // previously they were stranded until an app restart).
                    _logger.LogDebug(ex, "Queued agent sync paused (server unreachable) at {Id}; " +
                        "{Count} id(s) re-queued", id, ids.Count - i);
                    RequeuePending(ids.Skip(i));
                    return;
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "Queued agent sync failed for {Id}", id);
                    NotifySyncFailureOnce();
                }
            }
        }
        finally
        {
            _syncLock.Release();
        }
    }

    /// <summary>Re-queues ids an offline drain could not push and re-arms the
    /// debounce timer on a slower cadence so they retry without waiting for
    /// the next user turn (Phase 10 D3).</summary>
    private void RequeuePending(IEnumerable<string> ids)
    {
        lock (_queueLock)
        {
            foreach (var id in ids)
                _pending.Add(id);
            if (_pending.Count > 0)
                _debounce?.Change(OfflineRetryDebounce, Timeout.InfiniteTimeSpan);
        }
    }

    /// <summary>Snapshot of queued-but-unsynced ids (test seam).</summary>
    internal IReadOnlyCollection<string> PendingQueueSnapshot
    {
        get
        {
            lock (_queueLock)
            {
                return _pending.ToList();
            }
        }
    }

    private async Task PushConversationAsync(Conversation conversation, CancellationToken ct)
    {
        // 1. Ensure the server mirror exists (idempotent by our local id).
        var serverId = conversation.ServerId;
        if (string.IsNullOrEmpty(serverId))
        {
            var result = await _api.ImportConversationAsync(
                conversation.Id, conversation.Title, conversation.ModelId,
                conversation.CreatedAt, ct);
            serverId = result.Conversation.Id;
            await _store.SetConversationServerIdAsync(conversation.Id, serverId, ct);
        }

        // 2. Push unsynced user/assistant text in chronological batches.
        var unsynced = (await _store.GetUnsyncedMessagesAsync(conversation.Id, ct))
            .Where(m => m.Role is MessageRole.User or MessageRole.Assistant
                        && !string.IsNullOrWhiteSpace(m.Content))
            .ToList();
        if (unsynced.Count == 0)
            return;

        // At most ONE mirror recovery per push: a 404 from messages/import
        // means the mirror was deleted on the web — re-importing by our local
        // id resurrects it server-side; a second 404 after that propagates
        // (never loop).
        var reimported = false;

        foreach (var chunk in unsynced.Chunk(BatchSize))
        {
            var items = chunk.Select(m => new ImportMessageItem(
                m.Id,
                m.Role == MessageRole.User ? "user" : "assistant",
                m.Content!,
                m.ModelId,
                m.CreatedAt.UtcDateTime.ToString("o"))).ToList();

            ImportMessagesResponse response;
            try
            {
                response = await _api.ImportMessagesAsync(serverId, items, ct);
            }
            catch (HttpRequestException ex) when (
                ex.StatusCode == System.Net.HttpStatusCode.NotFound && !reimported)
            {
                reimported = true;
                _logger.LogWarning(ex, "Server mirror missing for {Id}; re-importing", conversation.Id);
                var result = await _api.ImportConversationAsync(
                    conversation.Id, conversation.Title, conversation.ModelId,
                    conversation.CreatedAt, ct);
                // The returned id is authoritative (normally the resurrected
                // row keeps its id; a fresh row gets a new one).
                serverId = result.Conversation.Id;
                await _store.SetConversationServerIdAsync(conversation.Id, serverId, ct);
                response = await _api.ImportMessagesAsync(serverId, items, ct);
            }

            await _store.MarkMessagesSyncedAsync(
                chunk.Select(m => m.Id).ToList(), serverId, ct);
            _logger.LogDebug("Agent sync batch for {Id}: imported {Imported}, skipped {Skipped}",
                conversation.Id, response.Imported, response.Skipped);
        }

        // Refresh last_synced_at now that the transcript is mirrored.
        await _store.SetConversationServerIdAsync(conversation.Id, serverId, ct);
        _logger.LogInformation("Agent sync pushed {Count} message(s) for {Id}",
            unsynced.Count, conversation.Id);
    }

    public void Dispose()
    {
        _debounce?.Dispose();
        _syncLock.Dispose();
    }
}

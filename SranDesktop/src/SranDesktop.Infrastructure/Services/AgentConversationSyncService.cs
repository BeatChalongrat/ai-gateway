using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Push sync of agent-mode turns to chat-edge's import endpoints. Since FIX 9
/// the server is the authoritative home of the VISIBLE transcript (user text +
/// final assistant text) so web and desktop show the same conversation:
/// a completed agent turn lands synchronously via
/// <see cref="SyncConversationNowAsync"/>, with the debounced queue as the
/// offline retry. Only user/assistant text syncs — the agent's working state
/// (tool chains, screenshots, tool-call metadata) stays local-only (chat-edge
/// cannot represent it; see AgentChainCodec.IsChainRow).
///
/// Conversations created through the server API (<c>source='server'</c>, the
/// normal case) use their own id as the server conversation id; legacy
/// <c>source='local'</c> conversations keep the Phase 3 mirror flow
/// (ImportConversation idempotent by local id). Sync state lives in SQLite:
/// conversations.server_id maps a local conversation to its mirror;
/// messages.server_id stores the SERVER CONVERSATION id as the synced marker
/// (the server dedupes by our message id as its client_id, so no per-message
/// server id exists). A push that discovers the conversation was deleted on
/// the server (404/410) deletes the local copy and raises
/// <see cref="RemoteConversationDeleted"/> — deletes propagate, never
/// resurrect (FIX 9 Part C).
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

    /// <inheritdoc />
    public event EventHandler<string>? RemoteConversationDeleted;

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
                    if (conversation is not null)
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

    /// <inheritdoc />
    public async Task<bool> SyncConversationNowAsync(string conversationId, CancellationToken ct = default)
    {
        if (!_settings.Current.SyncAgentConversations)
            return true; // user opted out of sync — nothing is "pending"

        await _syncLock.WaitAsync(ct);
        try
        {
            var conversation = await _store.GetConversationAsync(conversationId, ct);
            if (conversation is null)
                return true; // deleted while the turn ran — nothing to land
            await PushConversationAsync(conversation, ct);
            return true;
        }
        catch (HttpRequestException ex) when (ex.StatusCode is not null)
        {
            // The server ANSWERED with an error — retrying the same payload
            // won't help (matches the sweep's poisoned-conversation handling).
            _logger.LogWarning(ex, "Turn sync rejected for {Id} (HTTP {Status})",
                conversationId, (int)ex.StatusCode);
            NotifySyncFailureOnce();
            return false;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or IOException)
        {
            // Offline / server down — the debounced queue keeps retrying.
            _logger.LogInformation(ex, "Turn could not land on the server for {Id}; queued for retry",
                conversationId);
            RequeuePending(new[] { conversationId });
            return false;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Turn sync failed for {Id}", conversationId);
            NotifySyncFailureOnce();
            return false;
        }
        finally
        {
            _syncLock.Release();
        }
    }

    private async Task PushConversationAsync(Conversation conversation, CancellationToken ct)
    {
        // 1. Resolve the server conversation. Conversations created through
        //    the server API (the normal case) ARE server conversations — their
        //    local id is the server id. Legacy local-first conversations keep
        //    the Phase 3 mirror flow, idempotent by our local id.
        string serverId;
        if (conversation.Source == ConversationSource.Server)
        {
            serverId = conversation.Id;
        }
        else
        {
            serverId = conversation.ServerId ?? string.Empty;
            if (serverId.Length == 0)
            {
                ImportConversationResponse result;
                try
                {
                    result = await _a
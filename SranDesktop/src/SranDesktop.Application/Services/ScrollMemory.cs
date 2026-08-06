namespace SranDesktop.Application.Services;

/// <summary>
/// Per-conversation scroll positions (Phase 13 Task 3), ported from the web's
/// lib/scrollMemory. Switching to another chat and back used to always land at
/// the bottom, losing your place in a long thread.
///
/// IN-MEMORY, session-scoped, and deliberately NOT written to SQLite: an
/// offset is a pixel position into a rendered layout, so a value restored
/// after a restart (different window size, different font scale, messages
/// added from the web) would point at the wrong content — worse than the
/// honest default of starting at the bottom.
/// </summary>
public sealed class ScrollMemory
{
    /// <summary>Bounded so a long session can't grow the map without limit.
    /// Insertion order doubles as the LRU list, exactly like the web Map.</summary>
    public const int MaxRemembered = 50;

    private readonly object _gate = new();
    private readonly Dictionary<string, double> _offsets = new();
    private readonly LinkedList<string> _order = new();

    /// <summary>Shared instance for the chat view; injectable copies exist for tests.</summary>
    public static ScrollMemory Instance { get; } = new();

    public void Remember(string? conversationId, double offset)
    {
        if (string.IsNullOrEmpty(conversationId))
            return;
        lock (_gate)
        {
            // Re-insert so the most recently touched chat is newest.
            if (_offsets.ContainsKey(conversationId))
                _order.Remove(conversationId);
            _offsets[conversationId] = Math.Max(0, offset);
            _order.AddLast(conversationId);

            while (_order.Count > MaxRemembered && _order.First is { } oldest)
            {
                _offsets.Remove(oldest.Value);
                _order.RemoveFirst();
            }
        }
    }

    /// <summary>The saved offset, or null when this conversation has none —
    /// the caller then keeps the default "start at the bottom" behaviour.</summary>
    public double? Recall(string? conversationId)
    {
        if (string.IsNullOrEmpty(conversationId))
            return null;
        lock (_gate)
            return _offsets.TryGetValue(conversationId, out var v) ? v : null;
    }

    /// <summary>Drop a conversation's position, returning it to the default
    /// "start at the newest message" — what "jump to latest" means.</summary>
    public void Forget(string? conversationId)
    {
        if (string.IsNullOrEmpty(conversationId))
            return;
        lock (_gate)
        {
            if (_offsets.Remove(conversationId))
                _order.Remove(conversationId);
        }
    }

    public void Clear()
    {
        lock (_gate)
        {
            _offsets.Clear();
            _order.Clear();
        }
    }

    public int Count
    {
        get { lock (_gate) return _offsets.Count; }
    }
}

/// <summary>
/// Decides what a remembered offset means for a freshly rendered transcript.
/// Split out from the view so the awkward cases — the chat got shorter, the
/// user was already at the bottom, nothing was remembered — are testable
/// without a WPF ScrollViewer.
/// </summary>
public static class ScrollRestore
{
    /// <summary>
    /// The offset to scroll to, or null to keep the default "stick to the
    /// bottom" behaviour.
    ///
    /// Null is returned when there is nothing remembered, when the content is
    /// too short to scroll, and — importantly — when the remembered position
    /// WAS the bottom: restoring that as a fixed offset would unpin the view
    /// and stop a live stream from following.
    /// </summary>
    public static double? Resolve(double? saved, double scrollableHeight, double bottomThreshold)
    {
        if (saved is not { } offset || scrollableHeight <= 0)
            return null;
        // The thread may have shrunk (messages deleted, a narrower window):
        // clamp rather than scroll past the end.
        var target = Math.Clamp(offset, 0, scrollableHeight);
        return target >= scrollableHeight - bottomThreshold ? null : target;
    }
}

using SranDesktop.Application.Interfaces;

namespace SranDesktop.Application.Services;

/// <summary>One queued/visible toast. Reference identity is the handle the
/// UI uses to dismiss and to diff its rows.</summary>
public sealed class ToastItem
{
    public required ToastKind Kind { get; init; }
    public required string MessageKey { get; init; }
    public string? Detail { get; init; }
    public required TimeSpan Duration { get; init; }
    /// <summary>Recomputed when a pending toast is promoted to visible, and
    /// extended when an identical consecutive Show() dedupes onto it.</summary>
    public DateTimeOffset ExpiresAt { get; internal set; }
}

/// <summary>
/// Pure toast queue (Phase 8 Task 1): max 3 visible (newest at bottom),
/// overflow waits in a pending queue, identical consecutive messages dedupe
/// onto the newest visible toast (its timer restarts instead of stacking a
/// twin). Time is injected so tests never sleep; the UI host pumps
/// <see cref="ExpireDue"/> from a dispatcher timer.
/// </summary>
public sealed class ToastService : IToastService
{
    public const int MaxVisible = 3;
    public static readonly TimeSpan InfoDuration = TimeSpan.FromSeconds(3);
    public static readonly TimeSpan ErrorDuration = TimeSpan.FromSeconds(6);

    /// <summary>App-wide instance for UI call sites without DI access
    /// (MessageBubble code-behind); DI registers this same object.</summary>
    public static ToastService Instance { get; } = new();

    private readonly Func<DateTimeOffset> _now;
    private readonly object _lock = new();
    private readonly List<ToastItem> _visible = new();
    private readonly Queue<ToastItem> _pending = new();

    /// <summary>Raised (on the caller's thread) whenever the visible set
    /// changes — the UI host marshals to its dispatcher.</summary>
    public event EventHandler? Changed;

    public ToastService(Func<DateTimeOffset>? now = null)
    {
        _now = now ?? (() => DateTimeOffset.UtcNow);
    }

    public IReadOnlyList<ToastItem> Visible
    {
        get
        {
            lock (_lock)
            {
                return _visible.ToList();
            }
        }
    }

    public void Show(ToastKind kind, string messageKey, string? detail = null, TimeSpan? duration = null)
    {
        var effective = duration
            ?? (kind == ToastKind.Error ? ErrorDuration : InfoDuration);
        lock (_lock)
        {
            // Dedupe identical consecutive messages: restart the newest
            // visible toast's timer instead of stacking a twin.
            var newest = _visible.Count > 0 ? _visible[^1] : null;
            if (newest is not null
                && newest.Kind == kind
                && newest.MessageKey == messageKey
                && newest.Detail == detail)
            {
                newest.ExpiresAt = _now() + effective;
                RaiseChanged();
                return;
            }

            var item = new ToastItem
            {
                Kind = kind,
                MessageKey = messageKey,
                Detail = detail,
                Duration = effective,
                ExpiresAt = _now() + effective
            };
            if (_visible.Count < MaxVisible)
                _visible.Add(item); // newest at bottom
            else
                _pending.Enqueue(item);
        }
        RaiseChanged();
    }

    /// <summary>Remove expired toasts and promote pending ones. Called by the
    /// UI host on a timer; also safe to call from tests with a fake clock.</summary>
    public void ExpireDue()
    {
        var changed = false;
        lock (_lock)
        {
            var now = _now();
            changed = _visible.RemoveAll(t => t.ExpiresAt <= now) > 0;
            changed |= PromotePending(now);
        }
        if (changed)
            RaiseChanged();
    }

    /// <summary>Click-to-dismiss (and test hook).</summary>
    public void Dismiss(ToastItem item)
    {
        var changed = false;
        lock (_lock)
        {
            changed = _visible.Remove(item);
            changed |= PromotePending(_now());
        }
        if (changed)
            RaiseChanged();
    }

    private bool PromotePending(DateTimeOffset now)
    {
        var promoted = false;
        while (_visible.Count < MaxVisible && _pending.Count > 0)
        {
            var next = _pending.Dequeue();
            next.ExpiresAt = now + next.Duration; // clock starts when it shows
            _visible.Add(next);
            promoted = true;
        }
        return promoted;
    }

    private void RaiseChanged() => Changed?.Invoke(this, EventArgs.Empty);
}

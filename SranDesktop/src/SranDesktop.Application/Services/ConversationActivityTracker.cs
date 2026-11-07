using System.Collections.Concurrent;

namespace SranDesktop.Application.Services;

/// <summary>
/// Tracks which conversations have work in flight — an upload being read by
/// the server, or a turn waiting on the model.
///
/// Why this exists: the empty-chat cleaner deletes any untitled conversation
/// with no stored messages, on the reasoning that such a chat was provably
/// never used. That reasoning has a hole. A brand-new chat with a large
/// attachment is untitled and has no stored messages *for as long as the
/// upload takes* — which, for a scanned PDF the server OCRs before it
/// answers, was measured at two and a half minutes. A user who clicks to
/// another chat while waiting had theirs deleted underneath them; the reply
/// then landed on a conversation that no longer existed, locally (FOREIGN KEY
/// violation) or on the server (HTTP 404), and the message was lost.
///
/// Being "selected" is not the same as being "in use", so the cleaner needs
/// this second signal.
///
/// Counted rather than boolean: one conversation can have several uploads and
/// a turn running at once, and the last one to finish must not clear a flag
/// the others still need.
/// </summary>
public interface IConversationActivityTracker
{
    /// <summary>Marks the conversation busy until the returned scope is
    /// disposed. Safe to nest and to call from any thread.</summary>
    IDisposable Begin(string conversationId);

    /// <summary>True while any work started by <see cref="Begin"/> is still
    /// running for this conversation.</summary>
    bool IsBusy(string conversationId);
}

public sealed class ConversationActivityTracker : IConversationActivityTracker
{
    private readonly ConcurrentDictionary<string, int> _counts = new(StringComparer.OrdinalIgnoreCase);

    public IDisposable Begin(string conversationId)
    {
        if (string.IsNullOrWhiteSpace(conversationId))
            return NullScope.Instance;

        _counts.AddOrUpdate(conversationId, 1, (_, n) => n + 1);
        return new Scope(this, conversationId);
    }

    public bool IsBusy(string conversationId) =>
        !string.IsNullOrWhiteSpace(conversationId)
        && _counts.TryGetValue(conversationId, out var n)
        && n > 0;

    private void End(string conversationId)
    {
        // AddOrUpdate rather than a remove-if-zero race: the entry is only
        // dropped when it actually reaches zero, and a concurrent Begin that
        // slips in between simply re-raises the count.
        var remaining = _counts.AddOrUpdate(conversationId, 0, (_, n) => n > 0 ? n - 1 : 0);
        if (remaining <= 0)
            _counts.TryRemove(new KeyValuePair<string, int>(conversationId, remaining));
    }

    private sealed class Scope : IDisposable
    {
        private readonly ConversationActivityTracker _owner;
        private readonly string _id;
        private bool _done;

        public Scope(ConversationActivityTracker owner, string id)
        {
            _owner = owner;
            _id = id;
        }

        public void Dispose()
        {
            // Idempotent: a double-dispose must not decrement twice and free
            // a conversation another in-flight operation still holds.
            if (_done) return;
            _done = true;
            _owner.End(_id);
        }
    }

    private sealed class NullScope : IDisposable
    {
        public static readonly NullScope Instance = new();
        public void Dispose() { }
    }
}

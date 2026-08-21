using System.Windows;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Bounded LRU of rendered message trees, so a row scrolling back into view is
/// re-attached instead of re-parsed. Without it every row entering the viewport
/// re-parsed its markdown and rebuilt its whole visual tree on the UI thread —
/// measured at ~1 full render per row-entry, 52% of them for a message already
/// rendered earlier in the same scroll pass (ScrollRenderCostTests).
///
/// THE KEY must contain everything that changes the output:
///
///  • owner        — the message's identity, taken as the DataContext OBJECT
///                   REFERENCE. Not redundant with the markdown: a WPF element
///                   has exactly one parent, so two rows showing identical text
///                   must not be handed the same element. A bound id property
///                   cannot serve here — see MarkdownViewer.CacheKeyFor.
///  • markdown     — the content revision. An edited message renders under a new
///                   key and the old entry simply ages out; there is nothing to
///                   invalidate by hand.
///  • locale       — MarkdownRenderer bakes translated strings into the tree at
///                   render time (the code-block copy tooltip, the canvas "open"
///                   label). Those would not follow a language switch.
///  • theme        — see below; kept for safety, not because a leak is known.
///
/// DELIBERATELY NOT IN THE KEY — wrap width. It cannot vary: the bubble is
/// Width="768" in ChatView.xaml:85 and the renderer's own MessageColumnWidth is
/// a const 768. A key component that is always the same value is noise.
///
/// ON THEME: every brush in a rendered tree is applied with
/// SetResourceReference (21 sites in MarkdownRenderer; the only literal brushes
/// are Brushes.Transparent), so a cached tree already follows a theme switch on
/// its own. Theme is still in the key, and the cache is still cleared on
/// ThemeChanged, because the cost of being wrong is a permanently mis-coloured
/// message and the cost of being right is re-rendering the ~10 visible rows once
/// per theme toggle.
///
/// NOT THREAD-SAFE by design — every caller is on the UI thread.
/// </summary>
internal sealed class RenderedMessageCache
{
    /// <summary><paramref name="Owner"/> is compared by reference — view models
    /// in this app do not override Equals, and identity is exactly what is
    /// wanted: a reloaded conversation builds new view models and should not
    /// serve trees rendered for the old ones.</summary>
    internal readonly record struct Key(object Owner, string Markdown, string Locale, bool Dark);

    private sealed record Entry(Key Key, FrameworkElement Element);

    private readonly int _capacity;
    private readonly Dictionary<Key, LinkedListNode<Entry>> _index = new();
    private readonly LinkedList<Entry> _lru = new(); // front = most recently used

    public RenderedMessageCache(int capacity = 60)
    {
        if (capacity < 1)
            throw new ArgumentOutOfRangeException(nameof(capacity));
        _capacity = capacity;
    }

    public int Count => _index.Count;

    public int Capacity => _capacity;

    /// <summary>
    /// A hit returns an element that is safe for <paramref name="owner"/> to
    /// adopt: either unparented, or already owned by this same viewer.
    ///
    /// If some OTHER element still holds it, this is a miss and the entry is
    /// dropped — assigning it would throw ("already the logical child of
    /// another element"). That is the one-parent rule made a runtime check
    /// rather than an assumption, because the cache cannot prove on its own
    /// that a message is never realized twice at once.
    /// </summary>
    public bool TryGet(Key key, DependencyObject owner, out FrameworkElement element)
    {
        element = null!;
        if (!_index.TryGetValue(key, out var node))
            return false;

        var candidate = node.Value.Element;
        var parent = candidate.Parent;
        if (parent is not null && !ReferenceEquals(parent, owner))
        {
            _lru.Remove(node);
            _index.Remove(key);
            return false;
        }

        _lru.Remove(node);
        _lru.AddFirst(node);
        element = candidate;
        return true;
    }

    public void Put(Key key, FrameworkElement element)
    {
        if (_index.TryGetValue(key, out var existing))
        {
            _lru.Remove(existing);
            _index.Remove(key);
        }

        var node = _lru.AddFirst(new Entry(key, element));
        _index[key] = node;

        while (_index.Count > _capacity)
        {
            var oldest = _lru.Last!;
            _lru.RemoveLast();
            _index.Remove(oldest.Value.Key);
        }
    }

    public void Clear()
    {
        _index.Clear();
        _lru.Clear();
    }

    /// <summary>Test seam: keys from most to least recently used.</summary>
    internal IEnumerable<Key> KeysMostRecentFirst()
    {
        for (var node = _lru.First; node is not null; node = node.Next)
            yield return node.Value.Key;
    }
}

namespace SranDesktop.Infrastructure.Native.Uia;

/// <summary>
/// The element snapshot bridging read_screen_elements → uia_click/uia_set_value:
/// ids in the listing index into this store. One snapshot at a time —
/// invalidated wholesale by the next read_screen_elements call, because ids
/// from an old listing describe a screen that may no longer exist. Elements
/// are stored as <c>object</c> (really AutomationElement) so the staleness
/// logic is testable without real UIA.
/// </summary>
public sealed class UiaSnapshotStore
{
    public const string StaleError =
        "element list is stale — call read_screen_elements again";

    private readonly object _lock = new();
    private IReadOnlyList<object>? _elements;

    public void Set(IReadOnlyList<object> elements)
    {
        lock (_lock)
        {
            _elements = elements;
        }
    }

    /// <summary>Resolve an element id from the CURRENT snapshot. False (with
    /// the stale message) when no snapshot exists or the id is out of range.</summary>
    public bool TryGet(int elementId, out object? element, out string? error)
    {
        lock (_lock)
        {
            if (_elements is null || elementId < 0 || elementId >= _elements.Count)
            {
                element = null;
                error = StaleError;
                return false;
            }
            element = _elements[elementId];
            error = null;
            return true;
        }
    }
}

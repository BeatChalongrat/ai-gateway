namespace SranDesktop.Presentation.Services;

/// <summary>Navigation keys the model picker responds to (Phase C PART 4).</summary>
public enum PickerNavKey { None, Up, Down, Home, End }

/// <summary>
/// Pure roving-focus index math for the model picker's keyboard navigation
/// (Phase C PART 4). Up/Down move by one (wrapping), Home/End jump to the ends.
/// Kept free of WPF so it is unit-testable; the code-behind maps Key → NavKey
/// and moves actual focus.
/// </summary>
public static class ModelPickerNavigator
{
    public static int NextIndex(int current, int count, PickerNavKey key, bool wrap = true)
    {
        if (count <= 0)
            return -1;
        switch (key)
        {
            case PickerNavKey.Home:
                return 0;
            case PickerNavKey.End:
                return count - 1;
            case PickerNavKey.Down:
                if (current < 0) return 0;
                return wrap ? (current + 1) % count : System.Math.Min(current + 1, count - 1);
            case PickerNavKey.Up:
                if (current < 0) return count - 1;
                return wrap ? (current - 1 + count) % count : System.Math.Max(current - 1, 0);
            default:
                return current;
        }
    }
}

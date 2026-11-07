namespace SranDesktop.Application.Interfaces;

/// <summary>
/// The per-OS boundary (Infrastructure split, 2026-08-25). The portable
/// composition root (<c>AddSranCore</c>) builds the tool registry in a fixed
/// order and, at three named slots, asks every registered provider for the
/// tools that only a platform can supply — clipboard, screen/input, UI
/// automation. Each platform ships one or more providers
/// (<c>SranDesktop.Infrastructure.Windows</c>, <c>…Windows.Desktop</c>); a
/// host registers the packs it wants and the portable side never names a
/// platform type. The slots exist so the model-visible tool ORDER is exactly
/// what it was before the split — providers are asked in registration order.
/// </summary>
public enum PlatformToolSlot
{
    /// <summary>After the filesystem tools (read_clipboard / write_clipboard sat here).</summary>
    Clipboard,
    /// <summary>After use_skill (computer_screenshot … open_application sat here).</summary>
    ComputerUse,
    /// <summary>Right after ComputerUse (get_computer_state / read_screen_elements / uia_* sat here).</summary>
    UiAutomation,
}

public interface IPlatformToolProvider
{
    /// <summary>The tools this provider contributes at <paramref name="slot"/>,
    /// in the order they should be registered. Empty when it has none.</summary>
    IEnumerable<IMcpTool> CreateTools(PlatformToolSlot slot);
}

namespace SranDesktop.Presentation.Services;

/// <summary>What a composer Ctrl+V should do with the current clipboard payload.</summary>
public enum PasteMode
{
    /// <summary>Let the TextBox paste text normally.</summary>
    Text,

    /// <summary>Attach the clipboard bitmap as an image attachment.</summary>
    Image
}

/// <summary>
/// Paste precedence for the composer. Kept WPF-free (the real handler owns the
/// System.Windows.Clipboard calls) so the decision is unit-testable.
///
/// The image ALWAYS wins when the clipboard carries one: copies from a browser
/// or Word ship an HTML/text alternative alongside the bitmap, and the old
/// "text first" check silently dropped the image on those very common payloads.
/// </summary>
public static class ClipboardPasteDecider
{
    public static PasteMode Decide(bool hasImage, bool hasText) =>
        hasImage ? PasteMode.Image : PasteMode.Text;
}

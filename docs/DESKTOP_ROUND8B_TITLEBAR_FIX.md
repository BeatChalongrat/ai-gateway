# SRAN Desktop — Round 8b: Dark Title Bar for Dialog Windows + Button Color Fix

## Problem

The `MemoryView` and `KnowledgeBaseView` dialog windows use the default Windows native title bar (`WindowStyle` is not `None`). When the app is in dark mode, the title bar stays **white/light grey** — it doesn't match the dark theme at all. The `MainWindow` avoids this by using `WindowStyle="None"` + `WindowChrome`, but doing that for every dialog is overkill.

Also, the **"+ เพิ่มความจำ"** button in MemoryView appears to have a white/light background that clashes with the dark theme. It should use `AccentButton` style or theme-aware colors.

---

## Fix 1 — Dark Title Bar via DWM API (All Dialog Windows)

Windows 10 1809+ and Windows 11 support `DwmSetWindowAttribute` with `DWMWA_USE_IMMERSIVE_DARK_MODE` (attribute 20) to tell the OS to render the native title bar in dark mode. This is the simplest and most reliable approach.

### Step A: Create a helper class

Create `Helpers/DarkTitleBarHelper.cs` in the Presentation project:

```csharp
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace SranDesktop.Presentation.Helpers;

/// <summary>
/// Enables the Windows immersive dark title bar on any WPF Window.
/// Works on Windows 10 1809+ and Windows 11.
/// </summary>
internal static class DarkTitleBarHelper
{
    [DllImport("dwmapi.dll", PreserveSig = true)]
    private static extern int DwmSetWindowAttribute(
        IntPtr hwnd, int attr, ref int attrValue, int attrSize);

    private const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20;

    /// <summary>
    /// Call from the Window's SourceInitialized or Loaded event.
    /// Pass <paramref name="useDarkMode"/> = true to force dark title bar.
    /// </summary>
    public static void SetDarkTitleBar(Window window, bool useDarkMode)
    {
        if (window is null) return;
        try
        {
            var hwnd = new WindowInteropHelper(window).Handle;
            if (hwnd == IntPtr.Zero) return;
            int value = useDarkMode ? 1 : 0;
            DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ref value, sizeof(int));
        }
        catch
        {
            // Silently ignore on unsupported OS versions.
        }
    }
}
```

### Step B: Apply to every dialog Window

In each dialog's code-behind, add a `SourceInitialized` or `Loaded` handler that calls the helper. The simplest approach: override `OnSourceInitialized` in each Window.

**MemoryView.xaml.cs** — add:
```csharp
using SranDesktop.Presentation.Helpers;

// Inside the class:
protected override void OnSourceInitialized(EventArgs e)
{
    base.OnSourceInitialized(e);
    // Match the app's current theme
    var isDark = ThemeManager.Instance.IsDarkTheme; // or however you read the current theme
    DarkTitleBarHelper.SetDarkTitleBar(this, isDark);
}
```

**KnowledgeBaseView.xaml.cs** — same pattern.

**All other dialog Windows** (`InputDialog`, `MemoryFormDialog`, `FirstRunDialog`, `ShortcutsDialog`, `ToolConsentDialog`) — same pattern. Apply to ALL of them for consistency.

### Step C: Read the current theme state

Check how `ThemeManager` or `MainViewModel.IsDarkTheme` exposes the dark mode flag. The helper just needs a `bool`. Options:
- If `ThemeManager` is a static/singleton: `ThemeManager.Instance.IsDarkTheme`
- If it's only on the ViewModel: pass it as a constructor parameter to the dialog, or read `Application.Current.Resources["BgPrimaryBrush"]` and check if it's a dark color
- Simplest: just pass `true` always if the app defaults to dark, or read from the persisted settings

### Step D: React to theme changes while dialog is open

If the user can toggle dark/light mode while a dialog is open (unlikely but possible), subscribe to theme changes and re-call `SetDarkTitleBar`. If this is too complex, skip it — it's an edge case.

---

## Fix 2 — "+ เพิ่มความจำ" Button Color

The "Add Memory" button in `MemoryView.xaml` should match the dark theme. Check if it's using a hardcoded white `Background` or a wrong style.

**Expected fix:** The button should use:
```xml
<Button Style="{DynamicResource AccentButton}" ...>
```
or if it's already using `AccentButton`, check that `AccentButton` doesn't have a light-only `Background`. All brushes must be `DynamicResource` from the theme dictionaries.

If the button is inside a `Border` with a hardcoded `Background="#FFF"` or `Background="White"`, change it to `Background="{DynamicResource BgSecondaryBrush}"` or `Background="Transparent"`.

---

## Verification Checklist

- [ ] MemoryView title bar is dark when app is in dark mode
- [ ] KnowledgeBaseView title bar is dark when app is in dark mode
- [ ] ALL other dialog windows have dark title bars in dark mode
- [ ] Title bars are light/default when app is in light mode
- [ ] "+ เพิ่มความจำ" button looks correct in both dark and light themes
- [ ] No hardcoded white/light colors remain in any dialog
- [ ] Build: 0 errors, 0 warnings
- [ ] All 126+ tests pass

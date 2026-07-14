# SRAN Desktop — Round 8: Model Popup Bug Fix + Settings/Dialog Layout Polish

## Context

.NET 8 WPF app using CommunityToolkit.Mvvm (source generators). Two `ComposerControl` instances share the same `ChatViewModel`: one on the home screen (when `ActiveConversation` is null), one inside `ChatView` (when a conversation is open). Only one is visible at a time.

---

## Bug A — Model Popup Doesn't Open on Home Screen

### Symptom
Clicking the model selector pill on the **home screen** does nothing. The popup never appears. It works correctly inside an active conversation.

### Root Cause Analysis

The `OpenModelPopupCommand` in `ChatViewModel` sets `IsModelPopupOpen = true`. The code-behind `OnViewModelPropertyChanged` only opens the popup if `IsVisible`:

```csharp
// ComposerControl.xaml.cs line 44
if (vm.IsModelPopupOpen && IsVisible)
    ModelSelectorPopup.IsOpen = true;
```

**The home screen `ComposerControl` IS visible** (it's shown when `ActiveConversation` is null). Yet the popup doesn't open. The likely cause is a **timing/layout issue**: the `IsVisible` check may return `false` during the initial property-changed callback if the control hasn't completed its layout pass, OR the `Popup.PlacementTarget` binding isn't resolving because the popup is inside a `Grid` row that hasn't been measured.

### Required Fix

**Option 1 — Dispatcher defer (preferred, minimal change):**

In `ComposerControl.xaml.cs`, change `OnViewModelPropertyChanged` to defer the popup open to the next dispatcher frame, giving WPF time to complete layout:

```csharp
private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
{
    if (e.PropertyName != nameof(ChatViewModel.IsModelPopupOpen)) return;
    if (sender is not ChatViewModel vm) return;

    if (vm.IsModelPopupOpen && IsVisible)
    {
        // Defer to next frame so WPF has finished layout/measure —
        // fixes the home screen instance where IsVisible may lag.
        Dispatcher.InvokeAsync(() =>
        {
            if (vm.IsModelPopupOpen && IsVisible)
                ModelSelectorPopup.IsOpen = true;
        }, System.Windows.Threading.DispatcherPriority.Loaded);
    }
    else if (!vm.IsModelPopupOpen)
    {
        ModelSelectorPopup.IsOpen = false;
    }
}
```

**Option 2 — If Option 1 doesn't fix it**, the issue is that `IsVisible` returns `false` for the home screen composer even though it's on-screen. Debug by adding a temporary log:

```csharp
System.Diagnostics.Debug.WriteLine(
    $"[ComposerControl] IsModelPopupOpen changed. IsVisible={IsVisible}, " +
    $"IsLoaded={IsLoaded}, ActualWidth={ActualWidth}, Name={Name}");
```

If `IsVisible` is indeed `false`, the fix is to also check `Visibility == Visibility.Visible` on the parent chain, or give each ComposerControl an `x:Name` and use a different visibility signal (like checking if the parent StackPanel's Visibility trigger resolved to Visible).

**Option 3 — Ultimate fallback**: Remove the `IsVisible` guard entirely and instead use `IsLoaded && ActualWidth > 0` as the proxy for "this instance is the one on screen":

```csharp
if (vm.IsModelPopupOpen && IsLoaded && ActualWidth > 0)
    ModelSelectorPopup.IsOpen = true;
```

### Testing
1. Launch app → home screen (no conversation) → click model pill → popup MUST open
2. Click a model → popup closes → pill shows the selected model name
3. Open a conversation → click model pill → popup MUST open
4. Switch back to home (new chat) → model pill should still work
5. Press Escape or click outside → popup closes cleanly in both locations

---

## Bug B — Settings & Dialog Layout Polish

The current dialog windows (`MemoryView`, `KnowledgeBaseView`) and the sidebar settings popup work but look "developer-y". Polish them to match the main app's design language.

### B1. Settings Popup Menu (MainWindow.xaml, UserMenuPopup)

Current: `MinWidth="230"`, all items `Height="40"`, flat rows.

**Improvements:**
- Increase `MinWidth` to `260` for breathing room
- Add section headers as small grey labels between groups:
  - Group 1: **Appearance** → Dark mode, Language
  - Group 2: **Data** → Knowledge Base, Memory
  - Group 3: **Other** → Voice Settings, Desktop Settings, Keyboard Shortcuts
  - Group 4: Log out (separated by divider)
- Section header style: `FontSize="10.5"`, `FontWeight="SemiBold"`, `Foreground="{DynamicResource TextTertiaryBrush}"`, `Margin="12,10,12,2"`, `TextTransform` uppercase via converter or hardcoded Thai/English
- Add `Separator` (1px `Border` with `BorderBrush`) between groups
- Add a "Keyboard Shortcuts" menu item (icon: `Geo_Keyboard` or similar) that opens `ShortcutsDialog`
- Voice Settings currently does nothing — add `IsEnabled="False"` and grey out with `Opacity="0.5"` so it's clear it's coming soon, instead of silently closing the menu

### B2. KnowledgeBaseView (Window) Polish

File: `Views/KnowledgeBaseView.xaml`

Current: `Width="620" Height="640"`, basic layout.

**Improvements:**
- Set `WindowStyle="None"` and add a custom title bar with the icon, title, and close button (consistent with the app's borderless design), OR keep `WindowStyle` but add `ResizeMode="CanResizeWithGrip"` so users can resize
- Add rounded corners to the Window if going borderless (use `WindowChrome` with `CornerRadius="12"`)
- The header section (icon + title + subtitle) should have more vertical padding: `Margin="24,24,24,12"`
- The "+ สร้างฐานความรู้" / "+ Create KB" button: make it full-width with a dashed border when there are zero KBs (empty state enhancement)
- KB list items: add a subtle hover effect (`Background` changes on `IsMouseOver`)
- Add alternating row shading or card-style borders to each KB item for visual separation
- Empty state: show a centered illustration or icon with "No knowledge bases yet" text

### B3. MemoryView (Window) Polish

File: `Views/MemoryView.xaml`

Current: `Width="640" Height="700"`, basic layout.

**Improvements:**
- Match the same window chrome treatment as KnowledgeBaseView (consistent)
- The "การตั้งค่าที่แนะนำ" (Recommended Settings) section: add a subtle left border accent (4px rounded, accent color) to make it stand out as a callout card
- Memory items: each item should be a card with:
  - Rounded border (`CornerRadius="10"`)
  - Subtle background (`BgSecondaryBrush`)  
  - Clear visual separation (margin between cards)
  - The action buttons (edit ✏️, pin 📌, delete 🗑️) should only appear on hover for a cleaner look — use a `Style.Trigger` on `IsMouseOver`
- Search box: add a search icon inside the text field (left side), rounded corners (`CornerRadius="8"`), and placeholder text styling
- The "+ เพิ่มความจำ" / "+ Add Memory" button: match the accent style used in KB dialog
- Add a footer with "Export" and "Clear All" as secondary action buttons

### B4. General Dialog Consistency Rules

Apply these to ALL dialog windows (`KnowledgeBaseView`, `MemoryView`, `MemoryFormDialog`, `InputDialog`, `ToolConsentDialog`, `FirstRunDialog`, `ShortcutsDialog`):

1. **Consistent padding**: Header area `Margin="24,24,24,12"`, body `Margin="24,0,24,24"`
2. **Consistent fonts**: Title `FontSize="18" FontWeight="SemiBold"`, subtitle `FontSize="12.5"`, body `FontSize="13"`
3. **Button styles**: Primary actions use `AccentButton`, secondary use `FlatRowButton` with border, destructive use red foreground
4. **Scrollbar styling**: Use the same slim custom scrollbar as the sidebar (if one exists), or add a subtle thin scrollbar style
5. **Close/Cancel**: Every dialog should be closable with Escape key (`KeyDown` handler or `InputBindings`)
6. **Theme-aware**: All colors must use `DynamicResource` brushes, no hardcoded colors
7. **i18n**: All user-visible strings must use `{loc:T key}` — add any missing keys to `Dict.cs`

---

## Verification Checklist

- [ ] Model popup opens on home screen (no conversation)
- [ ] Model popup opens inside an active conversation
- [ ] Selecting "Auto" on home screen works → pill shows "Auto" with sparkle
- [ ] Selecting a specific model on home screen works → pill shows model name
- [ ] Popup closes on Escape, click-outside, and model selection in both locations
- [ ] Settings menu has section headers and proper grouping
- [ ] Voice Settings is visually disabled/greyed out
- [ ] KnowledgeBaseView has polished card layout with hover effects
- [ ] MemoryView has polished card layout with hover-reveal action buttons
- [ ] All dialogs closable with Escape
- [ ] All dialogs look correct in both Dark and Light themes
- [ ] All new strings are in Dict.cs (both EN and TH)
- [ ] Build succeeds with 0 errors, 0 warnings
- [ ] All existing tests pass

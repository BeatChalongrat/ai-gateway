# SRAN Desktop — Round 5: Inline Rename + Settings Popup Panel

## Context
Two UX gaps remain vs. the web chat:
1. **Rename** uses a modal `InputDialog` popup — web uses inline editing (name becomes an editable TextBox in place)
2. **Settings** is a separate modal Window — web shows a popup panel above the user footer row with Dark mode toggle, Language, Knowledge base, Voice settings, Memory, and Log out

---

## Fix 1 — Inline Rename (Folder + Conversation)

### Problem
When user clicks "Rename" in the context menu, an `InputDialog` window pops up. The web app instead turns the name text into an editable input field right in the sidebar row. Enter confirms, Escape cancels, click-outside confirms.

### What to Build

**Add `IsRenaming` property** to both `ConversationItemViewModel` and `FolderItemViewModel`:
```csharp
[ObservableProperty] private bool _isRenaming;
[ObservableProperty] private string _editingName = "";
```

**Change `RenameAsync` in `ConversationListViewModel`** — instead of showing InputDialog, set the item's `IsRenaming = true` and `EditingName = item.DisplayTitle`. The actual rename API call happens when the user confirms (Enter or focus-lost).

Add a new command `CommitInlineRenameCommand(ConversationItemViewModel item)` that:
- Reads `item.EditingName`, trims it
- If empty or unchanged, just sets `IsRenaming = false` (cancel)
- Otherwise calls the API to rename, updates the title, sets `IsRenaming = false`

Same pattern for `RenameFolderAsync` → set `folder.IsRenaming = true`, `folder.EditingName = folder.Name`.

Add `CommitFolderInlineRenameCommand(FolderItemViewModel folder)` similarly.

Add `CancelInlineRenameCommand` that just sets `IsRenaming = false` without saving.

**XAML changes in `SidebarView.xaml`:**

In the `ConversationRowTemplate`, replace the static TextBlock (line 159-163) with a switching pair:

```xml
<!-- Display mode (normal) -->
<TextBlock Grid.Column="1"
           Text="{Binding DisplayTitle}"
           TextTrimming="CharacterEllipsis"
           VerticalAlignment="Center"
           Foreground="{DynamicResource TextPrimaryBrush}"
           Visibility="{Binding IsRenaming, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}" />

<!-- Edit mode (inline rename) -->
<TextBox Grid.Column="1"
         Text="{Binding EditingName, UpdateSourceTrigger=PropertyChanged}"
         FontSize="14"
         Padding="2,1"
         Margin="-3,0,0,0"
         Background="{DynamicResource BgSecondaryBrush}"
         BorderBrush="{DynamicResource AccentBlueBrush}"
         BorderThickness="1"
         Foreground="{DynamicResource TextPrimaryBrush}"
         CaretBrush="{DynamicResource TextPrimaryBrush}"
         VerticalAlignment="Center"
         Visibility="{Binding IsRenaming, Converter={StaticResource BoolToVisibility}}"
         controls:InlineRenameHelper.IsActive="{Binding IsRenaming}" />
```

**Create `InlineRenameHelper` attached property** (new file `Controls/InlineRenameHelper.cs`):
- When `IsActive` becomes `true`: focus the TextBox, select all text
- On `KeyDown`: Enter → raise a "confirm" routed event or call the ViewModel command; Escape → cancel
- On `LostFocus` → confirm (same as Enter)

The cleanest approach is to handle this in code-behind (`SidebarView.xaml.cs`):
- Add `InlineRenameTextBox_Loaded` event handler that focuses + selects all
- Add `InlineRenameTextBox_PreviewKeyDown` for Enter/Escape
- Add `InlineRenameTextBox_LostFocus` for confirm on click-outside

**Same pattern for folder name** in the folder header section (lines 422-427): replace the folder name TextBlock with a display/edit pair.

**Also for NewFolder**: When `NewFolderCommand` creates a folder, immediately set `IsRenaming = true` on the new folder so the user can type the name inline (exactly like the web screenshot shows — "New folder" appears with the name selected for editing).

### Files to Modify
- `ViewModels/ConversationItemViewModel.cs` — Add `IsRenaming`, `EditingName`
- `ViewModels/FolderItemViewModel.cs` — Add `IsRenaming`, `EditingName`
- `ViewModels/ConversationListViewModel.cs` — Change `RenameAsync`/`RenameFolderAsync` to set inline mode; add commit/cancel commands; `NewFolderCommand` sets `IsRenaming=true`
- `Views/SidebarView.xaml` — Add edit TextBox in both conversation row and folder header
- `Views/SidebarView.xaml.cs` — Add event handlers for focus/keydown/lostfocus
- `Views/Dialogs/InputDialog.xaml[.cs]` — Can be kept for other uses but no longer used for rename

---

## Fix 2 — Settings Popup Panel (Replace Modal Window)

### Problem
The current Settings is a separate modal Window with General/Desktop Integration/Advanced sections. The web app shows settings as a **popup panel floating above the user footer** in the sidebar. The popup has these items:

| # | Label | Icon | Trailing |
|---|-------|------|----------|
| 1 | Dark mode | Moon/Sun | Toggle pill |
| 2 | Language | Globe | Locale chip (EN/TH) |
| 3 | Knowledge Base | Book | Chevron › |
| 4 | Voice Settings | Mic | Chevron › |
| 5 | Memory | Brain | Blue badge (count) + Chevron › |
| — | Separator | | |
| 6 | Log out | Logout arrow | (red text) |

### What to Build

**Replace the ContextMenu** on the user "..." button in `MainWindow.xaml` (lines 146-167) with a **WPF Popup** control. The Popup renders a custom panel with the menu items listed above.

The Popup should:
- Appear above the user footer (Placement="Top")
- Have the same themed styling as the context menus: `BgSecondaryBrush` background, 1px `BorderBrush` border, CornerRadius 12 (web uses `rounded-xl` for this menu), DropShadow
- Close on click-outside (StaysOpen="False")
- Each row is 40px tall, full-width, with icon + label + trailing element
- Use the `FlatRowButton` style pattern for hover

**Row details:**

1. **Dark mode** — Click toggles theme immediately (don't close popup). Trailing = a small pill/knob toggle indicator showing current state. Use existing `ThemeManager` and `ISettingsService`.

2. **Language** — Click toggles between EN and TH (don't close popup). Trailing = small chip showing "EN" or "TH". This requires adding locale support to `ISettingsService` if not already present. If i18n is not yet implemented in the desktop app, this item can show "EN" as static for now with a TODO.

3. **Knowledge Base** — Click navigates to a Knowledge Base view or opens a browser link. If KB management isn't built in the desktop app yet, open the web URL in the default browser: `Process.Start(new ProcessStartInfo($"{chatServerUrl}/chat/knowledge") { UseShellExecute = true })`. Close popup on click.

4. **Voice Settings** — Same approach: if not built, open web URL or show a "Coming soon" tooltip. Close popup on click.

5. **Memory** — Click navigates to Memory view or opens web URL. Trailing = blue pill badge with pending count (if available from API) + chevron. Close popup on click.

6. **Log out** — Red text, same as current. Close popup, execute `LogoutCommand`.

**Desktop-specific settings** (tray minimize, auto-start, server URLs, clean up) that were in the old SettingsView should move to a "Desktop settings" gear button in the popup (small gear icon at the top-right of the popup, or as an additional row labeled "Desktop settings ›" that opens the existing SettingsView dialog). Don't lose these features — they're desktop-specific and needed.

### Icons Needed
Add these geometries to `Resources/Icons.xaml` if not already present:
- `Geo_Moon` — crescent moon (dark mode indicator)
- `Geo_Sun` — sun (light mode indicator)
- `Geo_Globe` — globe (language)
- `Geo_Book` — book (knowledge base)
- `Geo_Mic` — microphone (voice settings)
- `Geo_Brain` — brain (memory)
- `Geo_ChevronRight` — already exists

### Popup XAML Structure

```xml
<Popup x:Name="UserMenuPopup"
       PlacementTarget="{Binding ElementName=UserMenuButton}"
       Placement="Top"
       StaysOpen="False"
       AllowsTransparency="True"
       PopupAnimation="Fade">
    <Border Background="{DynamicResource BgSecondaryBrush}"
            BorderBrush="{DynamicResource BorderBrush}"
            BorderThickness="1"
            CornerRadius="12"
            Padding="4,6"
            Margin="8,0,8,8"
            MinWidth="220">
        <Border.Effect>
            <DropShadowEffect BlurRadius="20" ShadowDepth="4" Opacity="0.4" Color="Black" />
        </Border.Effect>
        <StackPanel>
            <!-- Dark mode row -->
            <Button Style="{StaticResource FlatRowButton}" Height="40"
                    Click="ToggleTheme_Click">
                <DockPanel>
                    <controls:IconView DockPanel.Dock="Left" ... Width="16" Height="16" Margin="0,0,10,0" />
                    <TextBlock Text="Dark mode" VerticalAlignment="Center"
                               Foreground="{DynamicResource TextPrimaryBrush}" />
                    <!-- Toggle pill trailing -->
                    <Border DockPanel.Dock="Right" ... />
                </DockPanel>
            </Button>

            <!-- Language row -->
            <!-- Knowledge base row -->
            <!-- Voice settings row -->
            <!-- Memory row -->

            <!-- Separator -->
            <Separator Margin="4,4" />

            <!-- Log out (red) -->
            <Button Style="{StaticResource FlatRowButton}" Height="40"
                    Command="{Binding LogoutCommand}">
                <DockPanel>
                    <controls:IconView ... Stroke="{DynamicResource AccentRedBrush}" />
                    <TextBlock Text="Log out" Foreground="{DynamicResource AccentRedBrush}" />
                </DockPanel>
            </Button>
        </StackPanel>
    </Border>
</Popup>
```

**Toggle pill component** for Dark mode: a 36×20 rounded border with a 14×14 circle knob that slides left/right based on theme state. Use a simple animation or just two states via DataTrigger.

**Locale chip** for Language: a small 28×20 border with rounded corners showing "EN" or "TH" in 10px bold text.

### Files to Modify
- `Views/MainWindow.xaml` — Replace ContextMenu with Popup; add the settings panel rows
- `Views/MainWindow.xaml.cs` — Add click handlers for toggle theme, language, KB, Voice, Memory; change `UserMenuButton_Click` to toggle Popup instead of opening ContextMenu
- `Resources/Icons.xaml` — Add missing icon geometries
- `ViewModels/MainViewModel.cs` — Add `IsDarkTheme`, `CurrentLocale` observable properties if needed
- `Views/SettingsView.xaml` — Keep as-is for "Desktop settings" advanced access

### Avatar Initial
Currently hardcoded as "S". Change to derive from `UserEmail` — take first character of the email address, uppercase. Bind it: `Text="{Binding UserEmail, Converter={StaticResource FirstCharConverter}}"` or compute in ViewModel.

---

## Verification Checklist

After implementing, take screenshots of:

1. **Inline rename — conversation**: Right-click → Rename → name becomes editable TextBox, cursor inside, text selected
2. **Inline rename — folder**: Same, including after "New folder" (should auto-enter rename mode)
3. **Settings popup**: Click "..." → popup appears above user footer with all 6 rows
4. **Dark mode toggle**: Click toggles theme, popup stays open, pill animates
5. **Log out**: Red text, clicking logs out

Save screenshots as:
- `parity-r5-inline-rename-conversation.png`
- `parity-r5-inline-rename-folder.png`
- `parity-r5-settings-popup.png`
- `parity-r5-theme-toggle.png`

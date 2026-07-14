# SRAN Desktop — UI Polish Prompt

> Give this to Claude Fable with the ai-gateway codebase attached.
> Phase 1 is done. This prompt fixes 15 UI issues to bring visual quality up to Claude Desktop / ChatGPT level.

---

## YOUR MISSION

Polish the SRAN Desktop WPF app UI at `SranDesktop/`. The app is functional but looks like a prototype — default WPF controls, no animations, no hover states, no placeholder text. Your job is to make it look and feel like a modern premium chat app.

**DO NOT change any business logic, API calls, or data flow.** Only touch XAML, styles, themes, and visual code.

**Reference apps for visual quality:** Claude Desktop, ChatGPT Desktop, Slack, VS Code sidebar.

---

## ISSUE LIST — 15 fixes, ordered by impact

### ═══ P0: CRITICAL — Do these first ═══

### FIX 1: Rebuild Styles.xaml with global control styles

**File:** `src/SranDesktop.Presentation/Resources/Styles.xaml`
**Problem:** Only 23 lines. No global Button, TextBox, ComboBox, ScrollBar, or PasswordBox styles. Every view re-implements button templates inline → inconsistent look + XAML bloat.

**What to do:** Replace the entire file with a comprehensive style dictionary. Include these styles:

```xml
<!-- What the new Styles.xaml must contain: -->

<!-- 1. Window base (already exists, keep it) -->

<!-- 2. Global Button style (apply to ALL buttons by default) -->
<!-- - CornerRadius 6 on the border -->
<!-- - Background: Transparent, Foreground: TextPrimary, Border: BorderBrush -->
<!-- - Hover: Background transitions to BgSecondary over 150ms (use EventTrigger + ColorAnimation) -->
<!-- - Pressed: Opacity 0.8 -->
<!-- - Disabled: Opacity 0.45 -->
<!-- - Cursor: Hand -->
<!-- - Padding: 8,5 -->

<!-- 3. AccentButton style (for primary actions: Send, New Chat, Sign In, Save) -->
<!-- - Inherits Button but: Background=AccentBlue, Foreground=White, no border -->
<!-- - Hover: lighter blue (#60A5FA in light theme) via ColorAnimation 150ms -->
<!-- - Pressed: Opacity 0.85 -->

<!-- 4. DangerButton style (for Stop/Delete) -->
<!-- - Same as AccentButton but AccentRed -->

<!-- 5. IconButton style (for ⚙, 📎, ✕ buttons) -->
<!-- - 32x32, no border, transparent bg -->
<!-- - Hover: BgSecondary circle (CornerRadius 16) with 150ms transition -->

<!-- 6. Global TextBox style -->
<!-- - Background: BgSecondary, Border: BorderBrush, CornerRadius 6 -->
<!-- - Padding: 8,6 -->
<!-- - Focus: border becomes AccentBlue with 200ms ColorAnimation -->
<!-- - Placeholder support via adorner or style trigger on Text="" -->
<!-- - CaretBrush: TextPrimary -->

<!-- 7. Global PasswordBox style -->
<!-- - Same look as TextBox -->

<!-- 8. Global ComboBox style -->
<!-- - Custom template with rounded border, themed dropdown -->
<!-- - Match the TextBox look (BgSecondary bg, BorderBrush border, CornerRadius 6) -->
<!-- - Dropdown popup: BgPrimary bg, BorderBrush border, CornerRadius 8, shadow -->
<!-- - Items on hover: BgSecondary bg -->

<!-- 9. Slim ScrollBar style -->
<!-- - Width: 6px (expands to 8px on hover) -->
<!-- - Track: transparent -->
<!-- - Thumb: TextSecondary at 30% opacity, CornerRadius 3 -->
<!-- - Thumb hover: 60% opacity -->
<!-- - Auto-hide: collapse when not scrolling (use EventTrigger on ScrollViewer) -->

<!-- 10. Global ScrollViewer style -->
<!-- - Uses the slim scrollbar -->

<!-- 11. ProgressBar style -->
<!-- - Height 2, CornerRadius 1, AccentBlue fill, BgSecondary track -->
```

**IMPORTANT:** Use `<Style TargetType="Button">` (no x:Key) for the default button so it applies everywhere automatically. Named styles like `AccentButton` are opt-in via `Style="{StaticResource AccentButton}"`.

After creating global styles, go through ALL XAML files and:
- Remove every inline `<Button.Template><ControlTemplate>...</ControlTemplate></Button.Template>` that duplicates the global style
- Replace primary action buttons with `Style="{DynamicResource AccentButton}"`
- Replace Stop/Delete buttons with `Style="{DynamicResource DangerButton}"`
- Replace icon buttons with `Style="{DynamicResource IconButton}"`

### FIX 2: Add hover transitions everywhere

**Files:** All XAML views
**Problem:** Zero animations in the entire app. Every state change is an instant snap.

**What to do:** The global styles from Fix 1 handle buttons. Additionally add:

**Sidebar items** (`SidebarView.xaml` lines 84-98):
- Replace the instant Background setter with an EventTrigger animation:
```xml
<ControlTemplate.Triggers>
    <Trigger Property="IsMouseOver" Value="True">
        <Trigger.EnterActions>
            <BeginStoryboard>
                <Storyboard>
                    <ColorAnimation Storyboard.TargetName="Bd"
                                    Storyboard.TargetProperty="(Border.Background).(SolidColorBrush.Color)"
                                    To="{DynamicResource BgSecondaryColor}" Duration="0:0:0.15" />
                </Storyboard>
            </BeginStoryboard>
        </Trigger.EnterActions>
        <Trigger.ExitActions>
            <BeginStoryboard>
                <Storyboard>
                    <ColorAnimation Storyboard.TargetName="Bd"
                                    Storyboard.TargetProperty="(Border.Background).(SolidColorBrush.Color)"
                                    To="Transparent" Duration="0:0:0.15" />
                </Storyboard>
            </BeginStoryboard>
        </Trigger.ExitActions>
    </Trigger>
</ControlTemplate.Triggers>
```

**Message bubbles** (`MessageBubble.xaml`):
- Add fade-in when message appears: `<EventTrigger RoutedEvent="Loaded">` with OpacityAnimation from 0→1 over 200ms
- Add subtle Y-translate: TranslateTransform from Y=8→0 alongside the opacity fade

**Note on DynamicResource in animations:** WPF Storyboard animations cannot directly reference DynamicResource for `To`/`From` values. You need to use one of these workarounds:
- Use `<SolidColorBrush x:Key="BgSecondaryAnimBrush" Color="{DynamicResource BgSecondaryColor}" />` in theme dictionaries, then bind the Border.Background to this brush and animate the Color property using `To="{StaticResource BgSecondaryColor}"` (StaticResource works for Color values defined in the merged theme dictionary)
- OR set the initial `Background` to a new `<SolidColorBrush Color="Transparent" />` (not a resource) on the Border directly so the animation target property exists as a local SolidColorBrush, then animate its Color using the hardcoded color value

Pick the approach that compiles cleanly. The key visual requirement is a smooth ~150ms background transition on hover.

### FIX 3: Custom slim ScrollBar

**File:** `Styles.xaml` (as part of Fix 1)
**Problem:** Default chunky Windows scrollbar everywhere.

**Key template:** Vertical scrollbar thumb should be a 6px wide rounded rectangle with subtle opacity. Horizontal similar but 6px tall. The RepeatButtons (track) should be transparent.

```xml
<!-- ScrollBar thumb style reference -->
<Style x:Key="SlimScrollThumb" TargetType="Thumb">
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Thumb">
                <Border Background="{DynamicResource TextSecondaryBrush}"
                        CornerRadius="3"
                        Opacity="0.25"
                        Margin="1" />
                <!-- Hover: opacity 0.5 -->
            </ControlTemplate>
        </Setter.Value>
    </Setter>
</Style>
```

### FIX 4: Custom ComboBox template

**File:** `Styles.xaml` (as part of Fix 1)  
**Problem:** `SettingsView.xaml` lines 39-55 use default WPF ComboBox — OS chrome, wrong colors in dark mode.

The custom ComboBox needs:
- Closed state: looks like a TextBox with a small chevron (▾) on the right
- Dropdown: popup with BgPrimary background, slight shadow, rounded corners
- Items: hover highlight with BgSecondary

**After creating the global ComboBox style**, clean up `SettingsView.xaml`:
- Remove any inline ComboBox styling
- The Cancel button on line 71 needs `Style="{DynamicResource AccentButton}"` or at minimum the global button style
- The Save button can use `Style="{DynamicResource AccentButton}"`
- Remove inline `BorderThickness="0"` and `Foreground="White"` from Save — AccentButton handles it

### FIX 5: Animated typing indicator

**File:** `src/SranDesktop.Presentation/Controls/MessageBubble.xaml` lines 91-95
**Problem:** Static `"● ● ●"` text. Should be animated pulsing dots.

**Replace with:**
```xml
<!-- Instead of static TextBlock "● ● ●", use three dots with staggered opacity animation -->
<StackPanel Orientation="Horizontal" Margin="2,2"
            Visibility="{Binding ShowTypingIndicator, Converter={StaticResource BoolToVisibility}}">
    <Ellipse x:Name="Dot1" Width="6" Height="6" Margin="0,0,4,0"
             Fill="{DynamicResource TextSecondaryBrush}">
        <Ellipse.Triggers>
            <EventTrigger RoutedEvent="Loaded">
                <BeginStoryboard>
                    <Storyboard RepeatBehavior="Forever">
                        <DoubleAnimation Storyboard.TargetName="Dot1"
                                         Storyboard.TargetProperty="Opacity"
                                         From="0.3" To="1" Duration="0:0:0.6"
                                         AutoReverse="True" />
                    </Storyboard>
                </BeginStoryboard>
            </EventTrigger>
        </Ellipse.Triggers>
    </Ellipse>
    <Ellipse x:Name="Dot2" Width="6" Height="6" Margin="0,0,4,0"
             Fill="{DynamicResource TextSecondaryBrush}">
        <Ellipse.Triggers>
            <EventTrigger RoutedEvent="Loaded">
                <BeginStoryboard>
                    <Storyboard RepeatBehavior="Forever" BeginTime="0:0:0.2">
                        <DoubleAnimation Storyboard.TargetName="Dot2"
                                         Storyboard.TargetProperty="Opacity"
                                         From="0.3" To="1" Duration="0:0:0.6"
                                         AutoReverse="True" />
                    </Storyboard>
                </BeginStoryboard>
            </EventTrigger>
        </Ellipse.Triggers>
    </Ellipse>
    <Ellipse x:Name="Dot3" Width="6" Height="6"
             Fill="{DynamicResource TextSecondaryBrush}">
        <Ellipse.Triggers>
            <EventTrigger RoutedEvent="Loaded">
                <BeginStoryboard>
                    <Storyboard RepeatBehavior="Forever" BeginTime="0:0:0.4">
                        <DoubleAnimation Storyboard.TargetName="Dot3"
                                         Storyboard.TargetProperty="Opacity"
                                         From="0.3" To="1" Duration="0:0:0.6"
                                         AutoReverse="True" />
                    </Storyboard>
                </BeginStoryboard>
            </EventTrigger>
        </Ellipse.Triggers>
    </Ellipse>
</StackPanel>
```

---

### ═══ P1: MEDIUM — Do after P0 ═══

### FIX 6: Placeholder / watermark text

**Files:** `SidebarView.xaml` line 46-53, `ChatView.xaml` line 173-184

**Approach:** Create a `WatermarkAdorner` or use a simple overlay TextBlock technique:

```xml
<!-- Wrap each TextBox in a Grid with an overlay TextBlock -->
<Grid>
    <TextBox x:Name="SearchBox" ... />
    <TextBlock Text="Search conversations..."
               IsHitTestVisible="False"
               Foreground="{DynamicResource TextSecondaryBrush}"
               Opacity="0.6"
               Padding="10,7"
               FontSize="13">
        <TextBlock.Style>
            <Style TargetType="TextBlock">
                <Setter Property="Visibility" Value="Collapsed" />
                <Style.Triggers>
                    <DataTrigger Binding="{Binding Text, ElementName=SearchBox}" Value="">
                        <Setter Property="Visibility" Value="Visible" />
                    </DataTrigger>
                </Style.Triggers>
            </Style>
        </TextBlock.Style>
    </TextBlock>
</Grid>
```

**Placeholder texts:**
- Search box: `"Search conversations..."`
- Chat input: `"Type a message... (Shift+Enter for new line)"`
- Login email: `"you@company.com"`

### FIX 7: AI avatar on assistant messages

**File:** `MessageBubble.xaml`
**Problem:** Assistant messages have no visual identity.

**Add an avatar circle before the assistant bubble:**
```xml
<!-- Before the assistant StackPanel, add: -->
<Grid HorizontalAlignment="Left" MaxWidth="720"
      Visibility="{Binding IsUser, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}">
    <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto" />
        <ColumnDefinition Width="*" />
    </Grid.ColumnDefinitions>

    <!-- Avatar -->
    <Border Grid.Column="0" Width="28" Height="28"
            CornerRadius="14"
            Background="{DynamicResource AccentBlueBrush}"
            VerticalAlignment="Top"
            Margin="0,0,10,0">
        <TextBlock Text="S" FontSize="13" FontWeight="SemiBold"
                   Foreground="White"
                   HorizontalAlignment="Center" VerticalAlignment="Center" />
    </Border>

    <!-- Existing assistant StackPanel moves to Grid.Column="1" -->
    <StackPanel Grid.Column="1">
        <!-- ... existing content ... -->
    </StackPanel>
</Grid>
```

The "S" stands for SRAN. Color matches accent blue. For user messages, no avatar needed (the right-aligned blue bubble is enough identity).

### FIX 8: Send/Stop button hover + press states

**File:** `ChatView.xaml` lines 197-225
**Problem:** No visual feedback on hover or press.

**After Fix 1**, the `AccentButton` and `DangerButton` styles from `Styles.xaml` handle this automatically. Just apply:
- Send state: `Style="{DynamicResource AccentButton}"`
- Stop state: `Style="{DynamicResource DangerButton}"`

But the current XAML uses a DataTrigger to swap between Send/Stop. You need to keep that but reference the global styles instead of inline templates.

### FIX 9: Differentiate sidebar hover vs selected

**File:** `SidebarView.xaml` lines 89-97
**Problem:** Both `IsMouseOver` and `IsSelected` use `BgSecondaryBrush`.

**Fix:** Add a new theme color to both `Light.xaml` and `Dark.xaml`:

```xml
<!-- Light.xaml: add -->
<Color x:Key="BgSelectedColor">#E2E8F0</Color>
<SolidColorBrush x:Key="BgSelectedBrush" Color="{StaticResource BgSelectedColor}" />

<!-- Dark.xaml: add -->
<Color x:Key="BgSelectedColor">#334155</Color>
<SolidColorBrush x:Key="BgSelectedBrush" Color="{StaticResource BgSelectedColor}" />
```

Then in the sidebar ListBoxItem template:
- `IsMouseOver` → `BgSecondaryBrush` (subtle)
- `IsSelected` → `BgSelectedBrush` (stronger) + left accent bar:

```xml
<Trigger Property="IsSelected" Value="True">
    <Setter TargetName="Bd" Property="Background" Value="{DynamicResource BgSelectedBrush}" />
    <Setter TargetName="AccentBar" Property="Visibility" Value="Visible" />
</Trigger>
```

Add a 3px-wide accent bar on the left of the ListBoxItem border:
```xml
<Border x:Name="Bd" CornerRadius="6" Background="Transparent" Padding="8,7">
    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto" />
            <ColumnDefinition Width="*" />
        </Grid.ColumnDefinitions>
        <Border x:Name="AccentBar" Width="3" CornerRadius="2"
                Background="{DynamicResource AccentBlueBrush}"
                Visibility="Collapsed" Margin="0,2,6,2" />
        <ContentPresenter Grid.Column="1" />
    </Grid>
</Border>
```

### FIX 10: Collapsible sidebar

**File:** `MainWindow.xaml` lines 24-27
**Problem:** Fixed 264px, no collapse.

**Add a collapse toggle button and animate the width:**
```xml
<Grid.ColumnDefinitions>
    <ColumnDefinition x:Name="SidebarColumn" Width="264" MinWidth="0" />
    <ColumnDefinition Width="*" />
</Grid.ColumnDefinitions>
```

Add a toggle button at the top of the sidebar (hamburger ☰ icon) that animates `SidebarColumn.Width` between 264 and 0 using a `DoubleAnimation` on `ColumnDefinition.Width` (or simpler: toggle `Visibility` of the sidebar Grid with a `Width` animation on the parent).

**Simpler approach:** Add a button in the content area header that toggles sidebar visibility:
```xml
<!-- In the content area, add a header bar -->
<Grid Grid.Column="1">
    <Grid.RowDefinitions>
        <RowDefinition Height="Auto" />  <!-- header bar -->
        <RowDefinition Height="*" />     <!-- chat -->
    </Grid.RowDefinitions>

    <Border Grid.Row="0" Padding="8,6"
            BorderBrush="{DynamicResource BorderBrush}"
            BorderThickness="0,0,0,1">
        <Grid>
            <Button Content="☰" Style="{DynamicResource IconButton}"
                    HorizontalAlignment="Left"
                    Click="ToggleSidebar_Click" />
            <TextBlock Text="{Binding ActiveConversation.Title}"
                       HorizontalAlignment="Center"
                       VerticalAlignment="Center"
                       FontWeight="SemiBold"
                       TextTrimming="CharacterEllipsis"
                       Foreground="{DynamicResource TextPrimaryBrush}" />
        </Grid>
    </Border>

    <ContentPresenter Grid.Row="1" ... />
</Grid>
```

In code-behind, toggle the sidebar column width between 264 and 0.

### FIX 11: TextBox focus ring

**Handled by Fix 1** — the global TextBox style includes a focus trigger that changes `BorderBrush` to `AccentBlueBrush` with a 200ms animation.

---

### ═══ P2: LOW — Nice-to-have ═══

### FIX 12: Replace hardcoded Foreground="White"

**Files:**
- `ChatView.xaml` line 196 → use `Foreground="{DynamicResource TextOnAccentBrush}"`
- `SidebarView.xaml` line 42 → same
- `LoginView.xaml` line 80 → same
- `MessageBubble.xaml` line 34 → same

**Add to both theme files:**
```xml
<SolidColorBrush x:Key="TextOnAccentBrush" Color="White" />
```

### FIX 13: Code block copy button revert

**File:** `src/SranDesktop.Presentation/Controls/MarkdownRenderer.cs`
**Problem:** Copy button shows "Copied" permanently after clicking.

**Fix:** After setting content to "Copied ✓", start a DispatcherTimer that reverts to "Copy" after 2 seconds:
```csharp
copyBtn.Content = "Copied ✓";
var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
timer.Tick += (_, _) => { copyBtn.Content = "Copy"; timer.Stop(); };
timer.Start();
```

### FIX 14: Login loading spinner

**File:** `LoginView.xaml` lines 106-110
**Problem:** Static "Signing in…" text.

**Replace with a small spinning indicator:**
```xml
<StackPanel Orientation="Horizontal" HorizontalAlignment="Center"
            Margin="0,10,0,0"
            Visibility="{Binding IsBusy, Converter={StaticResource BoolToVisibility}}">
    <Border Width="16" Height="16" CornerRadius="8"
            BorderBrush="{DynamicResource AccentBlueBrush}"
            BorderThickness="2"
            RenderTransformOrigin="0.5,0.5">
        <Border.RenderTransform>
            <RotateTransform />
        </Border.RenderTransform>
        <Border.Triggers>
            <EventTrigger RoutedEvent="Loaded">
                <BeginStoryboard>
                    <Storyboard RepeatBehavior="Forever">
                        <DoubleAnimation Storyboard.TargetProperty="(UIElement.RenderTransform).(RotateTransform.Angle)"
                                         From="0" To="360" Duration="0:0:1" />
                    </Storyboard>
                </BeginStoryboard>
            </EventTrigger>
        </Border.Triggers>
        <!-- Make it look like a spinner by clipping one quadrant -->
        <Border.OpacityMask>
            <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
                <GradientStop Color="Transparent" Offset="0" />
                <GradientStop Color="Black" Offset="0.3" />
            </LinearGradientBrush>
        </Border.OpacityMask>
    </Border>
    <TextBlock Text="Signing in…" Margin="8,0,0,0"
               VerticalAlignment="Center"
               Foreground="{DynamicResource TextSecondaryBrush}" />
</StackPanel>
```

### FIX 15: Settings Cancel button consistency

**Handled by Fix 1** — the global Button style gives Cancel a proper look automatically. If you want it to stand out from Save, keep it as the default style (outline look) while Save uses `AccentButton`.

---

## ADDITIONAL THEME COLORS TO ADD

Add these to BOTH `Light.xaml` and `Dark.xaml`:

```xml
<!-- Light.xaml additions -->
<Color x:Key="BgSelectedColor">#E2E8F0</Color>
<Color x:Key="AccentBlueHoverColor">#60A5FA</Color>
<Color x:Key="AccentRedHoverColor">#F87171</Color>
<SolidColorBrush x:Key="BgSelectedBrush" Color="{StaticResource BgSelectedColor}" />
<SolidColorBrush x:Key="AccentBlueHoverBrush" Color="{StaticResource AccentBlueHoverColor}" />
<SolidColorBrush x:Key="AccentRedHoverBrush" Color="{StaticResource AccentRedHoverColor}" />
<SolidColorBrush x:Key="TextOnAccentBrush" Color="White" />

<!-- Dark.xaml additions -->
<Color x:Key="BgSelectedColor">#334155</Color>
<Color x:Key="AccentBlueHoverColor">#93C5FD</Color>
<Color x:Key="AccentRedHoverColor">#FCA5A5</Color>
<SolidColorBrush x:Key="BgSelectedBrush" Color="{StaticResource BgSelectedColor}" />
<SolidColorBrush x:Key="AccentBlueHoverBrush" Color="{StaticResource AccentBlueHoverColor}" />
<SolidColorBrush x:Key="AccentRedHoverBrush" Color="{StaticResource AccentRedHoverColor}" />
<SolidColorBrush x:Key="TextOnAccentBrush" Color="White" />
```

---

## FONT STACK

The app uses `Segoe UI` which is fine. But enhance the hierarchy:

| Purpose | Font | Size | Weight |
|---|---|---|---|
| App title (login, splash) | Segoe UI | 24 | SemiBold |
| Section headers | Segoe UI | 18 | SemiBold |
| Sidebar group labels | Segoe UI | 11 | SemiBold |
| Body text | Segoe UI | 14 | Normal |
| Meta text (timestamps, token counts) | Segoe UI | 11 | Normal |
| Code blocks | Cascadia Mono, Consolas | 13 | Normal |

---

## VERIFICATION CHECKLIST

After all fixes, verify:

1. [ ] **Theme switch works:** Toggle Light↔Dark in Settings. All controls re-style correctly. No hardcoded colors visible.
2. [ ] **Hover transitions smooth:** Hover over sidebar items, buttons, send button. All transitions ~150ms, no snapping.
3. [ ] **Typing indicator animated:** Send a message. See pulsing dots before the first token arrives.
4. [ ] **ScrollBars slim:** Scroll the chat area. Scrollbar is thin, subtle, not the chunky default.
5. [ ] **ComboBoxes themed:** Open Settings. Theme and Model dropdowns look native to the app, not OS-default.
6. [ ] **Placeholders visible:** Empty search box shows "Search conversations...", empty chat input shows "Type a message..."
7. [ ] **Focus rings work:** Tab through fields. Active field has blue border.
8. [ ] **Avatar shows:** Assistant messages have a blue "S" circle avatar on the left.
9. [ ] **Sidebar selected state:** Selected conversation has stronger highlight + blue accent bar. Hover is lighter.
10. [ ] **Message fade-in:** New messages fade in smoothly, not just appear.
11. [ ] **Login spinner:** Click Sign in with wrong creds. See spinning indicator, not just text.
12. [ ] **Copy revert:** Click copy on a code block. "Copied ✓" reverts to "Copy" after 2s.
13. [ ] **No compile errors.**
14. [ ] **All 44 existing tests still pass.**

---

## BUILD ORDER

1. Fix 1 → Styles.xaml (biggest impact — fixes buttons, textboxes, comboboxes globally)
2. Fix 3 → ScrollBar (part of Styles.xaml)
3. Fix 4 → ComboBox (part of Styles.xaml)
4. Fix 2 → Hover transitions (sidebar, buttons)
5. Fix 5 → Typing indicator animation
6. Fix 9 → Sidebar selected state (add theme colors)
7. Fix 6 → Placeholder text
8. Fix 7 → AI avatar
9. Fix 8 → Send/Stop button cleanup
10. Fix 10 → Collapsible sidebar
11. Fix 11 → Focus ring (already handled by Fix 1)
12. Fix 12 → Replace hardcoded "White"
13. Fix 13 → Copy button revert timer
14. Fix 14 → Login spinner
15. Fix 15 → Settings Cancel button (already handled by Fix 1)

Then run the verification checklist.

---

*End of UI Polish Prompt*

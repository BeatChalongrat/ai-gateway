# SRAN Desktop — Round 4: Themed Context Menus + App Icon

## Context
All context menus (Export, Conversation "...", Folder "...", User profile) currently render with **default WPF chrome** (white/light background, system font, standard highlight). They must be re-templated to match the web-chat dark/light palette. The app icon also needs replacing.

---

## Fix 1 — Custom ContextMenu / MenuItem / Separator Styles

### Problem
WPF's built-in ContextMenu ignores DynamicResource theme brushes. All four menus (Export, Conversation, Folder, User profile) look out of place — white background on dark theme, wrong font, wrong hover color.

### Web Reference (exact values from Tailwind config + globals.css)

| Token | Dark | Light |
|---|---|---|
| Menu background (`bg-bg-surface`) | `#2F2F2F` | `#F0F0F0` |
| Hover background (`bg-bg-elevated`) | `#3A3A3A` | `#E5E5E5` |
| Border (`border-border`) | `rgba(255,255,255,0.12)` ≈ `#3A3A3A` | `rgba(0,0,0,0.10)` ≈ `#E5E5E5` |
| Text normal (`text-text-secondary`) | `#A0A0A0` | `#5B5B5B` |
| Text hover (`text-text-primary`) | `#ECECEC` | `#0D0D0D` |
| Danger text (`text-status-danger`) | `#EF4444` | `#EF4444` |
| Danger hover bg | `rgba(239,68,68,0.15)` | `rgba(239,68,68,0.15)` |
| Shadow | `shadow-xl` (large drop shadow) | same |
| Border radius | `rounded-md` (6px) for compact menus, `rounded-lg` (8px) for wider | same |
| Item padding | `px-3 py-2` (12px horizontal, 8px vertical) | same |
| Icon gap | `gap-2` (8px) or `gap-2.5` (10px) | same |
| Font size | `text-xs` (12px) for menu items | same |
| Separator | `border-t border-border/60` (top border at 60% opacity) | same |
| Animation | `popover-appear`: scale(0.96)→scale(1), opacity 0→1, 150ms ease-out | same |

### What to Build

Add implicit styles for `ContextMenu`, `MenuItem`, and `Separator` in **`Resources/Styles.xaml`** so every context menu in the app automatically picks up the theme. Use existing DynamicResource brushes — they already map to the correct dark/light values:

**ContextMenu style:**
- `Background` = `BgSecondaryBrush` (maps to `#2F2F2F` dark / `#F0F0F0` light — same as web's `bg-bg-surface`)
- `BorderBrush` = `BorderBrush` (`#3A3A3A` / `#E5E5E5`)
- `BorderThickness` = `1`
- `CornerRadius` = `8` (via custom ControlTemplate with a Border)
- `Padding` = `4,6` (small vertical padding inside the menu)
- `DropShadowEffect`: BlurRadius=16, ShadowDepth=3, Opacity=0.4, Color=Black
- `HasDropShadow` = True, `AllowsTransparency` = True on the Popup
- `Foreground` = `TextSecondaryBrush`

**MenuItem style (full ControlTemplate needed):**
- Layout: `<StackPanel Orientation="Horizontal" gap="8">` with Icon area (16×16) + Header TextBlock
- Normal state: `Foreground` = `TextSecondaryBrush`, `Background` = Transparent
- Hover state (`IsHighlighted`): `Background` = `BgSelectedBrush` (`#3A3A3A`/`#E5E5E5`), `Foreground` = `TextPrimaryBrush`
- Padding: `12,8` (px-3 py-2 equivalent)
- FontSize: 12
- CornerRadius on the hover highlight Border: 4
- Margin inside menu: `4,1` (so hover pill has inset from menu edge, like web)
- For sub-menu items (Move to folder ▸): include `PART_Popup` for the submenu with same styling
- Support `MenuItem.Icon` content — the existing `controls:IconView` icons must keep working
- The icon's Stroke should follow the item's Foreground (inherit or bind)

**Danger MenuItem:** Items whose `Foreground` is explicitly set to `AccentRedBrush` (like Delete, Log out) should:
- Keep red text on hover (don't override to TextPrimaryBrush)
- Hover background: a semi-transparent red (`#26EF4444` = ~15% red) instead of `BgSelectedBrush`
- This requires a way to detect "danger" items. Options:
  - Check if `Foreground` is bound to `AccentRedBrush` via a trigger
  - OR add an attached property `MenuHelper.IsDanger="True"` and trigger on it
  - Simplest: use Tag="Danger" on danger MenuItems, trigger on `Tag`

**Separator style:**
- Height: 1
- Background: `BorderBrush` at 60% opacity (use `Opacity="0.6"` on the Border)
- Margin: `4,4` (horizontal inset + vertical spacing)

### Files to Modify

1. **`Resources/Styles.xaml`** — Add the three implicit styles at the bottom (after existing CheckBox/ProgressBar styles)

2. **`Views/SidebarView.xaml`** — On the Delete MenuItems in conversation and folder context menus, add `Tag="Danger"` so the style can detect them:
   - Conversation Delete item
   - Folder Delete item

3. **`Views/MainWindow.xaml`** — On the Log out MenuItem, add `Tag="Danger"`

4. **Both theme files** — Add a new brush for danger hover background:
   - **`Resources/Themes/Dark.xaml`**: `<SolidColorBrush x:Key="DangerHoverBgBrush" Color="#26EF4444" />`
   - **`Resources/Themes/Light.xaml`**: `<SolidColorBrush x:Key="DangerHoverBgBrush" Color="#26EF4444" />`

### ControlTemplate Skeleton for MenuItem

```xml
<ControlTemplate TargetType="MenuItem">
    <Grid>
        <Border x:Name="Bg"
                Background="Transparent"
                CornerRadius="4"
                Margin="4,1"
                Padding="12,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="Auto" />  <!-- icon -->
                    <ColumnDefinition Width="*" />      <!-- header -->
                    <ColumnDefinition Width="Auto" />  <!-- submenu arrow -->
                </Grid.ColumnDefinitions>
                <ContentPresenter x:Name="Icon"
                                  ContentSource="Icon"
                                  Width="16" Height="16"
                                  Margin="0,0,8,0"
                                  VerticalAlignment="Center"
                                  SnapsToDevicePixels="True" />
                <ContentPresenter x:Name="Header"
                                  Grid.Column="1"
                                  ContentSource="Header"
                                  VerticalAlignment="Center"
                                  RecognizesAccessKey="True" />
                <!-- submenu arrow for items with children (Move to folder ►) -->
                <Path x:Name="SubMenuArrow"
                      Grid.Column="2"
                      Data="M 0 0 L 4 4 L 0 8"
                      Stroke="{DynamicResource TextTertiaryBrush}"
                      StrokeThickness="1.2"
                      VerticalAlignment="Center"
                      Margin="8,0,0,0"
                      Visibility="Collapsed" />
            </Grid>
        </Border>
        <!-- Submenu popup (for items with Role=SubmenuHeader) -->
        <Popup x:Name="PART_Popup"
               Placement="Right"
               IsOpen="{Binding IsSubmenuOpen, RelativeSource={RelativeSource TemplatedParent}}"
               AllowsTransparency="True"
               PopupAnimation="Fade">
            <Border Background="{DynamicResource BgSecondaryBrush}"
                    BorderBrush="{DynamicResource BorderBrush}"
                    BorderThickness="1"
                    CornerRadius="8"
                    Padding="0,6">
                <Border.Effect>
                    <DropShadowEffect BlurRadius="16" ShadowDepth="3"
                                      Opacity="0.4" Color="Black" />
                </Border.Effect>
                <ScrollViewer VerticalScrollBarVisibility="Auto">
                    <ItemsPresenter KeyboardNavigation.DirectionalNavigation="Cycle" />
                </ScrollViewer>
            </Border>
        </Popup>
    </Grid>
    <ControlTemplate.Triggers>
        <!-- Show submenu arrow when item has children -->
        <Trigger Property="Role" Value="SubmenuHeader">
            <Setter TargetName="SubMenuArrow" Property="Visibility" Value="Visible" />
        </Trigger>
        <!-- Normal hover -->
        <MultiTrigger>
            <MultiTrigger.Conditions>
                <Condition Property="IsHighlighted" Value="True" />
                <Condition Property="Tag" Value="{x:Null}" />
            </MultiTrigger.Conditions>
            <Setter TargetName="Bg" Property="Background"
                    Value="{DynamicResource BgSelectedBrush}" />
            <Setter Property="Foreground"
                    Value="{DynamicResource TextPrimaryBrush}" />
        </MultiTrigger>
        <!-- Danger hover (Tag="Danger") -->
        <MultiTrigger>
            <MultiTrigger.Conditions>
                <Condition Property="IsHighlighted" Value="True" />
                <Condition Property="Tag" Value="Danger" />
            </MultiTrigger.Conditions>
            <Setter TargetName="Bg" Property="Background"
                    Value="{DynamicResource DangerHoverBgBrush}" />
        </MultiTrigger>
        <!-- Hide icon column when no icon is set -->
        <Trigger Property="Icon" Value="{x:Null}">
            <Setter TargetName="Icon" Property="Visibility" Value="Collapsed" />
            <Setter TargetName="Icon" Property="Margin" Value="0" />
        </Trigger>
    </ControlTemplate.Triggers>
</ControlTemplate>
```

### Important Notes
- The template must handle **three MenuItem roles**: `SubmenuItem` (normal click), `SubmenuHeader` (has children, shows arrow + PART_Popup), and `TopLevelItem`/`TopLevelHeader` (not used in context menus but define anyway for safety).
- The existing `controls:IconView` elements set as `MenuItem.Icon` must render correctly inside the `ContentPresenter x:Name="Icon"`.
- Test with both dark and light themes.
- The `Separator` inside ContextMenu uses `<Separator />` which needs its own implicit style in the ContextMenu's scope or globally.

---

## Fix 2 — Replace App Icon

### Problem
The current app icon is a blue "S" square. User wants to replace it with their custom icon at `C:\Users\Beat Beat\Downloads\desk-iconno bg.png`.

### Steps

1. **Convert PNG to ICO**: The PNG needs to be converted to a multi-resolution `.ico` file containing 16×16, 32×32, 48×48, and 256×256 sizes. Use an online converter or ImageMagick if available. The PNG has a transparent background — preserve the alpha channel.

2. **Replace the ICO file** at:
   - `SranDesktop/src/SranDesktop.App/Assets/app.ico`

3. **No code changes needed** — the `.csproj` and `MainWindow.xaml` already reference `Assets/app.ico`, so replacing the file is sufficient.

4. **Also update the empty-state logo** if desired:
   - `SranDesktop/src/SranDesktop.Presentation/Assets/sran-logo-white.png` (dark theme)
   - `SranDesktop/src/SranDesktop.Presentation/Assets/sran-logo.png` (light theme)
   - These are referenced in `Dark.xaml` line 53 and `Light.xaml` line 54 as `LogoSource`.

### Source file
The PNG is at: `C:\Users\Beat Beat\Downloads\desk-iconno bg.png`

---

## Verification Checklist

After implementing, take screenshots of:

1. **Dark theme** — Export "..." menu open → should have `#2F2F2F` background, rounded corners, shadow, themed text
2. **Dark theme** — Conversation "..." context menu → same styling, Delete item red with red hover tint
3. **Dark theme** — Folder "..." menu → same
4. **Dark theme** — User profile "..." menu → Settings normal, Log out red
5. **Light theme** — All four menus with `#F0F0F0` background, `#E5E5E5` hover
6. **App icon** — Visible in title bar, taskbar, and Alt+Tab
7. **Submenu** — "Move to folder ►" opens a themed submenu popup

Save screenshots as:
- `parity-r4-export-menu-dark.png`
- `parity-r4-conversation-menu-dark.png`
- `parity-r4-folder-menu-dark.png`
- `parity-r4-user-menu-dark.png`
- `parity-r4-menus-light.png`
- `parity-r4-app-icon.png`
- `parity-r4-submenu.png`

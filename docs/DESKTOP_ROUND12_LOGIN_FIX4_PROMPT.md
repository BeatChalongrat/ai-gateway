# Round 12 Fix 4 — Login Card Depth & Elevation

## Problem

The login card blends into the background — no visual depth. The colors are too close:
- Dark mode: background `#212121`, card `#2F2F2F` (only 14 hex steps apart)
- Light mode: background `#FFFFFF`, card `#F0F0F0` (slightly better but still flat)

The card needs more dimension to feel elevated and distinct from the background.

## Solution — 3 combined techniques

### 1. Drop shadow on the card

Add a `DropShadowEffect` to the login card Border. This is the most impactful change for perceived depth.

Find the login card Border (the one with `CornerRadius="16"`, `Padding="40,36"`, `MinWidth="400"`):

```xml
<Border VerticalAlignment="Center" HorizontalAlignment="Center"
        Background="{DynamicResource BgSecondaryBrush}"
        BorderBrush="{DynamicResource BorderBrush}"
        BorderThickness="1"
        CornerRadius="16"
        Padding="40,36"
        MinWidth="400">
```

Add a `DropShadowEffect`:

```xml
<Border VerticalAlignment="Center" HorizontalAlignment="Center"
        Background="{DynamicResource BgSecondaryBrush}"
        BorderBrush="{DynamicResource BorderBrush}"
        BorderThickness="1"
        CornerRadius="16"
        Padding="40,36"
        MinWidth="400">
    <Border.Effect>
        <DropShadowEffect Color="Black"
                          BlurRadius="40"
                          ShadowDepth="0"
                          Opacity="0.5"
                          Direction="270" />
    </Border.Effect>
    <StackPanel Width="320">
        <!-- ... existing form content ... -->
    </StackPanel>
</Border>
```

Key shadow settings:
- `ShadowDepth="0"` — shadow spreads equally in all directions (no directional offset)
- `BlurRadius="40"` — large soft spread for a gentle glow
- `Opacity="0.5"` — noticeable but not harsh
- `Color="Black"` — works in both dark and light themes

### 2. Brighter card background (new theme color)

The existing `BgSecondaryBrush` is too close to `BgPrimaryBrush`. Add a new elevated card color specifically for the login card.

**In `Resources/Themes/Dark.xaml`**, add a new color + brush:

```xml
<Color x:Key="BgElevatedColor">#383838</Color>
<SolidColorBrush x:Key="BgElevatedBrush" Color="{StaticResource BgElevatedColor}" />
```

**In `Resources/Themes/Light.xaml`**, add:

```xml
<Color x:Key="BgElevatedColor">#FFFFFF</Color>
<SolidColorBrush x:Key="BgElevatedBrush" Color="{StaticResource BgElevatedColor}" />
```

Then update the login card Border to use this new brush:

```xml
Background="{DynamicResource BgElevatedBrush}"
```

This gives dark mode: background `#212121` → card `#383838` (much more contrast, +23 hex steps instead of +14).

### 3. Stronger border

The current `BorderBrush` is very subtle. Make the card border slightly more visible:

```xml
BorderBrush="{DynamicResource BorderStrongBrush}"
```

If `BorderStrongBrush` doesn't exist, use an explicit semi-transparent white:

```xml
<Border ... BorderThickness="1">
    <Border.BorderBrush>
        <SolidColorBrush Color="White" Opacity="0.08" />
    </Border.BorderBrush>
```

For light mode this should be dark:
Actually, just keep `BorderBrush` but the shadow + brighter bg will already do the heavy lifting. If there IS a `BorderStrongBrush` or similar in the theme files, use that. Otherwise keep the existing `BorderBrush` — the shadow is what matters most.

## Summary of Changes

| File | Change |
|------|--------|
| `Dark.xaml` | Add `BgElevatedColor` = `#383838` + `BgElevatedBrush` |
| `Light.xaml` | Add `BgElevatedColor` = `#FFFFFF` + `BgElevatedBrush` |
| `LoginView.xaml` | Card Border: `Background` → `BgElevatedBrush`, add `DropShadowEffect` |

## Final Card Border XAML

```xml
<Border VerticalAlignment="Center" HorizontalAlignment="Center"
        Background="{DynamicResource BgElevatedBrush}"
        BorderBrush="{DynamicResource BorderBrush}"
        BorderThickness="1"
        CornerRadius="16"
        Padding="40,36"
        MinWidth="400">
    <Border.Effect>
        <DropShadowEffect Color="Black"
                          BlurRadius="40"
                          ShadowDepth="0"
                          Opacity="0.5"
                          Direction="270" />
    </Border.Effect>
    <StackPanel Width="320">
        <!-- ... form content unchanged ... -->
    </StackPanel>
</Border>
```

## Verification

1. `dotnet build` — no errors
2. Dark mode: card clearly stands out from background with visible shadow glow + lighter card surface
3. Light mode: card has subtle shadow, white card on off-white bg looks clean
4. Shadow doesn't look harsh or overdone — soft diffused glow
5. Grid pattern still visible behind the card but not through the card
6. No performance issues (single DropShadowEffect is GPU-accelerated in WPF)

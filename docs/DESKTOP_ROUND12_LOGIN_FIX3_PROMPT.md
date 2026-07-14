# Round 12 Fix 3 — Subtle Grid Background on Login Screen

## Context

Follow-up to Fix 2. The login card now has a border and larger logo, but the background behind the card is a flat solid color. The user chose **Option A: Subtle grid pattern** — thin grid lines behind the card to give a clean, techy feel.

## Implementation

In WPF, the grid pattern is best done with a `DrawingBrush` set as the `Background` of the login `UserControl`. The grid must be:

- **Very subtle** — near-invisible lines, just enough to add texture
- **Theme-aware** — lighter lines in dark mode, darker lines in light mode
- **Behind the card** — the card sits on top with its own elevated background

## File to Modify

### `src/SranDesktop.Presentation/Views/LoginView.xaml`

**Step 1: Replace the simple `Background` attribute with a layered background**

Currently the UserControl has:
```xml
Background="{DynamicResource BgPrimaryBrush}"
```

Remove this attribute from the `<UserControl>` tag. Instead, add the grid pattern as a background layer inside the root `<Grid>`. Replace the current root Grid with:

```xml
<Grid>
    <!-- Layer 0: solid background -->
    <Grid.Background>
        <SolidColorBrush Color="{DynamicResource BgPrimaryColor}" />
    </Grid.Background>

    <!-- Layer 1: subtle grid pattern overlay -->
    <Border>
        <Border.Background>
            <DrawingBrush TileMode="Tile" Viewport="0,0,40,40"
                          ViewportUnits="Absolute" Opacity="0.06">
                <DrawingBrush.Drawing>
                    <GeometryDrawing>
                        <GeometryDrawing.Pen>
                            <Pen Brush="{DynamicResource TextPrimaryBrush}"
                                 Thickness="0.5" />
                        </GeometryDrawing.Pen>
                        <GeometryDrawing.Geometry>
                            <GeometryGroup>
                                <!-- Horizontal line -->
                                <LineGeometry StartPoint="0,40" EndPoint="40,40" />
                                <!-- Vertical line -->
                                <LineGeometry StartPoint="40,0" EndPoint="40,40" />
                            </GeometryGroup>
                        </GeometryDrawing.Geometry>
                    </GeometryDrawing>
                </DrawingBrush.Drawing>
            </DrawingBrush>
        </Border.Background>
    </Border>

    <!-- Layer 2: login card (existing centered Border + StackPanel) -->
    <Border VerticalAlignment="Center" HorizontalAlignment="Center"
            Background="{DynamicResource BgSecondaryBrush}"
            BorderBrush="{DynamicResource BorderBrush}"
            BorderThickness="1"
            CornerRadius="16"
            Padding="40,36"
            MinWidth="400">
        <StackPanel Width="320">
            <!-- ... all existing login form content ... -->
        </StackPanel>
    </Border>
</Grid>
```

**Important notes:**

1. `Opacity="0.06"` on the DrawingBrush — this makes the grid extremely subtle. In dark mode (white text brush), you get faint white lines on dark bg. In light mode (dark text brush), you get faint dark lines on light bg. Adjust to `0.04` if too visible or `0.08` if too faint.

2. `Viewport="0,0,40,40"` — each grid cell is 40×40px. This gives a nice density without being too tight or too loose.

3. `Pen Brush="{DynamicResource TextPrimaryBrush}"` — uses the theme text color so lines automatically adapt to dark/light mode.

4. The `<Border>` for the grid pattern must come BEFORE the login card Border in the Grid's children so it renders behind the card (WPF paints children in order).

**Alternative approach if `BgPrimaryColor` doesn't exist as a Color resource:**

If the theme only has `BgPrimaryBrush` (a Brush, not a Color), keep `Background="{DynamicResource BgPrimaryBrush}"` on the `<UserControl>` tag and just add the grid overlay Border as the first child of the root Grid:

```xml
<UserControl ... Background="{DynamicResource BgPrimaryBrush}">
    <!-- ... Resources ... -->
    <Grid>
        <!-- Grid pattern overlay -->
        <Border>
            <Border.Background>
                <DrawingBrush TileMode="Tile" Viewport="0,0,40,40"
                              ViewportUnits="Absolute" Opacity="0.06">
                    <DrawingBrush.Drawing>
                        <GeometryDrawing>
                            <GeometryDrawing.Pen>
                                <Pen Brush="{DynamicResource TextPrimaryBrush}"
                                     Thickness="0.5" />
                            </GeometryDrawing.Pen>
                            <GeometryDrawing.Geometry>
                                <GeometryGroup>
                                    <LineGeometry StartPoint="0,40" EndPoint="40,40" />
                                    <LineGeometry StartPoint="40,0" EndPoint="40,40" />
                                </GeometryGroup>
                            </GeometryDrawing.Geometry>
                        </GeometryDrawing>
                    </DrawingBrush.Drawing>
                </DrawingBrush>
            </Border.Background>
        </Border>

        <!-- Login card -->
        <Border VerticalAlignment="Center" HorizontalAlignment="Center" ...>
            <!-- existing form -->
        </Border>
    </Grid>
</UserControl>
```

**This is the safer approach** — use this one. It preserves the existing background brush and just overlays the grid on top.

## Verification

1. `dotnet build` — no errors
2. Dark mode: faint white/light grid lines visible behind the card on dark background
3. Light mode: faint dark grid lines visible behind the card on light background
4. Grid lines do NOT show through the card (card has its own opaque background)
5. Grid covers the entire window area, not just the card
6. Grid is subtle — should be barely noticeable, just adding texture
7. No performance issues (DrawingBrush tiles efficiently in WPF)

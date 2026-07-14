# Round 12 Fix 2 — Login Card Border + Logo Adjustments

## Context

Follow-up to `DESKTOP_ROUND12_LOGIN_FIX_PROMPT.md`. The simplified login is working but needs 3 visual tweaks based on user feedback (see screenshot):

1. **Add a card border/frame** around the entire login form area — currently the form floats on the dark background with no visual boundary
2. **Remove "SRAN®" text** below the logo — redundant since the logo image already contains the SRAN wordmark
3. **Enlarge the logo** — currently 64×64, make it bigger (96×96)

## File to Modify

### `src/SranDesktop.Presentation/Views/LoginView.xaml`

**Change 1: Wrap the form in a card Border**

Find the inner `<StackPanel>` that contains the logo + form fields and wrap it in a card-style `Border`. Replace this structure:

```xml
<!-- Full-window centered layout — no columns, no branding panel -->
<Grid>
    <StackPanel VerticalAlignment="Center" HorizontalAlignment="Center"
                Width="340">
        <!-- ... all login content ... -->
    </StackPanel>
</Grid>
```

With:

```xml
<!-- Full-window centered layout -->
<Grid>
    <Border VerticalAlignment="Center" HorizontalAlignment="Center"
            Background="{DynamicResource BgSecondaryBrush}"
            BorderBrush="{DynamicResource BorderBrush}"
            BorderThickness="1"
            CornerRadius="16"
            Padding="40,36"
            MinWidth="400">
        <StackPanel Width="320">
            <!-- ... all login content stays the same ... -->
        </StackPanel>
    </Border>
</Grid>
```

Key properties:
- `BgSecondaryBrush` background — slightly elevated from the window bg (works in both dark/light themes)
- `BorderBrush` with 1px thickness — subtle border
- `CornerRadius="16"` — rounded card feel
- `Padding="40,36"` — generous inner padding
- `MinWidth="400"` — ensures the card doesn't look cramped
- Inner StackPanel width reduced to 320 (the padding handles the rest)

**Change 2: Remove "SRAN®" text**

Find and DELETE:

```xml
<TextBlock Text="SRAN®" FontSize="20" FontWeight="Medium"
           ... />
```

Or whatever renders the "SRAN®" text below the logo. It may look like:

```xml
<TextBlock Text="SRAN ®" .../>
```

Remove the entire TextBlock element. The logo image already has the SRAN wordmark baked in.

**Change 3: Enlarge the logo**

Find:

```xml
<Image Source="{DynamicResource LogoSource}"
       Width="64" Height="64"
```

Change to:

```xml
<Image Source="{DynamicResource LogoSource}"
       Width="96" Height="96"
```

Also keep `Margin="0,0,0,32"` on the logo's parent StackPanel (or apply directly to the Image) so there's good spacing between the logo and the first input.

## Summary of Final Visual Structure

```
┌─────────────────────────────────────┐
│          ┌────────────────┐         │
│          │                │         │
│          │   [Cat Logo]   │         │
│          │    96 × 96     │         │
│          │                │         │
│          │   อีเมล        │         │
│          │  ┌────────────┐│         │
│          │  │ ✉ email    ││         │
│          │  └────────────┘│         │
│          │   รหัสผ่าน     │         │
│          │  ┌────────────┐│         │
│          │  │ 🔒 ••••  👁││         │
│          │  └────────────┘│         │
│          │                │         │
│          │  [เข้าสู่ระบบ] │         │
│          │                │         │
│          │  ลืมรหัสผ่าน?  │         │
│          │                │         │
│          └────────────────┘         │
│         card with border            │
│                                     │
│       dark/light bg window          │
└─────────────────────────────────────┘
```

## Verification

1. `dotnet build` — no errors
2. Dark mode: card has subtle border visible against dark bg, elevated card bg
3. Light mode: card has subtle border visible against light bg
4. No "SRAN®" text visible — just the cat logo
5. Logo is noticeably larger than before (96px vs 64px)
6. Card is centered both horizontally and vertically
7. All existing functionality unchanged (focus ring, eye toggle, error card, fade-in, etc.)

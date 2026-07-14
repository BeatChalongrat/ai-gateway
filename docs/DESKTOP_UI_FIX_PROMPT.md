# SRAN Desktop — UI Fix Prompt (3 Issues)

> Give this to Claude Fable with the ai-gateway codebase attached.
> Fixes 3 issues found during visual audit after the UI alignment overhaul.

---

## Issue 1: Mojibake — Unicode em-dash/ellipsis rendering as â€¦ â€" â€™

**Problem:** The source code uses Unicode em-dash `—` (U+2014) and ellipsis `…` (U+2026) in user-visible strings. These render correctly in source but may display as mojibake (`â€¦`, `â€"`) at runtime if the file encoding isn't handled correctly during build.

**Fix:** Replace all Unicode punctuation in **user-visible strings** with ASCII equivalents. This is the safest approach — it guarantees correct display regardless of encoding.

Leave them in **code comments** — those are never displayed to users.

### Files and replacements:

**`src/SranDesktop.Presentation/Views/LoginView.xaml`** line 101:
```
"Signing in…" → "Signing in..."
```

**`src/SranDesktop.Presentation/Views/ChatView.xaml`** line 221:
```
"Agent Mode — the AI can use desktop tools with your permission"
→ "Agent Mode - the AI can use desktop tools with your permission"
```

**`src/SranDesktop.Presentation/Views/Dialogs/FirstRunDialog.xaml`** lines 54, 56, 58:
```
<Run Text="• Ctrl+Shift+Space" FontWeight="SemiBold" /> — open SRAN from anywhere
→ <Run Text="• Ctrl+Shift+Space" FontWeight="SemiBold" /> - open SRAN from anywhere

<Run Text="• Agent Mode" FontWeight="SemiBold" /> — let the AI read files, run commands, and more
→ <Run Text="• Agent Mode" FontWeight="SemiBold" /> - let the AI read files, run commands, and more

<Run Text="• Close to tray" FontWeight="SemiBold" /> — the app keeps running in the background
→ <Run Text="• Close to tray" FontWeight="SemiBold" /> - the app keeps running in the background
```

Also in FirstRunDialog.xaml: if bullet characters `•` (U+2022) are used in `<Run>` text, keep them — they should render fine as text content. But if they also appear as mojibake, replace with `-` or use a small Path dot.

**`src/SranDesktop.Presentation/ViewModels/ChatViewModel.cs`** — status text strings:
```csharp
// Line ~303:
"Thinking…" → "Thinking..."
// Line ~304:
"Searching the web…" → "Searching the web..."
// Line ~305:
"Generating an image…" → "Generating an image..."
// Line ~306:
"Generating a video…" → "Generating a video..."
// Line ~357:
$"Working… (step {status.Iteration}/{status.MaxIterations})" → $"Working... (step {status.Iteration}/{status.MaxIterations})"
// Line ~371:
$"Waiting for permission: {consent.ToolName}…" → $"Waiting for permission: {consent.ToolName}..."
```

**`src/SranDesktop.Presentation/ViewModels/ConversationListViewModel.cs`**:
```csharp
// Line ~106:
"Offline — showing local history" → "Offline - showing local history"
// Line ~136:
"Could not create a chat — server unreachable" → "Could not create a chat - server unreachable"
// Line ~157:
"Could not delete — server unreachable" → "Could not delete - server unreachable"
// Line ~184:
"Could not update pin — server unreachable" → "Could not update pin - server unreachable"
// Line ~206:
"Could not rename — server unreachable" → "Could not rename - server unreachable"
```

**`src/SranDesktop.Presentation/ViewModels/ChatViewModel.cs`** line ~129:
```csharp
"Could not load messages — server unreachable." → "Could not load messages - server unreachable."
```

**`src/SranDesktop.Presentation/ViewModels/AgentExecutionViewModel.cs`** line ~56:
```csharp
text[..90] + "…" → text[..90] + "..."
```

**`src/SranDesktop.Infrastructure/Api/PortalAuthClient.cs`** line ~124:
```csharp
value[..max] + "…" → value[..max] + "..."
```

**`src/SranDesktop.Infrastructure/Api/GatewayApiClient.cs`**:
```csharp
// Line ~63 (user-visible error):
"Agent Mode requires the gateway — check the Gateway URL in Settings, or turn off Agent Mode "
→ "Agent Mode requires the gateway - check the Gateway URL in Settings, or turn off Agent Mode "

// Line ~107 (truncation):
value[..max] + "…" → value[..max] + "..."
```

**`src/SranDesktop.Infrastructure/Api/ChatEdgeApiClient.cs`** line ~270:
```csharp
value[..max] + "…" → value[..max] + "..."
```

**`src/SranDesktop.Infrastructure/Mcp/Tools/SearchFilesTool.cs`** line ~99:
```csharp
line[..200] + "…" → line[..200] + "..."
```

**`src/SranDesktop.Application/Services/AgentOrchestrator.cs`** line ~243:
```csharp
content[..200] + "…" → content[..200] + "..."
```

**Rule:** Any string that could be displayed to the user or appears in a `.xaml` file — replace `—` with `-` and `…` with `...`. Comments can keep Unicode.

---

## Issue 2: Settings Dialog Missing Gear Icon

**Problem:** The Settings dialog title is just a plain `TextBlock` saying "Settings" with no icon. Every other UI element now has a vector icon, making this inconsistent.

**Fix:** Add a gear icon next to the "Settings" title.

**File:** `src/SranDesktop.Presentation/Views/SettingsView.xaml`

**Current** (line ~28):
```xml
<TextBlock Text="Settings" FontSize="18" FontWeight="SemiBold"
           Foreground="{DynamicResource TextPrimaryBrush}" />
```

**Replace with:**
```xml
<StackPanel Orientation="Horizontal" Margin="0,0,0,4">
    <Path Data="{StaticResource Geo_Settings}"
          Stroke="{DynamicResource TextPrimaryBrush}" StrokeThickness="2"
          StrokeStartLineCap="Round" StrokeEndLineCap="Round" StrokeLineJoin="Round"
          Width="20" Height="20" Stretch="Uniform"
          VerticalAlignment="Center" Margin="0,0,10,0" />
    <TextBlock Text="Settings" FontSize="18" FontWeight="SemiBold"
               Foreground="{DynamicResource TextPrimaryBrush}"
               VerticalAlignment="Center" />
</StackPanel>
```

**Note:** `Geo_Settings` must already exist in `Icons.xaml`. If the gear path geometry is incomplete or doesn't render well at 20px, use a simpler gear with fewer teeth — the key is that it's recognizable as a gear.

---

## Issue 3: Markdown Image Emoji → Vector Icon or Plain Text

**Problem:** When markdown contains an image link `![alt](url)`, the renderer displays it as `🖼 alt` using a Unicode emoji (U+1F5BC). This is the last emoji in the UI chrome.

**File:** `src/SranDesktop.Presentation/Controls/MarkdownRenderer.cs` line 373

**Current:**
```csharp
case LinkInline { IsImage: true } image:
    target.Add(BuildHyperlink(image, $"🖼 {LabelOf(image) ?? image.Url ?? "image"}"));
    break;
```

**Option A (simplest, recommended):** Replace emoji with text label:
```csharp
case LinkInline { IsImage: true } image:
    target.Add(BuildHyperlink(image, $"[Image: {LabelOf(image) ?? image.Url ?? "image"}]"));
    break;
```

**Option B (richer):** Use an InlineUIContainer with a small Path icon + text:
```csharp
case LinkInline { IsImage: true } image:
    var imageLabel = LabelOf(image) ?? image.Url ?? "image";
    var imageIcon = new System.Windows.Shapes.Path
    {
        Data = System.Windows.Media.Geometry.Parse(
            "M 21 19 L 21 5 C 21 3.9 20.1 3 19 3 L 5 3 C 3.9 3 3 3.9 3 5 L 3 19 C 3 20.1 3.9 21 5 21 L 19 21 C 20.1 21 21 20.1 21 19 Z M 8.5 13.5 L 11 16.51 L 14.5 12 L 19 18 L 5 18 L 8.5 13.5 Z"),
        Stretch = System.Windows.Media.Stretch.Uniform,
        Width = 14, Height = 14,
        StrokeThickness = 1.5,
        StrokeStartLineCap = System.Windows.Media.PenLineCap.Round,
        StrokeEndLineCap = System.Windows.Media.PenLineCap.Round,
        StrokeLineJoin = System.Windows.Media.PenLineJoin.Round,
        VerticalAlignment = System.Windows.VerticalAlignment.Center,
        Margin = new System.Windows.Thickness(0, 0, 4, 0)
    };
    imageIcon.SetResourceReference(System.Windows.Shapes.Shape.StrokeProperty, "AccentBlueBrush");
    
    var link = BuildHyperlink(image, imageLabel);
    // Prepend the icon inline
    if (link is System.Windows.Documents.Hyperlink hl)
    {
        hl.Inlines.InsertBefore(hl.Inlines.FirstInline, 
            new System.Windows.Documents.InlineUIContainer(imageIcon));
    }
    target.Add(link);
    break;
```

Pick Option A if you want it simple and safe. Pick Option B only if it compiles cleanly — don't fight WPF inline layout issues for this.

---

## Build Order

1. Fix all mojibake strings (Issue 1) — find-and-replace across listed files
2. Add gear icon to Settings dialog (Issue 2)
3. Replace image emoji in MarkdownRenderer (Issue 3)
4. Build: `dotnet build` — must succeed with 0 errors
5. Run all tests: `dotnet test` — 98 tests must pass

## Verification

After fixing, grep to confirm zero remaining issues:

```powershell
# Check no Unicode em-dash/ellipsis in user-visible .xaml strings
# (comments in .cs are fine)
Select-String -Path "src\**\*.xaml" -Pattern "[—…]" -Recurse

# Check no emoji in any XAML or renderer
Select-String -Path "src\**\*.xaml","src\**\MarkdownRenderer.cs" -Pattern "[\U0001F000-\U0001FFFF]" -Recurse
```

Both should return empty (or only comments).

---

*End of UI Fix Prompt*

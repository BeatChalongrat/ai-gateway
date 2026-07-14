# SRAN Desktop — Round 6: Model Selector + Usage Display (Web Parity)

## Context

The web chat has a rich model selector with capability badges (Image, Thinks, Video), categorized sections (Popular, Recently Used, per-provider), and a usage/credits popup. The desktop's model selector is a plain list showing only name + provider. This round brings full parity.

**Key finding: The backend already returns everything we need.** The `GET /api/v1/models` endpoint sends `capabilities[]`, `category`, `reasoning`, `icon`, pricing, context window. The `GET /api/v1/usage/me` endpoint returns credits remaining + per-model usage breakdown. The desktop just doesn't consume these fields.

---

## Fix 1 — Enrich ModelInfo DTO

### Current (`ChatEdgeModels.cs` lines 53-66)
```csharp
public sealed record ModelInfo(string Id, string? Name, string? Provider, string? ModelKind, bool Allowed)
```

### Target
```csharp
public sealed record ModelInfo(
    string Id,
    string? Name,
    string? Provider,
    string? ModelKind,
    bool Allowed,
    string? Description,
    int? ContextWindow,
    string[]? Capabilities,   // "vision", "image_generation_via_tools", "video_generation", "audio_generation", "tts", "reasoning", etc.
    string? Category,         // "popular", provider name, etc.
    bool Reasoning,           // model supports extended thinking
    string? Icon,
    decimal? InputPricePer1M,
    decimal? OutputPricePer1M
)
{
    public string DisplayName => Name ?? Id;

    // Computed badge helpers
    public bool SupportsImage => Capabilities?.Any(c =>
        c is "image_generation_via_tools" or "image_generation") == true;
    public bool SupportsVideo => Capabilities?.Any(c =>
        c is "video_generation" or "video_generation_via_tools") == true;
    public bool SupportsAudio => Capabilities?.Any(c =>
        c is "audio_generation" or "tts") == true;
    public bool SupportsThinking => Reasoning ||
        Capabilities?.Contains("reasoning") == true;
}
```

### Files to Modify
- `SranDesktop.Application/Models/ChatEdgeModels.cs` — extend ModelInfo record
- `SranDesktop.Infrastructure/Api/ChatEdgeApiClient.cs` — ensure JSON deserialization maps all fields (System.Text.Json should handle automatically with matching names; verify `capabilities` → `Capabilities` mapping via `[JsonPropertyName]` if needed)

---

## Fix 2 — Model Selector Popup Redesign

### Current State
`ComposerControl.xaml` lines 240-351: flat list, search box, name + provider, checkmark.

### Target (Match Web Screenshots)

The popup should have these sections in order:

```
┌─────────────────────────────────────────┐
│  🔍 Search models...                    │
├─────────────────────────────────────────┤
│  ✨ Auto   Selects the best model       │
│           automatically                 │
├─────────────────────────────────────────┤
│  POPULAR                                │
│  GPT-4o        openai   [🎨 Image] ✓   │
│  GPT-4o Mini   openai   [🎨 Image]     │
│  O4 Mini       openai   [🧠 Thinks]    │
│  GPT 4 Turbo   openai   [🎨 Image]     │
├─────────────────────────────────────────┤
│  RECENTLY USED                          │
│  GPT-4o        openai   [🎨 Image] ✓   │
│  GPT-4o Mini   openai   [🎨 Image]     │
├─────────────────────────────────────────┤
│  OPENAI                            71 ▾ │
│  (expandable full list)                 │
│  ANTHROPIC                          5 ▾ │
│  GOOGLE                            12 ▾ │
│  OPENROUTER                        20 ▾ │
└─────────────────────────────────────────┘
```

### 2.1 "Auto" Row

Add a special row at the top (below search, above Popular):

```xml
<Button Style="{StaticResource FlatRowButton}"
        Command="{Binding SelectModelCommand}"
        CommandParameter="auto"
        Height="48" Padding="12,8">
    <DockPanel>
        <!-- Sparkle icon (the ✨ wand icon from web) -->
        <Path DockPanel.Dock="Left"
              Data="{StaticResource Geo_Sparkle}"
              Fill="{DynamicResource AccentPurpleBrush}"
              Width="18" Height="18"
              Stretch="Uniform"
              Margin="0,0,10,0"
              VerticalAlignment="Center" />
        <StackPanel VerticalAlignment="Center">
            <TextBlock Text="Auto"
                       FontWeight="SemiBold"
                       FontSize="14"
                       Foreground="{DynamicResource TextPrimaryBrush}" />
            <TextBlock Text="Selects the best model automatically"
                       FontSize="11"
                       Foreground="{DynamicResource TextSecondaryBrush}"
                       Margin="0,1,0,0" />
        </StackPanel>
        <!-- Checkmark if auto is selected -->
        <Path DockPanel.Dock="Right" ... Visibility="{Binding IsAutoSelected}" />
    </DockPanel>
</Button>
```

The **sparkle icon** (`Geo_Sparkle`) — add to `Icons.xaml`. This is a 4-point star / magic wand sparkle shape. Use this SVG path data:
```
M12 2L14.09 8.26L20 10L14.09 11.74L12 18L9.91 11.74L4 10L9.91 8.26L12 2Z
M19 15L20 18L21 15L24 14L21 13L20 10L19 13L16 14Z
```
(Simplified multi-star sparkle.)

### 2.2 Capability Badges

Each model row gets small colored pill badges after the provider name:

```xml
<!-- Inside model row DataTemplate -->
<StackPanel Orientation="Horizontal" DockPanel.Dock="Right"
            Margin="8,0,0,0" VerticalAlignment="Center">

    <!-- Image badge -->
    <Border Visibility="{Binding SupportsImage, Converter={StaticResource BoolToVisibility}}"
            Background="#1A8B5CF6" CornerRadius="4"
            Padding="6,2" Margin="0,0,4,0">
        <StackPanel Orientation="Horizontal">
            <Path Data="{StaticResource Geo_ImageBadge}"
                  Stroke="#8B5CF6" StrokeThickness="1.2"
                  Width="12" Height="12" Stretch="Uniform"
                  Margin="0,0,4,0" />
            <TextBlock Text="Image" FontSize="11"
                       Foreground="#8B5CF6" FontWeight="Medium" />
        </StackPanel>
    </Border>

    <!-- Thinks badge -->
    <Border Visibility="{Binding SupportsThinking, Converter={StaticResource BoolToVisibility}}"
            Background="#1A3B82F6" CornerRadius="4"
            Padding="6,2" Margin="0,0,4,0">
        <StackPanel Orientation="Horizontal">
            <Path Data="{StaticResource Geo_ThinksBadge}"
                  Stroke="#3B82F6" StrokeThickness="1.2"
                  Width="12" Height="12" Stretch="Uniform"
                  Margin="0,0,4,0" />
            <TextBlock Text="Thinks" FontSize="11"
                       Foreground="#3B82F6" FontWeight="Medium" />
        </StackPanel>
    </Border>

    <!-- Video badge -->
    <Border Visibility="{Binding SupportsVideo, Converter={StaticResource BoolToVisibility}}"
            Background="#1AEF4444" CornerRadius="4"
            Padding="6,2" Margin="0,0,4,0">
        <StackPanel Orientation="Horizontal">
            <Path Data="{StaticResource Geo_VideoBadge}"
                  Stroke="#EF4444" StrokeThickness="1.2"
                  Width="12" Height="12" Stretch="Uniform"
                  Margin="0,0,4,0" />
            <TextBlock Text="Video" FontSize="11"
                       Foreground="#EF4444" FontWeight="Medium" />
        </StackPanel>
    </Border>

    <!-- Audio/Music badge -->
    <Border Visibility="{Binding SupportsAudio, Converter={StaticResource BoolToVisibility}}"
            Background="#1A10B981" CornerRadius="4"
            Padding="6,2" Margin="0,0,4,0">
        <StackPanel Orientation="Horizontal">
            <Path Data="{StaticResource Geo_AudioBadge}"
                  Stroke="#10B981" StrokeThickness="1.2"
                  Width="12" Height="12" Stretch="Uniform"
                  Margin="0,0,4,0" />
            <TextBlock Text="Music" FontSize="11"
                       Foreground="#10B981" FontWeight="Medium" />
        </StackPanel>
    </Border>

    <!-- Checkmark for selected model -->
    <Path Data="{StaticResource Geo_Check}"
          Stroke="{DynamicResource AccentGreenBrush}"
          StrokeThickness="2"
          Width="14" Height="14"
          Stretch="Uniform"
          Margin="4,0,0,0"
          Visibility="{Binding IsSelected, Converter={StaticResource BoolToVisibility}}" />
</StackPanel>
```

Badge colors (from the web):
| Badge | Text/Icon Color | Background |
|-------|----------------|------------|
| Image | `#8B5CF6` (purple) | `#1A8B5CF6` (10% purple) |
| Thinks | `#3B82F6` (blue) | `#1A3B82F6` (10% blue) |
| Video | `#EF4444` (red) | `#1AEF4444` (10% red) |
| Music/Audio | `#10B981` (green) | `#1A10B981` (10% green) |

### 2.3 Categorized Sections

Group models by section. The ViewModel should produce a grouped collection:

```csharp
// In ComposerViewModel or ChatViewModel
public ObservableCollection<ModelGroup> GroupedModels { get; } = new();

public sealed class ModelGroup
{
    public string Header { get; init; }       // "POPULAR", "RECENTLY USED", "OPENAI", etc.
    public int? Count { get; init; }          // model count for provider sections
    public bool IsExpanded { get; set; }      // for collapsible provider sections
    public List<ModelItemViewModel> Models { get; init; } = new();
}
```

**Grouping logic:**

1. **Popular** — models where `Category == "popular"` or a hardcoded popular list from the backend. If the backend doesn't provide a "popular" category, hardcode: `["gpt-4o", "gpt-4o-mini", "o4-mini-2025-04-16", "gpt-4-turbo-2024-04-09"]` (match what web shows).

2. **Recently Used** — track in local SQLite: `CREATE TABLE model_usage (model_id TEXT PRIMARY KEY, last_used_at TEXT, use_count INT)`. Update on every message send. Show top 5 most recently used.

3. **Per-Provider** — group remaining models by `Provider` field. Show provider name as header (uppercase) with model count. Collapsible — default collapsed, click to expand.

**XAML structure** for grouped list:

```xml
<ItemsControl ItemsSource="{Binding GroupedModels}">
    <ItemsControl.ItemTemplate>
        <DataTemplate DataType="{x:Type vm:ModelGroup}">
            <StackPanel>
                <!-- Section header -->
                <Button Style="{StaticResource FlatRowButton}"
                        Visibility="{Binding IsCollapsible, Converter={StaticResource BoolToVisibility}}"
                        Command="{Binding ToggleExpandCommand}"
                        Height="32" Padding="12,4">
                    <DockPanel>
                        <TextBlock Text="{Binding Header}"
                                   FontSize="11" FontWeight="Bold"
                                   Foreground="{DynamicResource TextTertiaryBrush}"
                                   VerticalAlignment="Center" />
                        <TextBlock DockPanel.Dock="Right"
                                   Text="{Binding Count}"
                                   FontSize="11"
                                   Foreground="{DynamicResource TextTertiaryBrush}"
                                   Margin="0,0,4,0"
                                   VerticalAlignment="Center" />
                        <!-- Chevron -->
                        <Path DockPanel.Dock="Right"
                              Data="{StaticResource Geo_ChevronDown}"
                              ... RenderTransform rotated when collapsed />
                    </DockPanel>
                </Button>

                <!-- Non-collapsible header (Popular, Recently Used) -->
                <TextBlock Visibility="{Binding IsCollapsible, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}"
                           Text="{Binding Header}"
                           FontSize="11" FontWeight="Bold"
                           Foreground="{DynamicResource TextTertiaryBrush}"
                           Padding="12,8,12,4" />

                <!-- Model rows -->
                <ItemsControl ItemsSource="{Binding Models}"
                              Visibility="{Binding IsExpanded, Converter={StaticResource BoolToVisibility}}">
                    <!-- Each model row with badges -->
                </ItemsControl>
            </StackPanel>
        </DataTemplate>
    </ItemsControl.ItemTemplate>
</ItemsControl>
```

### 2.4 Search Filtering

Already exists (bound to `ModelSearchText`). Ensure search filters across ALL groups — if a match is found in a collapsed provider section, auto-expand that section or show a flat filtered list.

---

## Fix 3 — Sparkle Icon on Composer Model Button

### Current
The model button in the composer shows: `[model icon] GPT-4o ▾`

### Target
When "Auto" is selected, show the sparkle icon instead of the model icon:

```xml
<!-- In ComposerControl.xaml, ModelSelectorButton area -->
<!-- Sparkle icon (visible when Auto selected) -->
<Path Data="{StaticResource Geo_Sparkle}"
      Fill="{DynamicResource AccentPurpleBrush}"
      Width="16" Height="16" Stretch="Uniform"
      Margin="0,0,6,0"
      Visibility="{Binding IsAutoSelected, Converter={StaticResource BoolToVisibility}}" />

<!-- Model display name -->
<TextBlock Text="{Binding CurrentModelDisplayName}" ... />
<!-- "Auto" when auto selected, model name otherwise -->
```

When Auto is selected, the button shows: `✨ Auto ▾`
When a specific model is selected, the button shows: `GPT-4o ▾` (no sparkle)

### Auto Model Logic

When the user selects "Auto":
- Set `CurrentModelName = "auto"` (the chat-edge-api/gateway should handle the "auto" model ID by routing to the best model)
- Set `CurrentModelDisplayName = "Auto"`
- Set `IsAutoSelected = true`
- The sparkle icon appears on the composer button

Check if the gateway already supports `model: "auto"` — from the web screenshot, it does (the web has "Auto" as an option and the usage shows "auto" as a model with 9M credits used via OpenRouter). If the gateway handles it, the desktop just needs to send `"auto"` as the model ID.

---

## Fix 4 — Usage/Credits Display

### 4.1 Add Usage API Client

```csharp
// In IChatEdgeApiClient.cs
Task<UsageResponse> GetUsageAsync();

// In ChatEdgeApiClient.cs
public async Task<UsageResponse> GetUsageAsync()
{
    var response = await _http.GetAsync("api/v1/usage/me");
    response.EnsureSuccessStatusCode();
    return await response.Content.ReadFromJsonAsync<UsageResponse>();
}
```

### 4.2 Usage DTO

```csharp
// In ChatEdgeModels.cs
public sealed record UsageResponse(
    UsageCredits Credits,
    UsageByModelItem[] UsageByModel,
    string Period
);

public sealed record UsageCredits(
    long Total,
    long Used,
    long Remaining
);

public sealed record UsageByModelItem(
    string Model,
    string? Provider,
    long Tokens,
    long CreditsUsed,
    int RequestCount,
    double Percentage
);
```

### 4.3 Usage Popup UI

Add a small clickable credits badge in the bottom-right corner of the window (matching the web's green circle + number):

```xml
<!-- In MainWindow.xaml, positioned at bottom-right -->
<Button x:Name="UsageBadge"
        Style="{StaticResource FlatRowButton}"
        HorizontalAlignment="Right"
        VerticalAlignment="Bottom"
        Margin="0,0,16,12"
        Padding="8,4"
        Click="UsageBadge_Click"
        Panel.ZIndex="10">
    <StackPanel Orientation="Horizontal">
        <!-- Green circle indicator -->
        <Ellipse Width="10" Height="10"
                 Stroke="#10B981" StrokeThickness="2"
                 Fill="Transparent"
                 Margin="0,0,6,0"
                 VerticalAlignment="Center" />
        <!-- Credits remaining -->
        <TextBlock Text="{Binding CreditsRemaining, StringFormat=N0}"
                   FontSize="12" FontWeight="Medium"
                   Foreground="{DynamicResource TextSecondaryBrush}"
                   VerticalAlignment="Center" />
        <!-- Chevron -->
        <Path Data="{StaticResource Geo_ChevronUp}"
              Stroke="{DynamicResource TextTertiaryBrush}"
              StrokeThickness="1.5"
              Width="10" Height="10"
              Stretch="Uniform"
              Margin="4,0,0,0"
              VerticalAlignment="Center" />
    </StackPanel>
</Button>
```

### 4.4 Usage Popup Content

When clicking the credits badge, show a Popup with:

```
┌──────────────────────────────────────────┐
│  Usage                    View in Portal │
├──────────────────────────────────────────┤
│      ┌───┐                               │
│      │94%│  939,228                       │
│      └───┘  of 1,000,000 credits remain  │
├──────────────────────────────────────────┤
│  USAGE BY MODEL                          │
│                                          │
│  auto                   9,152,160 cred   │
│  OpenRouter                     48.7%    │
│  ████████████████████░░░░░░░░░░░░        │
│                                          │
│  GPT-4o Mini            4,933,749 cred   │
│  OpenAI                         26.3%    │
│  ██████████░░░░░░░░░░░░░░░░░░░░░         │
│                                          │
│  gpt-image-1            2,298,000 cred   │
│  OpenAI                         12.2%    │
│  █████░░░░░░░░░░░░░░░░░░░░░░░░░░         │
│                                          │
│  (more rows...)                          │
├──────────────────────────────────────────┤
│  Total used            18,776,198 credits│
└──────────────────────────────────────────┘
```

**Circular progress ring** — use a custom `Arc` drawn with `ArcSegment`:

```xml
<Canvas Width="48" Height="48">
    <!-- Background circle -->
    <Ellipse Width="48" Height="48"
             Stroke="{DynamicResource BorderBrush}"
             StrokeThickness="4" Fill="Transparent" />
    <!-- Progress arc -->
    <Path Stroke="#10B981" StrokeThickness="4" StrokeLineCap="Round">
        <Path.Data>
            <PathGeometry>
                <PathFigure StartPoint="{Binding ArcStart}">
                    <ArcSegment Point="{Binding ArcEnd}"
                                Size="22,22"
                                IsLargeArc="{Binding IsLargeArc}"
                                SweepDirection="Clockwise" />
                </PathFigure>
            </PathGeometry>
        </Path.Data>
    </Path>
    <!-- Percentage text -->
    <TextBlock Canvas.Left="8" Canvas.Top="14"
               Text="{Binding UsagePercentText}"
               FontSize="13" FontWeight="Bold"
               Foreground="{DynamicResource TextPrimaryBrush}" />
</Canvas>
```

Better approach: create a `CircularProgressControl` UserControl that takes `Percentage` as a DependencyProperty and draws the arc in code-behind:

```csharp
public partial class CircularProgress : UserControl
{
    public static readonly DependencyProperty PercentageProperty =
        DependencyProperty.Register("Percentage", typeof(double), typeof(CircularProgress),
            new PropertyMetadata(0.0, OnPercentageChanged));

    private static void OnPercentageChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        var ctrl = (CircularProgress)d;
        ctrl.UpdateArc();
    }

    private void UpdateArc()
    {
        var pct = Percentage / 100.0;
        var angle = pct * 360;
        var radians = (angle - 90) * Math.PI / 180;
        var radius = 20;
        var cx = 24; var cy = 24;
        var endX = cx + radius * Math.Cos(radians);
        var endY = cy + radius * Math.Sin(radians);
        var isLargeArc = angle > 180;

        var figure = new PathFigure { StartPoint = new Point(cx, cy - radius) };
        figure.Segments.Add(new ArcSegment(
            new Point(endX, endY),
            new Size(radius, radius),
            0, isLargeArc, SweepDirection.Clockwise, true));
        ArcPath.Data = new PathGeometry { Figures = { figure } };
    }
}
```

**Usage bar per model** — simple progress bar:

```xml
<ProgressBar Value="{Binding Percentage}" Maximum="100"
             Height="4" Background="{DynamicResource BorderBrush}"
             Foreground="#3B82F6" />
```

### 4.5 Data Loading

- Fetch usage on app start and cache
- Refresh when usage popup opens (if last fetch > 60 seconds ago)
- Show "—" while loading

---

## Fix 5 — New Icon Geometries

Add to `Resources/Icons.xaml`:

```xml
<!-- Sparkle (Auto model) — 4-point star -->
<StreamGeometry x:Key="Geo_Sparkle">
    M12 1L14.5 8.5L22 11L14.5 13.5L12 21L9.5 13.5L2 11L9.5 8.5Z
    M20 3L21 6L22 3L25 2L22 1L21 -2L20 1L17 2Z
</StreamGeometry>

<!-- Image badge icon (landscape photo) -->
<StreamGeometry x:Key="Geo_ImageBadge">
    M2 4h16a2 2 0 012 2v8a2 2 0 01-2 2H2a2 2 0 01-2-2V6a2 2 0 012-2z
    M6 8a1.5 1.5 0 110 3 1.5 1.5 0 010-3z
    M20 14l-5-5-3 3-2-2-6 6
</StreamGeometry>

<!-- Thinks badge icon (brain/sparkle) -->
<StreamGeometry x:Key="Geo_ThinksBadge">
    M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z
    M8 12l3 3 5-5
</StreamGeometry>

<!-- Video badge icon (play button) -->
<StreamGeometry x:Key="Geo_VideoBadge">
    M3 4a2 2 0 00-2 2v8a2 2 0 002 2h12a2 2 0 002-2l4 3V5l-4 3V6a2 2 0 00-2-2H3z
</StreamGeometry>

<!-- Audio/Music badge icon (music note) -->
<StreamGeometry x:Key="Geo_AudioBadge">
    M9 18V5l12-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0z
    M21 16a3 3 0 11-6 0 3 3 0 016 0z
</StreamGeometry>
```

**NOTE**: These are approximate SVG paths. Fable should refine them to match the exact web icons — take screenshots of the web badges at 4x zoom and trace. Use Lucide or Heroicons as reference for clean 24×24 viewBox paths.

---

## Fix 6 — "View in Portal" Link

The usage popup has a "View in Portal" link at top-right. This opens the admin portal in the default browser:

```csharp
Process.Start(new ProcessStartInfo($"{_settings.ChatServerUrl}/portal/usage")
{
    UseShellExecute = true
});
```

---

## Files Summary

### New Files
```
Presentation/Controls/CircularProgress.xaml[.cs]   — reusable circular progress ring
```

### Modified Files
```
Application/Models/ChatEdgeModels.cs               — extend ModelInfo with capabilities, add UsageResponse DTOs
Application/Interfaces/IChatEdgeApiClient.cs        — add GetUsageAsync()
Infrastructure/Api/ChatEdgeApiClient.cs             — implement GetUsageAsync()
Presentation/Controls/ComposerControl.xaml          — redesign model popup (badges, groups, Auto row, sparkle)
Presentation/ViewModels/ChatViewModel.cs            — GroupedModels, model grouping logic, "auto" selection, usage fetch
Presentation/ViewModels/ModelItemViewModel.cs        — new VM wrapping ModelInfo with IsSelected, badge visibility
Presentation/Views/MainWindow.xaml                  — add UsageBadge + UsagePopup
Presentation/ViewModels/MainViewModel.cs            — CreditsRemaining, UsageByModel, usage data
Presentation/Resources/Icons.xaml                   — add Geo_Sparkle, badge icons
Presentation/Resources/Themes/Dark.xaml             — add AccentPurpleBrush if missing
Presentation/Resources/Themes/Light.xaml            — add AccentPurpleBrush if missing
```

### Optional: Local Recently Used Tracking
```
Infrastructure/Storage/ModelUsageRepository.cs       — SQLite model_usage table
Domain/Entities/ModelUsageEntry.cs                   — model_id, last_used_at, use_count
```

---

## Implementation Order

1. **ModelInfo DTO enrichment** — add fields, verify JSON mapping
2. **ModelItemViewModel** — wrap ModelInfo, expose badge booleans + IsSelected
3. **Model grouping logic** — Popular / Recently Used / per-Provider sections
4. **Redesign popup XAML** — grouped sections, capability badges, Auto row
5. **Sparkle icon** — add geometry, show on composer button when Auto
6. **Usage API** — client method + DTOs
7. **CircularProgress control** — custom arc drawing
8. **Usage popup** — credits badge + popup with chart
9. **"View in Portal" link**
10. **Test end-to-end**

---

## Verification Checklist

Take screenshots of:
1. **Model popup open** — showing Popular, Recently Used, provider sections with badges
2. **Auto selected** — sparkle icon visible on composer button
3. **Search filtering** — typing filters across groups
4. **Usage popup** — circular progress + per-model bars
5. **Badge variety** — show models with Image, Thinks, Video badges

Save as:
```
parity-r6-model-popup.png
parity-r6-auto-selected.png
parity-r6-search-filter.png
parity-r6-usage-popup.png
parity-r6-badges.png
```

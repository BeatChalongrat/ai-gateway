# SRAN Desktop — Round 9: Full Claude Desktop Parity (Mega Build)

## Context & Architecture Recap

- **.NET 8 WPF** with CommunityToolkit.Mvvm source generators (`[ObservableProperty]`, `[RelayCommand]`, partial `OnChanged`)
- **Backend**: chat-edge-api (`:4000/api/v1`) Go REST + SSE, gateway (`:8080`) OpenAI-compatible `/v1/chat/completions`
- **Agent loop**: `ChatOrchestrator.RunAsync()` — max 25 iterations, builds `GatewayCompletionRequest` with tools from `IMcpToolHost.GetToolDefinitions()`, streams via `GatewayChunkParser`, executes tool calls locally, feeds results back
- **SSE mapping**: `ChatService.MapFrame()` handles 13 event types including `reasoning_delta`, `image_generating`, `video_generating`, `memory_saved`/`memory_suggested`
- **Rendering**: Markdig → native WPF elements (`MarkdownRenderer.cs`). **No WebView2 yet.**
- **Already done**: streaming, models, memory, KB/RAG, i18n, themes, folders, file upload, image/video gen, plan execution, reasoning/thinking display + toggle, tool consent dialog, MCP local tools (computer control, file ops, shell)
- **Packages**: CommunityToolkit.Mvvm, Markdig, H.NotifyIcon.Wpf, Serilog, Microsoft.Data.Sqlite

---

## Phase 1 — Core Experience (Features A–F)

### Feature A: Artifacts (Inline Interactive Rendering)

**What Claude Does**: When the AI generates a React component, HTML page, SVG, or Mermaid diagram, Claude Desktop renders it as a live interactive artifact inline in the chat — with a "fullscreen" button, version history, and the ability to iterate on it.

**Implementation**:

#### A1. Add WebView2 NuGet package

```xml
<!-- SranDesktop.Presentation.csproj -->
<PackageReference Include="Microsoft.Web.WebView2" Version="1.*" />
```

#### A2. Create ArtifactControl

Create `Controls/ArtifactControl.xaml` — a reusable control that hosts a `WebView2` inside a bordered card:

```xml
<UserControl x:Class="SranDesktop.Presentation.Controls.ArtifactControl">
    <Border CornerRadius="12" BorderThickness="1"
            BorderBrush="{DynamicResource BorderBrush}"
            Background="{DynamicResource BgSecondaryBrush}"
            Margin="0,8" Padding="0">
        <Grid>
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" /> <!-- toolbar -->
                <RowDefinition Height="*" />    <!-- webview -->
            </Grid.RowDefinitions>

            <!-- Artifact toolbar: title + [copy] [fullscreen] [open external] -->
            <DockPanel Grid.Row="0" Margin="12,8" LastChildFill="True">
                <StackPanel DockPanel.Dock="Right" Orientation="Horizontal">
                    <Button Style="{DynamicResource IconButton}" Command="{Binding CopySourceCommand}" ToolTip="Copy code" />
                    <Button Style="{DynamicResource IconButton}" Command="{Binding ToggleFullscreenCommand}" ToolTip="Fullscreen" />
                    <Button Style="{DynamicResource IconButton}" Command="{Binding OpenExternalCommand}" ToolTip="Open in browser" />
                </StackPanel>
                <TextBlock Text="{Binding Title}" FontSize="13" FontWeight="SemiBold"
                           Foreground="{DynamicResource TextPrimaryBrush}"
                           VerticalAlignment="Center" TextTrimming="CharacterEllipsis" />
            </DockPanel>

            <!-- WebView2 renderer -->
            <wpf:WebView2 Grid.Row="1" x:Name="ArtifactWebView"
                          MinHeight="200" MaxHeight="600"
                          DefaultBackgroundColor="Transparent" />
        </Grid>
    </Border>
</UserControl>
```

Code-behind:
- `InitializeAsync()` — call `EnsureCoreWebView2Async()`, configure settings: `IsStatusBarEnabled = false`, `AreDevToolsEnabled = false`, `IsWebMessageEnabled = true`
- `RenderContent(string html)` — wrap the content in a full HTML document with theme-matched CSS variables (read from current theme), inject into WebView2 via `NavigateToString()`
- Support both raw HTML and React JSX:
  - Raw HTML/SVG: inject directly
  - React JSX: wrap with a runtime (load React + ReactDOM + Babel standalone from CDN, transpile in-browser)
  - Mermaid: load mermaid.js from CDN, render the diagram
- Handle `window.chrome.webview.postMessage()` for artifact → app communication (e.g., `sendPrompt()` equivalent)
- Handle theme changes: re-inject CSS variables when theme toggles

#### A3. Artifact Detection in SSE Stream

The AI signals artifacts in its response. Two detection strategies (implement both):

**Strategy 1 — Fenced blocks** (like plan cards):
```
:::artifact[title="Revenue Chart" type="react"]
import { useState } from 'react';
export default function Chart() { ... }
:::
```

Add a new regex in `ChatViewModel.cs` alongside `PlanFenceRegex`:
```csharp
private static readonly Regex ArtifactFenceRegex = new(
    @":::artifact\[(?<attrs>[^\]]*)\]\s*\n(?<code>[\s\S]*?)\n:::",
    RegexOptions.Compiled);
```

Parse `attrs` for `title`, `type` (react|html|svg|mermaid|code), `language`.

**Strategy 2 — SSE event** (if gateway supports it):
Add `artifact_start` / `artifact_delta` / `artifact_complete` to `ChatService.MapFrame()`. Define:
```csharp
public record ArtifactEvent(string Id, string Title, string Type, string Content);
```

#### A4. Artifact ViewModel

Create `ArtifactViewModel.cs`:
```csharp
public partial class ArtifactViewModel : ObservableObject
{
    [ObservableProperty] private string _id;
    [ObservableProperty] private string _title;
    [ObservableProperty] private string _type; // react, html, svg, mermaid
    [ObservableProperty] private string _sourceCode;
    [ObservableProperty] private bool _isFullscreen;
    [ObservableProperty] private int _version = 1;

    public ObservableCollection<ArtifactVersion> History { get; } = new();

    [RelayCommand]
    private void CopySource() => Clipboard.SetText(SourceCode);

    [RelayCommand]
    private void ToggleFullscreen() => IsFullscreen = !IsFullscreen;

    [RelayCommand]
    private void OpenExternal()
    {
        var path = Path.GetTempFileName() + ".html";
        File.WriteAllText(path, BuildFullHtml());
        Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
    }
}
```

#### A5. Integrate into MessageBubble

In `MessageBubble.xaml`, after the `MarkdownViewer`, add an `ItemsControl` bound to `Message.Artifacts`:
```xml
<ItemsControl ItemsSource="{Binding Artifacts}">
    <ItemsControl.ItemTemplate>
        <DataTemplate DataType="{x:Type vm:ArtifactViewModel}">
            <controls:ArtifactControl DataContext="{Binding}" />
        </DataTemplate>
    </ItemsControl.ItemTemplate>
</ItemsControl>
```

#### A6. Fullscreen overlay

When `IsFullscreen` is true, show the artifact in a fullscreen `Window` or overlay `Grid` on top of `MainWindow` with a dark scrim. The WebView2 expands to fill the screen. Close with Escape.

---

### Feature B: Projects (Persistent Workspaces)

**What Claude Does**: Projects group conversations with custom instructions, attached files, and scoped memory. When you chat inside a project, the AI automatically uses the project's context.

**Implementation**:

#### B1. Backend API endpoints (chat-edge-api)

These need to exist (if they don't, create stubs that store locally):

```
POST   /api/v1/projects                    → create project
GET    /api/v1/projects                    → list projects
GET    /api/v1/projects/:id               → get project detail
PUT    /api/v1/projects/:id               → update (name, instructions, etc.)
DELETE /api/v1/projects/:id               → delete project
POST   /api/v1/projects/:id/files         → upload file to project
DELETE /api/v1/projects/:id/files/:fileId → remove file
POST   /api/v1/projects/:id/conversations → create conversation in project
GET    /api/v1/projects/:id/conversations → list project conversations
```

#### B2. Add to IChatEdgeApiClient

```csharp
// ── Projects (R9 Feature B) ────────────────────────────────────
Task<ProjectListResponse> ListProjectsAsync(CancellationToken ct = default);
Task<ProjectDto> CreateProjectAsync(string name, string? instructions = null, CancellationToken ct = default);
Task<ProjectDto> GetProjectAsync(string id, CancellationToken ct = default);
Task<ProjectDto> UpdateProjectAsync(string id, string? name = null, string? instructions = null, CancellationToken ct = default);
Task DeleteProjectAsync(string id, CancellationToken ct = default);
Task<FileAttachmentDto> UploadProjectFileAsync(string projectId, Stream content, string fileName, string contentType, long? length, IProgress<double>? progress = null, CancellationToken ct = default);
Task DeleteProjectFileAsync(string projectId, string fileId, CancellationToken ct = default);
```

#### B3. DTOs

```csharp
public record ProjectDto(
    string Id,
    string Name,
    string? Instructions,
    DateTime CreatedAt,
    DateTime UpdatedAt,
    List<ProjectFileDto>? Files,
    int ConversationCount);

public record ProjectFileDto(string Id, string Name, string ContentType, long Size, DateTime UploadedAt);
public record ProjectListResponse(List<ProjectDto> Projects);
```

#### B4. Sidebar UI

Add a "Projects" section to the sidebar in `MainWindow.xaml`, above the conversation list:

```xml
<!-- Projects section -->
<StackPanel>
    <DockPanel Margin="16,12,16,4">
        <Button DockPanel.Dock="Right" Style="{DynamicResource IconButton}"
                Command="{Binding CreateProjectCommand}" ToolTip="{loc:T project.create}">
            <controls:IconView Geometry="{StaticResource Geo_Plus}" ... />
        </Button>
        <TextBlock Text="{loc:T sidebar.projects}" FontSize="11" FontWeight="SemiBold"
                   Foreground="{DynamicResource TextTertiaryBrush}" VerticalAlignment="Center" />
    </DockPanel>
    <ItemsControl ItemsSource="{Binding Projects}">
        <!-- Each project: icon + name, click to open project view -->
    </ItemsControl>
</StackPanel>
```

#### B5. Project View

Create `Views/ProjectView.xaml` — a panel that replaces the main content area when a project is selected:

Layout:
- **Header**: Project name (editable inline), description
- **Tabs or sections**:
  - **Instructions**: multiline text editor for custom system prompt
  - **Files**: drag-and-drop file list with upload/remove
  - **Conversations**: list of conversations in this project
  - **Start Chat**: button that creates a new conversation with project context injected

#### B6. Project Context Injection

When sending a message from a project conversation, prepend the project's instructions and file contents to the system prompt in `ChatOrchestrator.BuildGatewayMessages()` or `ChatViewModel.BuildGatewayMessages()`:

```csharp
if (project?.Instructions is not null)
    systemPrompt = $"{project.Instructions}\n\n{systemPrompt}";
// Also include project file summaries/content as context
```

---

### Feature C: Web Search Toggle

**What Claude Does**: A toggle in the composer enables/disables web search. When enabled, the AI can search the web mid-conversation.

**Implementation**:

#### C1. Add search tool to MCP host

In the MCP tool definitions (`IMcpToolHost.GetToolDefinitions()`), add:

```json
{
  "type": "function",
  "function": {
    "name": "web_search",
    "description": "Search the web for current information. Use when asked about recent events, current data, or anything that may have changed since training.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string", "description": "Search query" }
      },
      "required": ["query"]
    }
  }
}
```

#### C2. Implement the tool

Create `Infrastructure/Mcp/Tools/WebSearchTool.cs`:
- Use the gateway's search endpoint or a direct API (e.g., Brave Search, Tavily, or the chat-edge-api's `/api/v1/search` if it exists)
- Return top 5 results as JSON with title, url, snippet
- The orchestrator will feed these results back to the AI for synthesis

#### C3. Composer toggle

Add a search toggle button in `ComposerControl.xaml` toolbar, next to the image/video toggles:

```xml
<ToggleButton IsChecked="{Binding IsWebSearchOn}"
              ToolTip="{loc:T input.webSearch}"
              Margin="0,0,2,0" Width="28" Height="28" Cursor="Hand">
    <!-- Same template pattern as image/video toggles -->
    <!-- Icon: Geo_Search or Geo_Browser -->
</ToggleButton>
```

#### C4. SSE status event

When the search tool runs, show inline status: "Searching the web..." — the orchestrator already emits `AgentToolCallsEvent` which displays friendly status. Add to `FriendlyToolStatus`:

```csharp
"web_search" => "Searching the web...",
```

#### C5. Search results display

In the message bubble, when the AI cites search results, render them as clickable source cards:
```xml
<!-- Source citation card -->
<Border CornerRadius="8" BorderThickness="1" Padding="10,8" Margin="0,4" Cursor="Hand">
    <StackPanel>
        <TextBlock Text="{Binding Title}" FontSize="13" FontWeight="SemiBold" />
        <TextBlock Text="{Binding Url}" FontSize="11" Foreground="{DynamicResource TextTertiaryBrush}" />
        <TextBlock Text="{Binding Snippet}" FontSize="12" TextWrapping="Wrap" MaxHeight="40" />
    </StackPanel>
</Border>
```

---

### Feature D: Vision (Image Input for Analysis)

**What Claude Does**: Users can paste or drag-and-drop images into the composer. The AI analyzes the image content (OCR, description, analysis).

**Implementation**:

#### D1. Image paste in composer

In `ComposerControl.xaml.cs`, handle `Ctrl+V` with image clipboard:

```csharp
private async void InputBox_PreviewKeyDown(object sender, KeyEventArgs e)
{
    // Existing Enter handling...

    // Ctrl+V: check for image in clipboard
    if (e.Key == Key.V && Keyboard.Modifiers == ModifierKeys.Control)
    {
        if (Clipboard.ContainsImage())
        {
            e.Handled = true;
            var image = Clipboard.GetImage();
            if (DataContext is ChatViewModel vm)
                await vm.AttachClipboardImageAsync(image);
        }
    }
}
```

#### D2. Drag-and-drop images

Add `AllowDrop="True"` to the composer `Border` and handle `Drop`:

```csharp
private async void Composer_Drop(object sender, DragEventArgs e)
{
    if (e.Data.GetDataPresent(DataFormats.FileDrop))
    {
        var files = (string[])e.Data.GetData(DataFormats.FileDrop);
        var imageFiles = files.Where(f =>
            f.EndsWith(".png", StringComparison.OrdinalIgnoreCase) ||
            f.EndsWith(".jpg", StringComparison.OrdinalIgnoreCase) ||
            f.EndsWith(".jpeg", StringComparison.OrdinalIgnoreCase) ||
            f.EndsWith(".gif", StringComparison.OrdinalIgnoreCase) ||
            f.EndsWith(".webp", StringComparison.OrdinalIgnoreCase));
        if (DataContext is ChatViewModel vm)
            await vm.AttachFilesAsync(imageFiles.ToArray());
    }
}
```

#### D3. Image preview in pending files

When an image is attached, show a thumbnail preview instead of just the filename:

```xml
<DataTemplate DataType="{x:Type vm:PendingFileViewModel}">
    <!-- If IsImage: show thumbnail -->
    <Border CornerRadius="8" Margin="0,0,6,4">
        <Grid>
            <Image Source="{Binding ThumbnailSource}" MaxWidth="80" MaxHeight="80"
                   Stretch="UniformToFill" Visibility="{Binding IsImage, Converter={StaticResource BoolToVisibility}}" />
            <!-- Existing file chip for non-images -->
        </Grid>
    </Border>
</DataTemplate>
```

#### D4. Send images as multimodal content

In `ChatOrchestrator.RunAsync()`, when building `GatewayCompletionRequest`, if the user message has image attachments, format as multimodal content:

```csharp
// Instead of: { role: "user", content: "describe this image" }
// Send:       { role: "user", content: [
//                { type: "text", text: "describe this image" },
//                { type: "image_url", image_url: { url: "data:image/png;base64,..." } }
//              ] }
```

The gateway already supports multimodal `GatewayMessage` with `ContentParts`. Use this format for vision-capable models. Check `ModelInfo.Capabilities` for `"vision"`.

#### D5. Inline image display in chat

When a user message contains images, show them inline in the message bubble:

```xml
<WrapPanel Margin="0,0,0,8">
    <ItemsControl ItemsSource="{Binding InlineImages}">
        <ItemsControl.ItemTemplate>
            <DataTemplate>
                <Border CornerRadius="8" Margin="0,0,6,6" Cursor="Hand"
                        MouseLeftButtonUp="ImagePreview_Click">
                    <Image Source="{Binding Source}" MaxWidth="200" MaxHeight="200"
                           Stretch="UniformToFill" />
                </Border>
            </DataTemplate>
        </ItemsControl.ItemTemplate>
    </ItemsControl>
</WrapPanel>
```

---

### Feature E: Tool-Use UI Polish

**What Claude Does**: Tool executions are shown as collapsible sections with: tool name + icon, input parameters, execution time, output (collapsed by default), retry button on error.

**Implementation**:

#### E1. Redesign ToolExecutionItemViewModel

```csharp
public partial class ToolExecutionItemViewModel : ObservableObject
{
    [ObservableProperty] private string _toolCallId;
    [ObservableProperty] private string _toolName;
    [ObservableProperty] private string _friendlyName;
    [ObservableProperty] private string _icon; // Geometry key
    [ObservableProperty] private string _inputSummary; // brief description of args
    [ObservableProperty] private string _inputJson; // full JSON for expand
    [ObservableProperty] private string? _result;
    [ObservableProperty] private string? _error;
    [ObservableProperty] private bool _isRunning;
    [ObservableProperty] private bool _isExpanded;
    [ObservableProperty] private TimeSpan _duration;
    [ObservableProperty] private BitmapSource? _screenshotPreview; // for screenshot results
}
```

#### E2. Tool execution card UI

Replace the current inline text with a proper card in `MessageBubble.xaml`:

```xml
<Border CornerRadius="10" BorderThickness="1"
        BorderBrush="{DynamicResource BorderBrush}"
        Background="{DynamicResource BgSecondaryBrush}"
        Padding="10,8" Margin="0,4">
    <StackPanel>
        <!-- Header: icon + name + duration + expand toggle -->
        <DockPanel>
            <ToggleButton DockPanel.Dock="Right" IsChecked="{Binding IsExpanded}"
                          Style="{DynamicResource IconToggle}">
                <controls:IconView Geometry="{StaticResource Geo_ChevronDown}" Width="12" Height="12" />
            </ToggleButton>
            <TextBlock DockPanel.Dock="Right" Text="{Binding DurationText}" FontSize="11"
                       Foreground="{DynamicResource TextTertiaryBrush}" Margin="0,0,8,0"
                       VerticalAlignment="Center" />
            <StackPanel Orientation="Horizontal">
                <controls:IconView Geometry="{Binding IconGeometry}" Width="14" Height="14"
                                   Stroke="{DynamicResource TextSecondaryBrush}" Margin="0,0,8,0" />
                <TextBlock Text="{Binding FriendlyName}" FontSize="13"
                           Foreground="{DynamicResource TextPrimaryBrush}" />
            </StackPanel>
        </DockPanel>

        <!-- Running indicator -->
        <ProgressBar Height="2" IsIndeterminate="True" Margin="0,6,0,0"
                     Visibility="{Binding IsRunning, Converter={StaticResource BoolToVisibility}}" />

        <!-- Expanded: input + output -->
        <StackPanel Margin="0,8,0,0"
                    Visibility="{Binding IsExpanded, Converter={StaticResource BoolToVisibility}}">
            <!-- Input -->
            <TextBlock Text="Input" FontSize="11" Foreground="{DynamicResource TextTertiaryBrush}" />
            <Border Background="{DynamicResource BgPrimaryBrush}" CornerRadius="6" Padding="8" Margin="0,4,0,8">
                <TextBlock Text="{Binding InputJson}" FontFamily="Consolas" FontSize="11"
                           TextWrapping="Wrap" Foreground="{DynamicResource TextSecondaryBrush}" />
            </Border>
            <!-- Output -->
            <TextBlock Text="Output" FontSize="11" Foreground="{DynamicResource TextTertiaryBrush}" />
            <Border Background="{DynamicResource BgPrimaryBrush}" CornerRadius="6" Padding="8" Margin="0,4,0,0">
                <TextBlock Text="{Binding Result}" FontFamily="Consolas" FontSize="11"
                           TextWrapping="Wrap" MaxHeight="200"
                           Foreground="{DynamicResource TextSecondaryBrush}" />
            </Border>
            <!-- Screenshot preview if applicable -->
            <Image Source="{Binding ScreenshotPreview}" MaxWidth="400" MaxHeight="300"
                   Margin="0,8,0,0" Stretch="Uniform"
                   Visibility="{Binding HasScreenshot, Converter={StaticResource BoolToVisibility}}" />
            <!-- Error + retry -->
            <StackPanel Orientation="Horizontal" Margin="0,4,0,0"
                        Visibility="{Binding HasError, Converter={StaticResource BoolToVisibility}}">
                <TextBlock Text="{Binding Error}" FontSize="12" Foreground="{DynamicResource AccentRedBrush}" />
                <Button Content="Retry" Style="{DynamicResource FlatRowButton}" Margin="8,0,0,0"
                        Command="{Binding RetryCommand}" />
            </StackPanel>
        </StackPanel>
    </StackPanel>
</Border>
```

#### E3. Tool icon mapping

```csharp
public static string ToolIcon(string toolName) => toolName switch
{
    "computer_screenshot" => "Geo_Monitor",
    "computer_click" or "computer_drag" => "Geo_Cursor",
    "computer_type" or "computer_key" => "Geo_Keyboard",
    "computer_scroll" => "Geo_ArrowsVertical",
    "file_read" or "read_file" => "Geo_FileText",
    "file_write" or "write_file" => "Geo_FilePlus",
    "file_search" or "search_files" => "Geo_Search",
    "run_command" => "Geo_Terminal",
    "web_search" => "Geo_Browser",
    "clipboard_read" or "clipboard_write" => "Geo_Copy",
    "generate_image" => "Geo_ImageBadge",
    "generate_video" => "Geo_VideoBadge",
    _ => "Geo_Tool"
};
```

---

### Feature F: Conversation Branching

**What Claude Does**: When you edit a previous message, Claude creates a branch. You can navigate between branches with left/right arrows.

**Implementation**:

#### F1. Data model

Add to `MessageViewModel`:
```csharp
[ObservableProperty] private int _branchIndex;
[ObservableProperty] private int _branchCount;
public bool HasBranches => BranchCount > 1;
```

#### F2. Branch storage

Store branches as sibling messages with the same `ParentMessageId`. When the user edits a message:
1. Keep the original message and its children as branch 0
2. Create a new message (same parent) as branch 1
3. Send the new content and generate new AI responses under branch 1

#### F3. Branch navigation UI

In `MessageBubble.xaml`, add branch arrows above edited user messages:

```xml
<StackPanel Orientation="Horizontal" HorizontalAlignment="Center" Margin="0,4,0,0"
            Visibility="{Binding HasBranches, Converter={StaticResource BoolToVisibility}}">
    <Button Style="{DynamicResource IconButton}" Command="{Binding PreviousBranchCommand}" Width="24" Height="24">
        <controls:IconView Geometry="{StaticResource Geo_ChevronLeft}" Width="12" Height="12" />
    </Button>
    <TextBlock FontSize="11" VerticalAlignment="Center" Margin="4,0"
               Foreground="{DynamicResource TextTertiaryBrush}">
        <Run Text="{Binding BranchIndex, Mode=OneWay}" />/<Run Text="{Binding BranchCount, Mode=OneWay}" />
    </TextBlock>
    <Button Style="{DynamicResource IconButton}" Command="{Binding NextBranchCommand}" Width="24" Height="24">
        <controls:IconView Geometry="{StaticResource Geo_ChevronRight}" Width="12" Height="12" />
    </Button>
</StackPanel>
```

#### F4. Edit flow

Modify the existing message edit command:
1. When user clicks edit on a message, show an inline text editor
2. On submit: create a new branch (don't delete old messages)
3. Re-send from that point with the edited content
4. Update `BranchCount` on the parent message
5. Display the new branch (latest branch selected by default)

---

## Phase 2 — Power Features (Features G–I)

### Feature G: Scheduled Tasks

**What Claude Does**: Users can say "remind me every morning" or "run this daily at 6am" and Claude creates a scheduled task that runs automatically.

**Implementation**:

#### G1. ScheduledTask model

```csharp
public record ScheduledTaskDto(
    string Id,
    string Name,
    string Prompt,           // The message to send
    string? ConversationId,  // Run in this conversation (or create new)
    string? ProjectId,       // Run in this project context
    string CronExpression,   // "0 6 * * *" = 6am daily
    DateTime? NextRunAt,
    DateTime? LastRunAt,
    bool IsEnabled,
    string? LastResult);
```

#### G2. Local scheduler service

Create `Infrastructure/Services/TaskSchedulerService.cs`:
- Uses a `DispatcherTimer` ticking every 60 seconds
- Checks all enabled tasks against current time using a cron parser (use `Cronos` NuGet package)
- When a task fires: create/open conversation, send the prompt, save the result
- Store tasks in SQLite (`IConversationStore` or new `IScheduledTaskStore`)
- Runs in background, even when main window is minimized to tray

#### G3. Task management UI

Create `Views/ScheduledTasksView.xaml`:
- List of tasks with: name, schedule description ("Every day at 6:00 AM"), next run, toggle on/off
- Create dialog: name, prompt text, schedule picker (preset: hourly/daily/weekly/monthly + custom cron)
- Edit/delete existing tasks
- "Run Now" button for manual trigger
- Show last result preview

#### G4. Sidebar entry

Add "Scheduled Tasks" to the settings menu with a clock icon and badge showing active task count.

---

### Feature H: Skills System

**What Claude Does**: Skills are installable capability modules (e.g., "Create Word docs", "Build Excel spreadsheets", "Generate PDFs"). Users can install skills from a marketplace. Skills provide specialized prompts + tool definitions.

**Implementation**:

#### H1. Skill definition format

A skill is a folder containing:
```
my-skill/
  SKILL.md          # Prompt/instructions for the AI
  manifest.json     # Metadata: name, description, version, triggers
  tools.json        # Optional: additional tool definitions
  templates/        # Optional: file templates
```

`manifest.json`:
```json
{
  "name": "docx",
  "displayName": "Word Document Creator",
  "description": "Create professional Word documents with formatting",
  "version": "1.0.0",
  "triggers": ["word doc", ".docx", "report", "memo", "letter"],
  "icon": "document",
  "author": "SRAN"
}
```

#### H2. Skill store

Store skills in `%APPDATA%/SranDesktop/skills/`. Built-in skills ship with the app. User-installed skills go in a separate subfolder.

#### H3. Skill injection

When the AI receives a message, check if any skill triggers match. If so, prepend the skill's `SKILL.md` content to the system prompt.

In `ChatOrchestrator.BuildSystemPrompt()`:
```csharp
var matchedSkills = _skillManager.FindMatchingSkills(userMessage);
foreach (var skill in matchedSkills)
    systemPrompt += $"\n\n--- Skill: {skill.DisplayName} ---\n{skill.Instructions}";
```

#### H4. Skill management UI

Create `Views/SkillsView.xaml`:
- Grid of installed skill cards (icon, name, description, toggle on/off)
- "Install Skill" → browse a `.skill` zip file or enter a URL
- "Create Skill" → opens a dialog with SKILL.md editor
- Each skill card has: enable/disable toggle, edit, delete

#### H5. Built-in skills (ship with app)

Package these as embedded resources or files in the install directory:
- `docx` — Word document creation
- `xlsx` — Excel spreadsheet creation
- `pdf` — PDF generation
- `pptx` — PowerPoint presentations
- `web-search` — Enhanced web search with source formatting

---

### Feature I: MCP Connector Marketplace

**What Claude Does**: A marketplace of 2,300+ remote MCP servers (Slack, Gmail, Google Drive, Notion, etc.) installable with one click. Each connector provides tools the AI can use.

**Implementation**:

#### I1. Connector registry

Create a connector registry (JSON endpoint or embedded catalog):

```json
{
  "connectors": [
    {
      "id": "slack",
      "name": "Slack",
      "description": "Send messages, read channels, search Slack workspace",
      "icon": "slack.png",
      "category": "communication",
      "auth_type": "oauth2",
      "server_url": "https://mcp.sran.ai/slack",
      "tools": ["slack_send_message", "slack_read_channel", "slack_search"]
    }
  ]
}
```

#### I2. Connector management

Create `Infrastructure/Services/McpConnectorService.cs`:
- `ListAvailableAsync()` — fetch from registry
- `InstallAsync(connectorId)` — save config, initiate OAuth if needed
- `UninstallAsync(connectorId)` — remove config
- `GetInstalledAsync()` — list installed connectors
- Each installed connector adds its tools to `IMcpToolHost.GetToolDefinitions()`
- Remote tool execution: proxy tool calls to the connector's server URL

#### I3. Marketplace UI

Create `Views/ConnectorMarketplaceView.xaml`:
- Search bar + category filter (Communication, Productivity, Developer, Data, etc.)
- Grid of connector cards: icon, name, description, "Install" / "Connected" button
- Installed tab: manage connections, disconnect, test

#### I4. OAuth flow

For connectors requiring OAuth:
1. Open system browser to `{server_url}/auth?redirect=sran://callback`
2. Register a custom URI scheme `sran://` for the WPF app
3. Receive the callback with auth token
4. Store token securely via `DpapiCredentialStore`

---

## Phase 3 — Companion Features (Features J–L)

### Feature J: Voice Input/Output

**Implementation**:

#### J1. Speech-to-text (input)

Use `System.Speech.Recognition` (Windows built-in) or Azure Speech SDK:

```csharp
// In ComposerControl — add a mic button
private SpeechRecognitionEngine? _recognizer;

private void StartListening()
{
    _recognizer = new SpeechRecognitionEngine();
    _recognizer.SetInputToDefaultAudioDevice();
    _recognizer.LoadGrammar(new DictationGrammar());
    _recognizer.SpeechRecognized += (s, e) =>
        Dispatcher.Invoke(() => AppendToInput(e.Result.Text));
    _recognizer.RecognizeAsync(RecognizeMode.Multiple);
}
```

Add mic button to composer toolbar (activate the greyed-out Voice Settings):
```xml
<ToggleButton IsChecked="{Binding IsListening}" ToolTip="{loc:T input.voice}">
    <controls:IconView Geometry="{StaticResource Geo_Mic}" Width="16" Height="16" />
</ToggleButton>
```

#### J2. Text-to-speech (output)

Use `System.Speech.Synthesis` or the gateway's TTS endpoint (model `gpt-4o-mini-tts` exists):

```csharp
// Read aloud button on assistant messages
[RelayCommand]
private async Task ReadAloudAsync(MessageViewModel message)
{
    using var synth = new SpeechSynthesizer();
    synth.SetOutputToDefaultAudioDevice();
    synth.SpeakAsync(message.Content);
}
```

Or for higher quality, call the gateway's TTS model and play the audio stream.

#### J3. Voice Settings dialog

Implement the greyed-out Voice Settings menu item:
- Voice selection (system voices list)
- Speed slider
- Auto-read responses toggle
- Language preference

---

### Feature K: Share Conversation

**Implementation**:

#### K1. Backend endpoint

```
POST /api/v1/conversations/:id/share → { share_url: "https://chat.sran.ai/s/abc123" }
DELETE /api/v1/conversations/:id/share → remove sharing
```

#### K2. Share button

Add to the conversation header toolbar:
```xml
<Button Command="{Binding Chat.ShareCommand}" ToolTip="{loc:T header.share}">
    <controls:IconView Geometry="{StaticResource Geo_Share}" Width="14" Height="14" />
</Button>
```

#### K3. Share dialog

Show a dialog with the share URL, copy button, and toggle for public/private.

---

### Feature L: Writing Style Profiles

**Implementation**:

#### L1. Style analysis

When the user edits AI drafts, analyze the changes to learn preferences:
- Tone (formal/casual)
- Sentence length
- Vocabulary level
- Formatting preferences

Store as a profile in settings:
```csharp
public record WritingStyleProfile(
    string Name,
    string Description,  // "Concise, technical, prefers bullet points"
    string SystemPrompt); // Generated prompt fragment
```

#### L2. Style injection

When generating writing (emails, documents, etc.), append the style profile to the system prompt.

#### L3. Setup flow

Create a "Learn My Style" dialog:
1. Ask the user to paste 3-5 samples of their writing
2. Analyze patterns using the AI
3. Generate a style profile
4. Save and apply automatically

---

## New Icons Needed

Add these geometry resources to `App.xaml` or your icon dictionary:

- `Geo_Monitor` — screen/display
- `Geo_Keyboard` — keyboard
- `Geo_ArrowsVertical` — vertical scroll
- `Geo_Terminal` — terminal/console
- `Geo_Tool` — generic tool
- `Geo_Calendar` — calendar (for scheduled tasks)
- `Geo_Puzzle` — puzzle piece (for skills/plugins)
- `Geo_Store` — marketplace/store
- `Geo_Share` — share icon
- `Geo_Fullscreen` — expand to fullscreen
- `Geo_ChevronLeft` — left navigation

---

## New i18n Keys (Dict.cs)

Add to both EN and TH dictionaries:

```csharp
// Artifacts
["artifact.copyCode"] = "Copy code" / "คัดลอกโค้ด",
["artifact.fullscreen"] = "Fullscreen" / "เต็มจอ",
["artifact.openExternal"] = "Open in browser" / "เปิดในเบราว์เซอร์",

// Projects
["sidebar.projects"] = "Projects" / "โปรเจกต์",
["project.create"] = "New project" / "โปรเจกต์ใหม่",
["project.instructions"] = "Custom instructions" / "คำสั่งพิเศษ",
["project.files"] = "Project files" / "ไฟล์โปรเจกต์",
["project.startChat"] = "Start chat" / "เริ่มแชท",

// Web Search
["input.webSearch"] = "Web search" / "ค้นหาเว็บ",

// Scheduled Tasks
["menu.scheduledTasks"] = "Scheduled tasks" / "งานที่ตั้งเวลา",
["task.create"] = "New task" / "สร้างงานใหม่",
["task.runNow"] = "Run now" / "รันเดี๋ยวนี้",
["task.schedule"] = "Schedule" / "ตารางเวลา",

// Skills
["menu.skills"] = "Skills" / "ทักษะ",
["skill.install"] = "Install" / "ติดตั้ง",
["skill.installed"] = "Installed" / "ติดตั้งแล้ว",

// Connectors
["menu.connectors"] = "Connectors" / "เชื่อมต่อ",
["connector.install"] = "Connect" / "เชื่อมต่อ",
["connector.connected"] = "Connected" / "เชื่อมต่อแล้ว",
["connector.marketplace"] = "Marketplace" / "ตลาดส่วนเสริม",

// Voice
["input.voice"] = "Voice input" / "พูดข้อความ",
["voice.readAloud"] = "Read aloud" / "อ่านออกเสียง",
["voice.settings"] = "Voice settings" / "ตั้งค่าเสียง",

// Share
["header.share"] = "Share" / "แชร์",
["share.copyLink"] = "Copy link" / "คัดลอกลิงก์",

// Branching
["message.branch"] = "Branch" / "สาขา",
["message.edit"] = "Edit" / "แก้ไข",

// Writing style
["style.learn"] = "Learn my style" / "เรียนรู้สไตล์เขียน",
["style.profile"] = "Writing style" / "สไตล์การเขียน",
```

---

## NuGet Packages to Add

```xml
<PackageReference Include="Microsoft.Web.WebView2" Version="1.*" />
<PackageReference Include="Cronos" Version="0.8.*" />
<!-- Optional for higher quality speech: -->
<!-- <PackageReference Include="Microsoft.CognitiveServices.Speech" Version="1.*" /> -->
```

For `System.Speech`, add framework reference:
```xml
<FrameworkReference Include="Microsoft.WindowsDesktop.App.WPF" />
<!-- System.Speech is part of .NET on Windows -->
```

---

## Verification Checklist

### Phase 1
- [ ] **Artifacts**: AI returns HTML/React → renders in WebView2 inline in chat
- [ ] **Artifacts**: Fullscreen toggle works
- [ ] **Artifacts**: Copy source code works
- [ ] **Artifacts**: Theme-aware (dark/light CSS injected)
- [ ] **Projects**: Create project with name + instructions
- [ ] **Projects**: Upload files to project
- [ ] **Projects**: Start conversation in project → instructions auto-injected
- [ ] **Projects**: Projects show in sidebar
- [ ] **Web Search**: Toggle in composer
- [ ] **Web Search**: AI calls web_search tool → results shown inline
- [ ] **Vision**: Paste image (Ctrl+V) → shows thumbnail preview
- [ ] **Vision**: Drag-drop image → attaches correctly
- [ ] **Vision**: Image sent as multimodal content to vision-capable model
- [ ] **Vision**: Image displayed inline in user message bubble
- [ ] **Tool UI**: Tool calls show as collapsible cards with icon + duration
- [ ] **Tool UI**: Expand shows input JSON + output
- [ ] **Tool UI**: Screenshot results show preview image
- [ ] **Branching**: Edit message → creates branch, not delete
- [ ] **Branching**: Navigate branches with left/right arrows
- [ ] **Branching**: Branch counter shows "1/2", "2/3", etc.

### Phase 2
- [ ] **Scheduled Tasks**: Create task with name + prompt + schedule
- [ ] **Scheduled Tasks**: Task fires on schedule (test with "every 1 minute")
- [ ] **Scheduled Tasks**: Task list shows next run time
- [ ] **Scheduled Tasks**: Toggle enable/disable
- [ ] **Skills**: Built-in skills (docx, xlsx, pdf, pptx) load from disk
- [ ] **Skills**: Skill triggers auto-match user messages
- [ ] **Skills**: Install custom skill from .skill file
- [ ] **Skills**: Skills management UI works
- [ ] **MCP Connectors**: Marketplace UI shows available connectors
- [ ] **MCP Connectors**: Install connector → tools appear in AI toolbox
- [ ] **MCP Connectors**: Uninstall removes tools

### Phase 3
- [ ] **Voice**: Mic button records speech → text appears in composer
- [ ] **Voice**: Read aloud button on assistant messages
- [ ] **Voice Settings**: Voice selection, speed, auto-read toggle
- [ ] **Share**: Share button generates link
- [ ] **Share**: Copy link works
- [ ] **Writing Style**: Learn from user samples
- [ ] **Writing Style**: Apply style to AI-generated writing

### General
- [ ] All new strings in Dict.cs (EN + TH)
- [ ] Dark and light themes work for all new UI
- [ ] Escape closes all new dialogs
- [ ] Build: 0 errors, 0 warnings
- [ ] All existing 126+ tests pass
- [ ] New features have basic unit tests

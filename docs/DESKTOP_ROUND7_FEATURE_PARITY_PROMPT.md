# SRAN Desktop — Round 7: Feature Parity Build Prompt

> **Target:** Claude Fable — build all 6 features in a single pass.
> **Stack:** .NET 8 · WPF · CommunityToolkit.Mvvm · Clean Architecture (App/Domain/Infrastructure/Presentation)
> **Backend:** chat-edge-api at `:4000/api/v1` (Go) — already has all endpoints. Desktop is a consumer only.

---

## Table of Contents

1. [Feature A — Image Generation (SSE → Placeholder → Rendered Image)](#feature-a)
2. [Feature B — Video Generation (SSE → Placeholder → Video Player)](#feature-b)
3. [Feature C — Document Plan Execution (Excel / DOCX / PDF / PPTX)](#feature-c)
4. [Feature D — RAG: Knowledge Base In-App Management](#feature-d)
5. [Feature E — Memory In-App Management](#feature-e)
6. [Feature F — i18n (Thai / English)](#feature-f)
7. [Bug Fix — Model Popup on Home Screen](#bug-fix)
8. [Verification Checklist](#verification)

---

<a id="feature-a"></a>
## Feature A — Image Generation

### Context

The desktop already has image/video **mode toggles** on the composer (ToggleButtons with `IsImageModeOn` / `IsVideoModeOn`). When toggled on, `BuildGatewayMessages()` appends `[IMAGE MODE]` / `[VIDEO MODE]` hints to the system prompt. This works for the **gateway path** (direct `/chat/completions`).

However, for the **chat-edge-api path** (`StreamMessageAsync`), the server has its own `generate_image` tool that the AI calls. The desktop currently **ignores** the `message_files` SSE event, so generated images never appear.

### What to build

#### A1. Handle `message_files` SSE event

In `ChatService.cs`, the SSE mapper currently has:
```csharp
// message_sources, message_files, memory events etc. are ignored for now.
_ => null
```

Add a new event type and handle `message_files`:

```csharp
// In ChatStreamEvents.cs:
public sealed record MessageFilesEvent(IReadOnlyList<MessageFileInfo> Files) : ChatStreamEvent;

public sealed record MessageFileInfo(
    string Id,
    string Filename,
    string ContentType,
    long SizeBytes,
    string? Url);

// In ChatService.cs MapFrame():
"message_files" when Parse<MessageFilesPayload>(frame) is { } p =>
    new MessageFilesEvent(p.Files.Select(f => new MessageFileInfo(
        f.Id, f.Filename, f.ContentType, f.SizeBytes, f.Url)).ToList()),
```

The SSE payload shape is:
```json
{
  "files": [
    {
      "id": "uuid",
      "filename": "generated_image_abc123.png",
      "content_type": "image/png",
      "size_bytes": 123456,
      "url": "/api/v1/files/uuid/download"
    }
  ]
}
```

#### A2. Image placeholder in chat

When `StreamStatusEvent("image", "started")` arrives, show a shimmer placeholder in the message bubble:

- Size: 256×256 (w:256, h:256), rounded corners (12px)
- Background: `BgSecondaryBrush` with 1px `BorderBrush` border
- Center: animated Lucide ImagePlus icon (pulse animation) + text "กำลังสร้างรูปภาพ..." + subtext "อาจใช้เวลาสักครู่"
- Use a `Storyboard` with `DoubleAnimation` on `Opacity` (0.4 → 1.0, AutoReverse, RepeatBehavior=Forever) for the pulse

Create `Controls/ImageGeneratingPlaceholder.xaml` (UserControl).

#### A3. Render generated images in message

When `MessageFilesEvent` arrives with `content_type` starting with `image/`:

1. Download the image from `{chatEdgeBaseUrl}{file.Url}` (with auth header)
2. Create a `BitmapImage` from the downloaded bytes
3. Display in the message bubble as an `Image` control (max 512×512, maintain aspect ratio)
4. Add a "Save" context menu item that opens a `SaveFileDialog`

In `ChatViewModel`, handle the event in the streaming loop:
```csharp
case MessageFilesEvent mf:
    foreach (var file in mf.Files)
    {
        if (file.ContentType.StartsWith("image/"))
        {
            var imageBytes = await DownloadFileAsync(file.Url, ct);
            var attachment = new InlineAttachmentViewModel(file.Filename, file.ContentType, imageBytes);
            assistantMessage.InlineAttachments.Add(attachment);
        }
    }
    // Clear the image placeholder
    assistantMessage.IsGeneratingImage = false;
    break;
```

#### A4. Add `image_mode` field to `StreamMessageAsync`

Currently `StreamMessageAsync` sends `{content, stream:true, file_ids?}`. Add `image_mode` boolean:

```csharp
// In IChatEdgeApiClient.cs — update signature:
IAsyncEnumerable<SseFrame> StreamMessageAsync(
    string conversationId, string content, IReadOnlyList<string>? fileIds = null,
    bool imageMode = false, bool videoMode = false,
    CancellationToken ct = default);

// In ChatEdgeApiClient.cs — add to the JSON body:
var body = new { content, stream = true, file_ids = fileIds, image_mode = imageMode, video_mode = videoMode };
```

Then in `ChatViewModel.RunTurnAsync()`, pass the mode flags:
```csharp
var stream = _chatService.StreamAsync(Conversation.Id, content, fileIds,
    imageMode: IsImageModeOn, videoMode: IsVideoModeOn, ct);
```

#### A5. InlineAttachmentViewModel

New ViewModel for rendered media inside a message bubble:

```csharp
public partial class InlineAttachmentViewModel : ObservableObject
{
    public string Filename { get; }
    public string ContentType { get; }
    public byte[] RawBytes { get; }
    public BitmapSource? ImageSource { get; }  // decoded for image/*
    public bool IsImage => ContentType.StartsWith("image/");
    public bool IsVideo => ContentType.StartsWith("video/");

    [RelayCommand]
    private void SaveAs() { /* SaveFileDialog + File.WriteAllBytes */ }
}
```

Add `ObservableCollection<InlineAttachmentViewModel> InlineAttachments` to `MessageViewModel`.

#### A6. Message bubble XAML for inline attachments

In the assistant message DataTemplate (likely in `ChatView.xaml` or `MessageBubble` area), after the markdown content, add:

```xml
<ItemsControl ItemsSource="{Binding InlineAttachments}"
              Margin="0,8,0,0">
    <ItemsControl.ItemTemplate>
        <DataTemplate>
            <!-- Image -->
            <Border Visibility="{Binding IsImage, Converter={StaticResource BoolToVisibility}}"
                    CornerRadius="12" Margin="0,4"
                    MaxWidth="512" MaxHeight="512"
                    HorizontalAlignment="Left">
                <Border.ContextMenu>
                    <ContextMenu>
                        <MenuItem Header="{loc:T Key=common.save}" Command="{Binding SaveAsCommand}" />
                    </ContextMenu>
                </Border.ContextMenu>
                <Image Source="{Binding ImageSource}"
                       Stretch="Uniform"
                       RenderOptions.BitmapScalingMode="HighQuality" />
            </Border>
            <!-- Video (see Feature B) -->
        </DataTemplate>
    </ItemsControl.ItemTemplate>
</ItemsControl>
```

---

<a id="feature-b"></a>
## Feature B — Video Generation

### What to build

#### B1. Handle `video_generating` SSE status events

Already mapped to `StreamStatusEvent("video", status)` in `ChatService.cs`. Handle in `ChatViewModel`:

```csharp
case StreamStatusEvent { Kind: "video", Status: "started" }:
    assistantMessage.IsGeneratingVideo = true;
    break;
```

#### B2. Video placeholder

Create `Controls/VideoGeneratingPlaceholder.xaml`:

- Size: 320×180 (16:9 aspect), rounded corners
- Same shimmer animation as image but with Lucide Clapperboard icon
- Text: "กำลังสร้างวิดีโอ..." + "อาจใช้เวลาสักครู่"
- Optional: progress bar if the SSE status payload includes percentage

#### B3. Render generated videos

When `MessageFilesEvent` arrives with `content_type` starting with `video/`:

1. Download to a temp file (videos are too large for in-memory BitmapSource)
2. Display using WPF `MediaElement` inside the message bubble
3. Add play/pause overlay button, progress slider, and "Save" context menu

```xml
<Grid Visibility="{Binding IsVideo, Converter={StaticResource BoolToVisibility}}"
      MaxWidth="480" HorizontalAlignment="Left"
      Margin="0,4">
    <MediaElement x:Name="VideoPlayer"
                  Source="{Binding VideoFilePath}"
                  LoadedBehavior="Manual"
                  Stretch="Uniform" />
    <!-- Play/pause overlay -->
    <Button Style="{DynamicResource TransparentButton}"
            HorizontalAlignment="Center" VerticalAlignment="Center"
            Command="{Binding TogglePlayCommand}">
        <controls:IconView Geometry="{Binding PlayPauseIcon}"
                           Width="32" Height="32"
                           Fill="White" Opacity="0.8" />
    </Button>
</Grid>
```

For the `InlineAttachmentViewModel`, add:
```csharp
public string? VideoFilePath { get; set; }  // temp file path for MediaElement

[ObservableProperty] private bool _isPlaying;

[RelayCommand]
private void TogglePlay() { /* MediaElement.Play() / Pause() via messenger */ }
```

#### B4. Video mode in `StreamMessageAsync`

Already covered in A4 — the `video_mode` field is sent alongside `image_mode`.

---

<a id="feature-c"></a>
## Feature C — Document Plan Execution

### Context

The web chat uses structured plan cards: the AI outputs fenced JSON blocks (`:::excel-plan`, `:::doc-plan`, `:::pptx-plan`) which the frontend renders as interactive cards with an "Execute" button. The desktop needs to:

1. Parse these fenced blocks from the streamed markdown
2. Render plan cards with action summaries
3. Call the execution API endpoint
4. Display result cards with download buttons

### What to build

#### C1. Plan block parser

Add a post-processing step in the markdown content that detects plan blocks:

```
:::excel-plan
{ "operation": "...", "source_file_id": "...", "actions": [...] }
:::
```

Parse these into structured ViewModels:

```csharp
public class PlanBlockViewModel : ObservableObject
{
    public string PlanType { get; }        // "excel", "doc", "pptx"
    public string RawJson { get; }
    public string? Description { get; }
    public string? OutputFilename { get; }
    public List<PlanActionSummary> Actions { get; }
    public bool IsExecuting { get; set; }
    public PlanResultViewModel? Result { get; set; }

    [RelayCommand]
    private async Task ExecuteAsync() { /* call API */ }
}
```

#### C2. Plan card XAML control

Create `Controls/PlanCard.xaml`:

```
┌─────────────────────────────────────┐
│ 📊 Excel Edit Plan                  │
│ "Update Q3 revenue figures"         │
│                                     │
│ Changes (5 actions):                │
│  • Create sheet "Q3 Summary"        │
│  • Write cells A1:D10               │
│  • Create chart "Revenue Trend"     │
│  • Format range A1:D1 (bold)        │
│  • Auto filter A1:D10              │
│                                     │
│            [ Execute Plan ]          │
└─────────────────────────────────────┘
```

- Header: file type icon + plan type name
- Body: description (italic) + numbered action list with type-specific icons
- Footer: "Execute Plan" / "Generate Document" button (accent color)
- While executing: show progress ring + "Executing..." text
- After execution: replace with result card

#### C3. Execution API calls

Add to `IChatEdgeApiClient`:

```csharp
Task<PlanExecutionResult> ExecuteExcelPlanAsync(
    string conversationId, JsonElement plan, CancellationToken ct = default);

Task<PlanExecutionResult> ExecuteDocumentPlanAsync(
    string conversationId, JsonElement plan, CancellationToken ct = default);
```

Endpoints:
- `POST /api/v1/conversations/{id}/excel/execute` — body: the plan JSON
- `POST /api/v1/conversations/{id}/document/execute` — body: the plan JSON

Response shape:
```json
{
  "file_id": "uuid",
  "filename": "output.xlsx",
  "version": 2,
  "file_size_bytes": 12345,
  "sheet_count": 3,
  "chart_count": 1,
  "changes": ["Created sheet 'Q3'", "Wrote 40 cells", ...],
  "validation": { "status": "valid", "issues": [] },
  "download_url": "/api/v1/files/uuid/download"
}
```

#### C4. Result card

After execution succeeds, replace the "Execute" button with a result card:

```
┌─────────────────────────────────────┐
│ ✅ output.xlsx (v2)                 │
│ 12.3 KB · 3 sheets · 1 chart       │
│                                     │
│ Changes:                            │
│  ✓ Created sheet "Q3 Summary"       │
│  ✓ Wrote 40 cells                   │
│  ✓ Created chart "Revenue Trend"    │
│                                     │
│          [ Download File ]           │
└─────────────────────────────────────┘
```

The "Download File" button fetches `{chatEdgeBaseUrl}{download_url}` and opens a `SaveFileDialog`.

#### C5. Plan type recognition

The AI outputs plans in fenced blocks. Detect them in the streamed content:

```csharp
private static readonly Regex PlanBlockRegex = new(
    @"^:::(\w+-plan)\s*\n([\s\S]*?)\n:::\s*$",
    RegexOptions.Multiline);
```

In the markdown renderer, intercept these blocks and replace with `PlanCard` controls.

---

<a id="feature-d"></a>
## Feature D — RAG: Knowledge Base In-App Management

### Context

Currently, clicking "Knowledge Base" in the settings menu opens the web browser. The desktop needs its own KB management UI. The API endpoints already exist.

### What to build

#### D1. KB management page (as a dialog or panel)

Create `Views/KnowledgeBaseView.xaml` — a modal dialog or side panel (like SettingsView):

```
┌─────────────────────────────────────────────┐
│ ← Knowledge Base                             │
│ จัดการฐานความรู้สำหรับ RAG                      │
│                                              │
│ [ + Create Knowledge Base ]                  │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ 📚 Company Policies                      │ │
│ │ HR and compliance documents              │ │
│ │ 5 documents · 1,234 chunks · Active      │ │
│ │                          [ Manage ]       │ │
│ └──────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────┐ │
│ │ 📚 Product FAQ                           │ │
│ │ Customer-facing FAQ answers              │ │
│ │ 3 documents · 456 chunks · Active        │ │
│ │                          [ Manage ]       │ │
│ └──────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

#### D2. KnowledgeBaseViewModel

```csharp
public partial class KnowledgeBaseViewModel : ObservableObject
{
    public ObservableCollection<KnowledgeBaseItemViewModel> KnowledgeBases { get; } = new();

    [RelayCommand] private async Task LoadAsync();
    [RelayCommand] private async Task CreateAsync();
}

public partial class KnowledgeBaseItemViewModel : ObservableObject
{
    public string Id { get; }
    public string Name { get; set; }
    public string? Description { get; set; }
    public int DocumentCount { get; set; }
    public int TotalChunks { get; set; }
    public bool IsActive { get; set; }
    public ObservableCollection<KbDocumentViewModel> Documents { get; } = new();

    [RelayCommand] private async Task ToggleActiveAsync();
    [RelayCommand] private async Task DeleteAsync();
    [RelayCommand] private async Task UploadDocumentAsync();  // OpenFileDialog
    [RelayCommand] private async Task LoadDocumentsAsync();
}
```

#### D3. KB detail / document management panel

When clicking "Manage" on a KB, expand or navigate to show documents:

```
│ 📚 Company Policies (expanded)              │
│                                              │
│ Documents:                                   │
│ [ Upload Document ]                          │
│                                              │
│  📄 handbook.pdf   120 KB  234 chunks ✅     │
│  📄 policy.docx     45 KB   89 chunks ✅     │
│  📄 benefits.csv    12 KB   23 chunks ⚠️     │
│                                              │
│  Each row: filename, size, chunk count,      │
│  status pill (indexed/processing/failed),    │
│  [Reindex] [Delete] buttons                  │
```

#### D4. API client additions

Add to `IChatEdgeApiClient`:

```csharp
// Knowledge Base CRUD
Task<KnowledgeBaseDto> CreateKnowledgeBaseAsync(string name, string? description,
    int chunkSize = 512, int chunkOverlap = 50, CancellationToken ct = default);
Task<KnowledgeBaseDetailResponse> GetKnowledgeBaseAsync(string id, CancellationToken ct = default);
Task<KnowledgeBaseDto> UpdateKnowledgeBaseAsync(string id, string? name, string? description,
    bool? isActive, CancellationToken ct = default);
Task DeleteKnowledgeBaseAsync(string id, CancellationToken ct = default);

// Document management
Task<KbDocumentDto> UploadKbDocumentAsync(string kbId, Stream content, string fileName,
    string contentType, IProgress<double>? progress = null, CancellationToken ct = default);
Task DeleteKbDocumentAsync(string kbId, string docId, CancellationToken ct = default);
Task<KbDocumentDto> ReindexKbDocumentAsync(string kbId, string docId, CancellationToken ct = default);
```

Endpoints:
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/knowledge-bases` | Create KB |
| GET | `/api/v1/knowledge-bases` | List KBs (already exists) |
| GET | `/api/v1/knowledge-bases/{id}` | Get KB + docs |
| PATCH | `/api/v1/knowledge-bases/{id}` | Update KB |
| DELETE | `/api/v1/knowledge-bases/{id}` | Delete KB |
| POST | `/api/v1/knowledge-bases/{id}/documents` | Upload doc (multipart) |
| DELETE | `/api/v1/knowledge-bases/{id}/documents/{docId}` | Delete doc |
| POST | `/api/v1/knowledge-bases/{id}/documents/{docId}/reindex` | Reindex doc |

#### D5. Wire the settings menu

Change `OpenKnowledgeBase_Click` in `MainWindow.xaml.cs` from opening a browser to opening the new `KnowledgeBaseView` dialog.

---

<a id="feature-e"></a>
## Feature E — Memory In-App Management

### Context

Currently, the Memory settings menu item opens the browser. The desktop needs a full memory management UI.

### What to build

#### E1. Memory management page

Create `Views/MemoryView.xaml` — a dialog similar to KnowledgeBaseView:

```
┌──────────────────────────────────────────────────┐
│ ← Memory                                         │
│ ระบบจะจำข้อมูลสำคัญจากบทสนทนาโดยอัตโนมัติ           │
│                                                   │
│ ┌─ Suggested Preferences (2 pending) ───────────┐ │
│ │ "User prefers Thai language responses"         │ │
│ │                     [ ✓ Confirm ] [ ✗ Reject ] │ │
│ │ "User works in HR department"                  │ │
│ │                     [ ✓ Confirm ] [ ✗ Reject ] │ │
│ └────────────────────────────────────────────────┘ │
│                                                   │
│ Saved Memories (12)            [ + Add Memory ]   │
│ 🔍 [Search memories...]                           │
│                                                   │
│ ┌────────────────────────────────────────────────┐ │
│ │ 📌 User's name is Somchai                     │ │
│ │    fact · saved 3 days ago · used 5 times      │ │
│ │                    [ Edit ] [ Unpin ] [ Delete ]│ │
│ ├────────────────────────────────────────────────┤ │
│ │ Prefers concise responses                      │ │
│ │    preference · saved 1 week ago · used 2 times│ │
│ │                      [ Edit ] [ Pin ] [ Delete ]│ │
│ └────────────────────────────────────────────────┘ │
│                                                   │
│ ── Data Management ──                             │
│ [ Export Memories (JSON) ]                        │
│                                                   │
│ ── Danger Zone ──                                 │
│ [ Clear All Memories ]                            │
│ [ Permanently Purge (GDPR) ]                      │
└──────────────────────────────────────────────────┘
```

#### E2. MemoryViewModel

```csharp
public partial class MemoryViewModel : ObservableObject
{
    public ObservableCollection<MemoryItemViewModel> Memories { get; } = new();
    public ObservableCollection<MemoryItemViewModel> PendingMemories { get; } = new();

    [ObservableProperty] private string _searchText = string.Empty;
    [ObservableProperty] private int _totalCount;
    [ObservableProperty] private int _pendingCount;

    [RelayCommand] private async Task LoadAsync();
    [RelayCommand] private async Task AddMemoryAsync();      // opens form dialog
    [RelayCommand] private async Task ExportAsync();          // SaveFileDialog → JSON
    [RelayCommand] private async Task ClearAllAsync();        // confirmation dialog
    [RelayCommand] private async Task PurgeAsync();           // GDPR — type confirmation phrase

    partial void OnSearchTextChanged(string value) => _ = FilterAsync();
}

public partial class MemoryItemViewModel : ObservableObject
{
    public string Id { get; }
    public string Content { get; set; }
    public string MemoryType { get; set; }       // fact, preference, interest, instruction
    public string? Category { get; set; }
    public bool IsPinned { get; set; }
    public string Status { get; set; }           // active, pending_confirmation
    public DateTime CreatedAt { get; set; }
    public int UsageCount { get; set; }

    [RelayCommand] private async Task EditAsync();
    [RelayCommand] private async Task TogglePinAsync();
    [RelayCommand] private async Task DeleteAsync();
    [RelayCommand] private async Task ConfirmAsync();        // for pending
    [RelayCommand] private async Task RejectAsync();         // for pending
}
```

#### E3. Memory form dialog

Create `Views/MemoryFormDialog.xaml` for create/edit:

- Content: multiline TextBox (max 1500 chars, show counter)
- Memory type: ComboBox with options: Fact, Preference, Interest, Instruction
- Category: optional TextBox
- Buttons: Cancel / Save

#### E4. API client additions

Add to `IChatEdgeApiClient`:

```csharp
// Memory CRUD
Task<MemoryListResponse> ListMemoriesAsync(string? scopeType = null,
    string? memoryType = null, string? status = null, int limit = 100,
    CancellationToken ct = default);
Task<MemoryDto> CreateMemoryAsync(string content, string memoryType,
    string? category = null, CancellationToken ct = default);
Task<MemoryDto> UpdateMemoryAsync(string id, string? content = null,
    string? memoryType = null, string? category = null, CancellationToken ct = default);
Task DeleteMemoryAsync(string id, CancellationToken ct = default);
Task<MemoryDto> PinMemoryAsync(string id, bool pinned, CancellationToken ct = default);
Task<MemoryDto> ConfirmMemoryAsync(string id, bool confirmed, CancellationToken ct = default);
Task<BulkDeleteResponse> ClearAllMemoriesAsync(CancellationToken ct = default);
Task<int> GetPendingMemoryCountAsync(CancellationToken ct = default);
Task<string> ExportMemoriesAsync(CancellationToken ct = default);  // returns JSON string
Task PurgeMemoriesAsync(CancellationToken ct = default);           // GDPR permanent delete
```

Endpoints:
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/memories` | List (query: scope_type, memory_type, status, limit, offset) |
| POST | `/api/v1/memories` | Create |
| PUT | `/api/v1/memories/{id}` | Update |
| DELETE | `/api/v1/memories/{id}` | Soft delete |
| POST | `/api/v1/memories/{id}/pin` | Pin/unpin — body: `{"pinned": bool}` |
| POST | `/api/v1/memories/{id}/confirm` | Confirm/reject — body: `{"confirmed": bool}` |
| DELETE | `/api/v1/memories/all` | Bulk soft delete |
| GET | `/api/v1/memories/pending-count` | Returns `{"count": int}` |
| POST | `/api/v1/account/export-memories` | Returns JSON export |
| DELETE | `/api/v1/account/purge-memories` | GDPR permanent purge |

#### E5. Pending badge on Memory header button

In `MainWindow.xaml`, the Memory button in the header should show a small badge with pending count:

```xml
<Grid>
    <Button ... Click="OpenMemory_Click">
        <StackPanel Orientation="Horizontal">
            <controls:IconView Geometry="{StaticResource Geo_Brain}" ... />
            <TextBlock Text="Memory" ... />
        </StackPanel>
    </Button>
    <!-- Pending badge -->
    <Border Background="{DynamicResource AccentBrush}" CornerRadius="8"
            Padding="4,1" HorizontalAlignment="Right" VerticalAlignment="Top"
            Margin="0,-4,-4,0"
            Visibility="{Binding Chat.HasPendingMemories, Converter={StaticResource BoolToVisibility}}">
        <TextBlock Text="{Binding Chat.PendingMemoryCount}" FontSize="10"
                   Foreground="White" FontWeight="SemiBold" />
    </Border>
</Grid>
```

Poll pending count every 2 minutes (like the web does).

#### E6. Wire the settings menu

Change `OpenMemory_Click` in `MainWindow.xaml.cs` from opening a browser to opening the new `MemoryView` dialog.

---

<a id="feature-f"></a>
## Feature F — i18n (Thai / English)

### Architecture

Follow the same pattern as the web: a single dictionary file with compile-time key safety, a service that manages locale, and a markup extension for XAML bindings.

#### F1. Translation dictionary

Create `SranDesktop.Presentation/Localization/Dict.cs`:

```csharp
public static class Dict
{
    public static readonly IReadOnlyDictionary<string, string> En = new Dictionary<string, string>
    {
        // Sidebar
        ["sidebar.newChat"] = "New chat",
        ["sidebar.searchChats"] = "Search chats",
        ["sidebar.empty"] = "No conversations yet",
        ["sidebar.group.pinned"] = "PINNED",
        ["sidebar.group.today"] = "TODAY",
        ["sidebar.group.yesterday"] = "YESTERDAY",
        ["sidebar.group.last7"] = "PREVIOUS 7 DAYS",
        ["sidebar.group.last30"] = "PREVIOUS 30 DAYS",
        ["sidebar.group.older"] = "OLDER",

        // Menu
        ["menu.theme.dark"] = "Dark mode",
        ["menu.theme.light"] = "Light mode",
        ["menu.language"] = "Language",
        ["menu.knowledgeBase"] = "Knowledge Base",
        ["menu.voiceSettings"] = "Voice Settings",
        ["menu.logout"] = "Log out",

        // Empty state
        ["empty.greet.morning"] = "Good morning",
        ["empty.greet.afternoon"] = "Good afternoon",
        ["empty.greet.evening"] = "Good evening",
        ["empty.greet.lateNight"] = "Good evening",
        ["empty.greet.midnight"] = "Late night?",
        ["empty.subtitle"] = "How can I help you today?",
        ["empty.chip.write"] = "Write",
        ["empty.chip.learn"] = "Learn",
        ["empty.chip.code"] = "Code",
        ["empty.chip.analyze"] = "Analyze",

        // Input
        ["input.placeholder"] = "Message SRAN Chat...",
        ["input.placeholder.streaming"] = "Generating response...",
        ["input.attach"] = "Attach files",
        ["input.imageMode"] = "Generate image",
        ["input.videoMode"] = "Generate video",
        ["input.send"] = "Send message",
        ["input.stop"] = "Stop generating",
        ["input.footer"] = "SRAN Chat can make mistakes. Check important info.",

        // Conversation
        ["conv.rename"] = "Rename",
        ["conv.delete"] = "Delete",
        ["conv.pin"] = "Pin",
        ["conv.unpin"] = "Unpin",
        ["conv.moveToFolder"] = "Move to folder",
        ["conv.removeFromFolder"] = "Remove from folder",
        ["folder.new"] = "New folder",
        ["folder.rename"] = "Rename folder",
        ["folder.delete"] = "Delete folder",
        ["folder.defaultName"] = "New Folder",
        ["common.cancel"] = "Cancel",

        // Model selector
        ["model.auto.name"] = "Auto",
        ["model.auto.description"] = "Selects the best model automatically",
        ["model.searchPlaceholder"] = "Search models...",
        ["model.popular"] = "POPULAR",
        ["model.recent"] = "RECENTLY USED",

        // Memory
        ["memory.title"] = "Memory",
        ["memory.description"] = "Memories are automatically saved from your conversations",
        ["memory.addMemory"] = "Add Memory",
        ["memory.searchPlaceholder"] = "Search memories...",
        ["memory.suggested.title"] = "Suggested Preferences",
        ["memory.export.button"] = "Export Memories (JSON)",
        ["memory.danger.clearButton"] = "Clear All Memories",
        ["memory.danger.purgeButton"] = "Permanently Purge (GDPR)",

        // Knowledge Base
        ["kb.title"] = "Knowledge Base",
        ["kb.subtitle"] = "Manage knowledge bases for RAG",
        ["kb.create"] = "Create Knowledge Base",
        ["kb.card.manage"] = "Manage",
        ["kb.unit.document"] = "document",
        ["kb.unit.documents"] = "documents",
        ["kb.unit.chunk"] = "chunk",
        ["kb.unit.chunks"] = "chunks",

        // ... (add all ~213 keys from web dict.ts — see reference)
    };

    public static readonly IReadOnlyDictionary<string, string> Th = new Dictionary<string, string>
    {
        // Sidebar
        ["sidebar.newChat"] = "แชทใหม่",
        ["sidebar.searchChats"] = "ค้นหาแชท",
        ["sidebar.empty"] = "ยังไม่มีบทสนทนา",
        ["sidebar.group.pinned"] = "ปักหมุด",
        ["sidebar.group.today"] = "วันนี้",
        ["sidebar.group.yesterday"] = "เมื่อวาน",
        ["sidebar.group.last7"] = "7 วันที่แล้ว",
        ["sidebar.group.last30"] = "30 วันที่แล้ว",
        ["sidebar.group.older"] = "เก่ากว่า",

        // Menu
        ["menu.theme.dark"] = "โหมดมืด",
        ["menu.theme.light"] = "โหมดสว่าง",
        ["menu.language"] = "ภาษา",
        ["menu.knowledgeBase"] = "ฐานความรู้",
        ["menu.voiceSettings"] = "ตั้งค่าเสียง",
        ["menu.logout"] = "ออกจากระบบ",

        // Empty state
        ["empty.greet.morning"] = "สวัสดีตอนเช้า",
        ["empty.greet.afternoon"] = "สวัสดีตอนบ่าย",
        ["empty.greet.evening"] = "สวัสดีตอนเย็น",
        ["empty.greet.lateNight"] = "สวัสดีตอนค่ำ",
        ["empty.greet.midnight"] = "ดึกแล้วนะ",
        ["empty.subtitle"] = "วันนี้ให้ช่วยอะไรดี?",
        ["empty.chip.write"] = "เขียน",
        ["empty.chip.learn"] = "เรียนรู้",
        ["empty.chip.code"] = "โค้ด",
        ["empty.chip.analyze"] = "วิเคราะห์",

        // Input
        ["input.placeholder"] = "พิมพ์ข้อความ...",
        ["input.placeholder.streaming"] = "กำลังสร้างคำตอบ...",
        ["input.attach"] = "แนบไฟล์",
        ["input.imageMode"] = "สร้างรูปภาพ",
        ["input.videoMode"] = "สร้างวิดีโอ",
        ["input.send"] = "ส่งข้อความ",
        ["input.stop"] = "หยุดสร้าง",
        ["input.footer"] = "SRAN Chat อาจผิดพลาดได้ กรุณาตรวจสอบข้อมูลสำคัญ",

        // Conversation
        ["conv.rename"] = "เปลี่ยนชื่อ",
        ["conv.delete"] = "ลบ",
        ["conv.pin"] = "ปักหมุด",
        ["conv.unpin"] = "เลิกปักหมุด",
        ["conv.moveToFolder"] = "ย้ายไปโฟลเดอร์",
        ["conv.removeFromFolder"] = "นำออกจากโฟลเดอร์",
        ["folder.new"] = "โฟลเดอร์ใหม่",
        ["folder.rename"] = "เปลี่ยนชื่อโฟลเดอร์",
        ["folder.delete"] = "ลบโฟลเดอร์",
        ["folder.defaultName"] = "โฟลเดอร์ใหม่",
        ["common.cancel"] = "ยกเลิก",

        // Model selector
        ["model.auto.name"] = "อัตโนมัติ",
        ["model.auto.description"] = "เลือกโมเดลที่เหมาะสมอัตโนมัติ",
        ["model.searchPlaceholder"] = "ค้นหาโมเดล...",
        ["model.popular"] = "ยอดนิยม",
        ["model.recent"] = "ใช้ล่าสุด",

        // Memory
        ["memory.title"] = "ความจำ",
        ["memory.description"] = "ระบบจะจำข้อมูลสำคัญจากบทสนทนาโดยอัตโนมัติ",
        ["memory.addMemory"] = "เพิ่มความจำ",
        ["memory.searchPlaceholder"] = "ค้นหาความจำ...",
        ["memory.suggested.title"] = "การตั้งค่าที่แนะนำ",
        ["memory.export.button"] = "ส่งออกความจำ (JSON)",
        ["memory.danger.clearButton"] = "ล้างความจำทั้งหมด",
        ["memory.danger.purgeButton"] = "ลบถาวร (GDPR)",

        // Knowledge Base
        ["kb.title"] = "ฐานความรู้",
        ["kb.subtitle"] = "จัดการฐานความรู้สำหรับ RAG",
        ["kb.create"] = "สร้างฐานความรู้",
        ["kb.card.manage"] = "จัดการ",
        ["kb.unit.document"] = "เอกสาร",
        ["kb.unit.documents"] = "เอกสาร",
        ["kb.unit.chunk"] = "ชิ้น",
        ["kb.unit.chunks"] = "ชิ้น",

        // ... (mirror all English keys 1:1)
    };
}
```

**CRITICAL:** Every key in `En` MUST exist in `Th` and vice versa. Add a unit test that asserts key-set equality.

#### F2. Locale service

Create `SranDesktop.Presentation/Localization/LocaleService.cs`:

```csharp
public sealed class LocaleService : ObservableObject
{
    private static readonly Lazy<LocaleService> _instance = new(() => new());
    public static LocaleService Instance => _instance.Value;

    [ObservableProperty] private string _locale;

    public IReadOnlyDictionary<string, string> ActiveDict =>
        _locale == "th" ? Dict.Th : Dict.En;

    public string T(string key, params object[] args)
    {
        if (!ActiveDict.TryGetValue(key, out var template))
            return Dict.En.TryGetValue(key, out var fallback) ? fallback : key;
        return args.Length > 0 ? string.Format(template, args) : template;
    }

    public LocaleService()
    {
        // Auto-detect: check saved setting, then CultureInfo
        _locale = LoadSavedLocale() ?? DetectLocale();
    }

    partial void OnLocaleChanged(string value)
    {
        SaveLocale(value);
        OnPropertyChanged(nameof(ActiveDict));
        LocaleChanged?.Invoke(this, value);
    }

    public event EventHandler<string>? LocaleChanged;

    private string DetectLocale() =>
        CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "th" ? "th" : "en";

    private string? LoadSavedLocale() { /* read from UserSettings */ }
    private void SaveLocale(string locale) { /* save to UserSettings */ }
}
```

#### F3. XAML markup extension

Create `SranDesktop.Presentation/Localization/TExtension.cs`:

```csharp
[MarkupExtensionReturnType(typeof(string))]
public class TExtension : MarkupExtension
{
    public string Key { get; set; }

    public TExtension(string key) => Key = key;

    public override object ProvideValue(IServiceProvider serviceProvider)
    {
        var binding = new Binding(nameof(LocaleService.ActiveDict) + $"[{Key}]")
        {
            Source = LocaleService.Instance,
            FallbackValue = Key,
            Mode = BindingMode.OneWay
        };
        return binding.ProvideValue(serviceProvider);
    }
}
```

Usage in XAML:
```xml
xmlns:loc="clr-namespace:SranDesktop.Presentation.Localization"

<TextBlock Text="{loc:T sidebar.newChat}" />
<Button ToolTip="{loc:T input.attach}" />
```

#### F4. Language toggle in settings menu

Replace the static "EN" badge in MainWindow.xaml's user menu with a working toggle:

```xml
<Button Click="ToggleLanguage_Click" Style="{DynamicResource FlatRowButton}">
    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto" />
            <ColumnDefinition Width="*" />
            <ColumnDefinition Width="Auto" />
        </Grid.ColumnDefinitions>
        <controls:IconView Geometry="{StaticResource Geo_Globe}" ... />
        <TextBlock Grid.Column="1" Text="{loc:T menu.language}" ... />
        <Border Grid.Column="2" CornerRadius="4" Padding="6,2"
                Background="{DynamicResource BgSelectedBrush}">
            <TextBlock Text="{Binding Source={x:Static loc:LocaleService.Instance}, Path=Locale}"
                       FontSize="12" FontWeight="SemiBold"
                       Foreground="{DynamicResource TextSecondaryBrush}"
                       CharacterCasing="Upper" />
        </Border>
    </Grid>
</Button>
```

Code-behind:
```csharp
private void ToggleLanguage_Click(object sender, RoutedEventArgs e)
{
    var svc = LocaleService.Instance;
    svc.Locale = svc.Locale == "en" ? "th" : "en";
}
```

#### F5. Apply to all UI strings

Replace ALL hardcoded strings in XAML files with `{loc:T key}` bindings. Priority files:

1. `MainWindow.xaml` — sidebar headers, menu items, greeting, chips, footer
2. `ComposerControl.xaml` — placeholder, tooltips, button labels
3. `ChatView.xaml` — message area labels
4. `SettingsView.xaml` — all setting labels
5. `MemoryView.xaml` (new) — all labels
6. `KnowledgeBaseView.xaml` (new) — all labels
7. Context menus — rename, delete, pin, folder operations

For code-behind strings (like toast messages, error messages), use `LocaleService.Instance.T("key")`.

#### F6. Persist locale in UserSettings

Add `Locale` field to `UserSettings`:

```csharp
public string Locale { get; set; } = "en";
```

---

<a id="bug-fix"></a>
## Bug Fix — Model Popup on Home Screen

### Problem

The model selector popup does not open on the home screen (no active conversation). Root cause: two `ComposerControl` instances (home screen + ChatView) share the same `ChatViewModel`. The popup's `IsOpen` TwoWay binding to `IsModelPopupOpen` conflicts between the two instances.

### Fix (already partially applied — verify and complete)

**ComposerControl.xaml** — Remove the `Popup.Style` block with its TwoWay binding and DataTrigger. The popup's `IsOpen` is managed entirely in code-behind:

```xml
<!-- REMOVE this entire block: -->
<Popup.Style>
    <Style TargetType="Popup">
        <Setter Property="IsOpen" Value="{Binding IsModelPopupOpen, Mode=TwoWay}" />
        <Style.Triggers>
            <DataTrigger Binding="{Binding IsVisible, ...}" Value="False">
                <Setter Property="IsOpen" Value="False" />
            </DataTrigger>
        </Style.Triggers>
    </Style>
</Popup.Style>
```

**ComposerControl.xaml.cs** — Subscribe to `PropertyChanged` on the ViewModel. Only the visible instance opens its popup:

```csharp
private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
{
    if (e.PropertyName != nameof(ChatViewModel.IsModelPopupOpen)) return;
    if (sender is not ChatViewModel vm) return;

    if (vm.IsModelPopupOpen && IsVisible)
        ModelSelectorPopup.IsOpen = true;
    else if (!vm.IsModelPopupOpen)
        ModelSelectorPopup.IsOpen = false;
}

private void ModelSelectorPopup_Closed(object? sender, EventArgs e)
{
    if (DataContext is ChatViewModel vm && vm.IsModelPopupOpen)
        vm.IsModelPopupOpen = false;
}
```

---

<a id="verification"></a>
## Verification Checklist

### Image Generation
- [ ] Send "draw me a cat" → shimmer placeholder appears → image renders in bubble
- [ ] Right-click image → "Save" → SaveFileDialog works
- [ ] Toggle image mode on composer → send message → forces image generation
- [ ] Image placeholder shows Thai text "กำลังสร้างรูปภาพ..." when locale is Thai

### Video Generation
- [ ] Send "create a video of waves" → video placeholder appears → video player renders
- [ ] Video has play/pause controls and can be saved
- [ ] Toggle video mode on composer → forces video generation

### Document Plan Execution
- [ ] AI outputs `:::excel-plan` block → PlanCard renders with action list
- [ ] Click "Execute Plan" → progress indicator → result card with download button
- [ ] Download produces correct .xlsx file
- [ ] Same flow works for `:::doc-plan` (DOCX/PDF) and `:::pptx-plan`

### Knowledge Base Management
- [ ] Settings → Knowledge Base → opens in-app dialog (not browser)
- [ ] Create KB with name + description → appears in list
- [ ] Upload PDF/DOCX to KB → shows processing → shows indexed with chunk count
- [ ] Delete document from KB → removed
- [ ] Delete entire KB → removed with confirmation
- [ ] KB selector in composer still works (bind KB to conversation)

### Memory Management
- [ ] Settings → Memory → opens in-app dialog (not browser)
- [ ] Pending suggestions show with Confirm/Reject buttons
- [ ] Add new memory → form dialog → saves
- [ ] Edit existing memory → form dialog → updates
- [ ] Pin/unpin memory → visual indicator updates
- [ ] Delete memory → removed
- [ ] Search memories → filters list
- [ ] Export → saves JSON file
- [ ] Clear all → confirmation → soft deletes
- [ ] Purge (GDPR) → type confirmation phrase → permanent delete
- [ ] Pending badge on Memory button shows count

### i18n
- [ ] Settings → Language toggle → switches between EN and TH
- [ ] ALL sidebar text changes (New chat, Search, group headers)
- [ ] ALL menu items change language
- [ ] Greeting changes ("Good morning" ↔ "สวัสดีตอนเช้า")
- [ ] Composer placeholder changes
- [ ] Footer disclaimer changes
- [ ] Context menu items change (Rename, Delete, Pin, etc.)
- [ ] Model selector labels change (Auto, Search, Popular, Recently Used)
- [ ] Memory UI labels change
- [ ] KB UI labels change
- [ ] Persists after app restart

### Model Popup Bug Fix
- [ ] Home screen → click "Auto ∨" → popup opens with model list
- [ ] Select a model on home screen → composer updates
- [ ] Send message → conversation uses selected model
- [ ] Conversation view → popup still works as before

---

## Reference Files

The web chat implementations to reference for exact behavior:

| Feature | Web Source File |
|---------|----------------|
| Image placeholder | `apps/web-chat/src/components/chat/ImageGeneratingPlaceholder.tsx` |
| Video placeholder | `apps/web-chat/src/components/chat/VideoGeneratingPlaceholder.tsx` |
| Video player | `apps/web-chat/src/components/chat/VideoPlayer.tsx` |
| Excel plan card | `apps/web-chat/src/components/chat/ExcelPlanCard.tsx` |
| Doc plan card | `apps/web-chat/src/components/chat/DocPlanCard.tsx` |
| PPTX plan card | `apps/web-chat/src/components/chat/PptxPlanCard.tsx` |
| KB management | `apps/web-chat/src/app/chat/knowledge/page.tsx` |
| KB selector | `apps/web-chat/src/components/chat/KBSelector.tsx` |
| Memory management | `apps/web-chat/src/app/chat/memory/page.tsx` |
| Memory card | `apps/web-chat/src/components/chat/MemoryCard.tsx` |
| Memory form | `apps/web-chat/src/components/chat/MemoryFormDialog.tsx` |
| i18n dictionary | `apps/web-chat/src/lib/i18n/dict.ts` |
| i18n store | `apps/web-chat/src/lib/i18n/index.ts` |
| Tool definitions | `services/chat-edge-api/internal/service/tools.go` |
| Memory API | `services/chat-edge-api/internal/handler/memory_handler.go` |
| KB API | `services/chat-edge-api/internal/handler/knowledge.go` |

## Desktop Files to Modify

| File | Changes |
|------|---------|
| `ChatStreamEvents.cs` | Add `MessageFilesEvent`, `MemoryEvent` types |
| `ChatService.cs` | Handle `message_files`, `memory_*` SSE events |
| `IChatEdgeApiClient.cs` | Add KB CRUD, Memory CRUD, Plan execution methods |
| `ChatEdgeApiClient.cs` | Implement new API methods |
| `ChatViewModel.cs` | Handle new SSE events, add memory polling |
| `MessageViewModel.cs` | Add `InlineAttachments`, `IsGeneratingImage/Video`, plan blocks |
| `MainWindow.xaml` | Wire KB/Memory buttons to in-app dialogs, pending badge |
| `MainWindow.xaml.cs` | Replace browser-open with dialog-open for KB/Memory |
| `ComposerControl.xaml` | Fix popup, apply i18n markup |
| `ComposerControl.xaml.cs` | Code-behind popup fix |
| `UserSettings.cs` | Add `Locale` field |

## New Files to Create

| File | Purpose |
|------|---------|
| `Localization/Dict.cs` | EN + TH translation dictionary |
| `Localization/LocaleService.cs` | Singleton locale manager |
| `Localization/TExtension.cs` | XAML markup extension `{loc:T key}` |
| `Controls/ImageGeneratingPlaceholder.xaml` | Shimmer placeholder for image gen |
| `Controls/VideoGeneratingPlaceholder.xaml` | Shimmer placeholder for video gen |
| `Controls/PlanCard.xaml` | Plan card for Excel/Doc/PPTX execution |
| `Controls/PlanResultCard.xaml` | Result card after plan execution |
| `ViewModels/InlineAttachmentViewModel.cs` | Image/video attachment in message |
| `ViewModels/PlanBlockViewModel.cs` | Plan block data + execute command |
| `ViewModels/KnowledgeBaseViewModel.cs` | KB list + CRUD |
| `ViewModels/MemoryViewModel.cs` | Memory list + CRUD + pending |
| `ViewModels/MemoryItemViewModel.cs` | Single memory item |
| `Views/KnowledgeBaseView.xaml` | KB management dialog |
| `Views/MemoryView.xaml` | Memory management dialog |
| `Views/MemoryFormDialog.xaml` | Create/edit memory form |
| `Models/MemoryModels.cs` | DTOs for memory API |
| `Models/KnowledgeBaseModels.cs` | DTOs for KB API |
| `Models/PlanModels.cs` | DTOs for plan execution |

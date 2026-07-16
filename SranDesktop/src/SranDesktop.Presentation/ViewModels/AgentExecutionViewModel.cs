using System.Collections.ObjectModel;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using SranDesktop.Application.Events;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// One tool execution shown as a collapsible card in the message bubble
/// (R9 Feature E): icon + friendly name + duration header, expandable
/// input/output JSON, screenshot preview, error state.
/// </summary>
public partial class ToolExecutionItemViewModel : ObservableObject
{
    public string ToolCallId { get; }
    public string ToolName { get; }
    public string FriendlyName { get; }
    public string ArgsSummary { get; }

    /// <summary>Pretty-printed arguments for the expanded Input section.</summary>
    public string InputJson { get; }

    /// <summary>Icon geometry resolved from the tool name (theme resources).</summary>
    public Geometry? IconGeometry { get; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsPending))]
    [NotifyPropertyChangedFor(nameof(IsOk))]
    [NotifyPropertyChangedFor(nameof(IsFailed))]
    private string _state = "pending"; // pending | ok | failed

    [ObservableProperty] private string _durationText = string.Empty;
    [ObservableProperty] private string? _resultPreview;

    /// <summary>Full output for the expanded Output section.</summary>
    [ObservableProperty] private string? _fullResult;

    [ObservableProperty] private bool _isExpanded;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasScreenshot))]
    private BitmapSource? _screenshotPreview;

    // ── Live output (Phase B PART 1) ────────────────────────────────────────
    // run_command streams stdout/stderr here as it runs, rendered as a mini
    // terminal while the card is pending. Tail-bounded so a flood can't grow it.
    private readonly StringBuilder _streamBuffer = new();
    private const int StreamDisplayCap = 8000;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasStreamingOutput))]
    private string? _streamingOutput;

    public bool HasStreamingOutput => !string.IsNullOrEmpty(StreamingOutput);

    public void AppendStreamingLine(string line)
    {
        _streamBuffer.Append(line).Append('\n');
        if (_streamBuffer.Length > StreamDisplayCap)
            _streamBuffer.Remove(0, _streamBuffer.Length - StreamDisplayCap);
        StreamingOutput = _streamBuffer.ToString();
    }

    public bool IsPending => State == "pending";
    public bool IsOk => State == "ok";
    public bool IsFailed => State == "failed";
    public bool HasScreenshot => ScreenshotPreview is not null;

    /// <summary>
    /// Nested tool cards (Phase 16 A): populated for a run_subagent card from
    /// the nested run's live tool activity, using the SAME pending→ok/failed
    /// lifecycle as top-level cards. Empty for every other tool.
    /// </summary>
    public ObservableCollection<ToolExecutionItemViewModel> Children { get; } = new();

    public bool HasChildren => Children.Count > 0;

    /// <summary>The indented child list defaults to expanded while the
    /// sub-agent works, so its activity is visible without a click.</summary>
    [ObservableProperty] private bool _isChildListExpanded = true;

    /// <summary>
    /// Route one nested <see cref="AgentStreamEvent"/> into <see cref="Children"/>,
    /// reusing the top-level mapping: a tool-call opens a pending child card, a
    /// tool-result completes it (ok/failed). Only these two event kinds reach
    /// here (the publisher filters), and run_subagent never appears — depth-1.
    /// </summary>
    public void RouteNestedEvent(AgentStreamEvent evt)
    {
        switch (evt)
        {
            case AgentToolCallsEvent calls:
                foreach (var call in calls.ToolCalls)
                    Children.Add(new ToolExecutionItemViewModel(
                        call.Id, call.Function.Name, call.Function.Arguments));
                OnPropertyChanged(nameof(HasChildren));
                break;

            case AgentToolResultEvent result:
                var child = Children.FirstOrDefault(c => c.ToolCallId == result.ToolCallId);
                if (child is null)
                {
                    child = new ToolExecutionItemViewModel(
                        result.ToolCallId, result.ToolName, result.ArgumentsJson);
                    Children.Add(child);
                    OnPropertyChanged(nameof(HasChildren));
                }
                child.Complete(result);
                break;
        }
    }

    public ToolExecutionItemViewModel(string toolCallId, string toolName, string argumentsJson)
    {
        ToolCallId = toolCallId;
        ToolName = toolName;
        FriendlyName = Friendly(toolName);
        ArgsSummary = Compact(argumentsJson);
        InputJson = Pretty(argumentsJson);
        IconGeometry = System.Windows.Application.Current?.TryFindResource(ToolIcon(toolName)) as Geometry;
    }

    public void Complete(AgentToolResultEvent result)
    {
        State = result.Success ? "ok" : "failed";
        // The live mini-terminal gives way to the final captured output.
        _streamBuffer.Clear();
        StreamingOutput = null;
        DurationText = result.DurationMs > 0 ? $"{result.DurationMs} ms" : string.Empty;
        ResultPreview = result.ResultPreview;
        FullResult = Truncate(result.FullResult ?? result.ResultPreview, 4000);
        if (result.ImageDataUri is { Length: > 0 } dataUri)
            ScreenshotPreview = TryDecodeDataUri(dataUri);
    }

    /// <summary>Icon key per tool (R9 E3).</summary>
    public static string ToolIcon(string toolName) => toolName switch
    {
        "computer_screenshot" => "Geo_Monitor",
        "computer_click" or "computer_drag" => "Geo_Cursor",
        "computer_type" or "computer_key" => "Geo_Keyboard",
        "computer_scroll" => "Geo_ArrowsVertical",
        "read_file" => "Geo_FileText",
        "write_file" or "edit_file" => "Geo_FileCode",
        "create_directory" or "move_file" => "Geo_FileCode",
        "search_files" or "list_directory" => "Geo_Search",
        "run_command" => "Geo_Terminal",
        "web_search" => "Geo_Browser",
        "open_url" => "Geo_Browser",
        "open_application" => "Geo_Monitor",
        "read_clipboard" or "write_clipboard" => "Geo_Copy",
        "remember" => "Geo_FileText",
        "generate_image" => "Geo_ImageBadge",
        "generate_video" => "Geo_VideoBadge",
        _ => "Geo_Tool"
    };

    private static string Friendly(string toolName) => toolName switch
    {
        "computer_screenshot" => "Screenshot",
        "computer_click" => "Click",
        "computer_type" => "Type",
        "computer_key" => "Key press",
        "computer_scroll" => "Scroll",
        "computer_drag" => "Drag",
        "open_application" => "Open application",
        "read_file" => "Read file",
        "write_file" => "Write file",
        "edit_file" => "Edit file",
        "create_directory" => "Create folder",
        "move_file" => "Move file",
        "list_directory" => "List folder",
        "search_files" => "Search files",
        "run_command" => "Run command",
        "open_url" => "Open link",
        "read_clipboard" => "Read clipboard",
        "write_clipboard" => "Write clipboard",
        "remember" => "Remember",
        "web_search" => "Web search",
        _ => toolName.Replace('_', ' ')
    };

    private static string Compact(string json)
    {
        string text;
        try { using var doc = JsonDocument.Parse(json); text = JsonSerializer.Serialize(doc); }
        catch (JsonException) { text = json; }
        return text.Length > 90 ? text[..90] + "..." : text;
    }

    private static string Pretty(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
            return JsonSerializer.Serialize(doc, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (JsonException)
        {
            return json;
        }
    }

    private static string? Truncate(string? value, int max) =>
        value is null || value.Length <= max ? value : value[..max] + "\n[...]";

    private static BitmapSource? TryDecodeDataUri(string dataUri)
    {
        try
        {
            var comma = dataUri.IndexOf(',');
            if (comma < 0) return null;
            var bytes = Convert.FromBase64String(dataUri[(comma + 1)..]);
            var image = new BitmapImage();
            using var stream = new MemoryStream(bytes);
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = stream;
            image.EndInit();
            image.Freeze();
            return image;
        }
        catch (Exception ex) when (ex is FormatException or NotSupportedException or IOException)
        {
            return null;
        }
    }
}

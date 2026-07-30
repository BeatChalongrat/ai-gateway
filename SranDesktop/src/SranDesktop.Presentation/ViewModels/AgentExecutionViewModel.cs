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

    /// <summary>Human-readable input for the expanded section: "label: value"
    /// lines with localized labels instead of raw JSON (non-technical users
    /// shouldn't need to read JSON to see what the agent did). Falls back to
    /// pretty JSON when the arguments aren't a simple object.</summary>
    public string InputDisplay { get; }

    /// <summary>Icon geometry resolved from the tool name (theme resources).</summary>
    public Geometry? IconGeometry { get; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsPending))]
    [NotifyPropertyChangedFor(nameof(IsOk))]
    [NotifyPropertyChangedFor(nameof(IsVerificationPending))]
    [NotifyPropertyChangedFor(nameof(IsFailed))]
    [NotifyPropertyChangedFor(nameof(IsRetrying))]
    private string _state = "pending"; // pending | retrying | verification_pending | verified | failed

    /// <summary>R4/2b: amber "Retrying n/m — reason" line shown WHILE the runtime
    /// auto-retries a transient failure, so repeated attempts collapse into one card
    /// instead of a stack of red X's. Set by <see cref="BeginRetry"/>; cleared when the
    /// action next completes.</summary>
    [ObservableProperty] private string? _retryHint;

    [ObservableProperty] private string _durationText = string.Empty;
    [ObservableProperty] private string? _resultPreview;

    /// <summary>Full output for the expanded Output section.</summary>
    [ObservableProperty] private string? _fullResult;

    [ObservableProperty] private bool _isExpanded;

    /// <summary>R5: one localized, human-readable sentence shown on a failed card so a
    /// non-technical user sees WHY the step failed; the raw code/output stays available
    /// in the expanded Output section. Set in <see cref="Complete"/> only when failed.</summary>
    [ObservableProperty] private string? _humanError;

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
    public bool IsOk => State == "verified";
    public bool IsVerificationPending => State == "verification_pending";
    public bool IsFailed => State == "failed";
    public bool IsRetrying => State == "retrying";
    public bool HasScreenshot => ScreenshotPreview is not null;

    /// <summary>2b: mark this card as auto-retrying (from an <c>AgentToolRetryEvent</c>).
    /// Shows an amber "Retrying n/m — reason" line instead of a red X between attempts;
    /// the next <see cref="Complete"/> replaces it with the real terminal state.</summary>
    public void BeginRetry(int attempt, int maxAttempts, string reason)
    {
        RetryHint = string.Format(
            Localization.LocaleService.Instance.T("tool.retrying"), attempt, maxAttempts)
            + " — " + RetryReason(reason);
        State = "retrying";
    }

    /// <summary>Short localized phrase for the retryable FailureCode driving the retry.</summary>
    internal static string RetryReason(string? code)
    {
        var key = code switch
        {
            "FocusLost" => "tool.retry.reason.focusLost",
            "TargetNotFound" => "tool.retry.reason.targetNotFound",
            "StaleObservation" or "StaleWindow" => "tool.retry.reason.stale",
            "ApplicationNotReady" => "tool.retry.reason.notReady",
            _ => "tool.retry.reason.generic",
        };
        return Localization.LocaleService.Instance.T(key);
    }

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
                child.Complete(result, requireVerification: false);
                break;
        }
    }

    public ToolExecutionItemViewModel(string toolCallId, string toolName, string argumentsJson)
    {
        ToolCallId = toolCallId;
        ToolName = toolName;
        FriendlyName = Friendly(toolName);
        ArgsSummary = Humanize(toolName, argumentsJson);
        InputJson = Pretty(argumentsJson);
        InputDisplay = BuildInputDisplay(argumentsJson);
        IconGeometry = System.Windows.Application.Current?.TryFindResource(ToolIcon(toolName)) as Geometry;
    }

    /// <param name="requireVerification">
    /// Top-level authoritative cards (the default) obey the Phase 8 rule: green
    /// is reserved for an INDEPENDENTLY verified effect (a durable UnifiedResult
    /// with Verified=true); native acceptance alone stays amber. Nested
    /// sub-agent children pass <c>false</c> — they are an informational live
    /// surface with no durable verification pipeline of their own (the
    /// authoritative verification lives on the parent run_subagent card), so
    /// their native success is a terminal "done" state.
    /// </param>
    public void Complete(AgentToolResultEvent result, bool requireVerification = true)
    {
        State = requireVerification
            ? (result.UnifiedResult?.Verified == true
                ? "verified"
                : result.Success ? "verification_pending" : "failed")
            : (result.Success ? "verified" : "failed");
        // The terminal state replaces any in-flight retry hint.
        RetryHint = null;
        // R5: attach a human-readable reason only for genuinely failed (red) cards.
        if (State == "failed")
            HumanError = HumanizeFailure(ExtractFailureCode(result));
        // The live mini-terminal gives way to the final captured output.
        _streamBuffer.Clear();
        StreamingOutput = null;
        DurationText = result.DurationMs > 0 ? $"{result.DurationMs} ms" : string.Empty;
        // list_directory results use terse "D name" / "F name (size)" markers for
        // the model; swap them for folder/file emoji so non-technical users can
        // read the listing at a glance (display only — the model text is untouched).
        ResultPreview = MaybePrettifyListing(ToolName, result.Success, result.ResultPreview);
        FullResult = MaybePrettifyListing(ToolName, result.Success,
            Truncate(result.FullResult ?? result.ResultPreview, 4000));
        if (result.ImageDataUri is { Length: > 0 } dataUri)
            ScreenshotPreview = TryDecodeDataUri(dataUri);
    }

    /// <summary>R5: extract the leading FailureCode from a failed tool result — the
    /// ComputerAction.FailureCode when present, else the token before the first ':' in
    /// the error text (tools prefix hard failures as "Error: &lt;Code&gt;: reason").</summary>
    internal static string? ExtractFailureCode(AgentToolResultEvent result)
    {
        if (result.ComputerAction?.FailureCode is { Length: > 0 } code)
            return code;
        var text = result.FullResult ?? result.ResultPreview ?? string.Empty;
        const string prefix = "Error: ";
        if (text.StartsWith(prefix, StringComparison.Ordinal))
            text = text[prefix.Length..];
        var colon = text.IndexOf(':');
        return colon > 0 ? text[..colon].Trim() : null;
    }

    /// <summary>R5: localized one-line reason for a failed step.</summary>
    internal static string HumanizeFailure(string? code) =>
        Localization.LocaleService.Instance.T(FailureKey(code));

    /// <summary>Pure FailureCode → localization-key map (unit-testable). Only the red
    /// (Success=false) failure codes are mapped — FocusLost / TargetNotFound /
    /// readiness-not-ready / Stale* / WrongWindow / UnboundInput / capture-validation;
    /// anything else falls back to a generic sentence.</summary>
    internal static string FailureKey(string? code)
    {
        if (string.IsNullOrEmpty(code))
            return "tool.error.generic";
        bool Has(string s) => code.Contains(s, StringComparison.OrdinalIgnoreCase);
        if (Has("FocusLost")) return "tool.error.focusLost";
        if (Has("TargetNotFound")) return "tool.error.targetNotFound";
        if (Has("WrongWindow")) return "tool.error.wrongWindow";
        if (Has("UnboundInput")) return "tool.error.unboundInput";
        if (Has("Stale")) return "tool.error.stale";        // StaleObservation / StaleWindow / CaptureStale
        if (Has("NotReady")) return "tool.error.notReady";  // ApplicationNotReady
        // Click-path (uia_click / computer_click) red codes — these arrive as sentences,
        // so match on their distinctive phrase.
        if (Has("disabled")) return "tool.error.disabled";
        if (Has("clickable area")) return "tool.error.noClickable";
        // Match the OS-rejection code EXACTLY. A substring "rejected" also matches
        // policy/consent codes (CapabilityPolicyRejected, CapabilitySelectionRequired,
        // permission "rejected"), which must NOT show "Windows didn't accept the input" —
        // e.g. a non-click action that a policy blocked would wrongly read as a failed click.
        if (string.Equals(code, "OsInputRejected", StringComparison.OrdinalIgnoreCase))
            return "tool.error.osRejected";
        if (Has("Timeout") || Has("did not respond")) return "tool.error.timeout";
        if (Has("Mismatch") || Has("HostWindow") || Has("OffScreen") || Has("Occlud")
            || Has("Covered")) return "tool.error.validate"; // capture / point validation
        return "tool.error.generic";
    }

    /// <summary>Icon key per tool (R9 E3).</summary>
    public static string ToolIcon(string toolName) => toolName switch
    {
        "computer_screenshot" => "Geo_Monitor",
        "computer_click" or "computer_drag" or "uia_click" => "Geo_Cursor",
        "computer_type" or "computer_key" or "uia_set_value" => "Geo_Keyboard",
        "computer_scroll" => "Geo_ArrowsVertical",
        "computer_zoom" => "Geo_Search",
        "get_computer_state" or "read_screen_elements" => "Geo_Monitor",
        "read_file" => "Geo_FileText",
        "write_file" or "edit_file" => "Geo_FileCode",
        "create_directory" or "move_file" => "Geo_FileCode",
        "search_files" or "list_directory" => "Geo_Search",
        "run_command" => "Geo_Terminal",
        "run_python" => "Geo_Code",
        "web_search" or "web_fetch" => "Geo_Globe2",
        "open_url" => "Geo_Browser",
        "browser_navigate" or "browser_read_page" or "browser_find" or "browser_click"
            or "browser_type" or "browser_tabs" or "browser_list_tabs"
            or "browser_screenshot" => "Geo_Browser",
        "open_application" => "Geo_Monitor",
        "read_clipboard" or "write_clipboard" => "Geo_Copy",
        "remember" => "Geo_FileText",
        "use_skill" => "Geo_Puzzle",
        "run_subagent" => "Geo_Agent",
        "update_plan" => "Geo_Check",
        "generate_image" => "Geo_ImageBadge",
        "generate_video" => "Geo_VideoBadge",
        _ => "Geo_Tool"
    };

    /// <summary>Localized display name: "tool.name.{tool}" from the dictionary
    /// (TH users see Thai), falling back to the English switch below for any
    /// tool the dictionary doesn't know (e.g. remote MCP tools).</summary>
    private static string Friendly(string toolName)
    {
        var key = "tool.name." + toolName;
        var localized = Localization.LocaleService.Instance.T(key);
        return localized != key ? localized : FallbackFriendly(toolName);
    }

    private static string FallbackFriendly(string toolName) => toolName switch
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

    /// <summary>
    /// One-line human summary of a tool call's arguments for the card header:
    /// the salient value only ("_zeno_backups", "(960, 668)", a search query),
    /// never raw JSON. Unknown tools fall back to compact JSON. Internal for tests.
    /// </summary>
    internal static string Humanize(string toolName, string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return Compact(json);
            var r = doc.RootElement;

            string? S(string n) => r.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.String
                ? v.GetString() : null;
            string? I(string n) => r.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.Number
                ? v.GetRawText() : null;

            var summary = toolName switch
            {
                "list_directory" or "create_directory" => Leaf(S("path")),
                "read_file" or "write_file" or "edit_file" => Leaf(S("path")),
                "move_file" => JoinNonEmpty(" → ", Leaf(S("source")), Leaf(S("destination"))),
                "search_files" => S("pattern") ?? S("content_pattern"),
                "run_command" => Trunc(S("command"), 70),
                "run_python" => Trunc(S("code"), 70),
                "web_search" or "browser_find" => S("query"),
                "web_fetch" or "open_url" or "browser_navigate" => Trunc(S("url"), 70),
                "open_application" or "use_skill" => S("name"),
                "remember" => Trunc(S("note"), 70),
                "run_subagent" => Trunc(S("task"), 70),
                "computer_click" or "computer_scroll" =>
                    I("x") is { } x && I("y") is { } y ? $"({x}, {y})" : null,
                "computer_type" => Trunc(S("text"), 40),
                "computer_key" => S("keys"),
                "computer_screenshot" or "read_screen_elements" => S("window_title") ?? "",
                "get_computer_state" or "computer_zoom" or "browser_read_page"
                    or "browser_screenshot" or "browser_list_tabs"
                    or "read_clipboard" or "update_plan" => "",
                "uia_click" => I("element_id") is { } id ? "#" + id : null,
                "uia_set_value" => JoinNonEmpty(" ",
                    I("element_id") is { } id2 ? "#" + id2 : null, Trunc(S("text"), 40)),
                "browser_click" => I("ref") is { } rf ? "#" + rf : null,
                "browser_type" => JoinNonEmpty(" ",
                    I("ref") is { } rf2 ? "#" + rf2 : null, Trunc(S("text"), 40)),
                "browser_tabs" => S("action"),
                _ => null
            };
            return summary ?? Compact(json);
        }
        catch (JsonException)
        {
            return Compact(json);
        }
    }

    /// <summary>Last path segment ("_zeno_backups" from the full path) — the part
    /// a person recognizes; the full path stays in the expanded input.</summary>
    private static string? Leaf(string? path)
    {
        if (string.IsNullOrWhiteSpace(path))
            return null;
        var name = Path.GetFileName(path.TrimEnd('\\', '/'));
        return string.IsNullOrEmpty(name) ? path : name;
    }

    private static string? Trunc(string? s, int max) =>
        s is null ? null : s.Length <= max ? s : s[..max] + "…";

    private static string? JoinNonEmpty(string separator, params string?[] parts)
    {
        var kept = parts.Where(p => !string.IsNullOrEmpty(p)).ToArray();
        return kept.Length == 0 ? null : string.Join(separator, kept);
    }

    /// <summary>Expanded-input lines: "label: value" per argument with localized
    /// labels (tool.arg.*), long values capped. Pretty JSON only as a fallback
    /// for non-object arguments. Internal for tests.</summary>
    internal static string BuildInputDisplay(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return Pretty(json);
            var sb = new StringBuilder();
            foreach (var prop in doc.RootElement.EnumerateObject())
            {
                var value = prop.Value.ValueKind == JsonValueKind.String
                    ? prop.Value.GetString() ?? ""
                    : prop.Value.GetRawText();
                sb.Append(ArgLabel(prop.Name)).Append(": ")
                  .AppendLine(Trunc(value, 500));
            }
            return sb.Length == 0 ? "—" : sb.ToString().TrimEnd();
        }
        catch (JsonException)
        {
            return Pretty(json);
        }
    }

    private static string ArgLabel(string param)
    {
        var key = "tool.arg." + param;
        var localized = Localization.LocaleService.Instance.T(key);
        return localized != key ? localized : param;
    }

    /// <summary>Swap list_directory's terse "D " / "F " line markers for
    /// folder/file emoji (display only). Any other tool passes through.</summary>
    internal static string? MaybePrettifyListing(string toolName, bool success, string? text)
    {
        if (toolName != "list_directory" || !success || string.IsNullOrEmpty(text))
            return text;
        var sb = new StringBuilder(text.Length + 16);
        foreach (var line in text.Split('\n'))
        {
            var l = line.TrimEnd('\r');
            if (l.StartsWith("D ", StringComparison.Ordinal))
                sb.Append("📁 ").AppendLine(l[2..]);
            else if (l.StartsWith("F ", StringComparison.Ordinal))
                sb.Append("📄 ").AppendLine(l[2..]);
            else
                sb.AppendLine(l);
        }
        return sb.ToString().TrimEnd();
    }

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

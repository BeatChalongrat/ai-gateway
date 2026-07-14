using System.Text;
using CommunityToolkit.Mvvm.ComponentModel;
using SranDesktop.Domain.Enums;
using SranDesktop.Domain.ValueObjects;

namespace SranDesktop.Presentation.ViewModels;

public partial class MessageViewModel : ObservableObject
{
    private readonly StringBuilder _contentBuffer = new();
    private readonly StringBuilder _reasoningBuffer = new();

    /// <summary>Tool calls made during this assistant turn, shown inline as
    /// collapsible rows above the message content (Claude-style).</summary>
    public System.Collections.ObjectModel.ObservableCollection<ToolExecutionItemViewModel> ToolExecutions { get; } = new();

    /// <summary>Generated media (images/videos) rendered inside the bubble (R7 A/B).</summary>
    public System.Collections.ObjectModel.ObservableCollection<InlineAttachmentViewModel> InlineAttachments { get; } = new();

    /// <summary>Document plan cards parsed from :::excel-plan / :::doc-plan / :::pptx-plan fences (R7 C).</summary>
    public System.Collections.ObjectModel.ObservableCollection<PlanBlockViewModel> PlanBlocks { get; } = new();

    /// <summary>Web-search citations shown as clickable source cards (R9 C5).</summary>
    public System.Collections.ObjectModel.ObservableCollection<SourceViewModel> Sources { get; } = new();

    /// <summary>Live artifacts rendered in WebView2 (R9 A).</summary>
    public System.Collections.ObjectModel.ObservableCollection<ArtifactViewModel> Artifacts { get; } = new();

    /// <summary>Images the user attached, echoed inline in their bubble (R9 D5).</summary>
    public System.Collections.ObjectModel.ObservableCollection<InlineAttachmentViewModel> InlineImages { get; } = new();

    /// <summary>Shows the shimmer image placeholder while the server generates.</summary>
    [ObservableProperty]
    private bool _isGeneratingImage;

    /// <summary>Shows the shimmer video placeholder while the server generates.</summary>
    [ObservableProperty]
    private bool _isGeneratingVideo;

    public string Id { get; set; }
    public MessageRole Role { get; }
    public DateTimeOffset CreatedAt { get; }

    public bool IsUser => Role == MessageRole.User;
    public bool IsAssistant => Role == MessageRole.Assistant;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(RenderContent))]
    private string _content = string.Empty;

    /// <summary>
    /// Content with plan fences stripped, set once plan blocks are extracted.
    /// Null = render <see cref="Content"/> as-is. The raw Content (fences
    /// included) is what gets persisted, so cards rebuild on reload.
    /// </summary>
    private string? _displayContent;

    /// <summary>What the markdown viewer renders.</summary>
    public string RenderContent => _displayContent ?? Content;

    public void SetDisplayContent(string? value)
    {
        _displayContent = value;
        OnPropertyChanged(nameof(RenderContent));
    }

    [ObservableProperty]
    private string _reasoning = string.Empty;

    private DateTimeOffset? _reasoningStart;
    private TimeSpan? _reasoningElapsed;

    /// <summary>True once the model has finished thinking (content began or the turn completed).</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ReasoningHeader))]
    private bool _isReasoningComplete;

    /// <summary>"Thinking..." while reasoning streams; "Thought for X.Xs" once done (web parity A2).</summary>
    public string ReasoningHeader => IsReasoningComplete && _reasoningElapsed is { } d
        ? $"Thought for {d.TotalSeconds:F1}s"
        : "Thinking...";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ShowTypingIndicator))]
    private bool _isStreaming;

    [ObservableProperty]
    private bool _isError;

    /// <summary>
    /// True only for the most recent assistant turn. Web parity: the Regenerate
    /// action + the always-visible action row are reserved for the last reply;
    /// earlier replies show their (hover-only) actions without Regenerate.
    /// </summary>
    [ObservableProperty]
    private bool _isLastAssistantMessage;

    [ObservableProperty]
    private string? _statusText;

    /// <summary>"model · N tokens" line shown under completed assistant messages.</summary>
    [ObservableProperty]
    private string? _metaLine;

    public string? ModelId { get; set; }

    /// <summary>True when <see cref="Id"/> is a SERVER (chat-edge) message id —
    /// set for chat-edge-path assistant turns (StreamStartedEvent adopts the
    /// server id) and for history rows fetched from the server. Agent-path
    /// messages keep local ids and stay false (Phase 5: gates feedback POSTs).</summary>
    public bool IsServerMessage { get; set; }

    /// <summary>Phase 8 Task 3: true ONLY for messages appended live while the
    /// conversation is open (user send + assistant placeholder) — history
    /// loads and branch swaps stay false so they never animate. One-shot:
    /// the bubble clears it when the entrance runs, so a recycled or
    /// re-realized container can't replay it.</summary>
    public bool AnimateEntrance { get; set; }

    /// <summary>Router fallback notice ("Answered by X — Y was unavailable"),
    /// set mid-stream; merged into <see cref="MetaLine"/> by Complete so the
    /// "model · N tokens" line can't clobber it.</summary>
    public string? FallbackNotice { get; set; }

    // ── Branching (R9 F) ────────────────────────────────────────────────────

    /// <summary>Groups sibling branch heads created by editing this message.</summary>
    public string? BranchKey { get; set; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(BranchDisplay))]
    private int _branchIndex;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasBranches))]
    [NotifyPropertyChangedFor(nameof(BranchDisplay))]
    private int _branchCount;

    public bool HasBranches => BranchCount > 1;
    public string BranchDisplay => $"{BranchIndex + 1}/{BranchCount}";

    /// <summary>Inline edit mode for user messages (R9 F4).</summary>
    [ObservableProperty] private bool _isEditing;

    [ObservableProperty] private string _editingText = string.Empty;

    /// <summary>Local feedback state: "none" | "up" | "down".</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsThumbUp))]
    [NotifyPropertyChangedFor(nameof(IsThumbDown))]
    private string _feedback = "none";

    public bool IsThumbUp => Feedback == "up";
    public bool IsThumbDown => Feedback == "down";

    public bool ShowTypingIndicator => IsStreaming && Content.Length == 0;

    public MessageViewModel(string id, MessageRole role, string content, DateTimeOffset createdAt)
    {
        Id = id;
        Role = role;
        CreatedAt = createdAt;
        _contentBuffer.Append(content);
        _content = content;
    }

    public void AppendContent(string delta)
    {
        // First real content means the model stopped thinking.
        FinishReasoning();
        _contentBuffer.Append(delta);
        Content = _contentBuffer.ToString();
        if (StatusText is not null)
            StatusText = null;
        OnPropertyChanged(nameof(ShowTypingIndicator));
    }

    public void AppendReasoning(string delta)
    {
        _reasoningStart ??= DateTimeOffset.UtcNow;
        _reasoningBuffer.Append(delta);
        Reasoning = _reasoningBuffer.ToString();
    }

    private void FinishReasoning()
    {
        if (_reasoningStart is { } start && !IsReasoningComplete)
        {
            _reasoningElapsed = DateTimeOffset.UtcNow - start;
            IsReasoningComplete = true;
        }
    }

    public void Complete(TokenUsage? usage)
    {
        FinishReasoning();
        IsStreaming = false;
        StatusText = null;
        var parts = new List<string>();
        if (FallbackNotice is not null) parts.Add(FallbackNotice);
        if (!string.IsNullOrEmpty(ModelId)) parts.Add(ModelId!);
        if (usage is not null) parts.Add($"{usage.TotalTokens} tokens");
        if (parts.Count > 0)
            MetaLine = string.Join("  ·  ", parts);
    }

    public void SetError(string message)
    {
        IsStreaming = false;
        IsError = true;
        StatusText = null;
        if (Content.Length == 0)
        {
            _contentBuffer.Append(message);
            Content = message;
        }
        else
        {
            MetaLine = message;
        }
    }
}

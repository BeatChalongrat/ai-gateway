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

    /// <summary>Model-maintained plan/todo checklist (Phase A PART 5), rendered
    /// live above the reply and checked off as the agent progresses.</summary>
    public System.Collections.ObjectModel.ObservableCollection<PlanStepViewModel> PlanSteps { get; } = new();

    public bool HasPlan => PlanSteps.Count > 0;

    /// <summary>Replace the plan with the model's latest task list.</summary>
    public void UpdatePlan(IReadOnlyList<Application.Services.PlanStep> steps)
    {
        PlanSteps.Clear();
        foreach (var step in steps)
            PlanSteps.Add(new PlanStepViewModel(step.Step, step.Status));
        OnPropertyChanged(nameof(HasPlan));
    }

    /// <summary>Live artifacts rendered in WebView2 (R9 A).</summary>
    public System.Collections.ObjectModel.ObservableCollection<ArtifactViewModel> Artifacts { get; } = new();

    /// <summary>Images the user attached, echoed inline in their bubble (R9 D5).</summary>
    public System.Collections.ObjectModel.ObservableCollection<InlineAttachmentViewModel> InlineImages { get; } = new();

    /// <summary>SERVER file ids of the attachments uploaded with this (user)
    /// message (FIX 10). Persisted with the message so the push sync can link
    /// them server-side; null when the turn had no uploaded attachments.</summary>
    public List<string>? AttachedFileIds { get; set; }

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
        ? Localization.LocaleService.Instance.T("msg.thoughtFor", d.TotalSeconds)
        : Localization.LocaleService.Instance.T("msg.thinking");

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

    /// <summary>P3 — trailing turn-anchor spacer, in pixels. Non-zero ONLY on
    /// the last message of an anchored turn: the ChatView template renders it
    /// as an empty Border below the bubble, so the space is part of the
    /// VirtualizingStackPanel's SCROLLABLE EXTENT (unlike ListBox.Padding,
    /// which template-binds to the inner ScrollViewer's viewport chrome and
    /// never scrolls — the P3 desktop bug). Pure geometry, never content;
    /// lives on the VM so container recycling can't reset it. ChatView owns
    /// the value (assign / shrink / clear).</summary>
    [ObservableProperty]
    private double _turnSpacerHeight;

    // ── Long user-message collapse (P2) ─────────────────────────────────────
    // The threshold decision and expansion state live on the VIEW MODEL, not
    // the visual tree: MessageList containers recycle, so visual-only state
    // resets when a row scrolls out and back. The view owns layout, measures
    // the rendered text (wrapped line-boxes — never a "\n" count; Thai wraps),
    // and reports through ReportUserTextMetrics; everything downstream is
    // plain VM state.

    /// <summary>A user pill taller than this many rendered line-boxes starts collapsed.</summary>
    public const int CollapseThresholdLines = 12;

    /// <summary>The collapsed pill shows this many line-boxes.</summary>
    public const int CollapseVisibleLines = 8;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(UserTextMaxHeight))]
    private bool _canCollapse;

    /// <summary>Ephemeral per-message expansion — deliberately not persisted
    /// (every load starts collapsed).</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(UserTextMaxHeight))]
    private bool _isExpanded;

    private double _collapseCapHeight;

    /// <summary>What the user pill's TextBlock binds MaxHeight to: the
    /// 8-line cap while collapsed, unbounded otherwise (and before the first
    /// measurement, so nothing ever clips ahead of a real decision).</summary>
    public double UserTextMaxHeight =>
        CanCollapse && !IsExpanded && _collapseCapHeight > 0
            ? _collapseCapHeight
            : double.PositiveInfinity;

    /// <summary>Called by the view with the RENDERED height of the full text
    /// at the current wrap width, plus the effective line height. Re-fired on
    /// width changes so a window resize re-decides the threshold; idempotent
    /// otherwise.</summary>
    public void ReportUserTextMetrics(double fullTextHeight, double lineHeight)
    {
        if (!IsUser || lineHeight <= 0)
            return;
        _collapseCapHeight = lineHeight * CollapseVisibleLines;
        // Half-line tolerance: an exactly-threshold-height message must not
        // collapse, and sub-pixel layout noise must not flip the decision.
        CanCollapse = fullTextHeight > lineHeight * CollapseThresholdLines + lineHeight / 2;
        OnPropertyChanged(nameof(UserTextMaxHeight));
    }

    /// <summary>Jump-to-message entry point: navigation landing on a collapsed
    /// message must reveal it. Sidebar search currently opens only the
    /// conversation (ConversationListViewModel.OpenMessageHitAsync — the
    /// server sends no message id), so nothing calls this yet; it is the
    /// tested seam for when per-message navigation lands.</summary>
    public void ExpandForSearchHit()
    {
        if (CanCollapse)
            IsExpanded = true;
    }

    /// <summary>"model · N tokens" line shown under completed assistant messages.</summary>
    [ObservableProperty]
    private string? _metaLine;

    /// <summary>Compact local-time stamp shown in the message meta row (audit
    /// §3.8): "14:32" today, "6 Aug 14:32" (TH: "6 ส.ค. 14:32") for older
    /// messages. <see cref="CreatedAt"/> is offset-aware, so this is always the
    /// machine's local time regardless of the offset it was stored with.</summary>
    public string TimestampText => Services.MessageTimestampFormatter.Format(
        CreatedAt, DateTimeOffset.Now, Localization.LocaleService.Instance.Locale);

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

    // ── Durable tool history (Phase A PART 1) ───────────────────────────────

    /// <summary>
    /// The intermediate gateway messages this assistant turn produced — the
    /// assistant messages that carried tool_calls, every role:"tool" result, and
    /// screenshot placeholders — in order, WITHOUT this bubble's final answer.
    /// Kept so the next turn's request can replay the tool context (the model
    /// remembers what it read/edited/ran and saw), and persisted per turn so it
    /// survives conversation switches and restarts. Screenshots are stored as
    /// small placeholders, never raw base64. Null/empty for a plain reply.
    /// </summary>
    public List<Application.Models.GatewayMessage>? AgentToolChain { get; set; }

    /// <summary>
    /// True when this turn hit its step/time budget with the task unfinished
    /// (PART 1/6): the accumulated tool history is saved and the UI shows a
    /// "Continue" affordance that re-enters the loop to resume.
    /// </summary>
    [ObservableProperty]
    private bool _canContinue;

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
        if (usage is not null) parts.Add(Localization.LocaleService.Instance.T("msg.tokens", usage.TotalTokens));
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
            _errorInContent = true;
        }
        else
        {
            MetaLine = message;
        }
    }

    /// <summary>True when <see cref="SetError"/> injected the error text as the
    /// bubble's only content — a resume must remove it before streaming again.</summary>
    private bool _errorInContent;

    /// <summary>Phase 5 Task 3 (D3): a Continue after a connection drop resumes
    /// this bubble's turn — drop the error styling, and when the error text was
    /// injected as the bubble's only content, remove it so resumed output never
    /// appends after an error sentence.</summary>
    public void ClearError()
    {
        if (!IsError)
            return;
        IsError = false;
        MetaLine = null;
        if (_errorInContent)
        {
            _errorInContent = false;
            _contentBuffer.Clear();
            Content = string.Empty;
            OnPropertyChanged(nameof(ShowTypingIndicator));
        }
    }

    /// <summary>Phase 5 Task 3 (D2): empties the bubble before a reattach
    /// attempt. Every attempt re-delivers the full already-generated prefix as
    /// one delta, so a retry that didn't reset would duplicate text.</summary>
    public void ResetStreamedContent()
    {
        _contentBuffer.Clear();
        Content = string.Empty;
        _reasoningBuffer.Clear();
        Reasoning = string.Empty;
        _reasoningStart = null;
        _reasoningElapsed = null;
        IsReasoningComplete = false;
        _errorInContent = false;
        OnPropertyChanged(nameof(ShowTypingIndicator));
    }
}

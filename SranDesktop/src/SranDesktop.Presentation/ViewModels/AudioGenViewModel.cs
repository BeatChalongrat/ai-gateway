using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>One selectable voice for text-to-speech.</summary>
public sealed record VoiceChoice(string Id, string Name)
{
    public override string ToString() => Name;
}

/// <summary>
/// Audio generation form (Phase 14 Task 3) — desktop parity with the web
/// AudioGenForm.
///
/// A FORM, not a composer toggle, and that is not a style choice: chat-edge
/// exposes generate_image / generate_video as model-callable tools but has NO
/// generate_audio. Audio exists only as direct dispatch
/// (POST /audio/generations → poll → fetch), so an "audio mode" toggle would
/// bias the model toward a tool that does not exist and produce nothing.
///
/// Two modes on one form, mirroring the web: speech (a voice reads text) and
/// music (a track from a description).
/// </summary>
public partial class AudioGenViewModel : ObservableObject
{
    /// <summary>Placeholder voices until the catalogue exposes a real list —
    /// same fallback pair the web form uses.</summary>
    public static readonly IReadOnlyList<string> FallbackVoiceIds = new[] { "default", "narrator" };

    public const int MinDurationSec = 10;
    public const int MaxDurationSec = 60;
    public const int MaxPromptChars = 2000;

    private readonly AudioGenerationService _service;
    private readonly string _modelId;
    private readonly string? _conversationId;
    private CancellationTokenSource? _cts;

    public AudioGenViewModel(
        IChatEdgeApiClient api,
        ILogger logger,
        string modelId,
        string? conversationId,
        Func<TimeSpan, CancellationToken, Task>? delay = null)
    {
        _service = new AudioGenerationService(api, logger, delay);
        _service.ProgressChanged += (_, pct) => ProgressPct = pct;
        _modelId = modelId;
        _conversationId = conversationId;
        foreach (var id in FallbackVoiceIds)
            Voices.Add(new VoiceChoice(id, LocaleService.Instance.T("media.voice." + id)));
        _selectedVoice = Voices[0];
    }

    public System.Collections.ObjectModel.ObservableCollection<VoiceChoice> Voices { get; } = new();

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanSubmit))]
    private string _prompt = string.Empty;

    /// <summary>true = speech (TTS), false = music. Two modes, one form.</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsMusic))]
    private bool _isSpeech = true;

    public bool IsMusic => !IsSpeech;

    [ObservableProperty]
    private VoiceChoice? _selectedVoice;

    [ObservableProperty]
    private int _durationSec = 30;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanSubmit))]
    [NotifyPropertyChangedFor(nameof(IsIdle))]
    private bool _isBusy;

    [ObservableProperty]
    private int? _progressPct;

    [ObservableProperty]
    private string? _statusMessage;

    /// <summary>The finished audio. Non-null once a run succeeds.</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasResult))]
    private byte[]? _audio;

    public string? ResultFilename { get; private set; }
    public string? ResultContentType { get; private set; }

    public bool HasResult => Audio is { Length: > 0 };

    public bool IsIdle => !IsBusy;

    public bool CanSubmit => !IsBusy && !string.IsNullOrWhiteSpace(Prompt);

    /// <summary>Raised on success so the caller can attach the audio to the
    /// conversation — the desktop's media results live in the transcript.</summary>
    public event EventHandler<AudioGenerationResult>? Completed;

    [RelayCommand]
    private async Task GenerateAsync()
    {
        if (!CanSubmit)
            return;

        var prompt = Prompt.Trim();
        if (prompt.Length > MaxPromptChars)
        {
            StatusMessage = LocaleService.Instance.T("media.promptTooLong");
            return;
        }

        IsBusy = true;
        ProgressPct = null;
        Audio = null;
        StatusMessage = LocaleService.Instance.T(
            IsSpeech ? "media.generatingSpeech" : "media.generatingMusic");
        _cts = new CancellationTokenSource();
        try
        {
            var request = new AudioGenerationRequest(
                prompt,
                _modelId,
                IsSpeech ? "tts" : "music",
                // Only the field the chosen mode actually uses is sent, so the
                // gateway never sees a duration on a speech job.
                VoiceId: IsSpeech ? SelectedVoice?.Id : null,
                DurationSec: IsSpeech ? null : DurationSec,
                ConversationId: _conversationId);

            var result = await _service.GenerateAsync(request, _cts.Token);
            if (result.Success)
            {
                Audio = result.Audio;
                ResultFilename = result.Filename;
                ResultContentType = result.ContentType;
                StatusMessage = null;
                Completed?.Invoke(this, result);
            }
            else
            {
                StatusMessage = string.IsNullOrWhiteSpace(result.ErrorMessage)
                    ? LocaleService.Instance.T("media.genFailed")
                    : result.ErrorMessage;
            }
        }
        catch (OperationCanceledException)
        {
            StatusMessage = null; // the user cancelled; no error to report
        }
        finally
        {
            IsBusy = false;
            _cts?.Dispose();
            _cts = null;
        }
    }

    [RelayCommand]
    private void Cancel() => _cts?.Cancel();
}

using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Speech;

/// <summary>
/// Backend selector (R10 2D): prefers Azure (Thai support) when a key is
/// stored (DPAPI "azure_speech_key"), else Windows built-in. Events from both
/// children are re-raised here so subscribers survive backend switches.
/// Deploy story: paste an Azure key in Voice Settings → Thai speech works.
/// </summary>
public sealed class CompositeSpeechService : ISpeechService, IDisposable
{
    private readonly ICredentialStore _credentials;
    private readonly ISettingsService _settings;
    private readonly WindowsSpeechService _windows;
    private AzureSpeechService? _azure;
    private ISpeechService _active;

    public event EventHandler<SpeechRecognizedEventArgs>? TextRecognized;
    public event EventHandler<bool>? ListeningChanged;
    public event EventHandler? SpeakingCompleted;

    public CompositeSpeechService(ISettingsService settings, ICredentialStore credentials)
    {
        _settings = settings;
        _credentials = credentials;
        _windows = new WindowsSpeechService();
        Wire(_windows);
        _active = _windows;

        RebuildAzure();
        ApplyPreferredBackend();
    }

    public void ReloadConfiguration()
    {
        RebuildAzure();
        ApplyPreferredBackend();
    }

    /// <summary>(Re)create the Azure child from the stored key; call after the
    /// user saves a new key in Voice Settings.</summary>
    public void RebuildAzure()
    {
        var key = TryLoadKey();
        if (string.IsNullOrEmpty(key))
        {
            if (ReferenceEquals(_active, _azure))
                _active = _windows;
            _azure?.Dispose();
            _azure = null;
            return;
        }

        _azure?.Dispose();
        _azure = new AzureSpeechService(key!, _settings.Current.AzureSpeechRegion);
        Wire(_azure);
        ApplyPreferredBackend();
    }

    private void ApplyPreferredBackend()
    {
        var preferred = _settings.Current.SpeechBackend;
        _active = preferred switch
        {
            "windows" => _windows,
            "azure" when _azure is not null => _azure,
            // "auto"/unset: Azure when available (it has Thai), else Windows.
            _ => (ISpeechService?)_azure ?? _windows
        };
    }

    private string? TryLoadKey()
    {
        try
        {
            return _credentials.Load("azure_speech_key");
        }
        catch (Exception)
        {
            return null;
        }
    }

    private void Wire(ISpeechService child)
    {
        child.TextRecognized += (_, e) =>
        {
            if (ReferenceEquals(child, _active))
                TextRecognized?.Invoke(this, e);
        };
        child.ListeningChanged += (_, e) =>
        {
            if (ReferenceEquals(child, _active))
                ListeningChanged?.Invoke(this, e);
        };
        child.SpeakingCompleted += (_, e) =>
        {
            if (ReferenceEquals(child, _active))
                SpeakingCompleted?.Invoke(this, EventArgs.Empty);
        };
    }

    public IReadOnlyList<SpeechBackend> AvailableBackends
    {
        get
        {
            var backends = new List<SpeechBackend>(_windows.AvailableBackends);
            if (_azure is not null)
                backends.AddRange(_azure.AvailableBackends);
            return backends;
        }
    }

    public SpeechBackend ActiveBackend => _active.ActiveBackend;
    public IReadOnlyList<string> SupportedLanguages => _active.SupportedLanguages;
    public bool IsSpeaking => _active.IsSpeaking;

    public void SwitchBackend(string backendId)
    {
        _ = _active.StopRecognitionAsync();
        _active.StopSpeaking();
        _active = backendId switch
        {
            "azure" when _azure is not null => _azure,
            _ => _windows
        };
    }

    public Task<bool> StartRecognitionAsync(string language, CancellationToken ct = default) =>
        _active.StartRecognitionAsync(language, ct);

    public Task StopRecognitionAsync() => _active.StopRecognitionAsync();

    public Task SpeakAsync(string text, string? voiceName = null, double speed = 1.0, CancellationToken ct = default) =>
        _active.SpeakAsync(text, voiceName, speed, ct);

    public void StopSpeaking() => _active.StopSpeaking();

    public IReadOnlyList<VoiceInfo> GetVoices(string? language = null) => _active.GetVoices(language);

    public Task<bool> TestAzureKeyAsync(string key, string region, CancellationToken ct = default) =>
        AzureSpeechService.ValidateKeyAsync(key, region, ct);

    public void Dispose()
    {
        _windows.Dispose();
        _azure?.Dispose();
    }
}

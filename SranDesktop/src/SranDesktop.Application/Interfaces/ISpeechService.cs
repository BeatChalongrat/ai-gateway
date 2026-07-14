namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Speech abstraction (R10 Fix 2): STT + TTS behind one interface so the app
/// can use Windows built-in speech (free, English-ish) or Azure AI Speech
/// (Thai + 100 locales) — switched by config, no code change at deploy time.
/// </summary>
public interface ISpeechService
{
    /// <summary>Backends usable on this machine (Azure only when a key is set).</summary>
    IReadOnlyList<SpeechBackend> AvailableBackends { get; }

    SpeechBackend ActiveBackend { get; }

    /// <summary>STT languages the active backend accepts.</summary>
    IReadOnlyList<string> SupportedLanguages { get; }

    /// <summary>Switch active backend ("windows" | "azure"); no-op if unavailable.</summary>
    void SwitchBackend(string backendId);

    /// <summary>Start dictation; false when no recognizer/microphone/credential.</summary>
    Task<bool> StartRecognitionAsync(string language, CancellationToken ct = default);

    Task StopRecognitionAsync();

    /// <summary>Speak text (plain text — strip markdown first). speed 1.0 = normal.</summary>
    Task SpeakAsync(string text, string? voiceName = null, double speed = 1.0, CancellationToken ct = default);

    void StopSpeaking();

    bool IsSpeaking { get; }

    IReadOnlyList<VoiceInfo> GetVoices(string? language = null);

    /// <summary>Validate an Azure key/region pair without persisting it.</summary>
    Task<bool> TestAzureKeyAsync(string key, string region, CancellationToken ct = default);

    /// <summary>Re-read stored credentials/settings (call after Voice Settings save).</summary>
    void ReloadConfiguration();

    /// <summary>Recognized phrase (partial when IsFinal=false — Azure only).</summary>
    event EventHandler<SpeechRecognizedEventArgs>? TextRecognized;

    event EventHandler<bool>? ListeningChanged;

    event EventHandler? SpeakingCompleted;
}

public sealed record SpeechBackend(string Id, string DisplayName, bool SupportsThaiStt, bool SupportsThaiTts);

public sealed record VoiceInfo(string Id, string Name, string Language, string Gender);

public sealed record SpeechRecognizedEventArgs(string Text, bool IsFinal);

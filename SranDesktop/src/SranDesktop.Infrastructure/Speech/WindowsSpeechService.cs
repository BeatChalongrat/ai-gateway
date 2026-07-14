using System.Globalization;
using System.Speech.Recognition;
using System.Speech.Synthesis;
using SranDesktop.Application.Interfaces;
using AppVoiceInfo = SranDesktop.Application.Interfaces.VoiceInfo;
using AppSpeechArgs = SranDesktop.Application.Interfaces.SpeechRecognizedEventArgs;

namespace SranDesktop.Infrastructure.Speech;

/// <summary>
/// Windows built-in speech backend (R10 2B) — wraps System.Speech. STT is
/// limited to installed Windows recognizers (typically no Thai); TTS uses
/// installed SAPI voices.
/// </summary>
public sealed class WindowsSpeechService : ISpeechService, IDisposable
{
    private static readonly SpeechBackend Backend =
        new("windows", "Windows Built-in", SupportsThaiStt: false, SupportsThaiTts: false);

    private SpeechRecognitionEngine? _recognizer;
    private SpeechSynthesizer? _synthesizer;

    public IReadOnlyList<SpeechBackend> AvailableBackends { get; } = new[] { Backend };
    public SpeechBackend ActiveBackend => Backend;
    public bool IsSpeaking { get; private set; }

    public IReadOnlyList<string> SupportedLanguages =>
        SpeechRecognitionEngine.InstalledRecognizers()
            .Select(r => r.Culture.Name)
            .Distinct()
            .ToList();

    public event EventHandler<AppSpeechArgs>? TextRecognized;
    public event EventHandler<bool>? ListeningChanged;
    public event EventHandler? SpeakingCompleted;

    public void SwitchBackend(string backendId)
    {
        // Single-backend service — nothing to switch.
    }

    public Task<bool> StartRecognitionAsync(string language, CancellationToken ct = default)
    {
        if (_recognizer is not null)
            return Task.FromResult(true);

        try
        {
            var recognizers = SpeechRecognitionEngine.InstalledRecognizers();
            var match = recognizers.FirstOrDefault(r =>
                            string.Equals(r.Culture.Name, language, StringComparison.OrdinalIgnoreCase))
                        ?? recognizers.FirstOrDefault(r =>
                            r.Culture.TwoLetterISOLanguageName == CultureInfo.InstalledUICulture.TwoLetterISOLanguageName)
                        ?? recognizers.FirstOrDefault();
            if (match is null)
                return Task.FromResult(false);

            _recognizer = new SpeechRecognitionEngine(match);
            _recognizer.SetInputToDefaultAudioDevice();
            _recognizer.LoadGrammar(new DictationGrammar());
            _recognizer.SpeechRecognized += (_, e) =>
            {
                if (e.Result?.Text is { Length: > 0 } text)
                    TextRecognized?.Invoke(this, new AppSpeechArgs(text, IsFinal: true));
            };
            _recognizer.RecognizeAsync(RecognizeMode.Multiple);
            ListeningChanged?.Invoke(this, true);
            return Task.FromResult(true);
        }
        catch (Exception ex) when (ex is InvalidOperationException or ArgumentException or PlatformNotSupportedException)
        {
            _recognizer?.Dispose();
            _recognizer = null;
            return Task.FromResult(false);
        }
    }

    public Task StopRecognitionAsync()
    {
        if (_recognizer is not null)
        {
            try
            {
                _recognizer.RecognizeAsyncCancel();
            }
            finally
            {
                _recognizer.Dispose();
                _recognizer = null;
                ListeningChanged?.Invoke(this, false);
            }
        }
        return Task.CompletedTask;
    }

    public Task SpeakAsync(string text, string? voiceName = null, double speed = 1.0, CancellationToken ct = default)
    {
        StopSpeaking();
        _synthesizer = new SpeechSynthesizer();
        _synthesizer.SetOutputToDefaultAudioDevice();
        // speed 1.0 → rate 0; System.Speech rate spans -10..10.
        _synthesizer.Rate = Math.Clamp((int)Math.Round((speed - 1.0) * 10), -10, 10);
        if (!string.IsNullOrEmpty(voiceName))
        {
            try
            {
                _synthesizer.SelectVoice(voiceName);
            }
            catch (ArgumentException)
            {
                // Saved voice no longer installed — default voice speaks.
            }
        }
        _synthesizer.SpeakCompleted += (_, _) =>
        {
            IsSpeaking = false;
            SpeakingCompleted?.Invoke(this, EventArgs.Empty);
        };
        IsSpeaking = true;
        _synthesizer.SpeakAsync(text);
        return Task.CompletedTask;
    }

    public void StopSpeaking()
    {
        if (_synthesizer is null)
            return;
        try
        {
            _synthesizer.SpeakAsyncCancelAll();
        }
        finally
        {
            _synthesizer.Dispose();
            _synthesizer = null;
            IsSpeaking = false;
        }
    }

    public IReadOnlyList<AppVoiceInfo> GetVoices(string? language = null)
    {
        using var synth = new SpeechSynthesizer();
        return synth.GetInstalledVoices()
            .Where(v => v.Enabled)
            .Where(v => language is null
                || v.VoiceInfo.Culture.Name.StartsWith(language[..2], StringComparison.OrdinalIgnoreCase))
            .Select(v => new AppVoiceInfo(
                v.VoiceInfo.Name, v.VoiceInfo.Name,
                v.VoiceInfo.Culture.Name, v.VoiceInfo.Gender.ToString()))
            .ToList();
    }

    public Task<bool> TestAzureKeyAsync(string key, string region, CancellationToken ct = default) =>
        Task.FromResult(false); // not this backend's concern

    public void ReloadConfiguration()
    {
        // Stateless with respect to config — nothing to reload.
    }

    public void Dispose()
    {
        _ = StopRecognitionAsync();
        StopSpeaking();
    }
}

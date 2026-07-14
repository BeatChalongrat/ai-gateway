using Microsoft.CognitiveServices.Speech;
using Microsoft.CognitiveServices.Speech.Audio;
using SranDesktop.Application.Interfaces;
using AppVoiceInfo = SranDesktop.Application.Interfaces.VoiceInfo;
using AppSpeechArgs = SranDesktop.Application.Interfaces.SpeechRecognizedEventArgs;

namespace SranDesktop.Infrastructure.Speech;

/// <summary>
/// Azure AI Speech backend (R10 2C): continuous STT (Thai included) + neural
/// TTS. Constructed only when a subscription key exists.
/// </summary>
public sealed class AzureSpeechService : ISpeechService, IDisposable
{
    private static readonly SpeechBackend Backend =
        new("azure", "Azure AI Speech", SupportsThaiStt: true, SupportsThaiTts: true);

    private readonly string _subscriptionKey;
    private readonly string _region;

    private SpeechRecognizer? _recognizer;
    private SpeechSynthesizer? _synthesizer;

    public AzureSpeechService(string subscriptionKey, string region)
    {
        _subscriptionKey = subscriptionKey;
        _region = string.IsNullOrWhiteSpace(region) ? "southeastasia" : region;
    }

    public IReadOnlyList<SpeechBackend> AvailableBackends { get; } = new[] { Backend };
    public SpeechBackend ActiveBackend => Backend;
    public bool IsSpeaking { get; private set; }

    public IReadOnlyList<string> SupportedLanguages { get; } = new[]
    {
        "th-TH", "en-US", "en-GB", "ja-JP", "zh-CN", "ko-KR",
        "fr-FR", "de-DE", "es-ES", "pt-BR", "vi-VN", "id-ID"
        // Azure supports 100+ locales; this is the curated picker list.
    };

    public event EventHandler<AppSpeechArgs>? TextRecognized;
    public event EventHandler<bool>? ListeningChanged;
    public event EventHandler? SpeakingCompleted;

    public void SwitchBackend(string backendId)
    {
        // Single-backend service — nothing to switch.
    }

    public async Task<bool> StartRecognitionAsync(string language, CancellationToken ct = default)
    {
        if (_recognizer is not null)
            return true;
        try
        {
            var config = SpeechConfig.FromSubscription(_subscriptionKey, _region);
            config.SpeechRecognitionLanguage =
                string.IsNullOrEmpty(language) ? "th-TH" : language;
            config.SetProperty(PropertyId.SpeechServiceResponse_PostProcessingOption, "TrueText");

            var audio = AudioConfig.FromDefaultMicrophoneInput();
            _recognizer = new SpeechRecognizer(config, audio);

            _recognizer.Recognizing += (_, e) =>
            {
                if (e.Result.Text is { Length: > 0 } text)
                    TextRecognized?.Invoke(this, new AppSpeechArgs(text, IsFinal: false));
            };
            _recognizer.Recognized += (_, e) =>
            {
                if (e.Result.Reason == ResultReason.RecognizedSpeech
                    && e.Result.Text is { Length: > 0 } text)
                    TextRecognized?.Invoke(this, new AppSpeechArgs(text, IsFinal: true));
            };

            await _recognizer.StartContinuousRecognitionAsync();
            ListeningChanged?.Invoke(this, true);
            return true;
        }
        catch (Exception ex) when (ex is ApplicationException or InvalidOperationException or IOException)
        {
            _recognizer?.Dispose();
            _recognizer = null;
            return false;
        }
    }

    public async Task StopRecognitionAsync()
    {
        if (_recognizer is not null)
        {
            try
            {
                await _recognizer.StopContinuousRecognitionAsync();
            }
            finally
            {
                _recognizer.Dispose();
                _recognizer = null;
                ListeningChanged?.Invoke(this, false);
            }
        }
    }

    public async Task SpeakAsync(string text, string? voiceName = null, double speed = 1.0, CancellationToken ct = default)
    {
        StopSpeaking();
        var config = SpeechConfig.FromSubscription(_subscriptionKey, _region);
        var voice = string.IsNullOrEmpty(voiceName) ? "th-TH-PremwadeeNeural" : voiceName!;
        config.SpeechSynthesisVoiceName = voice;

        var lang = voice.Length >= 5 ? voice[..5] : "th-TH";
        var rate = Math.Clamp(speed, 0.5, 2.0);
        var ssml = $"""
            <speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{lang}'>
                <voice name='{voice}'>
                    <prosody rate='{rate.ToString("F1", System.Globalization.CultureInfo.InvariantCulture)}'>{System.Security.SecurityElement.Escape(text)}</prosody>
                </voice>
            </speak>
            """;

        _synthesizer = new SpeechSynthesizer(config,
            AudioConfig.FromDefaultSpeakerOutput());
        IsSpeaking = true;
        try
        {
            var result = await _synthesizer.SpeakSsmlAsync(ssml);
            _ = result; // playback runs to completion inside SpeakSsmlAsync
        }
        finally
        {
            IsSpeaking = false;
            SpeakingCompleted?.Invoke(this, EventArgs.Empty);
        }
    }

    public void StopSpeaking()
    {
        if (_synthesizer is null)
            return;
        try
        {
            _synthesizer.StopSpeakingAsync().GetAwaiter().GetResult();
        }
        catch (Exception)
        {
            // Synth already stopped/faulted.
        }
        finally
        {
            _synthesizer.Dispose();
            _synthesizer = null;
            IsSpeaking = false;
        }
    }

    public IReadOnlyList<AppVoiceInfo> GetVoices(string? language = null) => language switch
    {
        null or "th-TH" => new[]
        {
            new AppVoiceInfo("th-TH-PremwadeeNeural", "Premwadee (หญิง)", "th-TH", "Female"),
            new AppVoiceInfo("th-TH-NiwatNeural", "Niwat (ชาย)", "th-TH", "Male"),
            new AppVoiceInfo("th-TH-AcharaNeural", "Achara (หญิง)", "th-TH", "Female"),
        },
        "en-US" => new[]
        {
            new AppVoiceInfo("en-US-JennyNeural", "Jenny", "en-US", "Female"),
            new AppVoiceInfo("en-US-GuyNeural", "Guy", "en-US", "Male"),
        },
        _ => Array.Empty<AppVoiceInfo>()
    };

    /// <summary>Key check: fetch the voices list — fails fast on a bad key/region.</summary>
    public static async Task<bool> ValidateKeyAsync(string key, string region, CancellationToken ct = default)
    {
        try
        {
            var config = SpeechConfig.FromSubscription(key, region);
            using var synth = new SpeechSynthesizer(config, null as AudioConfig);
            using var result = await synth.GetVoicesAsync();
            return result.Reason == ResultReason.VoicesListRetrieved;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public Task<bool> TestAzureKeyAsync(string key, string region, CancellationToken ct = default) =>
        ValidateKeyAsync(key, region, ct);

    public void ReloadConfiguration()
    {
        // Key/region are ctor-fixed; the composite rebuilds this instance instead.
    }

    public void Dispose()
    {
        _ = StopRecognitionAsync();
        StopSpeaking();
    }
}

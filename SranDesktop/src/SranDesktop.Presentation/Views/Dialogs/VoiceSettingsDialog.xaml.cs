using System.Windows;
using System.Windows.Controls;
using SranDesktop.Application.Interfaces;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>
/// Voice settings (R9 J3, extended in R10 2F): backend selector
/// (Windows / Azure), Azure key + region + Test, recognition language,
/// TTS voice, speed, auto-read. The Azure key is stored in DPAPI only.
/// </summary>
public partial class VoiceSettingsDialog : Window
{
    private const string AzureKeyCredential = "azure_speech_key";

    private readonly ISettingsService _settings;
    private readonly ICredentialStore _credentials;
    private readonly ISpeechService _speech;

    public VoiceSettingsDialog(
        ISettingsService settings, ICredentialStore credentials, ISpeechService speech)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        _settings = settings;
        _credentials = credentials;
        _speech = speech;

        // Backends: Windows always; Azure always listed (configuring the key
        // here is exactly how it becomes usable).
        BackendBox.Items.Add(new ComboBoxItem { Content = "Windows Built-in", Tag = "windows" });
        BackendBox.Items.Add(new ComboBoxItem { Content = "Azure AI Speech", Tag = "azure" });
        var backend = settings.Current.SpeechBackend == "windows" ? "windows"
            : settings.Current.SpeechBackend == "azure" ? "azure"
            : _speech.ActiveBackend.Id;
        foreach (var item in BackendBox.Items.OfType<ComboBoxItem>())
            if (item.Tag as string == backend)
                BackendBox.SelectedItem = item;
        BackendBox.SelectedItem ??= BackendBox.Items[0];

        try
        {
            AzureKeyBox.Password = _credentials.Load(AzureKeyCredential) ?? "";
        }
        catch (Exception)
        {
            // No stored key.
        }
        RegionBox.Text = settings.Current.AzureSpeechRegion;

        RateSlider.Value = settings.Current.VoiceRate;
        AutoReadBox.IsChecked = settings.Current.VoiceAutoRead;

        RefreshLanguagesAndVoices();
    }

    private string SelectedBackend =>
        (BackendBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "windows";

    private void Backend_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (AzurePanel is null) return; // during InitializeComponent
        AzurePanel.Visibility = SelectedBackend == "azure" ? Visibility.Visible : Visibility.Collapsed;
        RefreshLanguagesAndVoices();
    }

    private void RefreshLanguagesAndVoices()
    {
        if (LanguageBox is null || VoiceBox is null) return;

        LanguageBox.Items.Clear();
        VoiceBox.Items.Clear();

        var azure = SelectedBackend == "azure";
        var languages = azure
            ? new[] { "th-TH", "en-US", "en-GB", "ja-JP", "zh-CN", "ko-KR", "fr-FR", "de-DE", "vi-VN" }
            : _speech.AvailableBackends.Any(b => b.Id == "windows")
                ? _speech.GetVoices().Select(v => v.Language).Distinct().DefaultIfEmpty("en-US").ToArray()
                : new[] { "en-US" };

        foreach (var lang in languages)
            LanguageBox.Items.Add(lang);
        var savedLang = _settings.Current.SpeechLanguage;
        LanguageBox.SelectedItem = LanguageBox.Items.Contains(savedLang)
            ? savedLang
            : LanguageBox.Items.Count > 0 ? LanguageBox.Items[0] : null;

        // Voices: Azure exposes curated neural voices; Windows the SAPI list.
        var voices = azure
            ? new[]
              {
                  new VoiceInfo("th-TH-PremwadeeNeural", "Premwadee (หญิง, ไทย)", "th-TH", "Female"),
                  new VoiceInfo("th-TH-NiwatNeural", "Niwat (ชาย, ไทย)", "th-TH", "Male"),
                  new VoiceInfo("en-US-JennyNeural", "Jenny (EN)", "en-US", "Female"),
                  new VoiceInfo("en-US-GuyNeural", "Guy (EN)", "en-US", "Male"),
              }
            : _speech.GetVoices().ToArray();

        foreach (var voice in voices)
            VoiceBox.Items.Add(voice);
        var savedVoice = _settings.Current.VoiceName;
        VoiceBox.SelectedValue = voices.Any(v => v.Id == savedVoice) ? savedVoice
            : voices.Length > 0 ? voices[0].Id : null;
    }

    private async void Test_Click(object sender, RoutedEventArgs e)
    {
        TestButton.IsEnabled = false;
        TestResult.Text = "...";
        try
        {
            var ok = await _speech.TestAzureKeyAsync(AzureKeyBox.Password.Trim(), RegionBox.Text.Trim());
            TestResult.Text = LocaleService.Instance.T(ok ? "voice.testOk" : "voice.testFail");
            TestResult.Foreground = (System.Windows.Media.Brush)FindResource(
                ok ? "AccentGreenBrush" : "AccentRedBrush");
        }
        finally
        {
            TestButton.IsEnabled = true;
        }
    }

    private void RateSlider_ValueChanged(object sender,
        RoutedPropertyChangedEventArgs<double> e)
    {
        if (RateLabel is not null)
            RateLabel.Text = ((int)e.NewValue).ToString();
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        var updated = _settings.Current.Clone();
        updated.SpeechBackend = SelectedBackend;
        updated.SpeechLanguage = LanguageBox.SelectedItem as string ?? "";
        updated.AzureSpeechRegion = RegionBox.Text.Trim();
        updated.VoiceName = VoiceBox.SelectedValue as string ?? "";
        updated.VoiceRate = (int)RateSlider.Value;
        updated.VoiceAutoRead = AutoReadBox.IsChecked == true;
        _settings.Save(updated);

        // Key → DPAPI only (R10: never plaintext in settings.json).
        var key = AzureKeyBox.Password.Trim();
        try
        {
            if (key.Length > 0)
                _credentials.Save(AzureKeyCredential, key);
            else
                _credentials.Delete(AzureKeyCredential);
        }
        catch (Exception)
        {
            // Credential store failure — settings still saved.
        }

        // Re-read the key + apply the chosen backend immediately.
        _speech.ReloadConfiguration();
        _speech.SwitchBackend(SelectedBackend);

        DialogResult = true;
    }
}

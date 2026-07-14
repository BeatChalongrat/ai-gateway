using System.IO;
using System.Text;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// "Learn my style" (R9 Feature L): the user pastes writing samples, a small
/// gateway completion distills them into a style-profile prompt fragment that
/// is stored in settings and injected whenever the AI drafts prose.
/// </summary>
public partial class WritingStyleViewModel : ObservableObject
{
    private readonly IGatewayApiClient _gateway;
    private readonly ISettingsService _settings;
    private readonly ILogger<WritingStyleViewModel> _logger;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(AnalyzeCommand))]
    private string _samples = string.Empty;

    [ObservableProperty] private bool _isAnalyzing;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasProfile))]
    private string _profile;

    public bool HasProfile => !string.IsNullOrWhiteSpace(Profile);

    [ObservableProperty] private string? _errorMessage;

    public WritingStyleViewModel(
        IGatewayApiClient gateway, ISettingsService settings,
        ILogger<WritingStyleViewModel> logger)
    {
        _gateway = gateway;
        _settings = settings;
        _logger = logger;
        _profile = settings.Current.WritingStyleProfile;
    }

    private bool CanAnalyze() => !IsAnalyzing && Samples.Trim().Length >= 100;

    [RelayCommand(CanExecute = nameof(CanAnalyze))]
    private async Task AnalyzeAsync()
    {
        IsAnalyzing = true;
        ErrorMessage = null;
        try
        {
            var request = new GatewayCompletionRequest
            {
                Model = "auto",
                Messages = new List<GatewayMessage>
                {
                    new()
                    {
                        Role = "system",
                        Content = "You are a writing-style analyst. Given samples of a person's " +
                                  "writing, produce a concise style profile (4-8 bullet points) " +
                                  "covering tone, sentence length, vocabulary level, and formatting " +
                                  "preferences. Write it as instructions an AI can follow to mimic " +
                                  "the style. Reply with the bullet points only."
                    },
                    new() { Role = "user", Content = Samples.Trim() }
                }
            };

            var parser = new GatewayChunkParser();
            var result = new StringBuilder();
            await foreach (var frame in _gateway.StreamCompletionAsync(request))
            {
                foreach (var evt in parser.Parse(frame.Data))
                {
                    if (evt is AgentContentDeltaEvent delta)
                        result.Append(delta.Text);
                    else if (evt is AgentErrorEvent error)
                        throw new InvalidOperationException(error.Message);
                }
            }

            var profile = result.ToString().Trim();
            if (profile.Length == 0)
                throw new InvalidOperationException("The analysis returned nothing.");

            Profile = profile;
            SaveProfile(profile);
        }
        catch (Exception ex) when (ex is System.Net.Http.HttpRequestException
                                       or InvalidOperationException or IOException)
        {
            _logger.LogWarning(ex, "Style analysis failed");
            ErrorMessage = ex.Message;
        }
        finally
        {
            IsAnalyzing = false;
        }
    }

    [RelayCommand]
    private void ClearProfile()
    {
        Profile = string.Empty;
        SaveProfile(string.Empty);
    }

    private void SaveProfile(string profile)
    {
        var updated = _settings.Current.Clone();
        updated.WritingStyleProfile = profile;
        _settings.Save(updated);
    }
}

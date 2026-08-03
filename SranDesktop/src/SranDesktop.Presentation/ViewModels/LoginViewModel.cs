using System.Diagnostics;
using System.Net;
using System.Net.Http;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Application.Interfaces;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.Services;

namespace SranDesktop.Presentation.ViewModels;

public partial class LoginViewModel : ObservableObject
{
    private readonly IAuthService _authService;
    private readonly ISettingsService _settings;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private string _email = string.Empty;

    /// <summary>Set from LoginView code-behind (PasswordBox does not support binding).</summary>
    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private string _password = string.Empty;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasError))]
    private string? _errorMessage;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private bool _isBusy;

    public bool HasError => ErrorMessage is not null;

    // ── Password show/hide ──────────────────────────────────────────────────

    [ObservableProperty]
    private bool _isPasswordHidden = true;

    [RelayCommand]
    private void TogglePasswordVisibility() => IsPasswordHidden = !IsPasswordHidden;

    // ── Theme toggle (sun/moon, top-right of the login screen) ──────────────

    [ObservableProperty]
    private bool _isDarkTheme = ThemeManager.IsDark;

    [RelayCommand]
    private void ToggleTheme()
    {
        var newTheme = IsDarkTheme ? "Light" : "Dark";
        ThemeManager.Apply(newTheme);
        IsDarkTheme = ThemeManager.IsDark;

        // Persist so the choice survives restarts (same as the in-app toggle).
        var updated = _settings.Current.Clone();
        updated.Theme = newTheme;
        _settings.Save(updated);
    }

    public event EventHandler? LoginSucceeded;

    /// <summary>Escape hatch (FIX-A): the login overlay covers the whole window
    /// while unauthenticated, so a wrong server URL used to be a dead-end (the
    /// only way out was hand-deleting settings.json). MainWindow subscribes and
    /// opens the Settings dialog — no authentication required.</summary>
    public event EventHandler? ServerSettingsRequested;

    [RelayCommand]
    private void OpenServerSettings() =>
        ServerSettingsRequested?.Invoke(this, EventArgs.Empty);

    public LoginViewModel(IAuthService authService, ISettingsService settings)
    {
        _authService = authService;
        _settings = settings;
    }

    private bool CanLogin() =>
        !IsBusy
        && !string.IsNullOrWhiteSpace(Email)
        && Password.Length > 0;

    [RelayCommand(CanExecute = nameof(CanLogin))]
    private async Task LoginAsync()
    {
        ErrorMessage = null;
        IsBusy = true;
        try
        {
            await _authService.LoginAsync(Email.Trim(), Password);
            Password = string.Empty;
            LoginSucceeded?.Invoke(this, EventArgs.Empty);
        }
        catch (HttpRequestException ex) when (ex.StatusCode is HttpStatusCode.Unauthorized
                                              or HttpStatusCode.Forbidden)
        {
            ErrorMessage = LocaleService.Instance.T("login.errorInvalid");
        }
        catch (HttpRequestException)
        {
            ErrorMessage = LocaleService.Instance.T("login.errorUnreachable");
        }
        catch (TaskCanceledException)
        {
            ErrorMessage = LocaleService.Instance.T("login.errorTimeout");
        }
        finally
        {
            IsBusy = false;
        }
    }

    [RelayCommand]
    private void ForgotPassword() => OpenInBrowser($"{PortalWebUrl}/forgot-password");

    private string PortalWebUrl => DerivePortalWebUrl(_settings.Current.GatewayBaseUrl);

    private void OpenInBrowser(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch (Exception)
        {
            // No browser / blocked by policy — nothing sensible to do.
        }
    }

    /// <summary>
    /// Portal web base for the forgot-password link. Production runs single-URL
    /// mode where nginx serves the Portal at the root path; local split-port dev
    /// has the Portal on :3001 instead of the gateway's :8080.
    /// </summary>
    public static string DerivePortalWebUrl(string gatewayBaseUrl)
    {
        var trimmed = gatewayBaseUrl.TrimEnd('/');
        return FirstRunViewModel.IsLocalDevUrl(trimmed)
            ? "http://localhost:3001"
            : trimmed;
    }
}

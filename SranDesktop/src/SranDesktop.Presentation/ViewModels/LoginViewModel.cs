using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.Services;

namespace SranDesktop.Presentation.ViewModels;

public partial class LoginViewModel : ObservableObject
{
    private readonly IAuthService _authService;
    private readonly ISettingsService _settings;
    private readonly LoginOAuthCoordinator? _oauth;
    private readonly SynchronizationContext? _uiContext;

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

    public LoginViewModel(
        IAuthService authService,
        ISettingsService settings,
        LoginOAuthCoordinator? oauthCoordinator = null)
    {
        _authService = authService;
        _settings = settings;
        _oauth = oauthCoordinator;
        // Created on the UI thread by DI; coordinator events arrive on
        // background threads and must be marshalled back before touching
        // bound state. Null in unit tests → handlers run inline.
        _uiContext = SynchronizationContext.Current;

        if (_oauth is not null)
        {
            _oauth.SignInSucceeded += (_, _) => OnUi(() =>
            {
                IsOAuthWaiting = false;
                LoginSucceeded?.Invoke(this, EventArgs.Empty);
            });
            _oauth.SignInFailed += (_, reason) => OnUi(() =>
            {
                IsOAuthWaiting = false;
                ErrorMessage = LocaleService.Instance.T(
                    reason == "browser_failed" ? "login.errorBrowser" : "login.errorGoogle");
            });
        }

        // Server UI truth for the password form (Phase 3 cutover hides it
        // without a desktop release). Fire-and-forget; unreachable server
        // keeps the fail-open default (form visible).
        _ = LoadPublicSettingsAsync();
    }

    // ── Google sign-in (SSO Phase 2) ────────────────────────────────────────

    /// <summary>True from "browser opened" until the deep link resolves —
    /// shows the "finish in your browser" hint. The button stays enabled
    /// so an abandoned browser tab never dead-ends the login screen.</summary>
    [ObservableProperty]
    private bool _isOAuthWaiting;

    /// <summary>Whether the email+password form renders. UI truth from
    /// /portal/auth/public-settings; the 403 on /login is the enforcement.</summary>
    [ObservableProperty]
    private bool _isPasswordLoginVisible = true;

    public bool IsGoogleSignInAvailable => _oauth is not null;

    [RelayCommand]
    private void SignInWithGoogle()
    {
        if (_oauth is null)
            return;
        ErrorMessage = null;
        IsOAuthWaiting = _oauth.StartSignIn();
    }

    internal async Task LoadPublicSettingsAsync()
    {
        try
        {
            var publicSettings = await _authService.GetPublicSettingsAsync();
            OnUi(() => IsPasswordLoginVisible = publicSettings.PasswordLoginEnabled);
        }
        catch (Exception)
        {
            // Offline / server down: keep the default (form visible) so
            // password login still works when the flag is on.
        }
    }

    private void OnUi(Action action)
    {
        if (_uiContext is not null)
            _uiContext.Post(_ => action(), null);
        else
            action();
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

# Round 12 Fix — Simplified Login Screen

## Context

The current login screen (built in R12) uses a split-screen layout: branding panel on the left (45%), login form on the right (55%), with OAuth buttons (Google/GitHub), sign-up link, and server status footer. The customer wants it **simplified** to a centered, clean design — just logo + email + password + sign in. No OAuth, no server status, no branding panel.

**What the user approved (mockup)**:
- Centered card on full-window background
- SRAN cat logo (use existing `LogoSource` from theme resources — already a cat icon)
- "SRAN®" text below logo
- Email field with mail icon (already built: `Geo_Mail`)
- Password field with lock icon + eye toggle (already built: `Geo_Lock`, `Geo_Eye`, `Geo_EyeOff`)
- "Sign in" button (accent)
- "Forgot password?" link
- Error card (keep existing)
- Fade-in entrance animation (keep existing)
- NO OAuth buttons (Google/GitHub)
- NO "or continue with" divider
- NO sign-up link
- NO server status footer (green dot + server name)
- NO branding panel (left 45%)
- NO responsive collapse logic

## Files to Modify

### 1. `src/SranDesktop.Presentation/Views/LoginView.xaml` — REPLACE ENTIRELY

Replace the entire content with this centered layout:

```xml
<UserControl x:Class="SranDesktop.Presentation.Views.LoginView"
             xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
             xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
             xmlns:d="http://schemas.microsoft.com/expression/blend/2008"
             xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
             xmlns:conv="clr-namespace:SranDesktop.Presentation.Converters"
             xmlns:controls="clr-namespace:SranDesktop.Presentation.Controls"
             xmlns:loc="clr-namespace:SranDesktop.Presentation.Localization"
             xmlns:vm="clr-namespace:SranDesktop.Presentation.ViewModels"
             mc:Ignorable="d"
             d:DataContext="{d:DesignInstance Type=vm:LoginViewModel}"
             IsVisibleChanged="LoginView_IsVisibleChanged"
             Background="{DynamicResource BgPrimaryBrush}">

    <UserControl.Resources>
        <conv:BoolToVisibilityConverter x:Key="BoolToVisibility" />

        <!-- Chromeless inner boxes — strip the app-wide TextBox/PasswordBox
             chrome to avoid double borders inside the icon containers. -->
        <Style x:Key="BareTextBox" TargetType="TextBox">
            <Setter Property="Background" Value="Transparent" />
            <Setter Property="BorderThickness" Value="0" />
            <Setter Property="Foreground" Value="{DynamicResource TextPrimaryBrush}" />
            <Setter Property="CaretBrush" Value="{DynamicResource TextPrimaryBrush}" />
            <Setter Property="SelectionBrush" Value="{DynamicResource BgSelectedBrush}" />
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="TextBox">
                        <Grid>
                            <ScrollViewer x:Name="PART_ContentHost"
                                          VerticalAlignment="Center" Focusable="False" />
                            <TextBlock x:Name="Watermark"
                                       Text="{Binding Path=(controls:WatermarkService.Text), RelativeSource={RelativeSource TemplatedParent}}"
                                       Foreground="{DynamicResource TextTertiaryBrush}"
                                       Padding="2,0,0,0" VerticalAlignment="Center"
                                       IsHitTestVisible="False" Visibility="Collapsed"
                                       TextTrimming="CharacterEllipsis" />
                        </Grid>
                        <ControlTemplate.Triggers>
                            <Trigger Property="Text" Value="">
                                <Setter TargetName="Watermark" Property="Visibility" Value="Visible" />
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style x:Key="BarePasswordBox" TargetType="PasswordBox">
            <Setter Property="Background" Value="Transparent" />
            <Setter Property="BorderThickness" Value="0" />
            <Setter Property="Foreground" Value="{DynamicResource TextPrimaryBrush}" />
            <Setter Property="CaretBrush" Value="{DynamicResource TextPrimaryBrush}" />
            <Setter Property="SelectionBrush" Value="{DynamicResource BgSelectedBrush}" />
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="PasswordBox">
                        <ScrollViewer x:Name="PART_ContentHost"
                                      VerticalAlignment="Center" Focusable="False" />
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
    </UserControl.Resources>

    <!-- Full-window centered layout — no columns, no branding panel -->
    <Grid>
        <StackPanel VerticalAlignment="Center" HorizontalAlignment="Center"
                    Width="340">

            <!-- ═══ Logo ═══ -->
            <StackPanel HorizontalAlignment="Center" Margin="0,0,0,32">
                <Image Source="{DynamicResource LogoSource}"
                       Width="64" Height="64"
                       RenderOptions.BitmapScalingMode="HighQuality"
                       HorizontalAlignment="Center" />
            </StackPanel>

            <!-- ═══ Email ═══ -->
            <TextBlock Text="{loc:T login.email}" FontSize="12"
                       Foreground="{DynamicResource TextSecondaryBrush}"
                       Margin="0,0,0,6" />
            <Border x:Name="EmailBorder"
                    CornerRadius="8" BorderThickness="1"
                    BorderBrush="{DynamicResource BorderBrush}"
                    Background="{DynamicResource BgSecondaryBrush}"
                    Padding="12,0" Height="42" Margin="0,0,0,16">
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="Auto" />
                        <ColumnDefinition Width="*" />
                    </Grid.ColumnDefinitions>
                    <controls:IconView Grid.Column="0" Width="16" Height="16"
                                       Geometry="{StaticResource Geo_Mail}"
                                       Stroke="{DynamicResource TextTertiaryBrush}"
                                       VerticalAlignment="Center" Margin="0,0,10,0" />
                    <TextBox Grid.Column="1" x:Name="EmailBox"
                             Style="{StaticResource BareTextBox}"
                             Text="{Binding Email, UpdateSourceTrigger=PropertyChanged}"
                             controls:WatermarkService.Text="{loc:T login.emailPlaceholder}"
                             FontSize="13" VerticalAlignment="Center"
                             GotKeyboardFocus="EmailBox_GotFocus"
                             LostKeyboardFocus="EmailBox_LostFocus" />
                </Grid>
            </Border>

            <!-- ═══ Password ═══ -->
            <TextBlock Text="{loc:T login.password}" FontSize="12"
                       Foreground="{DynamicResource TextSecondaryBrush}"
                       Margin="0,0,0,6" />
            <Border x:Name="PasswordBorder"
                    CornerRadius="8" BorderThickness="1"
                    BorderBrush="{DynamicResource BorderBrush}"
                    Background="{DynamicResource BgSecondaryBrush}"
                    Padding="12,0" Height="42" Margin="0,0,0,8">
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="Auto" />
                        <ColumnDefinition Width="*" />
                        <ColumnDefinition Width="Auto" />
                    </Grid.ColumnDefinitions>
                    <controls:IconView Grid.Column="0" Width="16" Height="16"
                                       Geometry="{StaticResource Geo_Lock}"
                                       Stroke="{DynamicResource TextTertiaryBrush}"
                                       VerticalAlignment="Center" Margin="0,0,10,0" />
                    <!-- Hidden (default) -->
                    <PasswordBox Grid.Column="1" x:Name="PasswordInput"
                                 Style="{StaticResource BarePasswordBox}"
                                 FontSize="13" VerticalAlignment="Center"
                                 PasswordChanged="PasswordInput_PasswordChanged"
                                 KeyDown="PasswordInput_KeyDown"
                                 GotKeyboardFocus="PasswordField_GotFocus"
                                 LostKeyboardFocus="PasswordField_LostFocus"
                                 Visibility="{Binding IsPasswordHidden, Converter={StaticResource BoolToVisibility}}" />
                    <!-- Revealed -->
                    <TextBox Grid.Column="1" x:Name="PasswordRevealBox"
                             Style="{StaticResource BareTextBox}"
                             Text="{Binding Password, UpdateSourceTrigger=PropertyChanged}"
                             FontSize="13" VerticalAlignment="Center"
                             KeyDown="PasswordInput_KeyDown"
                             GotKeyboardFocus="PasswordField_GotFocus"
                             LostKeyboardFocus="PasswordField_LostFocus"
                             Visibility="{Binding IsPasswordHidden, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}" />
                    <!-- Eye toggle -->
                    <Button Grid.Column="2" Width="28" Height="28"
                            Style="{DynamicResource IconButton}"
                            Command="{Binding TogglePasswordVisibilityCommand}"
                            ToolTip="{loc:T login.showPassword}"
                            Focusable="False">
                        <Grid>
                            <controls:IconView Width="14" Height="14"
                                               Geometry="{StaticResource Geo_Eye}"
                                               Stroke="{DynamicResource TextTertiaryBrush}"
                                               Visibility="{Binding IsPasswordHidden, Converter={StaticResource BoolToVisibility}}" />
                            <controls:IconView Width="14" Height="14"
                                               Geometry="{StaticResource Geo_EyeOff}"
                                               Stroke="{DynamicResource TextTertiaryBrush}"
                                               Visibility="{Binding IsPasswordHidden, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}" />
                        </Grid>
                    </Button>
                </Grid>
            </Border>

            <!-- Forgot password -->
            <TextBlock HorizontalAlignment="Right" Margin="0,0,0,20">
                <Hyperlink Command="{Binding ForgotPasswordCommand}"
                           Foreground="{DynamicResource AccentBlueBrush}"
                           TextDecorations="{x:Null}">
                    <Run Text="{loc:T login.forgotPassword}" FontSize="12" />
                </Hyperlink>
            </TextBlock>

            <!-- Error card -->
            <Border CornerRadius="8" Padding="12,10" Margin="0,0,0,16"
                    Background="{DynamicResource DangerHoverBgBrush}"
                    Visibility="{Binding HasError, Converter={StaticResource BoolToVisibility}}">
                <StackPanel Orientation="Horizontal">
                    <controls:IconView Width="14" Height="14"
                                       Geometry="{StaticResource Geo_AlertCircle}"
                                       Stroke="{DynamicResource AccentRedBrush}"
                                       VerticalAlignment="Top" Margin="0,1,8,0" />
                    <TextBlock Text="{Binding ErrorMessage}" FontSize="12.5"
                               Foreground="{DynamicResource AccentRedBrush}"
                               TextWrapping="Wrap" MaxWidth="300" />
                </StackPanel>
            </Border>

            <!-- Sign in -->
            <Button Style="{DynamicResource AccentButton}"
                    controls:ButtonHelper.CornerRadius="8"
                    Command="{Binding LoginCommand}"
                    Height="42" FontSize="14"
                    IsDefault="True">
                <Grid>
                    <TextBlock Text="{loc:T login.signIn}"
                               FontSize="14" FontWeight="SemiBold"
                               HorizontalAlignment="Center" VerticalAlignment="Center"
                               Visibility="{Binding IsBusy, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}" />
                    <StackPanel Orientation="Horizontal" HorizontalAlignment="Center"
                                Visibility="{Binding IsBusy, Converter={StaticResource BoolToVisibility}}">
                        <Border Width="16" Height="16" CornerRadius="8"
                                BorderThickness="2"
                                BorderBrush="{DynamicResource OnAccentBrush}"
                                Opacity="0.7" Margin="0,0,8,0"
                                VerticalAlignment="Center"
                                RenderTransformOrigin="0.5,0.5">
                            <Border.RenderTransform>
                                <RotateTransform />
                            </Border.RenderTransform>
                            <Border.OpacityMask>
                                <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
                                    <GradientStop Color="Transparent" Offset="0" />
                                    <GradientStop Color="Black" Offset="0.7" />
                                </LinearGradientBrush>
                            </Border.OpacityMask>
                            <Border.Triggers>
                                <EventTrigger RoutedEvent="Loaded">
                                    <BeginStoryboard>
                                        <Storyboard RepeatBehavior="Forever">
                                            <DoubleAnimation Storyboard.TargetProperty="(UIElement.RenderTransform).(RotateTransform.Angle)"
                                                             From="0" To="360" Duration="0:0:1" />
                                        </Storyboard>
                                    </BeginStoryboard>
                                </EventTrigger>
                            </Border.Triggers>
                        </Border>
                        <TextBlock Text="{loc:T login.signingIn}"
                                   FontSize="14" FontWeight="SemiBold"
                                   VerticalAlignment="Center" />
                    </StackPanel>
                </Grid>
            </Button>

            <!-- Forgot password (centered below button) -->
            <TextBlock HorizontalAlignment="Center" Margin="0,16,0,0">
                <Hyperlink Command="{Binding ForgotPasswordCommand}"
                           Foreground="{DynamicResource AccentBlueBrush}"
                           TextDecorations="{x:Null}">
                    <Run Text="{loc:T login.forgotPassword}" FontSize="12" />
                </Hyperlink>
            </TextBlock>

        </StackPanel>
    </Grid>
</UserControl>
```

**Wait — there's a duplicate "Forgot password?" in the template above.** Remove the first one (right-aligned, before the error card). Keep only the centered one below the Sign in button. The corrected structure is:

```
Email field
Password field (with eye toggle)
Error card (conditional)
Sign in button
Forgot password? (centered, below button)
```

### 2. `src/SranDesktop.Presentation/Views/LoginView.xaml.cs` — SIMPLIFY

Remove:
- The `SizeChanged="LoginView_SizeChanged"` handler reference (remove from XAML too — already done above)
- The `LoginView_SizeChanged` method and `BrandingMinWindowWidth` constant (no branding column to collapse)
- The server status check on visibility change (`_viewModel?.CheckServerStatusCommand.Execute(null);` in `LoginView_IsVisibleChanged`)
- The server status check in `LoginView_DataContextChanged`

Keep:
- `LoginView_IsVisibleChanged` — but only the fade animation + focus logic (remove the `CheckServerStatusCommand` call)
- `LoginView_DataContextChanged` — but only `PropertyChanged` wiring (remove `CheckServerStatusCommand` call)
- All focus ring methods (`EmailBox_GotFocus`, `EmailBox_LostFocus`, `PasswordField_GotFocus`, `PasswordField_LostFocus`, `SetFocusRing`)
- `PasswordInput_PasswordChanged` and `PasswordInput_KeyDown`

Replace with:

```csharp
using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

public partial class LoginView : UserControl
{
    private LoginViewModel? _viewModel;

    public LoginView()
    {
        InitializeComponent();
        DataContextChanged += LoginView_DataContextChanged;
    }

    private void LoginView_DataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (_viewModel is not null)
            _viewModel.PropertyChanged -= ViewModel_PropertyChanged;
        _viewModel = e.NewValue as LoginViewModel;
        if (_viewModel is not null)
            _viewModel.PropertyChanged += ViewModel_PropertyChanged;
    }

    private void ViewModel_PropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (_viewModel is null) return;
        if (e.PropertyName == nameof(LoginViewModel.Password)
            && PasswordInput.Password != _viewModel.Password)
        {
            PasswordInput.Password = _viewModel.Password;
        }
    }

    // ── Entrance fade-in ────────────────────────────────────────────────────

    private void LoginView_IsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (e.NewValue is not true) return;

        var fade = new DoubleAnimation(0, 1, TimeSpan.FromSeconds(0.4))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
        };
        BeginAnimation(OpacityProperty, fade);

        Dispatcher.BeginInvoke(() =>
        {
            if (_viewModel?.IsPasswordHidden is false)
                PasswordRevealBox.Focus();
            else
                EmailBox.Focus();
        }, System.Windows.Threading.DispatcherPriority.Input);
    }

    // ── Focus ring ──────────────────────────────────────────────────────────

    private void EmailBox_GotFocus(object sender, RoutedEventArgs e) =>
        SetFocusRing(EmailBorder, focused: true);

    private void EmailBox_LostFocus(object sender, RoutedEventArgs e) =>
        SetFocusRing(EmailBorder, focused: false);

    private void PasswordField_GotFocus(object sender, RoutedEventArgs e) =>
        SetFocusRing(PasswordBorder, focused: true);

    private void PasswordField_LostFocus(object sender, RoutedEventArgs e) =>
        SetFocusRing(PasswordBorder, focused: false);

    private void SetFocusRing(Border border, bool focused) =>
        border.BorderBrush = FindResource(focused ? "AccentBlueBrush" : "BorderBrush") as Brush;

    // ── Password plumbing ───────────────────────────────────────────────────

    private void PasswordInput_PasswordChanged(object sender, RoutedEventArgs e)
    {
        if (DataContext is LoginViewModel vm)
            vm.Password = ((PasswordBox)sender).Password;
    }

    private void PasswordInput_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && DataContext is LoginViewModel vm && vm.LoginCommand.CanExecute(null))
            vm.LoginCommand.Execute(null);
    }
}
```

### 3. `src/SranDesktop.Presentation/ViewModels/LoginViewModel.cs` — CLEAN UP

Remove these members (dead code after XAML cleanup):

```csharp
// DELETE these fields:
private static readonly SolidColorBrush StatusUnknownBrush = ...;
private static readonly SolidColorBrush StatusOkBrush = ...;
private static readonly SolidColorBrush StatusFailBrush = ...;

// DELETE these properties:
[ObservableProperty]
private SolidColorBrush _serverStatusColor = StatusUnknownBrush;

public string ServerDisplayName => ...;

// DELETE these commands:
[RelayCommand]
private void LoginWithGoogle() => ...;

[RelayCommand]
private void LoginWithGitHub() => ...;

[RelayCommand]
private void SignUp() => ...;

[RelayCommand]
private async Task ChangeServerAsync() { ... }

[RelayCommand]
private async Task CheckServerStatusAsync() { ... }

// DELETE these helpers:
private static async Task<bool> ProbeAsync(...) { ... }
public static string DeriveServerHost(...) { ... }
private static SolidColorBrush Frozen(...) { ... }

// DELETE the PortalWebUrl property (used by OAuth/SignUp — only ForgotPassword still needs it, keep DerivePortalWebUrl):
// Actually KEEP PortalWebUrl + DerivePortalWebUrl because ForgotPasswordCommand uses them.
```

**Keep these:**
- `_email`, `_password`, `_errorMessage`, `_isBusy`, `HasError`
- `_isPasswordHidden`, `TogglePasswordVisibility()`
- `LoginAsync()` (the core login logic)
- `ForgotPassword()` command
- `PortalWebUrl` property
- `DerivePortalWebUrl()` static method
- `OpenInBrowser()` helper
- `LoginSucceeded` event
- Constructor

**Remove the `IStartupManager` constructor parameter** since `ChangeServerAsync` (which used `FirstRunDialog`) is deleted. Update constructor:

```csharp
private readonly IAuthService _authService;
private readonly ISettingsService _settings;

public LoginViewModel(IAuthService authService, ISettingsService settings)
{
    _authService = authService;
    _settings = settings;
}
```

**Final LoginViewModel.cs:**

```csharp
using System.Diagnostics;
using System.Net;
using System.Net.Http;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Application.Interfaces;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

public partial class LoginViewModel : ObservableObject
{
    private readonly IAuthService _authService;
    private readonly ISettingsService _settings;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private string _email = string.Empty;

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

    [ObservableProperty]
    private bool _isPasswordHidden = true;

    [RelayCommand]
    private void TogglePasswordVisibility() => IsPasswordHidden = !IsPasswordHidden;

    public event EventHandler? LoginSucceeded;

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

    public static string DerivePortalWebUrl(string gatewayBaseUrl)
    {
        var trimmed = gatewayBaseUrl.TrimEnd('/');
        return FirstRunViewModel.IsLocalDevUrl(trimmed)
            ? "http://localhost:3001"
            : trimmed;
    }
}
```

### 4. DI Registration — Fix constructor

Find where `LoginViewModel` is registered in the DI container (likely `AppHostBuilder.cs` or similar). The constructor now takes 2 params instead of 3 — remove `IStartupManager`:

```csharp
// Before:
services.AddTransient<LoginViewModel>();
// This should still work since DI resolves by type — but if it was
// manually constructed anywhere, update those call sites too.
```

Search for `new LoginViewModel(` — if any manual construction exists, remove the `IStartupManager` argument.

### 5. Localization — Remove unused keys

In the locale JSON files (`en.json`, `th.json`, etc.), these keys are now unused and can be removed:

```
login.brandTagline
login.feature1
login.feature2
login.feature3
login.orContinueWith
login.noAccount
login.signUp
login.subtitle
login.connectedTo
login.changeServer
```

Keep these keys (still used):
```
login.email
login.emailPlaceholder
login.password
login.showPassword
login.signIn
login.signingIn
login.forgotPassword
login.errorInvalid
login.errorUnreachable
login.errorTimeout
```

## Verification Checklist

1. `dotnet build` — no errors
2. Launch app with no stored session → login screen shows centered with SRAN cat logo
3. Dark mode: logo is white on dark background, inputs have dark bg
4. Light mode: logo is dark on light background, inputs have white bg
5. Email field: mail icon on left, watermark placeholder, blue focus ring
6. Password field: lock icon on left, eye toggle on right, show/hide works
7. Sign in button: shows spinner during login, accent color
8. Error message: shows red card on wrong credentials
9. Forgot password: opens browser to portal forgot-password page
10. Fade-in animation plays on visibility change
11. Auto-focus on email field on entrance
12. Enter key in password field submits login
13. No OAuth buttons visible
14. No server status footer visible
15. No branding panel visible
16. Window resize — no crash (old responsive code removed)
17. After login → next app launch auto-restores session (existing behavior, unchanged)

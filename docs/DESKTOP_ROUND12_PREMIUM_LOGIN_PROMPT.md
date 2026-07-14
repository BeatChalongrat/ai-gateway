# SRAN Desktop — Round 12: Premium Login Screen Redesign

## Goal

Redesign the login screen from a plain developer-style card into a **premium, polished, luxurious** sign-in experience that matches the quality of the rest of the app. The login screen is the first thing customers see — it must set the tone.

Session persistence already works (DPAPI token storage + `TryRestoreSessionAsync`). This round is purely visual + UX polish.

---

## Current State (What to Change)

File: `Views/LoginView.xaml` (108 lines)

Current: A centered 400px card on a flat `BgSecondaryBrush` background. Contains:
- "SRAN Desktop" text title (no logo)
- "Sign in to your AI Gateway account" subtitle
- Plain TextBox for email
- Plain PasswordBox
- Error message TextBlock
- AccentButton "Sign in"
- Spinning border for busy state

**Problems:**
- No logo or branding
- No visual hierarchy — feels like a dev prototype
- No "Forgot password" or "Sign up" links
- No OAuth buttons (Portal supports Google + GitHub)
- No indication which server you're connecting to
- No animation or entrance transition
- The card has no shadow or depth
- Input fields use plain TextBox with external watermark service — not polished
- Error message just appears abruptly
- Busy spinner is a crude rotating border

---

## New Design

### Layout: Split-screen with branding panel

Replace the centered-card layout with a **split-screen design**:

```
┌──────────────────────────────────────────────────────────────┐
│                              │                               │
│      BRANDING PANEL          │        LOGIN FORM             │
│      (left 45%)              │        (right 55%)            │
│                              │                               │
│    ┌──────────────┐          │     SRAN Desktop              │
│    │   S  LOGO    │          │     Sign in to continue       │
│    └──────────────┘          │                               │
│                              │     ┌─────────────────────┐   │
│    SRAN Desktop              │     │ 📧 you@company.com  │   │
│    Your AI Gateway           │     └─────────────────────┘   │
│                              │     ┌─────────────────────┐   │
│    ─── ── ── ── ──           │     │ 🔒 ••••••••••       │   │
│                              │     └─────────────────────┘   │
│    ✦ 30+ AI capabilities     │                               │
│    ✦ Enterprise-grade        │     [      Sign in       ]    │
│    ✦ Thai & English          │                               │
│                              │     ──── or continue with ──  │
│                              │                               │
│                              │     [ G  Google ] [⌥ GitHub]  │
│                              │                               │
│    v1.2.0                    │     Don't have an account?    │
│                              │     Sign up                   │
│                              │                               │
│                              │     Connected to sran.ai  ⚙  │
│                              │                               │
└──────────────────────────────────────────────────────────────┘
```

### Colors & Theme

Use the existing dark theme palette — **do NOT introduce new colors**:

| Element | Brush |
|---------|-------|
| Branding panel background | `BgSidebarBrush` (#171717) — the darkest surface |
| Login form background | `BgPrimaryBrush` (#212121) |
| Card/input backgrounds | `BgSecondaryBrush` (#2F2F2F) |
| Input borders | `BorderBrush` (#3A3A3A) |
| Input focus border | `AccentBlueBrush` (#60A5FA) |
| Primary text | `TextPrimaryBrush` (#ECECEC) |
| Secondary text | `TextSecondaryBrush` (#A0A0A0) |
| Sign-in button | `AccentBrush` bg + `OnAccentBrush` fg |
| OAuth buttons | `BgSecondaryBrush` bg + `TextPrimaryBrush` fg + `BorderBrush` border |
| Error text | `AccentRedBrush` (#EF4444) |
| Links | `AccentBlueBrush` (#60A5FA) |
| Feature bullets | `AccentGreenBrush` (#10A37F) |
| Version text | `TextTertiaryBrush` (#6B6B6B) |

### XAML Structure

Replace the entire content of `LoginView.xaml`:

```xml
<UserControl x:Class="SranDesktop.Presentation.Views.LoginView"
             xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
             xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
             xmlns:loc="clr-namespace:SranDesktop.Presentation.Localization"
             xmlns:controls="clr-namespace:SranDesktop.Presentation.Controls">

    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="45*" />
            <ColumnDefinition Width="55*" />
        </Grid.ColumnDefinitions>

        <!-- ═══ LEFT: Branding Panel ═══ -->
        <Border Grid.Column="0" Background="{DynamicResource BgSidebarBrush}">
            <Grid Margin="48,0">
                <StackPanel VerticalAlignment="Center">

                    <!-- Logo badge -->
                    <Border Width="64" Height="64" CornerRadius="16"
                            Background="{DynamicResource AccentBrush}"
                            HorizontalAlignment="Left" Margin="0,0,0,24">
                        <TextBlock Text="S" FontSize="30" FontWeight="Bold"
                                   Foreground="{DynamicResource OnAccentBrush}"
                                   HorizontalAlignment="Center" VerticalAlignment="Center" />
                    </Border>

                    <!-- Brand name -->
                    <TextBlock Text="SRAN Desktop" FontSize="28" FontWeight="SemiBold"
                               Foreground="{DynamicResource TextPrimaryBrush}" />
                    <TextBlock Text="{loc:T login.brandTagline}"
                               FontSize="14" Margin="0,4,0,0"
                               Foreground="{DynamicResource TextSecondaryBrush}" />

                    <!-- Divider -->
                    <Border Height="1" Margin="0,32"
                            Background="{DynamicResource BorderBrush}" Opacity="0.5"
                            HorizontalAlignment="Left" Width="180" />

                    <!-- Feature highlights -->
                    <StackPanel Margin="0,0,0,0">
                        <StackPanel Orientation="Horizontal" Margin="0,0,0,12">
                            <Ellipse Width="6" Height="6" Fill="{DynamicResource AccentGreenBrush}"
                                     VerticalAlignment="Center" Margin="0,0,12,0" />
                            <TextBlock Text="{loc:T login.feature1}" FontSize="13"
                                       Foreground="{DynamicResource TextSecondaryBrush}" />
                        </StackPanel>
                        <StackPanel Orientation="Horizontal" Margin="0,0,0,12">
                            <Ellipse Width="6" Height="6" Fill="{DynamicResource AccentGreenBrush}"
                                     VerticalAlignment="Center" Margin="0,0,12,0" />
                            <TextBlock Text="{loc:T login.feature2}" FontSize="13"
                                       Foreground="{DynamicResource TextSecondaryBrush}" />
                        </StackPanel>
                        <StackPanel Orientation="Horizontal" Margin="0,0,0,12">
                            <Ellipse Width="6" Height="6" Fill="{DynamicResource AccentGreenBrush}"
                                     VerticalAlignment="Center" Margin="0,0,12,0" />
                            <TextBlock Text="{loc:T login.feature3}" FontSize="13"
                                       Foreground="{DynamicResource TextSecondaryBrush}" />
                        </StackPanel>
                    </StackPanel>
                </StackPanel>

                <!-- Version at bottom -->
                <TextBlock Text="{Binding AppVersion, StringFormat='v{0}'}"
                           FontSize="11" Foreground="{DynamicResource TextTertiaryBrush}"
                           VerticalAlignment="Bottom" Margin="0,0,0,32" />
            </Grid>
        </Border>

        <!-- ═══ RIGHT: Login Form ═══ -->
        <Border Grid.Column="1" Background="{DynamicResource BgPrimaryBrush}">
            <Grid MaxWidth="380" Margin="48,0" VerticalAlignment="Center">
                <StackPanel>

                    <!-- Header -->
                    <TextBlock Text="SRAN Desktop" FontSize="22" FontWeight="SemiBold"
                               Foreground="{DynamicResource TextPrimaryBrush}" />
                    <TextBlock Text="{loc:T login.subtitle}" FontSize="13"
                               Foreground="{DynamicResource TextSecondaryBrush}"
                               Margin="0,4,0,28" />

                    <!-- Email field with icon -->
                    <TextBlock Text="{loc:T login.email}" FontSize="12"
                               Foreground="{DynamicResource TextSecondaryBrush}"
                               Margin="0,0,0,6" />
                    <Border CornerRadius="8" BorderThickness="1"
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
                            <TextBox Grid.Column="1"
                                     Text="{Binding Email, UpdateSourceTrigger=PropertyChanged}"
                                     Background="Transparent" BorderThickness="0"
                                     FontSize="13" VerticalAlignment="Center"
                                     Foreground="{DynamicResource TextPrimaryBrush}"
                                     CaretBrush="{DynamicResource TextPrimaryBrush}">
                                <!-- Watermark via style or adorner -->
                            </TextBox>
                        </Grid>
                    </Border>

                    <!-- Password field with icon + show/hide toggle -->
                    <TextBlock Text="{loc:T login.password}" FontSize="12"
                               Foreground="{DynamicResource TextSecondaryBrush}"
                               Margin="0,0,0,6" />
                    <Border CornerRadius="8" BorderThickness="1"
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
                            <!-- PasswordBox (default) -->
                            <PasswordBox Grid.Column="1" x:Name="PasswordInput"
                                         Background="Transparent" BorderThickness="0"
                                         FontSize="13" VerticalAlignment="Center"
                                         Foreground="{DynamicResource TextPrimaryBrush}"
                                         CaretBrush="{DynamicResource TextPrimaryBrush}"
                                         PasswordChanged="PasswordInput_PasswordChanged"
                                         KeyDown="PasswordInput_KeyDown"
                                         Visibility="{Binding IsPasswordHidden, Converter={StaticResource BoolToVisibility}}" />
                            <!-- TextBox (reveal mode) -->
                            <TextBox Grid.Column="1"
                                     Text="{Binding Password, UpdateSourceTrigger=PropertyChanged}"
                                     Background="Transparent" BorderThickness="0"
                                     FontSize="13" VerticalAlignment="Center"
                                     Foreground="{DynamicResource TextPrimaryBrush}"
                                     CaretBrush="{DynamicResource TextPrimaryBrush}"
                                     Visibility="{Binding IsPasswordHidden, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}" />
                            <!-- Eye toggle -->
                            <Button Grid.Column="2" Width="28" Height="28"
                                    Style="{DynamicResource IconButton}"
                                    Command="{Binding TogglePasswordVisibilityCommand}">
                                <controls:IconView Width="14" Height="14"
                                                   Geometry="{Binding PasswordEyeIcon}"
                                                   Stroke="{DynamicResource TextTertiaryBrush}" />
                            </Button>
                        </Grid>
                    </Border>

                    <!-- Forgot password link -->
                    <TextBlock HorizontalAlignment="Right" Margin="0,0,0,20">
                        <Hyperlink Command="{Binding ForgotPasswordCommand}"
                                   Foreground="{DynamicResource AccentBlueBrush}"
                                   TextDecorations="None">
                            <Run Text="{loc:T login.forgotPassword}" FontSize="12" />
                        </Hyperlink>
                    </TextBlock>

                    <!-- Error message with icon (animated) -->
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
                                       TextWrapping="Wrap" />
                        </StackPanel>
                    </Border>

                    <!-- Sign in button -->
                    <Button Style="{DynamicResource AccentButton}"
                            Content="{loc:T login.signIn}"
                            Command="{Binding LoginCommand}"
                            Height="42" FontSize="14"
                            Margin="0,0,0,0">
                        <Button.Template>
                            <ControlTemplate TargetType="Button">
                                <Border CornerRadius="8"
                                        Background="{TemplateBinding Background}"
                                        Padding="0,10">
                                    <Grid HorizontalAlignment="Center">
                                        <!-- Normal state: text -->
                                        <TextBlock Text="{TemplateBinding Content}"
                                                   FontSize="{TemplateBinding FontSize}"
                                                   FontWeight="SemiBold"
                                                   Foreground="{TemplateBinding Foreground}"
                                                   VerticalAlignment="Center"
                                                   Visibility="{Binding IsBusy, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}" />
                                        <!-- Busy state: spinner + text -->
                                        <StackPanel Orientation="Horizontal"
                                                    Visibility="{Binding IsBusy, Converter={StaticResource BoolToVisibility}}">
                                            <!-- Small spinning dots or ring -->
                                            <Border Width="16" Height="16" CornerRadius="8"
                                                    BorderThickness="2"
                                                    BorderBrush="{TemplateBinding Foreground}"
                                                    Opacity="0.6" Margin="0,0,8,0"
                                                    RenderTransformOrigin="0.5,0.5">
                                                <Border.RenderTransform>
                                                    <RotateTransform />
                                                </Border.RenderTransform>
                                                <Border.OpacityMask>
                                                    <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
                                                        <GradientStop Color="Transparent" Offset="0" />
                                                        <GradientStop Color="White" Offset="0.7" />
                                                    </LinearGradientBrush>
                                                </Border.OpacityMask>
                                                <Border.Triggers>
                                                    <EventTrigger RoutedEvent="Loaded">
                                                        <BeginStoryboard>
                                                            <Storyboard RepeatBehavior="Forever">
                                                                <DoubleAnimation
                                                                    Storyboard.TargetProperty="(UIElement.RenderTransform).(RotateTransform.Angle)"
                                                                    From="0" To="360" Duration="0:0:1" />
                                                            </Storyboard>
                                                        </BeginStoryboard>
                                                    </EventTrigger>
                                                </Border.Triggers>
                                            </Border>
                                            <TextBlock Text="{loc:T login.signingIn}"
                                                       FontSize="14" FontWeight="SemiBold"
                                                       Foreground="{TemplateBinding Foreground}"
                                                       VerticalAlignment="Center" />
                                        </StackPanel>
                                    </Grid>
                                </Border>
                            </ControlTemplate>
                        </Button.Template>
                    </Button>

                    <!-- OAuth divider -->
                    <Grid Margin="0,24,0,20">
                        <Border Height="1" Background="{DynamicResource BorderBrush}"
                                VerticalAlignment="Center" />
                        <TextBlock Text="{loc:T login.orContinueWith}" FontSize="11"
                                   Foreground="{DynamicResource TextTertiaryBrush}"
                                   Background="{DynamicResource BgPrimaryBrush}"
                                   Padding="12,0" HorizontalAlignment="Center" />
                    </Grid>

                    <!-- OAuth buttons -->
                    <Grid Margin="0,0,0,28">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*" />
                            <ColumnDefinition Width="10" />
                            <ColumnDefinition Width="*" />
                        </Grid.ColumnDefinitions>

                        <!-- Google -->
                        <Button Grid.Column="0" Height="40"
                                Command="{Binding LoginWithGoogleCommand}"
                                Style="{DynamicResource FlatRowButton}"
                                BorderBrush="{DynamicResource BorderBrush}"
                                BorderThickness="1" Cursor="Hand">
                            <Button.Template>
                                <ControlTemplate TargetType="Button">
                                    <Border CornerRadius="8" Height="{TemplateBinding Height}"
                                            BorderThickness="{TemplateBinding BorderThickness}"
                                            BorderBrush="{TemplateBinding BorderBrush}"
                                            Background="{DynamicResource BgSecondaryBrush}"
                                            Cursor="Hand">
                                        <StackPanel Orientation="Horizontal" HorizontalAlignment="Center"
                                                    VerticalAlignment="Center">
                                            <!-- Google "G" icon: use a TextBlock or Path -->
                                            <TextBlock Text="G" FontSize="16" FontWeight="Bold"
                                                       Foreground="#4285F4"
                                                       VerticalAlignment="Center" Margin="0,0,8,0" />
                                            <TextBlock Text="Google" FontSize="13"
                                                       Foreground="{DynamicResource TextPrimaryBrush}"
                                                       VerticalAlignment="Center" />
                                        </StackPanel>
                                    </Border>
                                </ControlTemplate>
                            </Button.Template>
                        </Button>

                        <!-- GitHub -->
                        <Button Grid.Column="2" Height="40"
                                Command="{Binding LoginWithGitHubCommand}"
                                Style="{DynamicResource FlatRowButton}"
                                BorderBrush="{DynamicResource BorderBrush}"
                                BorderThickness="1" Cursor="Hand">
                            <Button.Template>
                                <ControlTemplate TargetType="Button">
                                    <Border CornerRadius="8" Height="{TemplateBinding Height}"
                                            BorderThickness="{TemplateBinding BorderThickness}"
                                            BorderBrush="{TemplateBinding BorderBrush}"
                                            Background="{DynamicResource BgSecondaryBrush}"
                                            Cursor="Hand">
                                        <StackPanel Orientation="Horizontal" HorizontalAlignment="Center"
                                                    VerticalAlignment="Center">
                                            <controls:IconView Width="16" Height="16"
                                                               Geometry="{StaticResource Geo_GitHub}"
                                                               Stroke="{DynamicResource TextPrimaryBrush}"
                                                               VerticalAlignment="Center" Margin="0,0,8,0" />
                                            <TextBlock Text="GitHub" FontSize="13"
                                                       Foreground="{DynamicResource TextPrimaryBrush}"
                                                       VerticalAlignment="Center" />
                                        </StackPanel>
                                    </Border>
                                </ControlTemplate>
                            </Button.Template>
                        </Button>
                    </Grid>

                    <!-- Sign up link -->
                    <TextBlock HorizontalAlignment="Center" FontSize="13" Margin="0,0,0,20">
                        <Run Text="{loc:T login.noAccount}"
                             Foreground="{DynamicResource TextSecondaryBrush}" />
                        <Hyperlink Command="{Binding SignUpCommand}"
                                   Foreground="{DynamicResource AccentBlueBrush}"
                                   TextDecorations="None">
                            <Run Text="{loc:T login.signUp}" />
                        </Hyperlink>
                    </TextBlock>

                    <!-- Server info footer -->
                    <Border CornerRadius="6" Padding="10,8"
                            Background="{DynamicResource BgSecondaryBrush}">
                        <DockPanel>
                            <Button DockPanel.Dock="Right" Width="24" Height="24"
                                    Style="{DynamicResource IconButton}"
                                    Command="{Binding ChangeServerCommand}"
                                    ToolTip="{loc:T login.changeServer}">
                                <controls:IconView Width="12" Height="12"
                                                   Geometry="{StaticResource Geo_Settings}"
                                                   Stroke="{DynamicResource TextTertiaryBrush}" />
                            </Button>
                            <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
                                <!-- Green dot = server reachable -->
                                <Ellipse Width="6" Height="6" Margin="0,0,8,0"
                                         Fill="{Binding ServerStatusColor}" />
                                <TextBlock FontSize="11"
                                           Foreground="{DynamicResource TextTertiaryBrush}">
                                    <Run Text="{loc:T login.connectedTo}" />
                                    <Run Text="{Binding ServerDisplayName}" />
                                </TextBlock>
                            </StackPanel>
                        </DockPanel>
                    </Border>

                </StackPanel>
            </Grid>
        </Border>
    </Grid>
</UserControl>
```

---

## LoginViewModel Changes

Expand `LoginViewModel.cs` with new properties and commands:

```csharp
public partial class LoginViewModel : ObservableObject
{
    private readonly IAuthService _authService;
    private readonly ISettingsService _settings;

    // ── Existing ──
    [ObservableProperty, NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private string _email = "";

    [ObservableProperty, NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private string _password = "";

    [ObservableProperty] private string? _errorMessage;
    [ObservableProperty] private bool _isBusy;

    public bool HasError => ErrorMessage is not null;
    partial void OnErrorMessageChanged(string? value)
        => OnPropertyChanged(nameof(HasError));

    // ── New: Password visibility ──
    [ObservableProperty] private bool _isPasswordHidden = true;
    public string PasswordEyeIcon => IsPasswordHidden ? "Geo_Eye" : "Geo_EyeOff";

    [RelayCommand]
    private void TogglePasswordVisibility()
    {
        IsPasswordHidden = !IsPasswordHidden;
        OnPropertyChanged(nameof(PasswordEyeIcon));
    }

    // ── New: Server info ──
    public string ServerDisplayName
    {
        get
        {
            var url = _settings.Get<string>("chat_edge_base_url") ?? "http://localhost:4000";
            try { return new Uri(url).Host; } catch { return url; }
        }
    }

    [ObservableProperty] private SolidColorBrush _serverStatusColor = Brushes.Gray;

    // ── New: App version ──
    public string AppVersion =>
        typeof(LoginViewModel).Assembly
            .GetCustomAttribute<System.Reflection.AssemblyInformationalVersionAttribute>()?
            .InformationalVersion?.Split('+')[0] ?? "1.0.0";

    // ── New: OAuth ──
    [RelayCommand]
    private void LoginWithGoogle()
    {
        // Open system browser to Portal's Google OAuth endpoint
        var baseUrl = _settings.Get<string>("gateway_base_url") ?? "http://localhost:8080";
        var url = $"{baseUrl}/portal/auth/google?redirect=sran-desktop://oauth-callback";
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }

    [RelayCommand]
    private void LoginWithGitHub()
    {
        var baseUrl = _settings.Get<string>("gateway_base_url") ?? "http://localhost:8080";
        var url = $"{baseUrl}/portal/auth/github?redirect=sran-desktop://oauth-callback";
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }

    // ── New: Sign up ──
    [RelayCommand]
    private void SignUp()
    {
        var baseUrl = _settings.Get<string>("gateway_base_url") ?? "http://localhost:8080";
        var url = $"{baseUrl}/portal/register";
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }

    // ── New: Forgot password ──
    [RelayCommand]
    private void ForgotPassword()
    {
        var baseUrl = _settings.Get<string>("gateway_base_url") ?? "http://localhost:8080";
        var url = $"{baseUrl}/portal/forgot-password";
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }

    // ── New: Change server ──
    [RelayCommand]
    private void ChangeServer()
    {
        // Show the FirstRunDialog again to reconfigure server URL
        var dialog = new FirstRunDialog(_settings) { Owner = App.Current.MainWindow };
        if (dialog.ShowDialog() == true)
        {
            OnPropertyChanged(nameof(ServerDisplayName));
            CheckServerStatusAsync().ConfigureAwait(false);
        }
    }

    // ── New: Server health check ──
    public async Task CheckServerStatusAsync()
    {
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
            var baseUrl = _settings.Get<string>("gateway_base_url") ?? "http://localhost:8080";
            var response = await http.GetAsync($"{baseUrl}/__gw/health");
            ServerStatusColor = response.IsSuccessStatusCode
                ? new SolidColorBrush(Color.FromRgb(0x10, 0xA3, 0x7F))  // AccentGreen
                : new SolidColorBrush(Color.FromRgb(0xEF, 0x44, 0x44)); // AccentRed
        }
        catch
        {
            ServerStatusColor = new SolidColorBrush(Color.FromRgb(0xEF, 0x44, 0x44));
        }
    }

    // ── Existing login logic (keep as-is) ──
    // ...
}
```

---

## Startup Splash Polish

The startup splash (shown during `TryRestoreSessionAsync`) is currently just a "SRAN Desktop" text. Make it a proper splash:

In `MainWindow.xaml`, replace the splash overlay (around line 969-976):

```xml
<!-- Startup splash — shown while restoring session -->
<Grid Visibility="{Binding IsInitializing, Converter={StaticResource BoolToVisibility}}"
      Background="{DynamicResource BgPrimaryBrush}">
    <StackPanel VerticalAlignment="Center" HorizontalAlignment="Center">
        <!-- Logo badge (same as login branding) -->
        <Border Width="56" Height="56" CornerRadius="14"
                Background="{DynamicResource AccentBrush}"
                HorizontalAlignment="Center" Margin="0,0,0,16">
            <TextBlock Text="S" FontSize="26" FontWeight="Bold"
                       Foreground="{DynamicResource OnAccentBrush}"
                       HorizontalAlignment="Center" VerticalAlignment="Center" />
        </Border>
        <TextBlock Text="SRAN Desktop" FontSize="18" FontWeight="SemiBold"
                   Foreground="{DynamicResource TextPrimaryBrush}"
                   HorizontalAlignment="Center" />
        <!-- Subtle loading indicator -->
        <ProgressBar IsIndeterminate="True" Width="120" Height="2"
                     Margin="0,16,0,0" BorderThickness="0"
                     Background="{DynamicResource BgSecondaryBrush}"
                     Foreground="{DynamicResource AccentBrush}" />
    </StackPanel>
</Grid>
```

---

## Entrance Animation

Add a subtle fade-in when the login screen appears (after splash hides):

```xml
<UserControl.Triggers>
    <EventTrigger RoutedEvent="Loaded">
        <BeginStoryboard>
            <Storyboard>
                <DoubleAnimation Storyboard.TargetProperty="Opacity"
                                 From="0" To="1" Duration="0:0:0.4"
                                 EasingFunction="{StaticResource EaseOut}" />
            </Storyboard>
        </BeginStoryboard>
    </EventTrigger>
</UserControl.Triggers>
```

Also add an `EaseOut` easing function in resources if one doesn't exist:
```xml
<CubicEase x:Key="EaseOut" EasingMode="EaseOut" />
```

---

## Input Field Focus Behavior

When the email field has focus, highlight its parent border:

In code-behind (`LoginView.xaml.cs`):
```csharp
private void EmailInput_GotFocus(object sender, RoutedEventArgs e)
{
    EmailBorder.BorderBrush = FindResource("AccentBlueBrush") as Brush;
}
private void EmailInput_LostFocus(object sender, RoutedEventArgs e)
{
    EmailBorder.BorderBrush = FindResource("BorderBrush") as Brush;
}
```

Give each input `Border` an `x:Name` (`EmailBorder`, `PasswordBorder`) and wire these events. Same pattern for the password field.

---

## New Icon Geometries

Add to `Icons.xaml`:

```xml
<!-- Mail icon -->
<Geometry x:Key="Geo_Mail">M2 4h20v16H2V4zm2 2v.01L12 12l8-5.99V6L12 12 4 6zm0 12h16V8.83l-8 5.99-8-5.99V18z</Geometry>

<!-- Lock icon -->
<Geometry x:Key="Geo_Lock">M12 2C9.24 2 7 4.24 7 7v3H5v12h14V10h-2V7c0-2.76-2.24-5-5-5zm0 2c1.65 0 3 1.35 3 3v3H9V7c0-1.65 1.35-3 3-3zm-5 8h10v8H7v-8z</Geometry>

<!-- Eye icon -->
<Geometry x:Key="Geo_Eye">M12 5C5.64 5 1 12 1 12s4.64 7 11 7 11-7 11-7-4.64-7-11-7zm0 12c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8a3 3 0 100 6 3 3 0 000-6z</Geometry>

<!-- Eye off icon -->
<Geometry x:Key="Geo_EyeOff">M12 5C5.64 5 1 12 1 12s1.5 2.29 4.06 4.44L2 19.5l1.41 1.41 17-17L19 2.5l-3.06 3.06C14.7 5.18 13.38 5 12 5zm0 12c2.76 0 5-2.24 5-5 0-.77-.18-1.5-.49-2.14l-2.37 2.37A3 3 0 0112 14a3 3 0 01-2.23-1.14L7.4 15.23C8.74 16.33 10.33 17 12 17z</Geometry>

<!-- GitHub icon -->
<Geometry x:Key="Geo_GitHub">M12 2C6.48 2 2 6.48 2 12c0 4.42 2.87 8.17 6.84 9.5.5.08.66-.23.66-.5v-1.69C6.73 19.91 6.14 17.97 6.14 17.97c-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.61.07-.61 1 .07 1.53 1.03 1.53 1.03.89 1.52 2.34 1.08 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.93 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.58 9.58 0 0112 6.8c.85 0 1.7.11 2.5.34 1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.85v2.75c0 .27.16.59.67.5A10.01 10.01 0 0022 12c0-5.52-4.48-10-10-10z</Geometry>

<!-- Alert circle icon -->
<Geometry x:Key="Geo_AlertCircle">M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z</Geometry>
```

---

## i18n Keys

Add to `Dict.cs`:

```csharp
// Login screen
["login.brandTagline"] = "Your AI-powered gateway" / "ผู้ช่วย AI สำหรับองค์กร",
["login.subtitle"] = "Sign in to continue" / "เข้าสู่ระบบเพื่อดำเนินการต่อ",
["login.email"] = "Email" / "อีเมล",
["login.password"] = "Password" / "รหัสผ่าน",
["login.signIn"] = "Sign in" / "เข้าสู่ระบบ",
["login.signingIn"] = "Signing in..." / "กำลังเข้าสู่ระบบ...",
["login.forgotPassword"] = "Forgot password?" / "ลืมรหัสผ่าน?",
["login.orContinueWith"] = "or continue with" / "หรือเข้าสู่ระบบด้วย",
["login.noAccount"] = "Don't have an account?" / "ยังไม่มีบัญชี?",
["login.signUp"] = "Sign up" / "สมัครสมาชิก",
["login.connectedTo"] = "Connected to" / "เชื่อมต่อกับ",
["login.changeServer"] = "Change server" / "เปลี่ยนเซิร์ฟเวอร์",
["login.feature1"] = "30+ AI-powered capabilities" / "ความสามารถ AI กว่า 30 อย่าง",
["login.feature2"] = "Enterprise-grade security" / "ความปลอดภัยระดับองค์กร",
["login.feature3"] = "Thai & English support" / "รองรับภาษาไทยและอังกฤษ",
```

---

## OAuth Deep Link Registration

For the Google/GitHub OAuth buttons to work, the desktop app needs to handle `sran-desktop://oauth-callback` URI scheme.

Register the URI scheme in Inno Setup (`SranDesktop.iss`):

```ini
[Registry]
; Register custom URI scheme for OAuth callbacks
Root: HKCU; Subkey: "SOFTWARE\Classes\sran-desktop"; \
  ValueType: string; ValueName: ""; ValueData: "SRAN Desktop"; Flags: uninsdeletekey
Root: HKCU; Subkey: "SOFTWARE\Classes\sran-desktop"; \
  ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "SOFTWARE\Classes\sran-desktop\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName},1"""
Root: HKCU; Subkey: "SOFTWARE\Classes\sran-desktop\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" --oauth-callback ""%1"""
```

In `App.xaml.cs`, handle the `--oauth-callback` argument:

```csharp
// In OnStartup, after argument parsing:
var oauthArg = args.FirstOrDefault(a => a.StartsWith("sran-desktop://oauth-callback"));
if (oauthArg is not null)
{
    // Parse tokens from the callback URL
    var uri = new Uri(oauthArg);
    var query = System.Web.HttpUtility.ParseQueryString(uri.Query);
    var accessToken = query["access_token"];
    var refreshToken = query["refresh_token"];

    if (accessToken is not null && refreshToken is not null)
    {
        // Inject tokens into the existing app instance (via single-instance messaging)
        // or set session directly if this is the main instance
        var session = _host.Services.GetRequiredService<ITokenSession>();
        session.SetSession(accessToken, refreshToken, null);
        // Fetch user info
        var auth = _host.Services.GetRequiredService<IAuthService>();
        await auth.TryRestoreSessionAsync();
    }
}
```

**Note**: If Portal OAuth doesn't support redirect to custom URI schemes, the OAuth buttons should just open the Portal registration/login page in the browser and let the user come back to the desktop app to login with their new credentials. This is simpler and doesn't require URI scheme registration:

```csharp
[RelayCommand]
private void LoginWithGoogle()
{
    var baseUrl = _settings.Get<string>("gateway_base_url") ?? "http://localhost:8080";
    Process.Start(new ProcessStartInfo($"{baseUrl}/portal/login?provider=google")
    { UseShellExecute = true });
}
```

Implement whichever approach matches what Portal currently supports. If Portal doesn't do OAuth redirect to custom schemes, use the simple approach (just open browser).

---

## Light Theme Compatibility

The split-screen design must also work in light theme. The same `DynamicResource` brushes will resolve to light values:

| Element | Light theme value |
|---------|-------------------|
| BgSidebarBrush | #F5F5F5 (light grey) |
| BgPrimaryBrush | #FFFFFF (white) |
| BgSecondaryBrush | #F0F0F0 |
| TextPrimaryBrush | #0D0D0D (near black) |
| AccentBrush | #0D0D0D (near black) |
| OnAccentBrush | #FFFFFF (white) |
| BorderBrush | #E0E0E0 |

The design should look equally premium in both themes. Test both.

---

## Responsive Behavior

If the window is resized narrow (< 700px width), hide the branding panel and show only the login form full-width. This handles cases where users resize the window small:

```xml
<Grid.ColumnDefinitions>
    <ColumnDefinition Width="45*" MinWidth="0">
        <ColumnDefinition.Style>
            <Style TargetType="ColumnDefinition">
                <Style.Triggers>
                    <DataTrigger Binding="{Binding ActualWidth, RelativeSource={RelativeSource AncestorType=UserControl}, Converter={StaticResource LessThanConverter}, ConverterParameter=700}" Value="True">
                        <Setter Property="Width" Value="0" />
                    </DataTrigger>
                </Style.Triggers>
            </Style>
        </ColumnDefinition.Style>
    </ColumnDefinition>
    <ColumnDefinition Width="55*" />
</Grid.ColumnDefinitions>
```

Or simpler: set `MinWidth="300"` on the branding column and let it collapse naturally.

---

## Verification Checklist

- [ ] Login screen shows split-screen: branding panel left, form right
- [ ] "S" logo badge rendered correctly (AccentBrush bg)
- [ ] Feature bullets with green dots visible on branding panel
- [ ] Version number shown at bottom of branding panel
- [ ] Email field has mail icon and focus highlight (blue border)
- [ ] Password field has lock icon and eye toggle (show/hide password)
- [ ] "Forgot password?" link opens Portal in browser
- [ ] Error message shows in red card with alert icon
- [ ] Sign-in button shows spinner when busy
- [ ] "or continue with" divider renders correctly
- [ ] Google and GitHub OAuth buttons open Portal in browser
- [ ] "Sign up" link opens Portal registration page
- [ ] Server info footer shows hostname + green/red status dot
- [ ] Gear icon opens FirstRunDialog to change server
- [ ] Startup splash shows logo + indeterminate progress bar
- [ ] Session persistence: login once → close app → reopen → auto-login (no login screen)
- [ ] Session expired: reopen → token refresh fails → login screen appears smoothly
- [ ] Fade-in animation when login screen appears
- [ ] Dark theme: all colors correct (screenshot compare)
- [ ] Light theme: all colors correct (screenshot compare)
- [ ] Window narrow (< 700px): branding panel hides, form expands
- [ ] All new i18n keys in Dict.cs (EN + TH)
- [ ] New icon geometries added to Icons.xaml
- [ ] Build: 0 errors, 0 warnings
- [ ] All 161+ tests pass

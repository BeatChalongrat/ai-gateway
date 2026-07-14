# SRAN Desktop — Phase 3 Build Prompt: Polish + Ship

> Give this to Claude Fable with the ai-gateway codebase attached.
> Phases 1-2 + UI polish are complete (85 tests pass). This prompt makes it production-ready.

---

## YOUR MISSION

Make SRAN Desktop installable, launchable, and production-quality. This phase adds: single-instance guard, system tray with minimize-to-tray, global hotkey (Ctrl+Shift+Space), auto-start on login, an app icon, Inno Setup installer, publish profile for single-file deployment, performance hardening, error handling edge cases, and first-run experience.

**DO NOT change** any chat logic, agent loop, MCP tools, or UI layout. This phase is infrastructure + packaging only.

---

## WHAT ALREADY EXISTS

- `H.NotifyIcon.Wpf` v2.1.4 NuGet — already referenced in `SranDesktop.App.csproj`, **zero code uses it**
- `App.xaml`: `ShutdownMode="OnMainWindowClose"` — must change for tray
- `App.xaml.cs`: `OnStartup` builds IHost, applies theme, shows MainWindow. `OnExit` stops host.
- `app.manifest`: `asInvoker`, PerMonitorV2 DPI, Windows 10/11 compat
- `appsettings.json`: Sran config + logging defaults
- Serilog: file sink to `%APPDATA%\SRAN Desktop\logs\`, daily rotation, 7 days
- **No** app icon (.ico), installer script, single-instance guard, global hotkey, or auto-start code

---

## SPECIFICATIONS

### 1. App Icon

Create or generate an app icon. At minimum, provide a placeholder icon file.

**File:** `src/SranDesktop.App/Assets/app.ico`

- Must include sizes: 16x16, 32x32, 48x48, 256x256
- If you cannot generate a multi-resolution .ico, create a simple one programmatically or use a placeholder
- The icon should represent "SRAN" — an "S" in a rounded square with accent blue (#3B82F6) background

**Wire it up in** `SranDesktop.App.csproj`:
```xml
<ApplicationIcon>Assets\app.ico</ApplicationIcon>
```

And in `App.xaml` or `MainWindow.xaml`:
```xml
Icon="pack://application:,,,/Assets/app.ico"
```

### 2. Single-Instance Guard

Prevent multiple instances. First instance owns the app; second instance activates the existing window.

**File:** `src/SranDesktop.App/SingleInstance.cs`

```csharp
using System.IO.Pipes;
using System.Threading;

namespace SranDesktop.App;

/// <summary>
/// Ensures only one instance runs. Second launch sends "activate" via named pipe
/// and exits. First instance receives it and brings its window to front.
/// </summary>
public static class SingleInstance
{
    private const string MutexName = "SranDesktop_SingleInstance_Mutex";
    private const string PipeName = "SranDesktop_SingleInstance_Pipe";
    private static Mutex? _mutex;

    /// <summary>Returns true if this is the first instance.</summary>
    public static bool Claim()
    {
        _mutex = new Mutex(true, MutexName, out var isNew);
        if (isNew) return true;

        // Send activation message to existing instance
        try
        {
            using var client = new NamedPipeClientStream(".", PipeName, PipeDirection.Out);
            client.Connect(2000);
            using var writer = new StreamWriter(client);
            writer.Write("activate");
            writer.Flush();
        }
        catch { /* existing instance may be shutting down */ }

        return false;
    }

    /// <summary>Start listening for second-instance activations. Call on UI thread.</summary>
    public static void ListenForActivation(Action onActivate)
    {
        Task.Run(async () =>
        {
            while (true)
            {
                try
                {
                    using var server = new NamedPipeServerStream(PipeName, PipeDirection.In, 1,
                        PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
                    await server.WaitForConnectionAsync();
                    using var reader = new StreamReader(server);
                    var msg = await reader.ReadToEndAsync();
                    if (msg == "activate")
                    {
                        System.Windows.Application.Current?.Dispatcher.InvokeAsync(onActivate);
                    }
                }
                catch (ObjectDisposedException) { break; }
                catch { /* retry */ }
            }
        });
    }

    public static void Release()
    {
        _mutex?.ReleaseMutex();
        _mutex?.Dispose();
    }
}
```

**Integration in App.xaml.cs** `OnStartup`:
```csharp
protected override void OnStartup(StartupEventArgs e)
{
    if (!SingleInstance.Claim())
    {
        Shutdown();
        return;
    }

    // ... existing host build + window creation ...

    SingleInstance.ListenForActivation(() =>
    {
        var window = MainWindow;
        if (window is not null)
        {
            if (window.WindowState == WindowState.Minimized)
                window.WindowState = WindowState.Normal;
            window.Show();
            window.Activate();
        }
    });
}
```

**In `OnExit`:**
```csharp
SingleInstance.Release();
```

### 3. System Tray (NotifyIcon)

Use the already-referenced `H.NotifyIcon.Wpf` package. Minimize to tray instead of closing.

**Modify** `App.xaml`:
```xml
<!-- Change shutdown mode -->
ShutdownMode="OnExplicitShutdown"
```

**Modify** `App.xaml.cs`:

```csharp
private H.NotifyIcon.TaskbarIcon? _trayIcon;

protected override void OnStartup(StartupEventArgs e)
{
    // ... existing code ...

    // Create tray icon
    _trayIcon = CreateTrayIcon();

    // Wire MainWindow close → minimize to tray (not exit)
    mainWindow.Closing += (s, args) =>
    {
        args.Cancel = true;
        mainWindow.Hide();
        _trayIcon?.ShowBalloonTip("SRAN Desktop", "Running in background. Click tray icon to restore.",
            H.NotifyIcon.BalloonIcon.Info);
    };
}

private H.NotifyIcon.TaskbarIcon CreateTrayIcon()
{
    var icon = new H.NotifyIcon.TaskbarIcon
    {
        ToolTipText = "SRAN Desktop",
        IconSource = new System.Windows.Media.Imaging.BitmapImage(
            new Uri("pack://application:,,,/Assets/app.ico")),
        ContextMenu = CreateTrayContextMenu()
    };

    // Double-click tray icon → show window
    icon.TrayMouseDoubleClick += (s, e) => ShowMainWindow();

    return icon;
}

private System.Windows.Controls.ContextMenu CreateTrayContextMenu()
{
    var menu = new System.Windows.Controls.ContextMenu();

    var showItem = new System.Windows.Controls.MenuItem { Header = "Show SRAN Desktop" };
    showItem.Click += (s, e) => ShowMainWindow();
    menu.Items.Add(showItem);

    var newChatItem = new System.Windows.Controls.MenuItem { Header = "New Chat" };
    newChatItem.Click += (s, e) =>
    {
        ShowMainWindow();
        // Trigger new chat command via MainViewModel
        if (MainWindow?.DataContext is MainViewModel vm)
            vm.Conversations.NewChatCommand.Execute(null);
    };
    menu.Items.Add(newChatItem);

    menu.Items.Add(new System.Windows.Controls.Separator());

    var exitItem = new System.Windows.Controls.MenuItem { Header = "Exit" };
    exitItem.Click += (s, e) =>
    {
        _trayIcon?.Dispose();
        Shutdown();
    };
    menu.Items.Add(exitItem);

    return menu;
}

private void ShowMainWindow()
{
    var window = MainWindow;
    if (window is null) return;
    window.Show();
    if (window.WindowState == WindowState.Minimized)
        window.WindowState = WindowState.Normal;
    window.Activate();
}
```

**In `OnExit`:**
```csharp
_trayIcon?.Dispose();
```

### 4. Global Hotkey (Ctrl+Shift+Space)

Register a system-wide hotkey that shows/hides the window from anywhere.

**File:** `src/SranDesktop.App/GlobalHotkey.cs`

```csharp
using System.Runtime.InteropServices;
using System.Windows.Interop;

namespace SranDesktop.App;

/// <summary>
/// Registers a system-wide hotkey via Win32 RegisterHotKey.
/// Ctrl+Shift+Space toggles the main window.
/// </summary>
public sealed class GlobalHotkey : IDisposable
{
    private const int HotkeyId = 0x1001;
    private const uint MOD_CTRL = 0x0002;
    private const uint MOD_SHIFT = 0x0004;
    private const uint MOD_NOREPEAT = 0x4000;
    private const uint VK_SPACE = 0x20;
    private const int WM_HOTKEY = 0x0312;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    private readonly IntPtr _hwnd;
    private readonly HwndSource _source;
    private readonly Action _onTriggered;
    private bool _registered;

    public GlobalHotkey(Window window, Action onTriggered)
    {
        _onTriggered = onTriggered;
        var helper = new WindowInteropHelper(window);
        // EnsureHandle creates the Win32 handle even if the window is hidden
        _hwnd = helper.EnsureHandle();
        _source = HwndSource.FromHwnd(_hwnd)
                  ?? throw new InvalidOperationException("Failed to get HwndSource");
        _source.AddHook(WndProc);
    }

    public bool Register()
    {
        _registered = RegisterHotKey(_hwnd, HotkeyId, MOD_CTRL | MOD_SHIFT | MOD_NOREPEAT, VK_SPACE);
        return _registered;
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == WM_HOTKEY && wParam.ToInt32() == HotkeyId)
        {
            _onTriggered();
            handled = true;
        }
        return IntPtr.Zero;
    }

    public void Dispose()
    {
        if (_registered)
            UnregisterHotKey(_hwnd, HotkeyId);
        _source.RemoveHook(WndProc);
    }
}
```

**Integration in App.xaml.cs** after MainWindow is created:

```csharp
private GlobalHotkey? _hotkey;

// In OnStartup, after mainWindow.Show():
_hotkey = new GlobalHotkey(mainWindow, () =>
{
    if (mainWindow.IsVisible && mainWindow.IsActive)
    {
        mainWindow.Hide(); // toggle off
    }
    else
    {
        ShowMainWindow(); // toggle on
    }
});
if (!_hotkey.Register())
{
    _logger.LogWarning("Failed to register global hotkey Ctrl+Shift+Space — another app may hold it");
}
```

**In `OnExit`:**
```csharp
_hotkey?.Dispose();
```

### 5. Auto-Start on Login

Add a toggle in Settings to start SRAN Desktop when Windows starts.

**File:** `src/SranDesktop.Infrastructure/Platform/AutoStartManager.cs`

```csharp
using Microsoft.Win32;

namespace SranDesktop.Infrastructure.Platform;

public static class AutoStartManager
{
    private const string RegistryKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run";
    private const string AppName = "SRAN Desktop";

    public static bool IsEnabled
    {
        get
        {
            using var key = Registry.CurrentUser.OpenSubKey(RegistryKey, false);
            return key?.GetValue(AppName) is not null;
        }
    }

    public static void Enable()
    {
        var exePath = Environment.ProcessPath;
        if (exePath is null) return;
        using var key = Registry.CurrentUser.OpenSubKey(RegistryKey, true);
        key?.SetValue(AppName, $"\"{exePath}\" --minimized");
    }

    public static void Disable()
    {
        using var key = Registry.CurrentUser.OpenSubKey(RegistryKey, true);
        key?.DeleteValue(AppName, false);
    }
}
```

**Handle `--minimized` flag in App.xaml.cs:**
```csharp
protected override void OnStartup(StartupEventArgs e)
{
    // ... after creating MainWindow ...
    if (e.Args.Contains("--minimized"))
    {
        // Don't show the window — just sit in tray
        // (window is created but not shown)
    }
    else
    {
        mainWindow.Show();
    }
}
```

**Add to SettingsView.xaml** — a checkbox:
```xml
<CheckBox Content="Start with Windows"
          IsChecked="{Binding StartWithWindows}"
          Margin="0,14,0,0" />
```

**In SettingsViewModel:**
```csharp
[ObservableProperty]
private bool _startWithWindows = AutoStartManager.IsEnabled;

// On Save:
if (StartWithWindows) AutoStartManager.Enable();
else AutoStartManager.Disable();
```

### 6. Publish Profile — Single-File Deployment

**File:** `src/SranDesktop.App/Properties/PublishProfiles/win-x64.pubxml`

```xml
<?xml version="1.0" encoding="utf-8"?>
<Project>
  <PropertyGroup>
    <Configuration>Release</Configuration>
    <Platform>Any CPU</Platform>
    <PublishDir>..\..\publish\win-x64</PublishDir>
    <RuntimeIdentifier>win-x64</RuntimeIdentifier>
    <SelfContained>true</SelfContained>
    <PublishSingleFile>true</PublishSingleFile>
    <PublishTrimmed>false</PublishTrimmed>
    <!-- WPF + reflection = don't trim -->
    <IncludeNativeLibrariesForSelfExtract>true</IncludeNativeLibrariesForSelfExtract>
    <EnableCompressionInSingleFile>true</EnableCompressionInSingleFile>
  </PropertyGroup>
</Project>
```

**Also add to `SranDesktop.App.csproj`:**
```xml
<PropertyGroup>
  <Version>1.0.0</Version>
  <FileVersion>1.0.0.0</FileVersion>
  <AssemblyVersion>1.0.0.0</AssemblyVersion>
  <Product>SRAN Desktop</Product>
  <Company>SRAN</Company>
  <Description>AI Desktop Assistant with MCP Agent capabilities</Description>
  <Copyright>© 2026 SRAN</Copyright>
  <ApplicationIcon>Assets\app.ico</ApplicationIcon>
</PropertyGroup>
```

**Publish command:**
```bash
dotnet publish src/SranDesktop.App/SranDesktop.App.csproj -c Release -p:PublishProfile=win-x64
```

This produces a single `SranDesktop.exe` (self-contained, no .NET runtime needed on target machine).

### 7. Inno Setup Installer

**File:** `build/SranDesktop.iss`

```iss
; SRAN Desktop Installer — Inno Setup 6.x
; Run: ISCC.exe build\SranDesktop.iss

#define MyAppName "SRAN Desktop"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SRAN"
#define MyAppURL "https://sran.ai"
#define MyAppExeName "SranDesktop.exe"

[Setup]
AppId={{B8A2E5F3-7C41-4D8B-9F6A-2E3D4C5B6A7B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\installer
OutputBaseFilename=SranDesktop-{#MyAppVersion}-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
; Install per-user (no admin needed)
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=..\src\SranDesktop.App\Assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "Start with Windows"; GroupDescription: "Additional options:"

[Files]
; Single-file publish output
Source: "..\publish\win-x64\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Include any other files from publish (native libs, etc.)
Source: "..\publish\win-x64\*.dll"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\publish\win-x64\*.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Registry]
; Auto-start (if task selected)
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#MyAppName}"; \
  ValueData: """{app}\{#MyAppExeName}"" --minimized"; \
  Tasks: startupicon; Flags: uninsdeletevalue

[UninstallDelete]
Type: dirifempty; Name: "{app}"

[Code]
// Kill running instance before install/uninstall
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;
```

### 8. First-Run Experience

When the app starts for the first time (no settings file exists), show a quick onboarding:

**File:** `src/SranDesktop.Presentation/Views/Dialogs/FirstRunDialog.xaml`

A simple welcome dialog with 2-3 pages (or a single page):

```xml
<Window ... Title="Welcome to SRAN Desktop" Width="520" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="{DynamicResource BgPrimaryBrush}">
    <StackPanel Margin="32,28">
        <!-- Logo / Title -->
        <TextBlock Text="Welcome to SRAN Desktop" FontSize="22" FontWeight="SemiBold"
                   HorizontalAlignment="Center"
                   Foreground="{DynamicResource TextPrimaryBrush}" />
        <TextBlock Text="AI assistant that lives on your desktop"
                   HorizontalAlignment="Center" Margin="0,6,0,24"
                   Foreground="{DynamicResource TextSecondaryBrush}" />

        <!-- Server Configuration -->
        <TextBlock Text="Server Configuration" FontWeight="SemiBold" Margin="0,0,0,8"
                   Foreground="{DynamicResource TextPrimaryBrush}" />
        <TextBlock Text="Chat Server URL" Style="{StaticResource FieldLabel}" />
        <TextBox Text="{Binding ChatEdgeBaseUrl}" Margin="0,0,0,8" />
        <TextBlock Text="Gateway URL" Style="{StaticResource FieldLabel}" />
        <TextBox Text="{Binding GatewayBaseUrl}" Margin="0,0,0,16" />

        <!-- Quick Tips -->
        <Border Background="{DynamicResource BgSecondaryBrush}" CornerRadius="8" Padding="16,12"
                Margin="0,0,0,16">
            <StackPanel>
                <TextBlock FontWeight="SemiBold" Margin="0,0,0,6"
                           Foreground="{DynamicResource TextPrimaryBrush}"
                           Text="Quick Tips" />
                <TextBlock TextWrapping="Wrap" Foreground="{DynamicResource TextSecondaryBrush}" FontSize="13">
                    <Run Text="• Ctrl+Shift+Space" FontWeight="SemiBold" /> — open SRAN from anywhere
                    <LineBreak />
                    <Run Text="• 🤖 Agent Mode" FontWeight="SemiBold" /> — let AI read files, run commands, browse
                    <LineBreak />
                    <Run Text="• Close to tray" FontWeight="SemiBold" /> — the app keeps running in background
                </TextBlock>
            </StackPanel>
        </Border>

        <!-- Options -->
        <CheckBox Content="Start with Windows" IsChecked="{Binding StartWithWindows}" Margin="0,0,0,6" />
        <CheckBox Content="Show system tray icon" IsChecked="True" IsEnabled="False" Margin="0,0,0,20" />

        <!-- Buttons -->
        <Button Content="Get Started" Style="{DynamicResource AccentButton}"
                HorizontalAlignment="Center" MinWidth="160" Padding="16,10"
                Click="GetStarted_Click" />
    </StackPanel>
</Window>
```

**Show in App.xaml.cs** on first run:
```csharp
// After building host, before showing MainWindow:
var settings = host.Services.GetRequiredService<ISettingsService>();
if (settings.IsFirstRun)
{
    var firstRun = new FirstRunDialog(settings);
    firstRun.ShowDialog();
    // firstRun applies user's choices to settings
}
```

**Detect first run:** Check if `%APPDATA%\SRAN Desktop\settings.json` exists. If not → first run.

### 9. Performance Hardening

#### 9.1 Virtualized Message List

**Modify `ChatView.xaml`** — replace `ItemsControl` with `ListBox` using `VirtualizingStackPanel`:

```xml
<!-- Replace the existing ItemsControl with: -->
<ListBox ItemsSource="{Binding Messages}"
         MaxWidth="860" Margin="24,0"
         Background="Transparent" BorderThickness="0"
         ScrollViewer.HorizontalScrollBarVisibility="Disabled"
         VirtualizingPanel.IsVirtualizing="True"
         VirtualizingPanel.VirtualizationMode="Recycling"
         VirtualizingPanel.ScrollUnit="Pixel"
         SelectionMode="Single"
         HorizontalContentAlignment="Stretch">
    <ListBox.ItemContainerStyle>
        <Style TargetType="ListBoxItem">
            <Setter Property="Padding" Value="0" />
            <Setter Property="Margin" Value="0" />
            <Setter Property="HorizontalContentAlignment" Value="Stretch" />
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="ListBoxItem">
                        <ContentPresenter />
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
    </ListBox.ItemContainerStyle>
    <ListBox.ItemTemplate>
        <DataTemplate DataType="{x:Type vm:MessageViewModel}">
            <controls:MessageBubble />
        </DataTemplate>
    </ListBox.ItemTemplate>
</ListBox>
```

This gives UI virtualization — only visible messages are rendered. Critical for conversations with 100+ messages.

#### 9.2 Streaming Throttle

During fast SSE streaming, too many UI updates per second can cause jank. Add a simple throttle in `ChatViewModel`:

```csharp
// In SendAsync / SendAgentAsync, for content deltas:
private readonly System.Diagnostics.Stopwatch _lastUiUpdate = new();

case ContentDeltaEvent delta:
    assistantMessage.AppendContent(delta.Text);
    // Throttle UI refresh to max ~30fps during streaming
    if (!_lastUiUpdate.IsRunning || _lastUiUpdate.ElapsedMilliseconds > 33)
    {
        MessageActivity?.Invoke(this, EventArgs.Empty);
        _lastUiUpdate.Restart();
    }
    break;
```

Also fire a final update when streaming completes.

#### 9.3 Markdown Rendering Debounce

If `MarkdownRenderer` re-renders on every content append, it's wasteful during streaming. Add a debounce:

```csharp
// In MarkdownRenderer or MessageBubble:
// Only re-render markdown when:
//   a) streaming is complete, OR
//   b) 200ms have passed since last render
// During streaming, show plain text; render markdown on completion.
```

**Simpler approach:** During `IsStreaming`, render content as plain text (TextBlock). On stream complete, switch to full Markdown rendering. This avoids the cost of re-parsing markdown dozens of times per second.

#### 9.4 Memory: Dispose Completed Streams

Ensure `HttpResponseMessage` and response streams from SSE are properly disposed. Check both `ChatEdgeApiClient` and `GatewayApiClient` — each streaming method should wrap the response in a `try/finally` or use `await using`.

#### 9.5 SQLite Write Batching

When saving agent mode messages (assistant + multiple tool results), batch them in a single transaction:

```csharp
await using var transaction = connection.BeginTransaction();
try
{
    foreach (var msg in messages)
        await InsertMessageAsync(msg, connection);
    await transaction.CommitAsync();
}
catch
{
    await transaction.RollbackAsync();
    throw;
}
```

### 10. Error Handling Hardening

#### 10.1 Unhandled Exception Handlers

**In App.xaml.cs:**

```csharp
protected override void OnStartup(StartupEventArgs e)
{
    // Global exception handlers
    DispatcherUnhandledException += (s, args) =>
    {
        _logger.LogCritical(args.Exception, "Unhandled UI thread exception");
        MessageBox.Show(
            $"An unexpected error occurred:\n\n{args.Exception.Message}\n\nThe error has been logged.",
            "SRAN Desktop Error",
            MessageBoxButton.OK,
            MessageBoxImage.Error);
        args.Handled = true; // Don't crash
    };

    AppDomain.CurrentDomain.UnhandledException += (s, args) =>
    {
        if (args.ExceptionObject is Exception ex)
            _logger.LogCritical(ex, "Unhandled domain exception (isTerminating={IsTerminating})", args.IsTerminating);
    };

    TaskScheduler.UnobservedTaskException += (s, args) =>
    {
        _logger.LogError(args.Exception, "Unobserved task exception");
        args.SetObserved(); // Don't crash
    };

    // ... rest of startup ...
}
```

#### 10.2 Network Resilience

Add retry with backoff for conversation list/sync:

```csharp
// In ConversationSyncService or ChatEdgeApiClient:
// On transient HTTP failures (408, 429, 500, 502, 503, 504, network errors):
// Retry up to 3 times with exponential backoff (1s, 2s, 4s)
// Use Polly if you want, or simple manual retry loop
```

**Simpler approach without new NuGet:** Add a helper:

```csharp
public static class RetryHelper
{
    public static async Task<T> WithRetryAsync<T>(
        Func<Task<T>> action,
        int maxRetries = 3,
        int baseDelayMs = 1000,
        CancellationToken ct = default)
    {
        for (int i = 0; ; i++)
        {
            try { return await action(); }
            catch (HttpRequestException) when (i < maxRetries)
            {
                await Task.Delay(baseDelayMs * (1 << i), ct);
            }
        }
    }
}
```

#### 10.3 Graceful Degradation

- **Gateway unreachable in agent mode:** Show clear error "Gateway is unreachable. Agent mode requires the gateway. Check your Gateway URL in Settings." + offer to switch to chat mode.
- **chat-edge-api unreachable:** Show "Chat server is unreachable. Check your connection." with a retry button in the error banner.
- **Auth token expired during streaming:** The 401-refresh logic in `AuthTokenHandler` already handles this for new requests. But a mid-stream 401 is different — detect it (the SSE stream will close with an error) and show "Session expired. Please sign in again."

### 11. Settings Enhancements

Add these new settings to `UserSettings` and the settings UI:

```csharp
// In UserSettings:
public bool MinimizeToTray { get; set; } = true;
public bool StartWithWindows { get; set; } = false;
public string GlobalHotkey { get; set; } = "Ctrl+Shift+Space"; // display only for now
public bool AgentModeDefault { get; set; } = false;
```

**In SettingsView.xaml**, add a new section below the existing fields:

```xml
<TextBlock Text="Desktop Integration" FontWeight="SemiBold" Margin="0,20,0,8"
           Foreground="{DynamicResource TextPrimaryBrush}" />
<CheckBox Content="Minimize to system tray on close"
          IsChecked="{Binding MinimizeToTray}" Margin="0,0,0,6" />
<CheckBox Content="Start with Windows"
          IsChecked="{Binding StartWithWindows}" Margin="0,0,0,6" />
<TextBlock Text="Global Hotkey: Ctrl+Shift+Space" FontSize="12" Margin="0,4,0,0"
           Foreground="{DynamicResource TextSecondaryBrush}" />
```

---

## BUILD ORDER

1. App icon (create `Assets/app.ico`, wire in .csproj and window)
2. Version + metadata in `.csproj`
3. `SingleInstance.cs` + wire in `App.xaml.cs`
4. System tray (`ShutdownMode` change, `H.NotifyIcon` setup, tray menu, minimize-to-tray)
5. `GlobalHotkey.cs` + wire in `App.xaml.cs`
6. `AutoStartManager.cs` + settings toggle
7. `FirstRunDialog.xaml` + first-run detection
8. Publish profile (`win-x64.pubxml`)
9. Inno Setup script (`build/SranDesktop.iss`)
10. Performance: virtualized message list, streaming throttle, markdown debounce
11. Error handlers: unhandled exceptions, network resilience, graceful degradation
12. Settings enhancements (MinimizeToTray, StartWithWindows toggles)
13. Update tests (single-instance, auto-start, hotkey registration)

---

## GOTCHAS

1. **H.NotifyIcon.Wpf v2.x API.** This is the `H.NotifyIcon` fork (not `Hardcodet.NotifyIcon.Wpf`). The API uses `TaskbarIcon` class with `IconSource` (not `Icon`), `TrayMouseDoubleClick` event, and `ShowBalloonTip` method. Check the v2 API if compilation fails.

2. **ShutdownMode change is breaking.** With `OnExplicitShutdown`, the app will NOT close when the user clicks X — you MUST handle the tray's Exit menu item calling `Application.Current.Shutdown()`. Test this carefully.

3. **GlobalHotkey and UAC.** `RegisterHotKey` works without admin. But if another app already holds Ctrl+Shift+Space, registration silently fails. Log a warning but don't crash. Consider making the hotkey configurable in the future.

4. **Single-file publish + WPF.** WPF with `PublishSingleFile` works on .NET 8 but requires `IncludeNativeLibrariesForSelfExtract=true`. Do NOT enable `PublishTrimmed` — WPF uses heavy reflection and trimming will break it.

5. **Inno Setup `PrivilegesRequired=lowest`.** This installs per-user (into `%LOCALAPPDATA%\Programs\`), no admin needed. The registry auto-start key uses `HKCU`, not `HKLM`. This is the correct choice for a developer/power-user tool.

6. **Icon .ico format.** WPF's `BitmapImage` can load .ico files directly. The icon must be embedded as a resource or included in the publish output. Use `Build Action: Resource` for the .ico file.

7. **Named pipe server lifetime.** The `SingleInstance.ListenForActivation` loop creates a new `NamedPipeServerStream` per connection (each pipe instance handles exactly one connection). This is correct — don't try to reuse the same server instance.

8. **`--minimized` on auto-start.** Don't forget: when started with `--minimized`, the MainWindow is created (needed for hotkey registration and DI) but NOT shown. The user sees only the tray icon.

---

## VERIFICATION CHECKLIST

1. [ ] App icon shows in title bar, taskbar, and tray
2. [ ] Second launch activates existing window instead of opening new instance
3. [ ] Closing main window minimizes to tray (shows balloon tip first time)
4. [ ] Tray icon context menu: Show, New Chat, Exit all work
5. [ ] Double-click tray icon restores window
6. [ ] Exit from tray menu actually closes the app
7. [ ] Ctrl+Shift+Space shows window when hidden
8. [ ] Ctrl+Shift+Space hides window when focused
9. [ ] Hotkey works from any app
10. [ ] "Start with Windows" checkbox adds/removes registry entry
11. [ ] `--minimized` flag: app starts in tray only
12. [ ] First run dialog appears on fresh install (delete settings.json to test)
13. [ ] `dotnet publish` produces single SranDesktop.exe
14. [ ] Inno Setup produces working installer
15. [ ] Installer creates Start Menu shortcut + optional desktop icon
16. [ ] Uninstaller cleans up registry auto-start entry
17. [ ] 100+ message conversation scrolls smoothly (virtualization)
18. [ ] Fast streaming doesn't cause UI jank (throttle working)
19. [ ] Unhandled exception shows error dialog, doesn't crash
20. [ ] Gateway unreachable → clear error message in agent mode
21. [ ] All 85 existing tests still pass
22. [ ] Dark + light theme: tray menu, first-run dialog, consent dialog all correct

---

*End of Phase 3 Build Prompt*

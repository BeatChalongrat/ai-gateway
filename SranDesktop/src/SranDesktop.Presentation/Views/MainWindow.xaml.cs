using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media.Animation;
using SranDesktop.Presentation.ViewModels;
using SranDesktop.Presentation.Views.Dialogs;

namespace SranDesktop.Presentation.Views;

public partial class MainWindow : Window
{
    private const double SidebarWidth = 264;

    private readonly MainViewModel _viewModel;
    private readonly Func<SettingsView> _settingsViewFactory;
    private readonly Func<KnowledgeBaseView> _knowledgeBaseViewFactory;
    private readonly Func<MemoryView> _memoryViewFactory;
    private readonly Func<ScheduledTasksView> _scheduledTasksViewFactory;
    private readonly Func<SkillsView> _skillsViewFactory;
    private readonly Func<ConnectorMarketplaceView> _connectorsViewFactory;
    private readonly Func<WritingStyleView> _writingStyleViewFactory;
    private readonly Func<Dialogs.VoiceSettingsDialog> _voiceSettingsFactory;
    private bool _sidebarCollapsed;

    public MainWindow(
        MainViewModel viewModel,
        Func<SettingsView> settingsViewFactory,
        Func<KnowledgeBaseView> knowledgeBaseViewFactory,
        Func<MemoryView> memoryViewFactory,
        Func<ScheduledTasksView> scheduledTasksViewFactory,
        Func<SkillsView> skillsViewFactory,
        Func<ConnectorMarketplaceView> connectorsViewFactory,
        Func<WritingStyleView> writingStyleViewFactory,
        Func<Dialogs.VoiceSettingsDialog> voiceSettingsFactory)
    {
        InitializeComponent();
        _viewModel = viewModel;
        _settingsViewFactory = settingsViewFactory;
        _knowledgeBaseViewFactory = knowledgeBaseViewFactory;
        _memoryViewFactory = memoryViewFactory;
        _scheduledTasksViewFactory = scheduledTasksViewFactory;
        _skillsViewFactory = skillsViewFactory;
        _connectorsViewFactory = connectorsViewFactory;
        _writingStyleViewFactory = writingStyleViewFactory;
        _voiceSettingsFactory = voiceSettingsFactory;
        DataContext = viewModel;
        Loaded += MainWindow_Loaded;
        PreviewKeyDown += MainWindow_PreviewKeyDown;
    }

    // A borderless (WindowStyle=None) window maximizes to the full MONITOR by
    // default, spilling ~8px past every edge and hiding behind the taskbar. Hook
    // WM_GETMINMAXINFO to clamp the maximized size/position to the work area.
    // Registered from Loaded (not OnSourceInitialized) so our hook runs AFTER the
    // WindowChrome worker's and wins the last write to MINMAXINFO.
    private void RegisterMaximizeFix()
    {
        if (PresentationSource.FromVisual(this) is HwndSource source)
            source.AddHook(WndProc);
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        const int WM_GETMINMAXINFO = 0x0024;
        if (msg == WM_GETMINMAXINFO)
        {
            WmGetMinMaxInfo(hwnd, lParam);
            // Do NOT set handled: let other hooks/default proc still run, but since
            // ours runs last it writes MINMAXINFO last and its values take effect.
        }
        return IntPtr.Zero;
    }

    private void WmGetMinMaxInfo(IntPtr hwnd, IntPtr lParam)
    {
        var mmi = Marshal.PtrToStructure<MINMAXINFO>(lParam);

        const uint MONITOR_DEFAULTTONEAREST = 0x00000002;
        var monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
        if (monitor != IntPtr.Zero)
        {
            var info = new MONITORINFO { cbSize = Marshal.SizeOf<MONITORINFO>() };
            GetMonitorInfo(monitor, ref info);
            var work = info.rcWork;
            var full = info.rcMonitor;

            // Positions are relative to the monitor's own origin.
            mmi.ptMaxPosition.X = work.Left - full.Left;
            mmi.ptMaxPosition.Y = work.Top - full.Top;
            mmi.ptMaxSize.X = work.Right - work.Left;
            mmi.ptMaxSize.Y = work.Bottom - work.Top;
            // Also cap the max TRACK size, else the window can still be dragged/sized
            // past the work area even after we clamp the maximized size.
            mmi.ptMaxTrackSize.X = work.Right - work.Left;
            mmi.ptMaxTrackSize.Y = work.Bottom - work.Top;
        }

        Marshal.StructureToPtr(mmi, lParam, true);
    }

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);

    [DllImport("user32.dll")]
    private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO lpmi);

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MINMAXINFO
    {
        public POINT ptReserved;
        public POINT ptMaxSize;
        public POINT ptMaxPosition;
        public POINT ptMinTrackSize;
        public POINT ptMaxTrackSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MONITORINFO
    {
        public int cbSize;
        public RECT rcMonitor;
        public RECT rcWork;
        public uint dwFlags;
    }

    private void MainWindow_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        var ctrl = (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control;
        var shift = (Keyboard.Modifiers & ModifierKeys.Shift) == ModifierKeys.Shift;

        // Esc stops the in-flight generation (same command as the composer's
        // Stop button; ShortcutsDialog has advertised this). Only when nothing
        // else claims Esc — an open popup, the expanded sidebar search, or an
        // inline editor keep their own Esc behavior (this is PreviewKeyDown,
        // so leaving the event unhandled lets it tunnel on to them). Modal
        // dialogs never reach here at all (they get their own key events).
        if (e.Key == Key.Escape)
        {
            var somethingElseClaimsEsc =
                UserMenuPopup.IsOpen
                || MemoryPopup.IsOpen
                || UsagePopup.IsOpen
                || _viewModel.Chat.IsModelPopupOpen
                || _viewModel.Chat.IsKnowledgePopupOpen
                || _viewModel.Conversations.IsSearchExpanded
                || _viewModel.Conversations.Conversations.Any(c => c.IsRenaming)
                || _viewModel.Conversations.Folders.Any(f => f.IsRenaming)
                || _viewModel.Chat.Messages.Any(m => m.IsEditing);
            if (!somethingElseClaimsEsc
                && _viewModel.Chat.CancelStreamingCommand.CanExecute(null))
            {
                e.Handled = true;
                _viewModel.Chat.CancelStreamingCommand.Execute(null);
            }
            return;
        }

        // Ctrl+Shift+S toggles the sidebar (same path as the ☰ button;
        // advertised in ShortcutsDialog since R9).
        if (e.Key == Key.S && ctrl && shift)
        {
            e.Handled = true;
            ToggleSidebar_Click(this, new RoutedEventArgs());
            return;
        }

        // Ctrl+/ opens the keyboard-shortcuts help (A6).
        if (e.Key == Key.OemQuestion && ctrl)
        {
            e.Handled = true;
            new ShortcutsDialog { Owner = this }.ShowDialog();
            return;
        }

        // Ctrl+K expands + focuses the sidebar search (web parity R2 Fix 1c).
        if (e.Key == Key.K && ctrl)
        {
            e.Handled = true;
            if (_sidebarCollapsed)
                ToggleSidebar_Click(this, new RoutedEventArgs());
            SidebarControl.FocusSearch();
            return;
        }

        // Ctrl+N starts a new chat (matches the sidebar hint).
        if (e.Key == Key.N && ctrl && !_viewModel.IsInitializing && _viewModel.IsAuthenticated)
        {
            e.Handled = true;
            if (_viewModel.Conversations.NewChatCommand.CanExecute(null))
                _viewModel.Conversations.NewChatCommand.Execute(null);
        }
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        Loaded -= MainWindow_Loaded;
        RegisterMaximizeFix();
        await _viewModel.InitializeAsync();
    }

    private void SettingsButton_Click(object sender, RoutedEventArgs e)
    {
        var settings = _settingsViewFactory();
        settings.Owner = this;
        settings.ShowDialog();
    }

    private void UserMenuButton_Click(object sender, RoutedEventArgs e)
        => UserMenuPopup.IsOpen = !UserMenuPopup.IsOpen;

    private void CloseUserMenu_Click(object sender, RoutedEventArgs e)
        => UserMenuPopup.IsOpen = false;

    private void OpenKnowledgeBase_Click(object sender, RoutedEventArgs e)
    {
        UserMenuPopup.IsOpen = false;
        var view = _knowledgeBaseViewFactory();
        view.Owner = this;
        view.ShowDialog();
    }

    private void OpenMemory_Click(object sender, RoutedEventArgs e)
    {
        UserMenuPopup.IsOpen = false;
        MemoryPopup.IsOpen = false;
        var view = _memoryViewFactory();
        view.Owner = this;
        view.ShowDialog();
        // Suggestions may have been confirmed/rejected — refresh the badge now.
        _ = _viewModel.Chat.RefreshPendingMemoriesAsync();
    }

    private void ToggleLanguage_Click(object sender, RoutedEventArgs e)
    {
        var svc = Localization.LocaleService.Instance;
        svc.Locale = svc.Locale == "en" ? "th" : "en";
    }

    private void OpenShortcuts_Click(object sender, RoutedEventArgs e)
    {
        UserMenuPopup.IsOpen = false;
        new ShortcutsDialog { Owner = this }.ShowDialog();
    }

    private void ShowOwnedDialog(Window view)
    {
        UserMenuPopup.IsOpen = false;
        view.Owner = this;
        view.ShowDialog();
    }

    private void OpenScheduledTasks_Click(object sender, RoutedEventArgs e)
        => ShowOwnedDialog(_scheduledTasksViewFactory());

    private void OpenSkills_Click(object sender, RoutedEventArgs e)
        => ShowOwnedDialog(_skillsViewFactory());

    private void OpenConnectors_Click(object sender, RoutedEventArgs e)
        => ShowOwnedDialog(_connectorsViewFactory());

    private void OpenWritingStyle_Click(object sender, RoutedEventArgs e)
        => ShowOwnedDialog(_writingStyleViewFactory());

    private void OpenVoiceSettings_Click(object sender, RoutedEventArgs e)
        => ShowOwnedDialog(_voiceSettingsFactory());

    private void OpenDesktopSettings_Click(object sender, RoutedEventArgs e)
    {
        UserMenuPopup.IsOpen = false;
        SettingsButton_Click(sender, e);
    }

    private void UsageBadge_Click(object sender, RoutedEventArgs e)
    {
        _ = _viewModel.RefreshUsageAsync(); // refresh if stale (>60s)
        UsagePopup.IsOpen = !UsagePopup.IsOpen;
    }

    private void OpenPortalUsage_Click(object sender, RoutedEventArgs e)
    {
        UsagePopup.IsOpen = false;
        OpenInBrowser("http://localhost:3001/usage");
    }

    private void Logout_Click(object sender, RoutedEventArgs e)
    {
        UserMenuPopup.IsOpen = false;
        if (_viewModel.LogoutCommand.CanExecute(null))
            _viewModel.LogoutCommand.Execute(null);
    }

    private static void OpenInBrowser(string url)
    {
        try
        {
            System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch
        {
            // Best-effort: no default browser / blocked — nothing sensible to do.
        }
    }

    // ---------- header: Memory popover + "..." export menu (web parity R2) ----------

    private void MemoryButton_Click(object sender, RoutedEventArgs e)
        => MemoryPopup.IsOpen = true;

    private void MemoryToggle_Click(object sender, RoutedEventArgs e)
    {
        MemoryPopup.IsOpen = false;
        if (_viewModel.Chat.ToggleMemoryCommand.CanExecute(null))
            _viewModel.Chat.ToggleMemoryCommand.Execute(null);
    }

    private void OpenMemorySettings_Click(object sender, RoutedEventArgs e)
        => OpenMemory_Click(sender, e);

    private void MoreMenuButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { ContextMenu: { } menu } fe)
        {
            menu.PlacementTarget = fe;
            menu.Placement = System.Windows.Controls.Primitives.PlacementMode.Bottom;
            menu.IsOpen = true;
        }
    }

    private void SidebarControl_CollapseRequested(object? sender, EventArgs e)
        => ToggleSidebar_Click(this, new RoutedEventArgs());

    // ---------- custom window controls ----------

    private void MinimizeButton_Click(object sender, RoutedEventArgs e)
        => WindowState = WindowState.Minimized;

    private void MaximizeButton_Click(object sender, RoutedEventArgs e)
        => WindowState = WindowState == WindowState.Maximized
            ? WindowState.Normal
            : WindowState.Maximized;

    private void CloseButton_Click(object sender, RoutedEventArgs e)
        => Close(); // App's MainWindow_Closing handles minimize-to-tray

    private void ToggleSidebar_Click(object sender, RoutedEventArgs e)
    {
        _sidebarCollapsed = !_sidebarCollapsed;
        var animation = new DoubleAnimation(
            _sidebarCollapsed ? 0 : SidebarWidth,
            TimeSpan.FromMilliseconds(180))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
        };
        SidebarRoot.BeginAnimation(WidthProperty, animation);
    }
}

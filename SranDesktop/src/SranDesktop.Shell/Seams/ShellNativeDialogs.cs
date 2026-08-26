using System.Windows;
using System.Windows.Controls;
using Microsoft.Extensions.Logging;
using Microsoft.Win32;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Shell.Seams;

/// <summary>
/// The shell's own native dialogs (§2.4). Pickers use <c>Microsoft.Win32</c>
/// (WPF framework, not the Presentation project — the shell may not reference
/// that). The secret prompt and the add-custom / spawn-consent flow are minimal
/// code-built WPF windows owned by the shell, so a page can never render the
/// approval of a child process or the entry of a key.
/// </summary>
public sealed class ShellNativeDialogs : IShellNativeDialogs
{
    private readonly IMcpConnectorService _connectors;
    private readonly IDialogService _dialogs;
    private readonly ILogger<ShellNativeDialogs> _logger;

    public ShellNativeDialogs(
        IMcpConnectorService connectors, IDialogService dialogs, ILogger<ShellNativeDialogs> logger)
    {
        _connectors = connectors;
        _dialogs = dialogs;
        _logger = logger;
    }

    private static System.Windows.Threading.Dispatcher Dispatcher =>
        System.Windows.Application.Current.Dispatcher;

    private static Window? Owner => System.Windows.Application.Current?.MainWindow;

    public Task<string?> PickSkillPackageAsync() => OnUi(() =>
    {
        var dialog = new OpenFileDialog
        {
            Title = "Install skill",
            Filter = "Skill packages (*.skill;*.zip)|*.skill;*.zip",
        };
        return dialog.ShowDialog() == true ? dialog.FileName : null;
    });

    public Task<string?> PickFolderAsync() => OnUi(() =>
    {
        var dialog = new OpenFolderDialog { Title = "Grant folder access" };
        return dialog.ShowDialog() == true ? dialog.FolderName : null;
    });

    public Task<string?> PromptApiKeyAsync(string connectorName) => OnUi(() =>
    {
        var window = BuildWindow($"{connectorName} API key", 420);
        var panel = (StackPanel)window.Content;
        panel.Children.Add(new TextBlock { Text = $"Enter your {connectorName} API key:", Margin = new Thickness(0, 0, 0, 6) });
        var box = new PasswordBox { Margin = new Thickness(0, 0, 0, 12) };
        panel.Children.Add(box);
        string? result = null;
        AddButtons(panel, window, onOk: () =>
        {
            result = box.Password;
            window.DialogResult = !string.IsNullOrWhiteSpace(result);
        });
        box.Focus();
        return window.ShowDialog() == true ? result : null;
    });

    public Task<bool> ShowAddCustomConnectorAsync() => OnUiAsync(() =>
    {
        var window = BuildWindow("Add custom MCP server (stdio)", 520);
        var panel = (StackPanel)window.Content;
        var name = LabeledBox(panel, "Name");
        var command = LabeledBox(panel, "Command (executable on PATH or absolute)");
        var args = LabeledBox(panel, "Arguments (space-separated)");
        var status = new TextBlock { Margin = new Thickness(0, 4, 0, 8), TextWrapping = TextWrapping.Wrap };
        panel.Children.Add(status);

        McpConnector? tested = null;

        var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var testBtn = new Button { Content = "Test", Width = 90, Margin = new Thickness(0, 0, 8, 0), IsDefault = false };
        var addBtn = new Button { Content = "Add", Width = 90, IsEnabled = false };
        var cancelBtn = new Button { Content = "Cancel", Width = 90, Margin = new Thickness(8, 0, 0, 0), IsCancel = true };

        testBtn.Click += async (_, _) =>
        {
            tested = null; addBtn.IsEnabled = false;
            var def = BuildDefinition(name.Text, command.Text, args.Text);
            if (def is null) { status.Text = "Name and command are required."; return; }
            status.Text = "Testing (spawning the process)…";
            testBtn.IsEnabled = false;
            try
            {
                var result = await _connectors.TestConnectionAsync(def);
                if (result.Success)
                {
                    tested = def;
                    addBtn.IsEnabled = true;
                    status.Text = $"OK — {result.ToolCount} tool(s) discovered.";
                }
                else
                {
                    status.Text = $"Failed: {result.Error}";
                }
            }
            catch (Exception ex)
            {
                status.Text = $"Failed: {ex.Message}";
            }
            finally { testBtn.IsEnabled = true; }
        };

        addBtn.Click += async (_, _) =>
        {
            if (tested is null) return;
            // NATIVE spawn consent — approving a child process is not the page's job.
            var approved = await _dialogs.ConfirmAsync(
                "Approve local process",
                $"Allow SRAN to spawn:\n\n{tested.Command} {string.Join(' ', tested.Args ?? new())}\n\n" +
                "It will run on this machine whenever its tools are used.");
            if (!approved) return;
            try
            {
                await _connectors.InstallCustomAsync(tested);
                window.DialogResult = true;
            }
            catch (Exception ex)
            {
                status.Text = $"Install failed: {ex.Message}";
            }
        };

        row.Children.Add(testBtn);
        row.Children.Add(addBtn);
        row.Children.Add(cancelBtn);
        panel.Children.Add(row);
        return Task.FromResult(window.ShowDialog() == true);
    });

    private static McpConnector? BuildDefinition(string name, string command, string args)
    {
        if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(command))
            return null;
        return new McpConnector
        {
            Id = $"custom:{Guid.NewGuid():N}",
            Name = name.Trim(),
            Description = "User-added stdio MCP server",
            Category = "developer",
            AuthType = "none",
            Transport = McpConnector.TransportStdio,
            Command = command.Trim(),
            Args = string.IsNullOrWhiteSpace(args)
                ? new List<string>()
                : args.Trim().Split(' ', StringSplitOptions.RemoveEmptyEntries).ToList(),
        };
    }

    // ── tiny WPF window helpers (code-built; no XAML, no Presentation) ─────

    private static Window BuildWindow(string title, double width)
    {
        var window = new Window
        {
            Title = title,
            Width = width,
            SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            ResizeMode = ResizeMode.NoResize,
            ShowInTaskbar = false,
            Owner = Owner is { IsLoaded: true } o ? o : null,
        };
        window.Content = new StackPanel { Margin = new Thickness(16) };
        return window;
    }

    private static TextBox LabeledBox(StackPanel panel, string label)
    {
        panel.Children.Add(new TextBlock { Text = label, Margin = new Thickness(0, 4, 0, 2) });
        var box = new TextBox { Margin = new Thickness(0, 0, 0, 6) };
        panel.Children.Add(box);
        return box;
    }

    private static void AddButtons(StackPanel panel, Window window, Action onOk)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var ok = new Button { Content = "OK", Width = 90, Margin = new Thickness(0, 0, 8, 0), IsDefault = true };
        ok.Click += (_, _) => onOk();
        var cancel = new Button { Content = "Cancel", Width = 90, IsCancel = true };
        row.Children.Add(ok);
        row.Children.Add(cancel);
        panel.Children.Add(row);
    }

    private static Task<T> OnUi<T>(Func<T> func) =>
        Dispatcher.CheckAccess() ? Task.FromResult(func()) : Dispatcher.InvokeAsync(func).Task;

    private static Task<T> OnUiAsync<T>(Func<Task<T>> func) =>
        Dispatcher.CheckAccess() ? func() : Dispatcher.InvokeAsync(func).Task.Unwrap();
}

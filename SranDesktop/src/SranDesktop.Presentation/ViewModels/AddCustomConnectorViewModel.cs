using System.IO;
using System.Text;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// "Add custom connector" form (custom-connector UI): lets the user register
/// ANY MCP server — HTTP or a local stdio command — like Claude Desktop's
/// custom connectors. Validation gates Add; Test connection dials the server
/// without persisting anything. stdio runs a local program, so BOTH Test and
/// Add pass through one themed consent showing the exact command line before
/// the first spawn. The pasted API key only ever travels to the service
/// (→ DPAPI); it is never part of the persisted definition.
/// </summary>
public partial class AddCustomConnectorViewModel : ObservableObject
{
    private readonly IMcpConnectorService _connectors;
    private readonly IDialogService _dialogs;
    private bool _stdioConfirmed; // one warning per dialog, not per click

    /// <summary>Raised when the dialog should close (true = a connector was added).</summary>
    public event EventHandler<bool>? RequestClose;

    public AddCustomConnectorViewModel(IMcpConnectorService connectors, IDialogService dialogs)
    {
        _connectors = connectors;
        _dialogs = dialogs;
    }

    // ── form fields ──────────────────────────────────────────────────────────

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanAdd), nameof(ValidationText))]
    private string _name = "";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsHttp), nameof(CanAdd), nameof(ValidationText))]
    private bool _isStdio;

    /// <summary>Radio partner of <see cref="IsStdio"/> so both buttons two-way
    /// bind without an inverse-bool converter.</summary>
    public bool IsHttp
    {
        get => !IsStdio;
        set => IsStdio = !value;
    }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanAdd), nameof(ValidationText))]
    private string _serverUrl = "";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanAdd), nameof(ValidationText))]
    private string _command = "";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanAdd), nameof(ValidationText))]
    private string _argumentsLine = "";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanAdd), nameof(ValidationText))]
    private string _envLines = "";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(UseNoAuth), nameof(CanAdd), nameof(ValidationText))]
    private bool _useApiKey;

    /// <summary>Radio partner of <see cref="UseApiKey"/> (same trick as IsHttp).</summary>
    public bool UseNoAuth
    {
        get => !UseApiKey;
        set => UseApiKey = !value;
    }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanAdd), nameof(ValidationText))]
    private string _headerName = "Authorization";

    /// <summary>Set from the PasswordBox in code-behind (PasswordBox has no
    /// bindable Password by design); held in memory only.</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanAdd), nameof(ValidationText))]
    private string _apiKey = "";

    // ── test / busy state ────────────────────────────────────────────────────

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanAdd))]
    private bool _isBusy;

    [ObservableProperty] private string _testStatusText = "";
    [ObservableProperty] private bool _testFailed;

    // ── validation ───────────────────────────────────────────────────────────

    public bool CanAdd => !IsBusy && FirstErrorKey() is null;

    /// <summary>Inline hint under the form — doubles as the "what's missing"
    /// guide while the form is incomplete; empty when valid.</summary>
    public string ValidationText =>
        FirstErrorKey() is { } key ? LocaleService.Instance.T(key) : "";

    /// <summary>First failing rule as a Dict key (null = form is valid).
    /// Internal for tests — the can-add gating is exactly "this is null".</summary>
    internal string? FirstErrorKey()
    {
        if (string.IsNullOrWhiteSpace(Name))
            return "connector.err.nameRequired";
        if (_connectors.IsInstalled(MakeId(Name)))
            return "connector.err.nameTaken";

        if (IsStdio)
        {
            if (string.IsNullOrWhiteSpace(Command))
                return "connector.err.commandRequired";
            if (TryParseEnv(EnvLines, out _) is { } envError)
                return envError;
            return null; // auth section applies to HTTP only
        }

        if (ValidateServerUrl(ServerUrl) is { } urlError)
            return urlError;
        if (UseApiKey)
        {
            if (string.IsNullOrWhiteSpace(HeaderName))
                return "connector.err.headerRequired";
            if (string.IsNullOrEmpty(ApiKey))
                return "connector.err.keyRequired";
        }
        return null;
    }

    /// <summary>https required for non-localhost — a pasted API key must never
    /// travel cleartext to a remote host (mirrors the service-side rule).</summary>
    internal static string? ValidateServerUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url))
            return "connector.err.urlRequired";
        if (!Uri.TryCreate(url.Trim(), UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https"))
            return "connector.err.urlInvalid";
        if (uri.Scheme == "http" && !uri.IsLoopback)
            return "connector.err.urlHttps";
        return null;
    }

    /// <summary>Stable id from the display name: "custom-" + slug, so custom
    /// ids can never collide with catalog ids (which are bare words).</summary>
    internal static string MakeId(string name)
    {
        var slug = new StringBuilder();
        var lastDash = true;
        foreach (var ch in name.Trim().ToLowerInvariant())
        {
            if (char.IsLetterOrDigit(ch))
            {
                slug.Append(ch);
                lastDash = false;
            }
            else if (!lastDash)
            {
                slug.Append('-');
                lastDash = true;
            }
        }
        var text = slug.ToString().TrimEnd('-');
        return "custom-" + (text.Length > 0 ? text : "connector");
    }

    /// <summary>One text line → argv, honoring double quotes (so
    /// <c>-y pkg "C:\My Data"</c> stays three arguments).</summary>
    internal static List<string> SplitArguments(string? line)
    {
        var result = new List<string>();
        if (string.IsNullOrWhiteSpace(line))
            return result;

        var current = new StringBuilder();
        var inQuotes = false;
        foreach (var ch in line)
        {
            if (ch == '"')
            {
                inQuotes = !inQuotes;
            }
            else if (char.IsWhiteSpace(ch) && !inQuotes)
            {
                if (current.Length > 0)
                {
                    result.Add(current.ToString());
                    current.Clear();
                }
            }
            else
            {
                current.Append(ch);
            }
        }
        if (current.Length > 0)
            result.Add(current.ToString());
        return result;
    }

    /// <summary>Multiline KEY=value → dictionary. Returns an error KEY for a
    /// malformed line, null when ok (env = null when there are no entries).</summary>
    internal static string? TryParseEnv(string? text, out Dictionary<string, string>? env)
    {
        env = null;
        if (string.IsNullOrWhiteSpace(text))
            return null;

        var parsed = new Dictionary<string, string>();
        foreach (var raw in text.Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0)
                continue;
            var eq = line.IndexOf('=');
            if (eq <= 0)
                return "connector.err.envInvalid";
            parsed[line[..eq].Trim()] = line[(eq + 1)..].Trim();
        }
        env = parsed.Count > 0 ? parsed : null;
        return null;
    }

    /// <summary>The definition the service receives. The API key is NOT part
    /// of it — it goes as a separate argument straight to DPAPI.</summary>
    internal McpConnector BuildConnector()
    {
        var name = Name.Trim();
        TryParseEnv(EnvLines, out var env);
        var useApiKey = !IsStdio && UseApiKey;
        var header = HeaderName.Trim();
        return new McpConnector
        {
            Id = MakeId(name),
            Name = name,
            Description = IsStdio
                ? $"{Command.Trim()} {ArgumentsLine.Trim()}".Trim()
                : ServerUrl.Trim(),
            Category = "custom",
            AuthType = useApiKey ? "api_key" : "none",
            Transport = IsStdio ? McpConnector.TransportStdio : McpConnector.TransportHttp,
            ServerUrl = IsStdio ? "" : ServerUrl.Trim(),
            Command = IsStdio ? Command.Trim() : null,
            Args = IsStdio ? SplitArguments(ArgumentsLine) : null,
            Env = IsStdio ? env : null,
            AuthHeader = useApiKey
                && !header.Equals("Authorization", StringComparison.OrdinalIgnoreCase)
                ? header : null
        };
    }

    /// <summary>The exact command line shown in the stdio consent dialog.</summary>
    internal string StdioCommandLine() =>
        $"{Command.Trim()} {ArgumentsLine.Trim()}".Trim();

    /// <summary>stdio runs a local program with the user's permissions — one
    /// themed consent (exact command line shown) before the FIRST spawn,
    /// whether that spawn is Test or Add.</summary>
    private async Task<bool> ConfirmStdioAsync()
    {
        if (!IsStdio || _stdioConfirmed)
            return true;
        _stdioConfirmed = await _dialogs.ConfirmAsync(
            LocaleService.Instance.T("connector.stdioWarningTitle"),
            LocaleService.Instance.T("connector.stdioWarning", StdioCommandLine()));
        return _stdioConfirmed;
    }

    [RelayCommand]
    private async Task TestConnectionAsync()
    {
        if (!CanAdd)
            return;
        if (!await ConfirmStdioAsync())
            return;

        IsBusy = true;
        TestFailed = false;
        TestStatusText = LocaleService.Instance.T("connector.testing");
        try
        {
            var result = await _connectors.TestConnectionAsync(
                BuildConnector(), UseApiKey && !IsStdio ? ApiKey : null);
            TestFailed = !result.Success;
            TestStatusText = result.Success
                ? LocaleService.Instance.T("connector.testOk", result.ToolCount)
                : result.Error ?? LocaleService.Instance.T("connector.connectFailed");
        }
        finally
        {
            IsBusy = false;
        }
    }

    [RelayCommand]
    private async Task AddAsync()
    {
        if (!CanAdd)
            return;
        if (!await ConfirmStdioAsync())
            return;

        IsBusy = true;
        try
        {
            await _connectors.InstallCustomAsync(
                BuildConnector(), UseApiKey && !IsStdio ? ApiKey : null);
        }
        catch (Exception ex) when (ex is ArgumentException or IOException)
        {
            TestFailed = true;
            TestStatusText = ex.Message;
            return;
        }
        finally
        {
            IsBusy = false;
        }
        RequestClose?.Invoke(this, true);
    }
}

using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Opens a link in the user's default browser so the USER can look at it.
///
/// <para><b>This is not browser automation.</b> It hands a URL to the system
/// browser and reports that it did so — nothing more. It does not read the page,
/// does not know whether the page loaded, and makes no claim about content.
/// <c>browser_navigate</c> is the tool for driving a page and verifying the
/// result, and it still goes exclusively through the extension bridge.</para>
///
/// <para>Why this works again (P3-5). The two jobs were previously collapsed:
/// because agent-driven navigation must be observable, this tool was made
/// bridge-only as well, so opening a link became impossible whenever the Chrome
/// extension was not installed or paired. But "show the user a page" needs no
/// observation — the user is looking at it. The invariant worth keeping is not
/// "never launch a browser", it is "never claim a page state you did not
/// observe", which is why this returns
/// <see cref="ToolResult.ExecutedUnverified"/> and never
/// <see cref="ToolResult.Verified"/>.</para>
///
/// <para>What constrains it: the launch goes through
/// <see cref="IBrowserLauncher"/>, which refuses any scheme other than
/// http/https — so no <c>file://</c>, no <c>ms-settings:</c>, no custom protocol
/// handler that would launch a local program. The scheme is checked here too, so
/// the refusal reaches the model as an explanation rather than a bare false. The
/// consent gate (<see cref="PermissionLevel.Ask"/>) shows the user the exact URL
/// first. Because the launch is delegated, this file starts no process itself and
/// stays subject to the unmodified
/// <c>No_Agent_Tool_Starts_A_Host_Process_Outside_The_Audited_Runners</c>
/// invariant.</para>
/// </summary>
public sealed class OpenUrlTool : IMcpTool
{
    private readonly IBrowserLauncher _launcher;

    public OpenUrlTool(IBrowserLauncher launcher) => _launcher = launcher;

    public string Name => "open_url";
    public string Description =>
        "Open an http(s) link in the user's default web browser so the user can read it. "
        + "Returns once the browser has been asked to open it; whether the page loaded is "
        + "NOT reported, so never treat this as confirmation that the page exists or says "
        + "anything in particular. To read or interact with a page, use browser_navigate "
        + "and the other browser_* tools instead.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "url": { "type": "string", "description": "Absolute http:// or https:// URL" }
            },
            "required": ["url"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var url = ToolHelpers.GetString(arguments, "url");
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https"))
            return Task.FromResult(ToolResult.Fail(
                "URL must be absolute and use http:// or https://. Other schemes "
                + "(file://, custom protocol handlers) are refused: the shell would treat "
                + "them as an instruction to launch a local program, not to show a page."));

        // AbsoluteUri, not the raw string — the parsed-and-reserialized form is what
        // the scheme check above actually approved.
        if (!_launcher.Open(uri.AbsoluteUri))
            return Task.FromResult(ToolResult.FailedEffect(
                "BrowserLaunchFailed",
                "The default browser could not be started.",
                retryable: false));

        // Deliberately NOT Verified. The browser was asked to open the URL; that is
        // the whole of what happened and the whole of what is reported. Claiming a
        // loaded page here would be a false success — the class of defect that costs
        // days to find, because every layer downstream believes the claim.
        return Task.FromResult(ToolResult.ExecutedUnverified(
            $"Asked the default browser to open {uri.AbsoluteUri}. Whether the page "
            + "loaded was not observed.",
            "NavigationNotObserved",
            "open_url hands the URL to the system browser and cannot see the resulting "
            + "page. Use browser_navigate if the outcome needs to be verified.",
            retryable: false,
            evidence: new[] { $"dispatched_url={uri.AbsoluteUri}" }));
    }
}

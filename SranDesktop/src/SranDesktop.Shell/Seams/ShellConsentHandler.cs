using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Shell.Seams;

/// <summary>
/// Seam 5 — the tool-consent handler. In the WPF app this lives in Presentation
/// ViewModels, not in DI. Phase 1 runs no chat, so the shell's handler
/// <b>denies and logs</b>. That is deliberate: <c>IChatOrchestrator</c> already
/// denies when nobody is subscribed, but an explicit subscriber makes the
/// denial visible in the log instead of silent — and a consent path that
/// silently allows is exactly the default that ships. Phase 3 replaces this
/// with the <c>consent.request</c> / <c>consent.respond</c> round-trip.
/// </summary>
public sealed class ShellConsentHandler
{
    private readonly ILogger<ShellConsentHandler> _logger;

    public ShellConsentHandler(ILogger<ShellConsentHandler> logger)
    {
        _logger = logger;
    }

    public void Attach(IChatOrchestrator orchestrator)
    {
        orchestrator.ConsentRequested += HandleAsync;
        _logger.LogInformation("Consent handler attached: Phase 1 policy = DENY all (logged).");
    }

    public Task<ToolConsentResponse> HandleAsync(ToolConsentRequest request)
    {
        // Never log ArgumentsJson: a tool argument can carry file contents,
        // URLs with tokens, or clipboard text.
        _logger.LogWarning(
            "Consent DENIED (Phase 1 policy): tool={Tool} category={Category} scope={Scope} callId={CallId}",
            request.ToolName, request.Category, request.Scope, request.ToolCallId);
        return Task.FromResult(ToolConsentResponse.Deny);
    }
}

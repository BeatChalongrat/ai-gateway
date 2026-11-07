namespace SranDesktop.Application.Interfaces;

/// <summary>
/// FIX-F: decides whether a read-class tool call (read_file / list_directory /
/// search_files) targets a location OUTSIDE the user's chosen scope (working
/// folder + granted folders) and therefore must ask before running, even
/// though reads are otherwise consent-free. Implemented in Infrastructure on
/// top of PathGuard; the orchestrator only consumes the verdict.
/// </summary>
public interface ISensitiveReadGuard
{
    bool RequiresConsent(IMcpTool tool, string argumentsJson);
}

using System.IO;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// One packaged connector in the managed folder, with whatever the resolver says
/// about it right now.
///
/// The card exists mainly so a refusal has somewhere to be shown. A connector
/// that cannot run because its binary changed, because it now asks for the
/// network, or because nobody has ever approved it is not broken — it is waiting
/// for a decision, and a UI that only listed working connectors would leave the
/// user with no way to make one.
/// </summary>
public sealed partial class LocalConnectorItemViewModel : ObservableObject
{
    private readonly IConnectorManifestResolver _resolver;
    private readonly IConnectorTrustAdministration _trust;
    private readonly Action _reload;
    private readonly ILogger _logger;

    public LocalConnectorItemViewModel(
        ConnectorDeclaration declaration,
        IConnectorManifestResolver resolver,
        IConnectorTrustAdministration trust,
        Action reload,
        ILogger logger)
    {
        Declaration = declaration;
        _resolver = resolver;
        _trust = trust;
        _reload = reload;
        _logger = logger;
        Resolution = resolver.Resolve(declaration.ConnectorId);
    }

    public ConnectorDeclaration Declaration { get; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(StatusText))]
    [NotifyPropertyChangedFor(nameof(IsApproved))]
    [NotifyPropertyChangedFor(nameof(NeedsDecision))]
    [NotifyPropertyChangedFor(nameof(ActionText))]
    private ConnectorResolution _resolution;

    public string Name => Declaration.Name;
    public string ConnectorId => Declaration.ConnectorId;
    public string Version => Declaration.Version;
    public string Publisher => Declaration.Publisher ?? "Unidentified publisher";

    public bool IsApproved => Resolution.Resolved;

    /// <summary>Every refusal here is one a person can act on, so the card always
    /// offers the approval screen rather than only when nothing is wrong.</summary>
    public bool NeedsDecision => !Resolution.Resolved;

    public string ActionText => IsApproved ? "Review" : "Review and approve";

    public string StatusText => Resolution.Resolved
        ? "Approved and ready"
        : Resolution.DenialReason ?? "This connector cannot run.";

    [RelayCommand]
    private async Task ReviewAsync()
    {
        var declaration = _resolver.ReadDeclaration(ConnectorId) ?? Declaration;
        var executableHash = "";
        try
        {
            if (declaration.Transport == ConnectorTransportKind.Stdio
                && File.Exists(declaration.ExecutablePath))
                executableHash = ConnectorTrustPolicy.HashExecutable(declaration.ExecutablePath);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger.LogWarning(ex,
                "Connector {Id} executable could not be hashed for the approval screen",
                ConnectorId);
        }

        var dialog = new Views.Dialogs.ConnectorApprovalDialog(new ConnectorApprovalRequest(
            declaration,
            executableHash,
            declaration.ComputeFingerprint(executableHash),
            Resolution.Trust,
            Resolution.PermissionExpansions,
            Resolution.DenialCode))
        {
            Owner = System.Windows.Application.Current?.MainWindow
        };
        if (dialog.ShowDialog() != true)
            return;

        // A click the gate refused is not an approval and not a denial — it is a
        // decision that did not happen, and it says so.
        if (dialog.RejectedGestureReason is { Length: > 0 } rejected)
        {
            _logger.LogWarning(
                "Connector {Id} approval gesture refused: {Reason}", ConnectorId, rejected);
            return;
        }

        try
        {
            if (dialog.Evidence is { } evidence)
                await _trust.ApproveAsync(ConnectorId, dialog.Decision, evidence);
            else
                // Denial needs no evidence: refusing to run is always safe, and
                // demanding proof for it would be a reason to leave a connector
                // approved.
                await _trust.ApproveAsync(
                    ConnectorId,
                    dialog.Decision,
                    UserApprovalEvidence.FromAdministrativePolicy("user-denial"));
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException
                                       or UnauthorizedAccessException
                                       or FileNotFoundException)
        {
            _logger.LogError(ex, "Connector {Id} decision could not be recorded", ConnectorId);
        }

        Resolution = _resolver.Resolve(ConnectorId);
        _reload();
    }
}

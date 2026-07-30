using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using SranDesktop.Application.Models;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>One line of "what this connector will be able to do".</summary>
public sealed record ConnectorPermissionLine(string Title, string Detail, bool IsNew);

/// <summary>
/// What the user is shown before a local connector is allowed to run.
///
/// Everything here is derived from the manifest ON DISK and the identity
/// computed from it a moment ago — never from anything the server said about
/// itself, because the server has not been started and will not be unless this
/// screen is approved. The hash is shown in short form for the same reason a
/// fingerprint is: nobody compares 64 hex characters, but they will notice that
/// it changed.
/// </summary>
public sealed partial class ConnectorApprovalViewModel : ObservableObject
{
    private readonly ConnectorApprovalRequest _request;

    public ConnectorApprovalViewModel(ConnectorApprovalRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);
        _request = request;
        var declaration = request.Declaration;

        Name = declaration.Name;
        ConnectorId = declaration.ConnectorId;
        Version = declaration.Version;
        Publisher = string.IsNullOrWhiteSpace(declaration.Publisher)
            // Not "trusted publisher, name unavailable": an unsigned package from
            // nobody in particular should read as exactly that.
            ? "Unidentified publisher"
            : declaration.Publisher!;
        InstallSource = declaration.InstallSource;
        Description = declaration.Description;
        RiskSummary = string.IsNullOrWhiteSpace(declaration.RiskSummary)
            ? "The package does not describe what its tools do."
            : declaration.RiskSummary;

        ExecutablePath = declaration.Transport == ConnectorTransportKind.Http
            ? declaration.ServerUrl
            : declaration.ExecutablePath;
        ExecutableFingerprint = Shorten(
            declaration.Transport == ConnectorTransportKind.Http
                ? request.ManifestFingerprint
                : request.ExecutableSha256);
        ManifestFingerprint = Shorten(request.ManifestFingerprint);
        IsRemote = declaration.Transport == ConnectorTransportKind.Http;

        foreach (var line in BuildPermissions(declaration, request.PermissionExpansions))
            Permissions.Add(line);

        IsReapproval = request.Existing is { State: not ConnectorTrustState.Untrusted };
        ReapprovalReason = request.Existing?.StateReason ?? "";
        HasExpansions = request.PermissionExpansions.Count > 0;
        PreviousDenialCode = request.PreviousDenialCode ?? "";

        Headline = IsReapproval
            ? $"{Name} changed and needs approving again"
            : $"Allow {Name} to run?";
    }

    public string Headline { get; }
    public string Name { get; }
    public string ConnectorId { get; }
    public string Version { get; }
    public string Publisher { get; }
    public string InstallSource { get; }
    public string Description { get; }
    public string RiskSummary { get; }
    public string ExecutablePath { get; }
    public string ExecutableFingerprint { get; }
    public string ManifestFingerprint { get; }
    public bool IsRemote { get; }
    public bool IsReapproval { get; }
    public bool HasExpansions { get; }
    public string ReapprovalReason { get; }
    public string PreviousDenialCode { get; }

    public ObservableCollection<ConnectorPermissionLine> Permissions { get; } = [];

    /// <summary>What re-approval will be needed for. Stated up front so the user
    /// knows the decision is about this build, not about the name.</summary>
    public string UpdatePolicy =>
        IsRemote
            ? "This approval covers this server address. If the connector is later "
              + "pointed somewhere else, it stops working until you approve it again."
            : "This approval covers this exact program and the permissions listed above. "
              + "If the file is replaced, updated, or starts asking for more, it stops "
              + "working until you approve it again.";

    /// <summary>Everything the approval screen showed, as text — the "Inspect
    /// details" affordance, so the full values behind the shortened fingerprints
    /// can be read rather than taken on faith.</summary>
    public string InspectionText
    {
        get
        {
            var declaration = _request.Declaration;
            var lines = new List<string>
            {
                $"Connector:        {Name} ({ConnectorId})",
                $"Version:          {Version}",
                $"Publisher:        {Publisher}",
                $"Install source:   {InstallSource}",
                $"Transport:        {(IsRemote ? "remote HTTP" : "local process")}",
                IsRemote
                    ? $"Server URL:       {declaration.ServerUrl}"
                    : $"Executable:       {declaration.ExecutablePath}"
            };
            if (!IsRemote)
                lines.Add($"Executable hash:  {_request.ExecutableSha256}");
            lines.Add($"Manifest:         {declaration.ManifestPath}");
            lines.Add($"Manifest hash:    {_request.ManifestFingerprint}");
            lines.Add($"Protocol version: {declaration.ProtocolVersion}");
            lines.Add("");
            lines.Add("Permissions requested:");
            lines.AddRange(Permissions.Select(line => $"  - {line.Title}: {line.Detail}"));
            lines.Add("");
            lines.Add("Tools:     " + Join(declaration.ToolCapabilities));
            lines.Add("Resources: " + Join(declaration.ResourceCapabilities));
            lines.Add("Prompts:   " + Join(declaration.PromptCapabilities));
            lines.Add("");
            lines.Add("Declared as read-only:   " + Join(declaration.ReadOnlyTools));
            lines.Add("Declared as destructive: " + Join(declaration.DestructiveTools));
            return string.Join(Environment.NewLine, lines);
        }
    }

    private static IEnumerable<ConnectorPermissionLine> BuildPermissions(
        ConnectorDeclaration declaration,
        IReadOnlyList<string> expansions)
    {
        var lines = new List<ConnectorPermissionLine>();

        if (declaration.Permissions.WorkspaceAccess.Count > 0)
            lines.Add(new ConnectorPermissionLine(
                "Files",
                "Read and write inside: "
                + string.Join(", ", declaration.Permissions.WorkspaceAccess),
                expansions.Any(item => item.StartsWith("workspace:", StringComparison.Ordinal))));
        else
            lines.Add(new ConnectorPermissionLine(
                "Files",
                declaration.Transport == ConnectorTransportKind.Http
                    ? "No access to files on this computer."
                    : "Only its own private folder. It cannot see your documents.",
                false));

        lines.Add(new ConnectorPermissionLine(
            "Network",
            declaration.Permissions.RequiresNetwork
                ? "Can reach the internet."
                : "Blocked from the network entirely.",
            expansions.Contains("network")));

        lines.Add(new ConnectorPermissionLine(
            "Credentials",
            declaration.Permissions.CredentialRequirements.Count == 0
                ? "Asks for none."
                : "Asks for: "
                  + string.Join(", ", declaration.Permissions.CredentialRequirements),
            expansions.Any(item => item.StartsWith("credential:", StringComparison.Ordinal))));

        var external = declaration.ExternalMutationTools.Concat(declaration.DestructiveTools)
            .ToList();
        lines.Add(new ConnectorPermissionLine(
            "Effects outside this computer",
            external.Count == 0
                ? "The package declares none."
                : string.Join(", ", external),
            false));

        if (declaration.Permissions.Capabilities.Count > 0)
            lines.Add(new ConnectorPermissionLine(
                "Other capabilities",
                string.Join(", ", declaration.Permissions.Capabilities),
                expansions.Any(item => item.StartsWith("capability:", StringComparison.Ordinal))));

        return lines;
    }

    private static string Join(IReadOnlyList<string> values) =>
        values.Count == 0 ? " none" : " " + string.Join(", ", values);

    /// <summary>Shortened for reading, grouped so a changed character is visible.
    /// The full value is one click away in the inspection text.</summary>
    private static string Shorten(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return "not recorded";
        var trimmed = value.Length > 16 ? value[..16] : value;
        return string.Join(" ", Enumerable.Range(0, (trimmed.Length + 3) / 4)
            .Select(index => trimmed.Substring(index * 4,
                Math.Min(4, trimmed.Length - index * 4))));
    }
}

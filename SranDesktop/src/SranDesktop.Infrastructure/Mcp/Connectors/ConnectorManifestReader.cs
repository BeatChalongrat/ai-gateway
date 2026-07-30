using System.Text.Json;
using System.Text.Json.Serialization;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Mcp.Connectors;

/// <summary>Why a manifest on disk could not become a declaration. Every value
/// is a refusal — there is no "parsed with warnings" outcome, because a manifest
/// we only half understood is a permission set we only half read.</summary>
public enum ConnectorManifestRejection
{
    None,
    Missing,
    TooLarge,
    Malformed,
    SchemaInvalid,
    IdMismatch,
    ExecutableOutsideInstallation,
    ExecutableMissing,
    UnsupportedTransport,
    UnsupportedProtocolVersion
}

public sealed record ConnectorManifestReadResult(
    ConnectorDeclaration? Declaration,
    ConnectorManifestRejection Rejection,
    string? Reason)
{
    public static ConnectorManifestReadResult Ok(ConnectorDeclaration declaration) =>
        new(declaration, ConnectorManifestRejection.None, null);

    public static ConnectorManifestReadResult Reject(
        ConnectorManifestRejection rejection, string reason) =>
        new(null, rejection, reason);
}

/// <summary>
/// Parses and validates a connector manifest file.
///
/// The manifest is attacker-controlled input in the most direct sense: it is a
/// file inside a package the user is being asked to trust, and everything the
/// approval dialog shows comes from it. So the parse is strict, the size is
/// bounded before the bytes are read, and the executable path is resolved
/// against the connector's own directory and then checked to still be inside it
/// — a manifest that says <c>..\..\Windows\System32\cmd.exe</c> is refused
/// rather than shown to the user as if it were part of the package.
/// </summary>
public static class ConnectorManifestReader
{
    /// <summary>A manifest is a short declaration. Anything larger is either a
    /// mistake or an attempt to make the parse itself the attack.</summary>
    public const int MaximumManifestBytes = 64 * 1024;

    public const string ManifestFileName = "connector.manifest.json";

    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Disallow,
        AllowTrailingCommas = false
    };

    public static ConnectorManifestReadResult Read(
        string manifestPath,
        string expectedConnectorId,
        string installationDirectory)
    {
        FileInfo file;
        try
        {
            file = new FileInfo(manifestPath);
            if (!file.Exists)
                return ConnectorManifestReadResult.Reject(
                    ConnectorManifestRejection.Missing,
                    $"No manifest at {manifestPath}.");
            if (file.Length > MaximumManifestBytes)
                return ConnectorManifestReadResult.Reject(
                    ConnectorManifestRejection.TooLarge,
                    $"The manifest is {file.Length} bytes; the limit is "
                    + $"{MaximumManifestBytes}.");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException
                                       or ArgumentException or NotSupportedException
                                       or PathTooLongException)
        {
            return ConnectorManifestReadResult.Reject(
                ConnectorManifestRejection.Missing, ex.Message);
        }

        ManifestDocument? document;
        try
        {
            document = JsonSerializer.Deserialize<ManifestDocument>(
                File.ReadAllText(manifestPath), Options);
        }
        catch (Exception ex) when (ex is JsonException or IOException
                                       or UnauthorizedAccessException)
        {
            return ConnectorManifestReadResult.Reject(
                ConnectorManifestRejection.Malformed,
                $"The manifest could not be parsed: {ex.Message}");
        }

        if (document is null)
            return ConnectorManifestReadResult.Reject(
                ConnectorManifestRejection.Malformed, "The manifest is empty.");

        return Validate(document, expectedConnectorId, installationDirectory, manifestPath);
    }

    private static ConnectorManifestReadResult Validate(
        ManifestDocument document,
        string expectedConnectorId,
        string installationDirectory,
        string manifestPath)
    {
        foreach (var (value, field) in new[]
                 {
                     (document.ConnectorId, "connector_id"),
                     (document.Name, "name"),
                     (document.Version, "version")
                 })
            if (string.IsNullOrWhiteSpace(value))
                return ConnectorManifestReadResult.Reject(
                    ConnectorManifestRejection.SchemaInvalid,
                    $"The manifest is missing '{field}'.");

        if (!string.Equals(document.ConnectorId, expectedConnectorId, StringComparison.Ordinal))
            // The directory name is what the rest of the system keys on, so a
            // manifest declaring a different id could impersonate an approval
            // granted to another connector.
            return ConnectorManifestReadResult.Reject(
                ConnectorManifestRejection.IdMismatch,
                $"The manifest declares '{document.ConnectorId}' but it is installed as "
                + $"'{expectedConnectorId}'.");

        var transport = document.Transport?.ToLowerInvariant() switch
        {
            null or "" or "stdio" => ConnectorTransportKind.Stdio,
            "http" => ConnectorTransportKind.Http,
            _ => (ConnectorTransportKind?)null
        };
        if (transport is not { } transportKind)
            return ConnectorManifestReadResult.Reject(
                ConnectorManifestRejection.UnsupportedTransport,
                $"Transport '{document.Transport}' is not supported.");

        var protocolVersion = string.IsNullOrWhiteSpace(document.ProtocolVersion)
            ? McpProtocolFeatureMatrix.PreferredVersion
            : document.ProtocolVersion!;
        if (!McpProtocolFeatureMatrix.SupportedVersions.Contains(protocolVersion))
            return ConnectorManifestReadResult.Reject(
                ConnectorManifestRejection.UnsupportedProtocolVersion,
                $"Protocol version '{protocolVersion}' is not supported by this build.");

        var executablePath = "";
        if (transportKind == ConnectorTransportKind.Stdio)
        {
            if (string.IsNullOrWhiteSpace(document.Executable))
                return ConnectorManifestReadResult.Reject(
                    ConnectorManifestRejection.SchemaInvalid,
                    "A stdio connector manifest must name an 'executable'.");

            var resolution = ResolveInsideInstallation(
                document.Executable!, installationDirectory);
            if (resolution.Rejection != ConnectorManifestRejection.None)
                return ConnectorManifestReadResult.Reject(
                    resolution.Rejection, resolution.Reason!);
            executablePath = resolution.Path!;
        }
        else if (!Uri.TryCreate(document.ServerUrl, UriKind.Absolute, out var uri)
                 || uri.Scheme is not ("http" or "https")
                 || (uri.Scheme == "http" && !uri.IsLoopback))
        {
            return ConnectorManifestReadResult.Reject(
                ConnectorManifestRejection.SchemaInvalid,
                "An HTTP connector manifest needs an absolute https URL (http is accepted "
                + "for loopback only).");
        }

        var permissions = new ConnectorPermissionSet
        {
            Capabilities = Clean(document.Capabilities),
            WorkspaceAccess = Clean(document.WorkspaceAccess),
            RequiresNetwork = document.RequiresNetwork,
            CredentialRequirements = Clean(document.Credentials)
        };

        return ConnectorManifestReadResult.Ok(new ConnectorDeclaration
        {
            ConnectorId = document.ConnectorId!,
            Name = document.Name!,
            Version = document.Version!,
            Publisher = string.IsNullOrWhiteSpace(document.Publisher) ? null : document.Publisher,
            Description = document.Description ?? "",
            Transport = transportKind,
            ExecutablePath = executablePath,
            ServerUrl = document.ServerUrl ?? "",
            InstallSource = string.IsNullOrWhiteSpace(document.InstallSource)
                ? "local-package"
                : document.InstallSource!,
            ProtocolVersion = protocolVersion,
            ManifestPath = manifestPath,
            Permissions = permissions,
            ToolCapabilities = Clean(document.Tools),
            ResourceCapabilities = Clean(document.Resources),
            PromptCapabilities = Clean(document.Prompts),
            RiskSummary = document.RiskSummary ?? "",
            ReadOnlyTools = Clean(document.ReadOnlyTools),
            LocalMutationTools = Clean(document.LocalMutationTools),
            ExternalMutationTools = Clean(document.ExternalMutationTools),
            DestructiveTools = Clean(document.DestructiveTools),
            VerificationTools = new Dictionary<string, string>(
                document.VerificationTools ?? new Dictionary<string, string>(),
                StringComparer.OrdinalIgnoreCase),
            CreatedAtUtc = document.CreatedAtUtc ?? DateTimeOffset.UtcNow
        });
    }

    /// <summary>
    /// Resolves the declared executable against the connector's own directory and
    /// proves the result is still inside it. Both halves matter: resolving lets a
    /// package say <c>bin\server.exe</c>, and re-checking afterwards is what stops
    /// <c>..\..\evil.exe</c> from resolving somewhere else entirely.
    /// </summary>
    private static (string? Path, ConnectorManifestRejection Rejection, string? Reason)
        ResolveInsideInstallation(string declared, string installationDirectory)
    {
        string full;
        string root;
        try
        {
            root = Path.GetFullPath(installationDirectory)
                       .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                   + Path.DirectorySeparatorChar;
            full = Path.GetFullPath(Path.Combine(installationDirectory, declared));
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException
                                       or PathTooLongException)
        {
            return (null, ConnectorManifestRejection.SchemaInvalid, ex.Message);
        }

        if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            return (null, ConnectorManifestRejection.ExecutableOutsideInstallation,
                $"The manifest points at '{declared}', which resolves outside the "
                + "connector's own installation directory.");
        if (!File.Exists(full))
            return (null, ConnectorManifestRejection.ExecutableMissing,
                $"The declared executable '{declared}' is not present in the package.");
        return (full, ConnectorManifestRejection.None, null);
    }

    private static IReadOnlyList<string> Clean(List<string>? values) =>
        values?.Where(value => !string.IsNullOrWhiteSpace(value))
            .Select(value => value.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList() ?? [];

    /// <summary>The on-disk shape. Kept separate from <see
    /// cref="ConnectorDeclaration"/> so the domain type never has to model
    /// "possibly absent, possibly nonsense".</summary>
    private sealed class ManifestDocument
    {
        [JsonPropertyName("connector_id")] public string? ConnectorId { get; set; }
        [JsonPropertyName("name")] public string? Name { get; set; }
        [JsonPropertyName("version")] public string? Version { get; set; }
        [JsonPropertyName("publisher")] public string? Publisher { get; set; }
        [JsonPropertyName("description")] public string? Description { get; set; }
        [JsonPropertyName("transport")] public string? Transport { get; set; }
        [JsonPropertyName("executable")] public string? Executable { get; set; }
        [JsonPropertyName("server_url")] public string? ServerUrl { get; set; }
        [JsonPropertyName("install_source")] public string? InstallSource { get; set; }
        [JsonPropertyName("protocol_version")] public string? ProtocolVersion { get; set; }
        [JsonPropertyName("capabilities")] public List<string>? Capabilities { get; set; }
        [JsonPropertyName("workspace_access")] public List<string>? WorkspaceAccess { get; set; }
        [JsonPropertyName("requires_network")] public bool RequiresNetwork { get; set; }
        [JsonPropertyName("credentials")] public List<string>? Credentials { get; set; }
        [JsonPropertyName("tools")] public List<string>? Tools { get; set; }
        [JsonPropertyName("resources")] public List<string>? Resources { get; set; }
        [JsonPropertyName("prompts")] public List<string>? Prompts { get; set; }
        [JsonPropertyName("risk_summary")] public string? RiskSummary { get; set; }
        [JsonPropertyName("read_only_tools")] public List<string>? ReadOnlyTools { get; set; }
        [JsonPropertyName("local_mutation_tools")] public List<string>? LocalMutationTools { get; set; }
        [JsonPropertyName("external_mutation_tools")] public List<string>? ExternalMutationTools { get; set; }
        [JsonPropertyName("destructive_tools")] public List<string>? DestructiveTools { get; set; }
        [JsonPropertyName("verification_tools")] public Dictionary<string, string>? VerificationTools { get; set; }
        [JsonPropertyName("created_at")] public DateTimeOffset? CreatedAtUtc { get; set; }
    }
}

namespace SranDesktop.Application.Models;

/// <summary>
/// Identity of one authenticated extension/native-host/desktop connection.
/// Element references and responses must never cross this boundary.
/// </summary>
public sealed record BrowserBridgeSession(
    string BridgeSessionId,
    string ExtensionSessionId,
    string BrowserInstanceId,
    string BrowserProfileId,
    string NativeConnectionId,
    int ProtocolVersion,
    long SessionGeneration,
    DateTimeOffset ConnectedAtUtc,
    DateTimeOffset LastHeartbeatUtc);


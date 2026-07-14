using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Portal-JWT-authenticated client for the backend's connector OAuth broker
/// (Phase 7 C3). The desktop never sees provider tokens through this — only
/// tickets, connection state, and disconnects.
/// </summary>
public interface IConnectorAuthClient
{
    /// <summary>Mint a one-time connect ticket; null on any failure
    /// (offline, signed out, unknown connector).</summary>
    Task<ConnectorTicket?> CreateTicketAsync(string connectorId, CancellationToken ct = default);

    /// <summary>The caller's connector connection state; empty on failure.</summary>
    Task<IReadOnlyList<ConnectorConnection>> GetConnectionsAsync(CancellationToken ct = default);

    /// <summary>Best-effort server-side disconnect (token delete + provider
    /// revocation). False on failure — local uninstall proceeds regardless.</summary>
    Task<bool> DisconnectAsync(string connectorId, CancellationToken ct = default);
}

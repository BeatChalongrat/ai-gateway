namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Supplies the gateway API key (gw_…) used by Agent Mode. The gateway's
/// /v1/chat/completions rejects portal JWTs, so the app self-provisions a key
/// once via POST /portal/projects/{id}/keys (portal-JWT-authenticated) and
/// caches it in the DPAPI credential store.
/// </summary>
public interface IAgentKeyProvider
{
    /// <summary>Returns a cached key, or provisions one through the portal. Null if provisioning failed.</summary>
    Task<string?> GetOrProvisionKeyAsync(CancellationToken ct = default);

    /// <summary>Drops the cached/stored key and provisions a fresh one.
    /// Returns null when provisioning fails.</summary>
    Task<string?> ReprovisionAsync(CancellationToken ct = default);
}

using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Api;

/// <summary>
/// Mints and caches the Agent Mode gateway API key. The HttpClient is
/// portal-JWT-authenticated (AuthTokenHandler pipeline) against the gateway
/// base URL; the raw gw_… key from the create response is stored via DPAPI.
/// </summary>
public sealed class AgentKeyProvider : IAgentKeyProvider
{
    internal const string CredentialKey = "gateway_agent_api_key";
    private const string KeyName = "SRAN Desktop Agent";
    private const string FallbackProjectName = "SRAN Desktop";

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;
    private readonly ICredentialStore _credentials;
    private readonly ILogger<AgentKeyProvider> _logger;
    private readonly SemaphoreSlim _provisionLock = new(1, 1);

    private string? _cached;

    public AgentKeyProvider(HttpClient http, ICredentialStore credentials, ILogger<AgentKeyProvider> logger)
    {
        _http = http;
        _credentials = credentials;
        _logger = logger;
    }

    public async Task<string?> GetOrProvisionKeyAsync(CancellationToken ct = default)
    {
        if (_cached is not null)
            return _cached;

        await _provisionLock.WaitAsync(ct);
        try
        {
            if (_cached is not null)
                return _cached;

            var stored = _credentials.Load(CredentialKey);
            if (!string.IsNullOrEmpty(stored))
                return _cached = stored;

            var key = await ProvisionAsync(ct);
            if (key is not null)
            {
                _credentials.Save(CredentialKey, key);
                _cached = key;
            }
            return key;
        }
        finally
        {
            _provisionLock.Release();
        }
    }

    private void Invalidate()
    {
        _cached = null;
        _credentials.Delete(CredentialKey);
    }

    public async Task<string?> ReprovisionAsync(CancellationToken ct = default)
    {
        var staleKey = _cached;
        await _provisionLock.WaitAsync(ct);
        try
        {
            // Single-flight: a concurrent caller already minted a fresh key
            // while we waited — reuse it instead of burning another one.
            if (_cached is not null && _cached != staleKey)
                return _cached;

            Invalidate();
            var key = await ProvisionAsync(ct);
            if (key is not null)
            {
                _credentials.Save(CredentialKey, key);
                _cached = key;
            }
            return key;
        }
        finally
        {
            _provisionLock.Release();
        }
    }

    private async Task<string?> ProvisionAsync(CancellationToken ct)
    {
        try
        {
            var projectId = await ResolveProjectIdAsync(ct);
            if (projectId is null)
            {
                _logger.LogWarning("Agent key provisioning failed: no usable portal project");
                return null;
            }

            using var response = await _http.PostAsJsonAsync(
                $"portal/projects/{Uri.EscapeDataString(projectId)}/keys",
                new { name = KeyName }, JsonOptions, ct);
            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning("Agent key creation failed: HTTP {Status}", (int)response.StatusCode);
                return null;
            }

            var body = await response.Content.ReadFromJsonAsync<KeyCreateResponse>(JsonOptions, ct);
            if (string.IsNullOrEmpty(body?.ApiKey))
            {
                _logger.LogWarning("Agent key creation returned no api_key");
                return null;
            }

            _logger.LogInformation("Provisioned agent gateway key {Prefix}… in project {Project}",
                body.KeyPrefix, projectId);
            return body.ApiKey;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            _logger.LogWarning(ex, "Agent key provisioning failed");
            return null;
        }
    }

    private async Task<string?> ResolveProjectIdAsync(CancellationToken ct)
    {
        using (var response = await _http.GetAsync("portal/projects", ct))
        {
            if (response.IsSuccessStatusCode)
            {
                var list = await response.Content.ReadFromJsonAsync<ProjectListResponse>(JsonOptions, ct);
                var first = list?.Projects?.FirstOrDefault();
                if (first?.Id is { Length: > 0 } id)
                    return id;
            }
        }

        // No project yet — create one for the desktop app.
        using var create = await _http.PostAsJsonAsync(
            "portal/projects", new { name = FallbackProjectName }, JsonOptions, ct);
        if (!create.IsSuccessStatusCode)
            return null;
        var created = await create.Content.ReadFromJsonAsync<ProjectDto>(JsonOptions, ct);
        return created?.Id;
    }

    private sealed record ProjectDto([property: JsonPropertyName("id")] string? Id);

    private sealed record ProjectListResponse(
        [property: JsonPropertyName("projects")] List<ProjectDto>? Projects);

    private sealed record KeyCreateResponse(
        [property: JsonPropertyName("api_key")] string? ApiKey,
        [property: JsonPropertyName("key_prefix")] string? KeyPrefix);
}

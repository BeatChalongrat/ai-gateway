using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

public sealed class TokenSession : ITokenSession
{
    private const string AccessTokenKey = "chat_edge_access_token";
    private const string RefreshTokenKey = "chat_edge_refresh_token";
    private const string UserKey = "chat_edge_user";

    private readonly ICredentialStore _credentialStore;
    private readonly object _lock = new();

    private string? _accessToken;
    private string? _refreshToken;
    private UserInfo? _user;
    private bool _loaded;

    public TokenSession(ICredentialStore credentialStore)
    {
        _credentialStore = credentialStore;
    }

    public string? AccessToken
    {
        get { EnsureLoaded(); lock (_lock) return _accessToken; }
    }

    public string? RefreshToken
    {
        get { EnsureLoaded(); lock (_lock) return _refreshToken; }
    }

    public UserInfo? User
    {
        get { EnsureLoaded(); lock (_lock) return _user; }
    }

    public bool HasSession => AccessToken is not null && RefreshToken is not null;

    public event EventHandler? SessionChanged;

    public void SetSession(string accessToken, string refreshToken, UserInfo? user)
    {
        EnsureLoaded();
        lock (_lock)
        {
            _accessToken = accessToken;
            _refreshToken = refreshToken;
            if (user is not null)
                _user = user;

            _credentialStore.Save(AccessTokenKey, accessToken);
            _credentialStore.Save(RefreshTokenKey, refreshToken);
            if (_user is not null)
                _credentialStore.Save(UserKey, JsonSerializer.Serialize(_user));
        }

        SessionChanged?.Invoke(this, EventArgs.Empty);
    }

    public void Clear()
    {
        lock (_lock)
        {
            _accessToken = null;
            _refreshToken = null;
            _user = null;
            _loaded = true;

            _credentialStore.Delete(AccessTokenKey);
            _credentialStore.Delete(RefreshTokenKey);
            _credentialStore.Delete(UserKey);
        }

        SessionChanged?.Invoke(this, EventArgs.Empty);
    }

    private void EnsureLoaded()
    {
        lock (_lock)
        {
            if (_loaded) return;
            _loaded = true;

            _accessToken = _credentialStore.Load(AccessTokenKey);
            _refreshToken = _credentialStore.Load(RefreshTokenKey);

            var userJson = _credentialStore.Load(UserKey);
            if (userJson is not null)
            {
                try
                {
                    _user = JsonSerializer.Deserialize<UserInfo>(userJson);
                }
                catch (JsonException)
                {
                    _user = null;
                }
            }
        }
    }
}

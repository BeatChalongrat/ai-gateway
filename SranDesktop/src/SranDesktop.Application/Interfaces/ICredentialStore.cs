namespace SranDesktop.Application.Interfaces;

/// <summary>Encrypted-at-rest key/value store for secrets (tokens, API keys).</summary>
public interface ICredentialStore
{
    void Save(string key, string value);
    string? Load(string key);
    void Delete(string key);
}

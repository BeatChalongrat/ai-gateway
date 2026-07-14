using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

public class TokenSessionTests
{
    private sealed class InMemoryCredentialStore : ICredentialStore
    {
        public readonly Dictionary<string, string> Data = new();
        public void Save(string key, string value) => Data[key] = value;
        public string? Load(string key) => Data.TryGetValue(key, out var v) ? v : null;
        public void Delete(string key) => Data.Remove(key);
    }

    [Fact]
    public void SetSession_Persists_And_Exposes_Tokens()
    {
        var store = new InMemoryCredentialStore();
        var session = new TokenSession(store);

        session.SetSession("access-1", "refresh-1", new UserInfo("u1", "a@b.c", "A"));

        session.AccessToken.Should().Be("access-1");
        session.RefreshToken.Should().Be("refresh-1");
        session.User!.Email.Should().Be("a@b.c");
        session.HasSession.Should().BeTrue();
        store.Data.Should().ContainKey("chat_edge_access_token");
    }

    [Fact]
    public void New_Instance_Restores_From_Store()
    {
        var store = new InMemoryCredentialStore();
        new TokenSession(store).SetSession("access-1", "refresh-1", new UserInfo("u1", "a@b.c", null));

        var restored = new TokenSession(store);

        restored.AccessToken.Should().Be("access-1");
        restored.User!.Id.Should().Be("u1");
        restored.HasSession.Should().BeTrue();
    }

    [Fact]
    public void Clear_Removes_Everything()
    {
        var store = new InMemoryCredentialStore();
        var session = new TokenSession(store);
        session.SetSession("a", "r", null);

        session.Clear();

        session.HasSession.Should().BeFalse();
        session.AccessToken.Should().BeNull();
        store.Data.Should().BeEmpty();
    }

    [Fact]
    public void SetSession_Raises_SessionChanged()
    {
        var session = new TokenSession(new InMemoryCredentialStore());
        var raised = 0;
        session.SessionChanged += (_, _) => raised++;

        session.SetSession("a", "r", null);
        session.Clear();

        raised.Should().Be(2);
    }
}

using System.Net;
using System.Net.Http;
using System.Text;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Api;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class AgentKeyProviderTests
{
    private sealed class InMemoryCredentialStore : ICredentialStore
    {
        private readonly Dictionary<string, string> _values = new();
        public void Save(string key, string value) => _values[key] = value;
        public string? Load(string key) => _values.TryGetValue(key, out var v) ? v : null;
        public void Delete(string key) => _values.Remove(key);
    }

    /// <summary>Answers the portal provisioning routes; key creation can be
    /// held open via <see cref="Gate"/> to exercise concurrent callers.</summary>
    private sealed class ProvisioningHandler : HttpMessageHandler
    {
        private int _keyCreates;

        public int KeyCreates => _keyCreates;
        public bool HoldKeyCreate { get; init; }
        public TaskCompletionSource Gate { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            if (request.Method == HttpMethod.Get && request.RequestUri!.AbsolutePath.EndsWith("portal/projects"))
                return Json("""{"projects":[{"id":"p1"}]}""");

            if (request.Method == HttpMethod.Post && request.RequestUri!.AbsolutePath.Contains("/keys"))
            {
                if (HoldKeyCreate)
                    await Gate.Task;
                var n = Interlocked.Increment(ref _keyCreates);
                return Json($$"""{"api_key":"gw_fresh_{{n}}","key_prefix":"gw_fresh"}""");
            }

            return new HttpResponseMessage(HttpStatusCode.NotFound);
        }

        private static HttpResponseMessage Json(string body) => new(HttpStatusCode.OK)
        {
            Content = new StringContent(body, Encoding.UTF8, "application/json")
        };
    }

    private static AgentKeyProvider Create(ProvisioningHandler handler, ICredentialStore store) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("http://localhost:8080") },
            store, NullLogger<AgentKeyProvider>.Instance);

    [Fact]
    public async Task Reprovision_Replaces_Cached_And_Stored_Key()
    {
        var store = new InMemoryCredentialStore();
        store.Save(AgentKeyProvider.CredentialKey, "gw_stale");
        var handler = new ProvisioningHandler();
        var provider = Create(handler, store);
        (await provider.GetOrProvisionKeyAsync()).Should().Be("gw_stale");

        var key = await provider.ReprovisionAsync();

        key.Should().Be("gw_fresh_1");
        store.Load(AgentKeyProvider.CredentialKey).Should().Be("gw_fresh_1");
        (await provider.GetOrProvisionKeyAsync()).Should().Be("gw_fresh_1");
        handler.KeyCreates.Should().Be(1);
    }

    [Fact]
    public async Task Concurrent_Reprovision_Provisions_Only_Once()
    {
        var store = new InMemoryCredentialStore();
        store.Save(AgentKeyProvider.CredentialKey, "gw_stale");
        var handler = new ProvisioningHandler { HoldKeyCreate = true };
        var provider = Create(handler, store);
        await provider.GetOrProvisionKeyAsync(); // prime the cache with the stale key

        var first = provider.ReprovisionAsync();
        var second = provider.ReprovisionAsync();
        handler.Gate.SetResult();
        var keys = await Task.WhenAll(first, second);

        keys.Should().AllBe("gw_fresh_1");
        handler.KeyCreates.Should().Be(1);
        store.Load(AgentKeyProvider.CredentialKey).Should().Be("gw_fresh_1");
    }
}

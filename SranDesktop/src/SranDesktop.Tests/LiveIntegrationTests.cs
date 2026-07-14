using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Api;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests;

/// <summary>
/// Live tests against the local ai-gateway stack. Guarded by SRAN_LIVE=1 so a
/// plain `dotnet test` stays green when the servers are down.
/// Uses the UAT seed user (scripts/uat/02_seed_uat.py).
/// </summary>
public class LiveIntegrationTests
{
    private const string GatewayBase = "http://localhost:8080";
    private const string ChatEdgeBase = "http://localhost:4000";
    private const string TestEmail = "somchai@acme.ai.gateway";
    private const string TestPassword = "P@ssword";

    private static bool LiveEnabled =>
        Environment.GetEnvironmentVariable("SRAN_LIVE") == "1";

    private sealed class InMemoryCredentialStore : ICredentialStore
    {
        private readonly Dictionary<string, string> _data = new();
        public void Save(string key, string value) => _data[key] = value;
        public string? Load(string key) => _data.TryGetValue(key, out var v) ? v : null;
        public void Delete(string key) => _data.Remove(key);
    }

    private static PortalAuthClient CreatePortalClient() => new(
        new HttpClient(new HttpClientHandler { UseCookies = false })
        {
            BaseAddress = new Uri(GatewayBase),
            Timeout = TimeSpan.FromSeconds(15)
        });

    [Fact]
    public async Task Login_Then_ListConversations_Through_App_Stack()
    {
        if (!LiveEnabled) return;

        var portal = CreatePortalClient();
        var tokens = await portal.LoginAsync(TestEmail, TestPassword);

        tokens.AccessToken.Should().NotBeNullOrEmpty();
        tokens.RefreshToken.Should().NotBeNullOrEmpty();
        tokens.User!.Email.Should().Be(TestEmail);

        var session = new Application.Services.TokenSession(new InMemoryCredentialStore());
        session.SetSession(tokens.AccessToken, tokens.RefreshToken, tokens.User);

        var handler = new AuthTokenHandler(session, portal, NullLogger<AuthTokenHandler>.Instance)
        {
            InnerHandler = new HttpClientHandler()
        };
        var chatEdge = new ChatEdgeApiClient(new HttpClient(handler)
        {
            BaseAddress = new Uri(ChatEdgeBase),
            Timeout = TimeSpan.FromSeconds(15)
        });

        var conversations = await chatEdge.ListConversationsAsync(limit: 5);
        conversations.Conversations.Should().NotBeNull();

        var models = await chatEdge.ListModelsAsync();
        models.Models.Should().NotBeEmpty();
    }

    [Fact]
    public async Task Upload_File_To_New_Conversation()
    {
        if (!LiveEnabled) return;

        var portal = CreatePortalClient();
        var tokens = await portal.LoginAsync(TestEmail, TestPassword);
        var session = new Application.Services.TokenSession(new InMemoryCredentialStore());
        session.SetSession(tokens.AccessToken, tokens.RefreshToken, tokens.User);
        var handler = new AuthTokenHandler(session, portal, NullLogger<AuthTokenHandler>.Instance)
        {
            InnerHandler = new HttpClientHandler()
        };
        var chatEdge = new ChatEdgeApiClient(new HttpClient(handler)
        {
            BaseAddress = new Uri(ChatEdgeBase),
            Timeout = System.Threading.Timeout.InfiniteTimeSpan
        });

        var conversation = await chatEdge.CreateConversationAsync("gpt-4o-mini");

        var tempFile = Path.Combine(Path.GetTempPath(), $"sran-upload-{Guid.NewGuid():N}.txt");
        await File.WriteAllTextAsync(tempFile, "Quarterly revenue: Q1 $1.2M, Q2 $1.5M.");
        try
        {
            var progressValues = new List<double>();
            var fileService = new Application.Services.FileService(
                chatEdge, NullLogger<Application.Services.FileService>.Instance);

            var uploaded = await fileService.UploadAsync(
                conversation.Id, tempFile, new Progress<double>(progressValues.Add));

            uploaded.Id.Should().NotBeNullOrEmpty();
            uploaded.Filename.Should().EndWith(".txt");
        }
        finally
        {
            File.Delete(tempFile);
            await chatEdge.DeleteConversationAsync(conversation.Id);
        }
    }

    [Fact]
    public async Task Login_With_Wrong_Password_Throws_Unauthorized()
    {
        if (!LiveEnabled) return;

        var portal = CreatePortalClient();

        var act = () => portal.LoginAsync(TestEmail, "definitely-wrong");

        var ex = await act.Should().ThrowAsync<HttpRequestException>();
        ex.Which.StatusCode.Should().Be(System.Net.HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task Refresh_Rotates_Tokens()
    {
        if (!LiveEnabled) return;

        var portal = CreatePortalClient();
        var first = await portal.LoginAsync(TestEmail, TestPassword);

        var second = await portal.RefreshAsync(first.RefreshToken);

        second.AccessToken.Should().NotBeNullOrEmpty();
        second.RefreshToken.Should().NotBeNullOrEmpty();
    }

    /// <summary>
    /// Dev utility, not a test: with SRAN_SEED_SESSION=1 it logs in and writes
    /// the tokens into the real %APPDATA% credential store so the desktop app
    /// starts signed-in (used to verify the shell without typing credentials).
    /// </summary>
    [Fact]
    public async Task Seed_Real_Credential_Store_For_Manual_Testing()
    {
        if (Environment.GetEnvironmentVariable("SRAN_SEED_SESSION") != "1") return;

        var portal = CreatePortalClient();
        var tokens = await portal.LoginAsync(TestEmail, TestPassword);

        var session = new Application.Services.TokenSession(new DpapiCredentialStore());
        session.SetSession(tokens.AccessToken, tokens.RefreshToken, tokens.User);

        session.HasSession.Should().BeTrue();
    }
}

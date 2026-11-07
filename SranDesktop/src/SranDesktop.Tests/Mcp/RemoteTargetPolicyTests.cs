using System.Net;
using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Remote;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>FIX69 — where a user-typed remote MCP URL may point, and how a
/// refused key is reported. No network: the "server" is an HttpListener on
/// loopback, reachable only because the policy is told to allow it.</summary>
public sealed class RemoteTargetPolicyTests
{
    [Theory]
    [InlineData("127.0.0.1", true)]
    [InlineData("10.1.2.3", true)]
    [InlineData("172.16.0.9", true)]
    [InlineData("172.31.255.1", true)]
    [InlineData("192.168.1.1", true)]
    [InlineData("169.254.1.1", true)]
    [InlineData("100.64.0.1", true)]
    [InlineData("0.0.0.0", true)]
    [InlineData("::1", true)]
    [InlineData("fe80::1", true)]
    [InlineData("fd12::1", true)]
    [InlineData("::ffff:10.0.0.1", true)]
    [InlineData("8.8.8.8", false)]
    [InlineData("172.32.0.1", false)]
    [InlineData("2606:4700::1111", false)]
    public void Internal_Addresses_Are_Classified(string ip, bool internalExpected) =>
        RemoteTargetPolicy.IsInternal(IPAddress.Parse(ip)).Should().Be(internalExpected);

    [Theory]
    [InlineData("169.254.169.254")]
    [InlineData("::ffff:169.254.169.254")]
    [InlineData("100.100.100.200")]
    [InlineData("fd00:ec2::254")]
    public void Metadata_Is_Refused_Even_When_Private_Is_Allowed(string ip)
    {
        RemoteTargetPolicy.IsMetadata(IPAddress.Parse(ip)).Should().BeTrue();
        RemoteTargetPolicy.Refuse(IPAddress.Parse(ip), allowPrivate: true).Should().Be("metadata");
    }

    [Fact]
    public void Private_Is_Refused_By_Default_And_Allowed_By_The_Switch()
    {
        var ip = IPAddress.Parse("192.168.10.5");
        RemoteTargetPolicy.Refuse(ip, allowPrivate: false).Should().Be("internal");
        RemoteTargetPolicy.Refuse(ip, allowPrivate: true).Should().BeNull();
        RemoteTargetPolicy.Refuse(IPAddress.Parse("8.8.8.8"), allowPrivate: false).Should().BeNull();
    }

    [Theory]
    [InlineData("http://example.com/mcp", "https-required")]
    [InlineData("http://127.0.0.1:9000", "https-required")]
    [InlineData("ftp://example.com", "https-required")]
    [InlineData("not a url", "invalid-url")]
    [InlineData("", "invalid-url")]
    [InlineData("https://user:pw@example.com/mcp", "userinfo-in-url")]
    [InlineData("https://example.com/mcp", null)]
    [InlineData("https://localhost:5555", null)]
    public void Remote_Url_Rule_Is_Https_Only(string url, string? reason) =>
        RemoteTargetPolicy.ValidateRemoteUrl(url).Should().Be(reason);

    [Fact]
    public async Task Policy_Client_Refuses_Loopback_At_Connect_Time_Unless_Allowed()
    {
        using var refused = RemoteTargetPolicy.CreateHttpClient(allowPrivate: false, TimeSpan.FromSeconds(5));
        var act = () => refused.GetAsync("http://127.0.0.1:1/");
        var ex = await act.Should().ThrowAsync<HttpRequestException>();
        RemoteTargetPolicy.FindRefusal(ex.Which).Should().NotBeNull().And.Subject.As<RemoteTargetRefusedException>().Reason.Should().Be("internal");

        using var allowed = RemoteTargetPolicy.CreateHttpClient(allowPrivate: true, TimeSpan.FromSeconds(5));
        var act2 = () => allowed.GetAsync("http://127.0.0.1:1/");
        var ex2 = await act2.Should().ThrowAsync<HttpRequestException>(); // port 1 refuses — the policy did not
        RemoteTargetPolicy.FindRefusal(ex2.Which).Should().BeNull();
    }

    [Fact]
    public void Classify_Names_A_Rejected_Key_And_A_Refused_Target()
    {
        McpConnectorService.ClassifyFailure(new HttpRequestException("401", null, HttpStatusCode.Unauthorized)).Should().Be("key-rejected");
        McpConnectorService.ClassifyFailure(new HttpRequestException("403", null, HttpStatusCode.Forbidden)).Should().Be("key-rejected");
        McpConnectorService.ClassifyFailure(new HttpRequestException("404", null, HttpStatusCode.NotFound)).Should().Be("not-mcp");
        McpConnectorService.ClassifyFailure(new HttpRequestException("outer", new RemoteTargetRefusedException("metadata", "h", IPAddress.Loopback))).Should().Be("target-metadata");
        McpConnectorService.ClassifyFailure(new HttpRequestException("ssl", new System.Security.Authentication.AuthenticationException("bad cert"))).Should().Be("tls");
        McpConnectorService.ClassifyFailure(new TaskCanceledException()).Should().Be("timeout");
        McpConnectorService.ClassifyFailure(new McpProtocolException("x")).Should().Be("not-mcp");
        McpConnectorService.ClassifyFailure(new HttpRequestException("conn refused")).Should().Be("unreachable");
    }

    [Fact]
    public async Task Test_Connection_Reports_Key_Rejected_When_The_Server_Answers_401()
    {
        var prefix = $"http://127.0.0.1:{FreePort()}/";
        using var listener = new HttpListener();
        listener.Prefixes.Add(prefix);
        listener.Start();
        var serve = Task.Run(async () =>
        {
            var ctx = await listener.GetContextAsync();
            var auth = ctx.Request.Headers["Authorization"];
            ctx.Response.StatusCode = auth == "Bearer right-key" ? 200 : 401;
            if (auth == "Bearer right-key")
            {
                ctx.Response.ContentType = "application/json";
                var body = System.Text.Encoding.UTF8.GetBytes("{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"serverInfo\":{\"name\":\"t\",\"version\":\"0\"}}}");
                await ctx.Response.OutputStream.WriteAsync(body);
            }
            ctx.Response.Close();
        });

        var stateFile = Path.Combine(Path.GetTempPath(), $"sran-fix69-{Guid.NewGuid():N}.json");
        var creds = new Mock<ICredentialStore>();
        var svc = new McpConnectorService(creds.Object, NullLogger<McpConnectorService>.Instance,
            stateFilePath: stateFile, dialTimeout: TimeSpan.FromSeconds(10), allowPrivateRemoteTargets: true);
        var connector = new McpConnector { Id = "custom-t", Name = "t", Transport = McpConnector.TransportHttp, ServerUrl = prefix.TrimEnd('/'), AuthType = "api_key" };

        var result = await svc.TestConnectionAsync(connector, "wrong-key");
        result.Success.Should().BeFalse();
        result.Code.Should().Be("key-rejected");
        result.Error.Should().NotContain("wrong-key", "the key must never appear in a message the UI shows");
        await serve;
        listener.Stop();
        File.Delete(stateFile);
    }

    [Fact]
    public async Task Test_Connection_Refuses_An_Internal_Target_By_Default()
    {
        var stateFile = Path.Combine(Path.GetTempPath(), $"sran-fix69-{Guid.NewGuid():N}.json");
        var svc = new McpConnectorService(new Mock<ICredentialStore>().Object, NullLogger<McpConnectorService>.Instance,
            stateFilePath: stateFile, dialTimeout: TimeSpan.FromSeconds(10), allowPrivateRemoteTargets: false);
        var connector = new McpConnector { Id = "custom-i", Name = "i", Transport = McpConnector.TransportHttp, ServerUrl = "https://127.0.0.1:1", AuthType = "none" };
        var result = await svc.TestConnectionAsync(connector);
        result.Success.Should().BeFalse();
        result.Code.Should().Be("target-internal");
        File.Delete(stateFile);
    }

    [Fact]
    public void Extra_Roots_Accept_Their_Own_Chain_Only_And_Never_A_Name_Mismatch()
    {
        using var rsa = System.Security.Cryptography.RSA.Create(2048);
        var req = new System.Security.Cryptography.X509Certificates.CertificateRequest(
            "CN=mcp.internal.test", rsa, System.Security.Cryptography.HashAlgorithmName.SHA256,
            System.Security.Cryptography.RSASignaturePadding.Pkcs1);
        using var selfSigned = req.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(30));
        using var other = new System.Security.Cryptography.X509Certificates.CertificateRequest(
            "CN=other.test", rsa, System.Security.Cryptography.HashAlgorithmName.SHA256,
            System.Security.Cryptography.RSASignaturePadding.Pkcs1).CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(30));
        var roots = new System.Security.Cryptography.X509Certificates.X509Certificate2Collection { selfSigned };

        // a chain error is what an untrusted private CA produces — accepted only when the cert chains to the extra roots
        RemoteTargetPolicy.ValidateWithExtraRoots(selfSigned, null, System.Net.Security.SslPolicyErrors.RemoteCertificateChainErrors, roots).Should().BeTrue();
        RemoteTargetPolicy.ValidateWithExtraRoots(other, null, System.Net.Security.SslPolicyErrors.RemoteCertificateChainErrors, roots).Should().BeFalse();
        // the system already trusting it stays accepted; anything but a pure chain error is refused regardless
        RemoteTargetPolicy.ValidateWithExtraRoots(selfSigned, null, System.Net.Security.SslPolicyErrors.None, roots).Should().BeTrue();
        RemoteTargetPolicy.ValidateWithExtraRoots(selfSigned, null, System.Net.Security.SslPolicyErrors.RemoteCertificateNameMismatch, roots).Should().BeFalse();
        RemoteTargetPolicy.ValidateWithExtraRoots(selfSigned, null, System.Net.Security.SslPolicyErrors.RemoteCertificateChainErrors | System.Net.Security.SslPolicyErrors.RemoteCertificateNameMismatch, roots).Should().BeFalse();
        RemoteTargetPolicy.ValidateWithExtraRoots(null, null, System.Net.Security.SslPolicyErrors.RemoteCertificateChainErrors, roots).Should().BeFalse();
    }

    private static int FreePort()
    {
        var l = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        l.Start();
        var port = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return port;
    }
}

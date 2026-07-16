using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.R11;

public class UpdateServiceTests
{
    private sealed class ScriptedHandler : HttpMessageHandler
    {
        private readonly Dictionary<string, Func<HttpResponseMessage>> _routes = new();

        public void Map(string url, string body, string contentType = "application/json") =>
            _routes[url] = () => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(body, Encoding.UTF8, contentType)
            };

        public void MapBytes(string url, byte[] body) =>
            _routes[url] = () => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new ByteArrayContent(body)
            };

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(_routes.TryGetValue(request.RequestUri!.ToString(), out var factory)
                ? factory()
                : new HttpResponseMessage(HttpStatusCode.NotFound) { Content = new StringContent("") });
    }

    private const string ManifestUrl = "https://releases.example.com/desktop/latest.json";

    private static UpdateService Create(ScriptedHandler handler) =>
        new(ManifestUrl, NullLogger<UpdateService>.Instance, new HttpClient(handler));

    [Fact]
    public async Task Newer_Version_Is_Detected()
    {
        var handler = new ScriptedHandler();
        handler.Map(ManifestUrl, """
            {"version":"99.0.0","downloadUrl":"SranDesktop-99.0.0-Setup.exe",
             "sha256":"","releaseNotes":"big one","minVersion":"1.0.0","mandatory":false}
            """);
        var service = Create(handler);

        var result = await service.CheckForUpdateAsync();

        Assert.NotNull(result);
        Assert.Equal("99.0.0", result!.NewVersion);
        Assert.False(result.IsMandatory);
        // Relative downloadUrl resolves against the manifest URL.
        Assert.Equal("https://releases.example.com/desktop/SranDesktop-99.0.0-Setup.exe",
            result.DownloadUrl);
    }

    [Fact]
    public async Task Same_Or_Older_Version_Returns_Null()
    {
        var handler = new ScriptedHandler();
        handler.Map(ManifestUrl, """{"version":"0.0.1","downloadUrl":"x.exe"}""");
        Assert.Null(await Create(handler).CheckForUpdateAsync());
    }

    [Fact]
    public async Task Unreachable_Manifest_Returns_Null()
    {
        Assert.Null(await Create(new ScriptedHandler()).CheckForUpdateAsync());
    }

    [Fact]
    public async Task MinVersion_Above_Current_Forces_Mandatory()
    {
        var handler = new ScriptedHandler();
        handler.Map(ManifestUrl, """
            {"version":"99.0.0","downloadUrl":"x.exe","minVersion":"98.0.0","mandatory":false}
            """);
        var result = await Create(handler).CheckForUpdateAsync();
        Assert.True(result!.IsMandatory);
    }

    [Fact]
    public async Task Download_Verifies_Sha256_And_Rejects_Mismatch()
    {
        var payload = Encoding.UTF8.GetBytes("fake installer bytes");
        var goodHash = Convert.ToHexString(SHA256.HashData(payload)).ToLowerInvariant();
        var downloadUrl = "https://releases.example.com/desktop/SranDesktop-99.0.1-Setup.exe";

        var handler = new ScriptedHandler();
        handler.MapBytes(downloadUrl, payload);
        var service = Create(handler);

        // Good hash → file lands on disk.
        var ok = new SranDesktop.Application.Interfaces.UpdateCheckResult(
            "99.0.1", "1.0.0", downloadUrl, goodHash, null, false);
        var path = await service.DownloadUpdateAsync(ok);
        Assert.True(File.Exists(path));
        File.Delete(path);

        // Bad hash → deleted + throws.
        var bad = ok with { NewVersion = "99.0.2", Sha256 = new string('a', 64) };
        handler.MapBytes("https://releases.example.com/desktop/SranDesktop-99.0.1-Setup.exe", payload);
        handler.MapBytes(downloadUrl.Replace("99.0.1", "99.0.2"), payload);
        var badResult = bad with { DownloadUrl = downloadUrl.Replace("99.0.1", "99.0.2") };
        await Assert.ThrowsAsync<InvalidOperationException>(
            () => service.DownloadUpdateAsync(badResult));
    }

    [Fact]
    public void Shipped_Release_Manifest_Pins_A_Sha256()
    {
        // 17E: an empty sha256 makes VerifyHash accept ANY downloaded file
        // (VerifyHash returns true when the manifest pins no hash). The shipped
        // manifest must therefore always carry a real hash — guard against a
        // regression back to "".
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        string? manifest = null;
        while (dir is not null)
        {
            var candidate = Path.Combine(dir.FullName, "releases", "latest.json");
            if (File.Exists(candidate)) { manifest = candidate; break; }
            dir = dir.Parent;
        }
        Assert.NotNull(manifest);

        using var doc = System.Text.Json.JsonDocument.Parse(File.ReadAllText(manifest!));
        var sha = doc.RootElement.GetProperty("sha256").GetString();
        Assert.False(string.IsNullOrWhiteSpace(sha));
        Assert.Equal(64, sha!.Length); // hex-encoded SHA-256
    }

    [Theory]
    [InlineData("1.2.0", true, "1.2.0")]
    [InlineData("1.2.0-beta+sha123", true, "1.2.0")]
    [InlineData("garbage", false, "0.0")]
    public void Version_Parsing_Handles_Suffixes(string input, bool expectOk, string expected)
    {
        var ok = UpdateService.TryParseVersion(input, out var version);
        Assert.Equal(expectOk, ok);
        if (expectOk)
            Assert.Equal(Version.Parse(expected), version);
    }
}

public class FirstRunHeuristicsTests
{
    [Theory]
    [InlineData("http://localhost:8080", true)]
    [InlineData("http://127.0.0.1:8080", true)]
    [InlineData("https://sran.yourcompany.com", false)]
    public void Local_Dev_Url_Detection(string url, bool expected)
    {
        Assert.Equal(expected, FirstRunViewModel.IsLocalDevUrl(url));
    }
}

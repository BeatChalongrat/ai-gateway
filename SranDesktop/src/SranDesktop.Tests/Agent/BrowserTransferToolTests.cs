using System.Security.Cryptography;
using System.Text.Json;
using FluentAssertions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

public sealed class BrowserTransferToolTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(),
        $"sran-browser-transfer-{Guid.NewGuid():N}");
    private readonly string _outside = Path.Combine(
        Path.GetTempPath(),
        $"sran-browser-transfer-outside-{Guid.NewGuid():N}");
    private readonly WorkspaceFileBroker _files;
    private readonly Mock<IBrowserBridgeService> _bridge = new();

    public BrowserTransferToolTests()
    {
        Directory.CreateDirectory(_root);
        Directory.CreateDirectory(_outside);
        _files = new WorkspaceFileBroker(
            [new WorkspaceMount(_root, WorkspaceMountAccess.ReadWrite)],
            _root);
    }

    [Fact]
    public async Task Upload_Reads_Only_Authorized_File_And_Verifies_Metadata()
    {
        var path = Path.Combine(_root, "fixture.txt");
        await File.WriteAllTextAsync(path, "authorized upload");
        _bridge.Setup(bridge => bridge.RequestAsync(
                "page.upload",
                It.IsAny<object>(),
                It.IsAny<CancellationToken>(),
                It.IsAny<TimeSpan?>()))
            .ReturnsAsync(JsonSerializer.SerializeToElement(new
            {
                upload_verified = true,
                selected_file_name = "fixture.txt",
                selected_file_size = 17
            }));

        var result = await new BrowserUploadTool(_bridge.Object, _files)
            .ExecuteAsync(BoundArguments(new
            {
                path,
                expected_state = "Upload selected fixture.txt"
            }), CancellationToken.None);

        result.Success.Should().BeTrue(result.Error);
        result.Effect!.Verified.Should().BeTrue();
        result.Effect.Evidence.Should().Contain(item =>
            item.StartsWith("upload_sha256=", StringComparison.Ordinal));
    }

    [Fact]
    public async Task Upload_Outside_Workspace_Is_Denied_Before_Browser_Dispatch()
    {
        var path = Path.Combine(_outside, "secret.txt");
        await File.WriteAllTextAsync(path, "must not leave boundary");

        var result = await new BrowserUploadTool(_bridge.Object, _files)
            .ExecuteAsync(BoundArguments(new
            {
                path,
                expected_state = "selected"
            }), CancellationToken.None);

        result.Success.Should().BeFalse();
        _bridge.Verify(bridge => bridge.RequestAsync(
            It.IsAny<string>(),
            It.IsAny<object>(),
            It.IsAny<CancellationToken>(),
            It.IsAny<TimeSpan?>()), Times.Never);
    }

    [Fact]
    public async Task Download_Verifies_Browser_Hash_And_Workspace_Readback()
    {
        var bytes = "verified browser download"u8.ToArray();
        var hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        _bridge.Setup(bridge => bridge.RequestAsync(
                "page.download",
                It.IsAny<object>(),
                It.IsAny<CancellationToken>(),
                It.IsAny<TimeSpan?>()))
            .ReturnsAsync(JsonSerializer.SerializeToElement(new
            {
                download_verified = true,
                file_name = "download.txt",
                content_base64 = Convert.ToBase64String(bytes),
                byte_count = bytes.Length,
                sha256 = hash
            }));
        var destination = Path.Combine(_root, "download.txt");

        var result = await new BrowserDownloadTool(_bridge.Object, _files)
            .ExecuteAsync(BoundArguments(new
            {
                destination_path = destination,
                expected_file_name = "download.txt"
            }), CancellationToken.None);

        result.Success.Should().BeTrue(result.Error);
        (await File.ReadAllBytesAsync(destination)).Should().Equal(bytes);
        result.Effect!.Evidence.Should().Contain(item =>
            item.StartsWith("download_sha256=", StringComparison.Ordinal));
    }

    [Fact]
    public async Task Download_Hash_Mismatch_Never_Writes()
    {
        var bytes = "tampered"u8.ToArray();
        _bridge.Setup(bridge => bridge.RequestAsync(
                "page.download",
                It.IsAny<object>(),
                It.IsAny<CancellationToken>(),
                It.IsAny<TimeSpan?>()))
            .ReturnsAsync(JsonSerializer.SerializeToElement(new
            {
                download_verified = true,
                file_name = "download.txt",
                content_base64 = Convert.ToBase64String(bytes),
                byte_count = bytes.Length,
                sha256 = new string('0', 64)
            }));
        var destination = Path.Combine(_root, "download.txt");

        var result = await new BrowserDownloadTool(_bridge.Object, _files)
            .ExecuteAsync(BoundArguments(new
            {
                destination_path = destination,
                expected_file_name = "download.txt"
            }), CancellationToken.None);

        result.Success.Should().BeFalse();
        File.Exists(destination).Should().BeFalse();
    }

    private static JsonElement BoundArguments(object values)
    {
        var common = JsonSerializer.SerializeToElement(new
        {
            @ref = 4,
            tab_id = 7,
            observation_id = "observation-current",
            dom_generation = 9
        });
        var properties = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(
            common.GetRawText())!;
        foreach (var pair in JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(
                     JsonSerializer.Serialize(values))!)
            properties[pair.Key] = pair.Value;
        return JsonSerializer.SerializeToElement(properties);
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
            Directory.Delete(_root, recursive: true);
        if (Directory.Exists(_outside))
            Directory.Delete(_outside, recursive: true);
    }
}


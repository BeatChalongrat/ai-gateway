using FluentAssertions;
using SranDesktop.App.Hosting;
using Xunit;

namespace SranDesktop.Tests.Security;

public sealed class LiveLogSinkSecretTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-live-log-{Guid.NewGuid():N}");

    [Fact]
    public void Real_Rolling_File_Sink_Redacts_Before_Bytes_Reach_Disk()
    {
        Directory.CreateDirectory(_root);
        var path = Path.Combine(_root, "app-.log");
        var secrets = new[]
        {
            "sk-liveSinkSyntheticKey123456789",
            "Bearer liveSinkBearerToken123456789",
            "refresh_token=refreshTokenSynthetic123456789",
            "postgres://runtime:databasePassword123@example.invalid/db",
            "AKIAIOSFODNN7EXAMPLE",
            "security.test@example.invalid",
            "081-234-5678"
        };
        const string privateKey =
            "-----BEGIN PRIVATE KEY-----\nSYNTHETIC_PRIVATE_KEY_MATERIAL\n-----END PRIVATE KEY-----";

        RedactingFileLogProbe.Emit(path, secrets, privateKey);

        var files = Directory.GetFiles(_root, "*.log");
        files.Should().NotBeEmpty();
        var persisted = string.Join("\n", files.Select(File.ReadAllText));
        foreach (var secret in secrets)
            persisted.Should().NotContain(secret);
        persisted.Should().NotContain("SYNTHETIC_PRIVATE_KEY_MATERIAL");
        persisted.Should().Contain("[REDACTED");
        persisted.Should().Contain("argumentCount=4");
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
            Directory.Delete(_root, recursive: true);
    }
}

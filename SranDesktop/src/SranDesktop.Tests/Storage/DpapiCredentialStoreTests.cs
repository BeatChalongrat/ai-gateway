using FluentAssertions;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Storage;

public class DpapiCredentialStoreTests : IDisposable
{
    private readonly string _filePath;

    public DpapiCredentialStoreTests()
    {
        _filePath = Path.Combine(Path.GetTempPath(), $"sran-test-{Guid.NewGuid():N}", "credentials.dat");
    }

    [Fact]
    public void Save_Then_Load_RoundTrips()
    {
        var store = new DpapiCredentialStore(_filePath);

        store.Save("access_token", "secret-value-123");

        store.Load("access_token").Should().Be("secret-value-123");
    }

    [Fact]
    public void Load_MissingKey_ReturnsNull()
    {
        var store = new DpapiCredentialStore(_filePath);

        store.Load("nope").Should().BeNull();
    }

    [Fact]
    public void Delete_RemovesKey()
    {
        var store = new DpapiCredentialStore(_filePath);
        store.Save("k", "v");

        store.Delete("k");

        store.Load("k").Should().BeNull();
    }

    [Fact]
    public void File_On_Disk_Is_Not_Plaintext()
    {
        var store = new DpapiCredentialStore(_filePath);
        store.Save("k", "super-secret-marker");

        var raw = File.ReadAllText(_filePath);

        raw.Should().NotContain("super-secret-marker");
    }

    [Fact]
    public void Values_Survive_New_Store_Instance()
    {
        new DpapiCredentialStore(_filePath).Save("k", "persisted");

        new DpapiCredentialStore(_filePath).Load("k").Should().Be("persisted");
    }

    public void Dispose()
    {
        var dir = Path.GetDirectoryName(_filePath)!;
        if (Directory.Exists(dir))
            Directory.Delete(dir, recursive: true);
    }
}

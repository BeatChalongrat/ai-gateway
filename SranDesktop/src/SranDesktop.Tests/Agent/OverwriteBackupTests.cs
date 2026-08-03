using System.Text.Json;
using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>FIX-G: overwriting an existing file first snapshots it to
/// "&lt;file&gt;.sran.bak" so a wrong overwrite is recoverable; brand-new files
/// create no backup (nothing to lose).</summary>
public sealed class OverwriteBackupTests : IDisposable
{
    private readonly string _dir;

    public OverwriteBackupTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), $"sran-bak-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_dir);
    }

    public void Dispose()
    {
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    [Fact]
    public async Task Overwriting_An_Existing_File_Keeps_A_Bak_With_The_Old_Content()
    {
        var path = Path.Combine(_dir, "report.txt");
        await File.WriteAllTextAsync(path, "original");

        var result = await new WriteFileTool().ExecuteAsync(
            Args(new { path, content = "replaced" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        File.ReadAllText(path).Should().Be("replaced");
        File.ReadAllText(path + ".sran.bak").Should().Be("original");
        result.Content.Should().Contain(".sran.bak", "the user must learn where the old version went");
    }

    [Fact]
    public async Task A_New_File_Creates_No_Backup()
    {
        var path = Path.Combine(_dir, "fresh.txt");

        var result = await new WriteFileTool().ExecuteAsync(
            Args(new { path, content = "hello" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        File.Exists(path + ".sran.bak").Should().BeFalse();
        result.Content.Should().NotContain(".sran.bak");
    }

    [Fact]
    public async Task Edit_File_Backs_Up_The_PreEdit_Content()
    {
        var path = Path.Combine(_dir, "code.cs");
        await File.WriteAllTextAsync(path, "var x = 1;");

        var result = await new EditFileTool().ExecuteAsync(
            Args(new { path, old_string = "1", new_string = "2" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        File.ReadAllText(path).Should().Be("var x = 2;");
        File.ReadAllText(path + ".sran.bak").Should().Be("var x = 1;");
    }

    [Fact]
    public async Task Move_With_Overwrite_Backs_Up_The_Clobbered_Destination()
    {
        var source = Path.Combine(_dir, "new.txt");
        var destination = Path.Combine(_dir, "old.txt");
        await File.WriteAllTextAsync(source, "incoming");
        await File.WriteAllTextAsync(destination, "about to be replaced");

        var result = await new MoveFileTool().ExecuteAsync(
            Args(new { source, destination, overwrite = true }), CancellationToken.None);

        result.Success.Should().BeTrue();
        File.ReadAllText(destination).Should().Be("incoming");
        File.ReadAllText(destination + ".sran.bak").Should().Be("about to be replaced");
    }

    [Fact]
    public async Task The_Backup_Always_Holds_The_Immediately_Previous_Version()
    {
        var path = Path.Combine(_dir, "doc.txt");
        await File.WriteAllTextAsync(path, "v1");

        await new WriteFileTool().ExecuteAsync(Args(new { path, content = "v2" }), CancellationToken.None);
        await new WriteFileTool().ExecuteAsync(Args(new { path, content = "v3" }), CancellationToken.None);

        File.ReadAllText(path).Should().Be("v3");
        File.ReadAllText(path + ".sran.bak").Should().Be("v2", "latest-only backup, not a history");
    }
}

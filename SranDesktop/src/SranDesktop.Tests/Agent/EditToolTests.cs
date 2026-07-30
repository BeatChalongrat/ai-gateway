using System.Text.Json;
using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Phase A PART 2: edit_file / create_directory / move_file.</summary>
[Collection("PathGuardGrants")]
public class EditToolTests : IDisposable
{
    private readonly string _workDir;

    public EditToolTests()
    {
        _workDir = Path.Combine(Path.GetTempPath(), $"sran-edit-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_workDir);
        PathGuard.ConfigureMounts(
            [new WorkspaceMount(_workDir, WorkspaceMountAccess.ReadWrite)],
            _workDir);
    }

    private static JsonElement Args(object o) =>
        JsonSerializer.SerializeToElement(o);

    // ---------- edit_file ----------

    [Fact]
    public async Task Edit_Replaces_Unique_Match()
    {
        var file = Path.Combine(_workDir, "prog.cs");
        await File.WriteAllTextAsync(file, "line1\nvar x = 1;\nline3\n");

        var result = await new EditFileTool().ExecuteAsync(
            Args(new { path = file, old_string = "var x = 1;", new_string = "var x = 42;" }),
            CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("line 2");
        (await File.ReadAllTextAsync(file)).Should().Be("line1\nvar x = 42;\nline3\n");
    }

    [Fact]
    public async Task Edit_Ambiguous_Match_Errors_Asking_For_Context()
    {
        var file = Path.Combine(_workDir, "dup.txt");
        await File.WriteAllTextAsync(file, "foo\nfoo\nbar\n");

        var result = await new EditFileTool().ExecuteAsync(
            Args(new { path = file, old_string = "foo", new_string = "baz" }),
            CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("2");
        result.Error.Should().Contain("unique");
        // Unchanged.
        (await File.ReadAllTextAsync(file)).Should().Be("foo\nfoo\nbar\n");
    }

    [Fact]
    public async Task Edit_ReplaceAll_Changes_Every_Occurrence()
    {
        var file = Path.Combine(_workDir, "dup2.txt");
        await File.WriteAllTextAsync(file, "foo foo foo");

        var result = await new EditFileTool().ExecuteAsync(
            Args(new { path = file, old_string = "foo", new_string = "bar", replace_all = true }),
            CancellationToken.None);

        result.Success.Should().BeTrue();
        (await File.ReadAllTextAsync(file)).Should().Be("bar bar bar");
    }

    [Fact]
    public async Task Edit_Miss_Reports_Clear_Error()
    {
        var file = Path.Combine(_workDir, "miss.txt");
        await File.WriteAllTextAsync(file, "hello world");

        var result = await new EditFileTool().ExecuteAsync(
            Args(new { path = file, old_string = "goodbye", new_string = "x" }),
            CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("not found");
    }

    [Fact]
    public async Task Edit_Missing_File_Suggests_WriteFile()
    {
        var result = await new EditFileTool().ExecuteAsync(
            Args(new { path = Path.Combine(_workDir, "nope.txt"), old_string = "a", new_string = "b" }),
            CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("write_file");
    }

    [Fact]
    public async Task Edit_Rejects_Path_Outside_Roots()
    {
        var result = await new EditFileTool().ExecuteAsync(
            Args(new { path = "C:\\Windows\\System32\\drivers\\etc\\hosts", old_string = "a", new_string = "b" }),
            CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("outside authorized workspace mounts");
    }

    [Fact]
    public async Task Edit_Sequence_On_One_File_Works()
    {
        var file = Path.Combine(_workDir, "seq.cs");
        await File.WriteAllTextAsync(file, "int a;\nint b;\n");
        var tool = new EditFileTool();

        (await tool.ExecuteAsync(Args(new { path = file, old_string = "int a;", new_string = "int a = 1;" }),
            CancellationToken.None)).Success.Should().BeTrue();
        (await tool.ExecuteAsync(Args(new { path = file, old_string = "int b;", new_string = "int b = 2;" }),
            CancellationToken.None)).Success.Should().BeTrue();

        (await File.ReadAllTextAsync(file)).Should().Be("int a = 1;\nint b = 2;\n");
    }

    // ---------- create_directory ----------

    [Fact]
    public async Task CreateDirectory_Makes_Nested_Path()
    {
        var dir = Path.Combine(_workDir, "a", "b", "c");

        var result = await new CreateDirectoryTool().ExecuteAsync(
            Args(new { path = dir }), CancellationToken.None);

        result.Success.Should().BeTrue();
        Directory.Exists(dir).Should().BeTrue();
    }

    [Fact]
    public async Task CreateDirectory_Rejects_State_And_Outside()
    {
        var result = await new CreateDirectoryTool().ExecuteAsync(
            Args(new { path = "C:\\Windows\\evil" }), CancellationToken.None);
        result.Success.Should().BeFalse();
    }

    // ---------- move_file ----------

    [Fact]
    public async Task MoveFile_Renames_Within_Roots()
    {
        var src = Path.Combine(_workDir, "old.txt");
        var dst = Path.Combine(_workDir, "sub", "new.txt");
        await File.WriteAllTextAsync(src, "data");

        var result = await new MoveFileTool().ExecuteAsync(
            Args(new { source = src, destination = dst }), CancellationToken.None);

        result.Success.Should().BeTrue();
        File.Exists(src).Should().BeFalse();
        (await File.ReadAllTextAsync(dst)).Should().Be("data");
    }

    [Fact]
    public async Task MoveFile_Rejects_Destination_Outside_Roots()
    {
        var src = Path.Combine(_workDir, "x.txt");
        await File.WriteAllTextAsync(src, "data");

        var result = await new MoveFileTool().ExecuteAsync(
            Args(new { source = src, destination = "C:\\Windows\\x.txt" }), CancellationToken.None);

        result.Success.Should().BeFalse();
        File.Exists(src).Should().BeTrue();
    }

    public void Dispose()
    {
        PathGuard.ConfigureMounts([]);
        if (Directory.Exists(_workDir))
            Directory.Delete(_workDir, recursive: true);
    }
}

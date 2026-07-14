using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class McpToolTests : IDisposable
{
    private readonly string _workDir;

    public McpToolTests()
    {
        // Inside the temp folder → within PathGuard's allowed roots.
        _workDir = Path.Combine(Path.GetTempPath(), $"sran-tools-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_workDir);
    }

    private static JsonElement Args(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private static string J(string path) => JsonSerializer.Serialize(path);

    // ---------- read_file ----------

    [Fact]
    public async Task ReadFile_Reads_Text()
    {
        var file = Path.Combine(_workDir, "note.txt");
        await File.WriteAllTextAsync(file, "hello สวัสดี");

        var result = await new ReadFileTool().ExecuteAsync(
            Args($"{{\"path\":{J(file)}}}"), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Be("hello สวัสดี");
    }

    [Fact]
    public async Task ReadFile_Rejects_Path_Outside_Allowed_Roots()
    {
        var result = await new ReadFileTool().ExecuteAsync(
            Args("""{"path":"C:\\Windows\\System32\\drivers\\etc\\hosts"}"""),
            CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("outside the allowed directories");
    }

    [Fact]
    public async Task ReadFile_Rejects_Traversal_Escape()
    {
        var sneaky = Path.Combine(_workDir, "..", "..", "..", "..", "Windows", "win.ini");

        var result = await new ReadFileTool().ExecuteAsync(
            Args($"{{\"path\":{J(sneaky)}}}"), CancellationToken.None);

        result.Success.Should().BeFalse();
    }

    [Fact]
    public async Task ReadFile_Detects_Binary()
    {
        var file = Path.Combine(_workDir, "bin.dat");
        await File.WriteAllBytesAsync(file, new byte[] { 1, 0, 2, 0, 3 });

        var result = await new ReadFileTool().ExecuteAsync(
            Args($"{{\"path\":{J(file)}}}"), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("base64");
        result.Content.Should().Contain(Convert.ToBase64String(new byte[] { 1, 0, 2, 0, 3 }));
    }

    // ---------- write_file ----------

    [Fact]
    public async Task WriteFile_Writes_And_Creates_Parents()
    {
        var file = Path.Combine(_workDir, "sub", "dir", "out.txt");

        var result = await new WriteFileTool().ExecuteAsync(
            Args($"{{\"path\":{J(file)},\"content\":\"data123\"}}"), CancellationToken.None);

        result.Success.Should().BeTrue();
        (await File.ReadAllTextAsync(file)).Should().Be("data123");
    }

    [Fact]
    public async Task WriteFile_Rejects_Outside_Roots()
    {
        var result = await new WriteFileTool().ExecuteAsync(
            Args("""{"path":"C:\\Windows\\evil.txt","content":"x"}"""), CancellationToken.None);

        result.Success.Should().BeFalse();
    }

    // ---------- list_directory ----------

    [Fact]
    public async Task ListDirectory_Lists_Files_And_Dirs()
    {
        Directory.CreateDirectory(Path.Combine(_workDir, "subdir"));
        await File.WriteAllTextAsync(Path.Combine(_workDir, "a.txt"), "aaa");

        var result = await new ListDirectoryTool().ExecuteAsync(
            Args($"{{\"path\":{J(_workDir)}}}"), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("D  subdir");
        result.Content.Should().Contain("F  a.txt");
    }

    // ---------- search_files ----------

    [Fact]
    public async Task SearchFiles_Globs_And_Greps()
    {
        await File.WriteAllTextAsync(Path.Combine(_workDir, "one.log"), "alpha\nneedle here\nbeta");
        await File.WriteAllTextAsync(Path.Combine(_workDir, "two.log"), "nothing");

        var result = await new SearchFilesTool().ExecuteAsync(
            Args($"{{\"directory\":{J(_workDir)},\"pattern\":\"*.log\",\"content_pattern\":\"needle\"}}"),
            CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("one.log:2");
        result.Content.Should().NotContain("two.log");
    }

    // ---------- open_url ----------

    [Fact]
    public async Task OpenUrl_Rejects_Non_Http_Schemes()
    {
        var result = await new OpenUrlTool().ExecuteAsync(
            Args("""{"url":"file:///C:/Windows/System32/calc.exe"}"""), CancellationToken.None);

        result.Success.Should().BeFalse();
    }

    // ---------- run_command ----------

    [Fact]
    public async Task RunCommand_Executes_And_Captures_Output()
    {
        var result = await new RunCommandTool(NullLogger<RunCommandTool>.Instance).ExecuteAsync(
            Args("""{"command":"echo agent-test-123"}"""), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("agent-test-123");
        result.Content.Should().Contain("[exit code 0]");
    }

    [Theory]
    [InlineData("rm -rf /")]
    [InlineData("del /s C:\\Users")]
    [InlineData("shutdown /s /t 0")]
    [InlineData("FORMAT C:")]
    public async Task RunCommand_Blocklist_Rejects_Destructive_Commands(string command)
    {
        var result = await new RunCommandTool(NullLogger<RunCommandTool>.Instance).ExecuteAsync(
            Args(JsonSerializer.Serialize(new { command })), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("blocklist");
    }

    [Fact]
    public async Task RunCommand_Rejects_Working_Directory_Outside_Roots()
    {
        var result = await new RunCommandTool(NullLogger<RunCommandTool>.Instance).ExecuteAsync(
            Args("""{"command":"echo hi","working_directory":"C:\\Windows"}"""),
            CancellationToken.None);

        result.Success.Should().BeFalse();
    }

    // ---------- registry / host ----------

    [Fact]
    public void ToolHost_Builds_Valid_Definitions()
    {
        var registry = new McpToolRegistry();
        registry.Register(new ReadFileTool());
        registry.Register(new WriteFileTool());
        var host = new McpToolHost(registry);

        var definitions = host.GetToolDefinitions();

        definitions.Should().HaveCount(2);
        definitions.Should().OnlyContain(d => d.Type == "function");
        definitions.Select(d => d.Function.Name).Should().BeEquivalentTo("read_file", "write_file");
        host.Resolve("READ_FILE").Should().NotBeNull(); // case-insensitive
        host.Resolve("nope").Should().BeNull();
    }

    public void Dispose()
    {
        if (Directory.Exists(_workDir))
            Directory.Delete(_workDir, recursive: true);
    }
}

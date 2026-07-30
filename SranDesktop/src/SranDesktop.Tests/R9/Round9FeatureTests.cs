using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.R9;

public class CronHelperTests
{
    [Theory]
    [InlineData("0 6 * * *")]
    [InlineData("*/5 * * * *")]
    [InlineData("0 6 * * 1")]
    public void Valid_Cron_Yields_Future_Occurrence(string cron)
    {
        var next = CronHelper.NextOccurrence(cron);
        Assert.NotNull(next);
        Assert.True(next > DateTimeOffset.UtcNow.AddSeconds(-1));
    }

    [Theory]
    [InlineData("not a cron")]
    [InlineData("99 99 * * *")]
    [InlineData("")]
    public void Invalid_Cron_Returns_Null(string cron)
    {
        Assert.Null(CronHelper.NextOccurrence(cron));
    }
}

public class SkillManagerTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), $"skills-test-{Guid.NewGuid():N}");
    private readonly SkillManager _manager;

    public SkillManagerTests()
    {
        _manager = new SkillManager(_root, NullLogger<SkillManager>.Instance);
    }

    [Fact]
    public async Task Builtins_Are_Written_And_Loaded()
    {
        var skills = await _manager.GetSkillsAsync();
        Assert.Contains(skills, s => s.Manifest.Name == "docx" && s.IsBuiltin);
        Assert.Contains(skills, s => s.Manifest.Name == "xlsx");
        Assert.Contains(skills, s => s.Manifest.Name == "pptx");
        Assert.All(skills, s => Assert.False(string.IsNullOrWhiteSpace(s.Instructions)));
    }

    [Fact]
    public async Task Trigger_Matching_Is_Case_Insensitive()
    {
        var matched = await _manager.FindMatchingSkillsAsync("Please make me an EXCEL sheet of Q3 numbers");
        Assert.Contains(matched, s => s.Manifest.Name == "xlsx");
    }

    [Fact]
    public async Task Unmatched_Message_Returns_Empty()
    {
        var matched = await _manager.FindMatchingSkillsAsync("hello there");
        Assert.Empty(matched);
    }

    [Fact]
    public async Task Disabled_Skill_Stops_Matching()
    {
        var skills = await _manager.GetSkillsAsync();
        var xlsx = skills.First(s => s.Manifest.Name == "xlsx");
        await _manager.SetEnabledAsync(xlsx, false);
        var matched = await _manager.FindMatchingSkillsAsync("make an excel file");
        Assert.DoesNotContain(matched, s => s.Manifest.Name == "xlsx");
    }

    public void Dispose()
    {
        try { Directory.Delete(_root, recursive: true); } catch (IOException) { }
    }
}

public class ScheduledTaskStoreTests : IDisposable
{
    private readonly string _dbPath = Path.Combine(Path.GetTempPath(), $"tasks-test-{Guid.NewGuid():N}.db");

    [Fact]
    public async Task Upsert_RoundTrips_And_Deletes()
    {
        var store = new SqliteScheduledTaskStore(_dbPath);
        var task = new ScheduledTask
        {
            Name = "Morning brief",
            Prompt = "Summarize my day",
            CronExpression = "0 6 * * *",
            NextRunAt = DateTimeOffset.Now.AddHours(1)
        };

        await store.UpsertAsync(task);
        var all = await store.GetAllAsync();
        var loaded = Assert.Single(all);
        Assert.Equal(task.Name, loaded.Name);
        Assert.Equal(task.CronExpression, loaded.CronExpression);
        Assert.True(loaded.IsEnabled);
        Assert.NotNull(loaded.NextRunAt);

        loaded.IsEnabled = false;
        loaded.LastResult = "ok";
        await store.UpsertAsync(loaded);
        var reloaded = (await store.GetAllAsync()).Single();
        Assert.False(reloaded.IsEnabled);
        Assert.Equal("ok", reloaded.LastResult);

        await store.DeleteAsync(task.Id);
        Assert.Empty(await store.GetAllAsync());
    }

    public void Dispose()
    {
        try { File.Delete(_dbPath); } catch (IOException) { }
    }
}

public class ProjectStoreTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), $"projects-test-{Guid.NewGuid():N}");

    [Fact]
    public async Task Project_Crud_And_Conversation_Lookup()
    {
        var store = new JsonProjectStore(_root);
        var project = new Project { Name = "Research" };
        project.ConversationIds.Add("conv-1");
        await store.UpsertAsync(project);

        var found = await store.FindByConversationAsync("conv-1");
        Assert.NotNull(found);
        Assert.Equal("Research", found!.Name);
        Assert.Null(await store.FindByConversationAsync("conv-x"));

        await store.DeleteAsync(project.Id);
        Assert.Empty(await store.GetAllAsync());
    }

    [Fact]
    public async Task AddFile_Copies_Into_Project_Folder()
    {
        var store = new JsonProjectStore(_root);
        var project = new Project { Name = "Docs" };
        await store.UpsertAsync(project);

        var source = Path.Combine(_root, "source.txt");
        Directory.CreateDirectory(_root);
        await File.WriteAllTextAsync(source, "hello");

        var stored = await store.AddFileAsync(project, source);
        Assert.True(File.Exists(stored));
        Assert.Contains(stored, project.Files);

        await store.RemoveFileAsync(project, stored);
        Assert.False(File.Exists(stored));
        Assert.Empty(project.Files);
    }

    public void Dispose()
    {
        try { Directory.Delete(_root, recursive: true); } catch (IOException) { }
    }
}

public class ArtifactHtmlBuilderTests
{
    [Fact]
    public void Html_Type_Wraps_Fragment_In_Document()
    {
        var html = ArtifactHtmlBuilder.Build("html", "<h1>Hi</h1>", "Test", dark: true);
        Assert.Contains("<!DOCTYPE html>", html);
        Assert.Contains("<h1>Hi</h1>", html);
        Assert.Contains("#212121", html); // dark bg
    }

    [Fact]
    public void Full_Html_Document_Gets_Csp_Injected_But_Keeps_Content()
    {
        // Phase 6 Task 3: the in-app render injects the CSP meta into the
        // author's own <head> while preserving the document.
        var source = "<html><head><title>x</title></head><body>x</body></html>";
        var html = ArtifactHtmlBuilder.Build("html", source, "T", dark: false);
        Assert.Contains("Content-Security-Policy", html);
        Assert.Contains("<body>x</body>", html);
    }

    [Fact]
    public void React_Type_Includes_Babel_And_Strips_Imports()
    {
        var source = "import { useState } from 'react';\nexport default function App() { return <div/>; }";
        var html = ArtifactHtmlBuilder.Build("react", source, "T", dark: false);
        Assert.Contains("babel", html);
        Assert.DoesNotContain("import { useState }", html);
        Assert.Contains("function App()", html);
    }

    [Fact]
    public void Code_Type_Escapes_Content()
    {
        var html = ArtifactHtmlBuilder.Build("code", "<script>alert(1)</script>", "T", dark: true);
        Assert.DoesNotContain("<script>alert(1)</script>", html);
        Assert.Contains("&lt;script&gt;", html);
    }

    [Fact]
    public void Mermaid_Uses_Theme()
    {
        var dark = ArtifactHtmlBuilder.Build("mermaid", "graph TD; A-->B", "T", dark: true);
        Assert.Contains("theme: 'dark'", dark);
        var light = ArtifactHtmlBuilder.Build("mermaid", "graph TD; A-->B", "T", dark: false);
        Assert.Contains("theme: 'default'", light);
    }

    // Phase 6 Task 3: every in-app artifact type carries the CSP + bundled host.
    [Theory]
    [InlineData("react", "export default function App(){return <div/>;}")]
    [InlineData("mermaid", "graph TD; A-->B")]
    [InlineData("svg", "<svg></svg>")]
    [InlineData("code", "print(1)")]
    [InlineData("html", "<h1>Hi</h1>")]
    public void InApp_Render_Has_Csp_And_No_Cdn(string type, string source)
    {
        var html = ArtifactHtmlBuilder.Build(type, source, "T", dark: true, forExternalBrowser: false);
        Assert.Contains("Content-Security-Policy", html);
        Assert.DoesNotContain("unpkg.com", html);
        Assert.DoesNotContain("jsdelivr.net", html);
    }

    [Fact]
    public void React_InApp_References_Bundled_Host()
    {
        var html = ArtifactHtmlBuilder.Build("react", "export default function App(){return null;}",
            "T", dark: false, forExternalBrowser: false);
        Assert.Contains("https://appassets.sran/react.production.min.js", html);
        Assert.Contains("https://appassets.sran/babel.min.js", html);
    }

    [Fact]
    public void External_Render_Uses_Cdn_And_No_Csp()
    {
        // The "Open in browser" copy runs outside the sandbox in the user's
        // own browser, where appassets.sran wouldn't resolve.
        var html = ArtifactHtmlBuilder.Build("react", "export default function App(){return null;}",
            "T", dark: false, forExternalBrowser: true);
        Assert.Contains("unpkg.com", html);
        Assert.DoesNotContain("appassets.sran", html);
        Assert.DoesNotContain("Content-Security-Policy", html);
    }
}

public class ToolIconTests
{
    [Theory]
    [InlineData("computer_screenshot", "Geo_Monitor")]
    [InlineData("web_search", "Geo_Globe2")] // humanize pass: globe (Geo_Browser now = browser_* tools)
    [InlineData("browser_navigate", "Geo_Browser")]
    [InlineData("run_command", "Geo_Terminal")]
    [InlineData("something_unknown", "Geo_Tool")]
    public void Tool_Icon_Mapping(string tool, string expected)
    {
        Assert.Equal(expected, ToolExecutionItemViewModel.ToolIcon(tool));
    }
}

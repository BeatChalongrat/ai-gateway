using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Shell.Bridge;
using SranDesktop.Shell.Seams;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// Phase 5 §2.3 — the DISPATCH-level refusals, the ones that do not exist at the
/// parse layer: a builtin skill is not deletable, a revoke of a path never
/// granted is refused, a settings key outside the allow-list is refused, and a
/// folder grant only ever comes from the native picker (the page names no
/// path). Exercises the real <see cref="ShellHostQueryCoordinator.RouteAsync"/>
/// with mocked services — no live WebView2.
/// </summary>
public sealed class ShellHostQueryCoordinatorTests
{
    private readonly Mock<ISkillManager> _skills = new();
    private readonly Mock<IScheduledTaskStore> _taskStore = new();
    private readonly Mock<ITaskSchedulerService> _scheduler = new();
    private readonly Mock<IMcpConnectorService> _connectors = new();
    private readonly Mock<ISettingsService> _settings = new();
    private readonly Mock<IStartupManager> _startup = new();
    private readonly Mock<IConversationStore> _store = new();
    private readonly Mock<ICrashReportService> _crash = new();
    private readonly Mock<IShellNativeDialogs> _native = new();
    private UserSettings _current = new();

    private ShellHostQueryCoordinator Build()
    {
        _settings.SetupGet(s => s.Current).Returns(() => _current);
        _settings.Setup(s => s.Save(It.IsAny<UserSettings>())).Callback<UserSettings>(u => _current = u);
        var oauth = new ConnectorOAuthCoordinator(
            Mock.Of<IConnectorAuthClient>(), _connectors.Object, Mock.Of<IBrowserLauncher>(),
            NullLogger<ConnectorOAuthCoordinator>.Instance);
        return new ShellHostQueryCoordinator(
            _skills.Object, _taskStore.Object, _scheduler.Object, _connectors.Object, oauth,
            _settings.Object, _startup.Object, _store.Object, _crash.Object, _native.Object,
            NullLogger<ShellHostQueryCoordinator>.Instance);
    }

    private static HostQuery Query(string op, object? args = null) =>
        new("req-1", HostQueryProtocol.ParseOp(op)!.Value,
            args is null ? default : JsonSerializer.SerializeToElement(args));

    private static Skill MakeSkill(string name, bool builtin) => new()
    {
        Manifest = new SkillManifest { Name = name },
        Instructions = "", Path = "x", IsBuiltin = builtin, IsEnabled = true,
    };

    [Fact]
    public async Task A_Builtin_Skill_Cannot_Be_Deleted()
    {
        _skills.Setup(s => s.GetSkillsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new[] { MakeSkill("core", builtin: true) });

        var (ok, _, error) = await Build().RouteAsync(Query("skills.delete", new { skillId = "core" }));

        ok.Should().BeFalse();
        error.Should().Be("refused");
        _skills.Verify(s => s.DeleteAsync(It.IsAny<Skill>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Deleting_An_Unknown_Skill_Is_NotFound()
    {
        _skills.Setup(s => s.GetSkillsAsync(It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<Skill>());
        var (ok, _, error) = await Build().RouteAsync(Query("skills.delete", new { skillId = "ghost" }));
        ok.Should().BeFalse();
        error.Should().Be("not-found");
    }

    [Fact]
    public async Task Settings_Set_On_A_Key_Outside_The_Allow_List_Is_Refused()
    {
        // The exact hostile input: a real page control name that is NOT settable.
        var (ok, _, error) = await Build().RouteAsync(
            Query("settings.set", new { key = "granted_folders", value = new[] { @"C:\" } }));
        ok.Should().BeFalse();
        error.Should().Be("refused");
        _settings.Verify(s => s.Save(It.IsAny<UserSettings>()), Times.Never);
    }

    [Fact]
    public async Task Settings_Set_On_An_Allowed_Key_With_A_Bad_Value_Is_Invalid()
    {
        var (ok, _, error) = await Build().RouteAsync(
            Query("settings.set", new { key = "theme", value = "Neon" }));
        ok.Should().BeFalse();
        error.Should().Be("invalid");
        _settings.Verify(s => s.Save(It.IsAny<UserSettings>()), Times.Never);
    }

    [Fact]
    public async Task Settings_Set_On_An_Allowed_Key_Persists()
    {
        var (ok, _, _) = await Build().RouteAsync(
            Query("settings.set", new { key = "theme", value = "Light" }));
        ok.Should().BeTrue();
        _current.Theme.Should().Be("Light");
    }

    [Fact]
    public async Task Revoking_A_Folder_Never_Granted_Is_NotFound()
    {
        _current = new UserSettings { GrantedFolders = { @"C:\Users\me\work" } };
        var (ok, _, error) = await Build().RouteAsync(
            Query("folders.revoke", new { folder = @"C:\Users\me\secrets" }));
        ok.Should().BeFalse();
        error.Should().Be("not-found");
        _settings.Verify(s => s.Save(It.IsAny<UserSettings>()), Times.Never);
    }

    [Fact]
    public async Task Folder_Grant_Comes_Only_From_The_Native_Picker_Not_From_The_Page()
    {
        // The page's args are irrelevant: pickAndGrant takes no path. Here the
        // native picker is stubbed to return nothing (user cancelled), and even a
        // page-supplied "path" grants nothing.
        _native.Setup(n => n.PickFolderAsync()).ReturnsAsync((string?)null);
        var (ok, data, _) = await Build().RouteAsync(
            Query("folders.pickAndGrant", new { path = @"C:\Windows\System32" }));
        ok.Should().BeTrue();
        JsonSerializer.Serialize(data).Should().Contain("cancelled");
        _settings.Verify(s => s.Save(It.IsAny<UserSettings>()), Times.Never,
            "no path from the page can ever be granted");
    }

    [Fact]
    public async Task An_Api_Key_Connector_Prompts_Natively_And_The_Key_Never_Crosses_The_Bridge()
    {
        _connectors.Setup(c => c.ListAvailableAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new[] { new McpConnector { Id = "acme", Name = "Acme", AuthType = "api_key" } });
        _native.Setup(n => n.PromptApiKeyAsync("Acme")).ReturnsAsync("secret-xyz");

        var (ok, _, _) = await Build().RouteAsync(Query("connectors.install", new { connectorId = "acme" }));

        ok.Should().BeTrue();
        // The key came from the NATIVE prompt and went straight to the service —
        // the page never supplied it and never sees it.
        _native.Verify(n => n.PromptApiKeyAsync("Acme"), Times.Once);
        _connectors.Verify(c => c.InstallAsync("acme", "secret-xyz", null, It.IsAny<CancellationToken>()), Times.Once);
    }
}

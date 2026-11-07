using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Platform;

public class JsonSettingsServiceTests : IDisposable
{
    private readonly string _dir;
    private readonly string _path;

    public JsonSettingsServiceTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), $"sran-settings-{Guid.NewGuid():N}");
        _path = Path.Combine(_dir, "settings.json");
    }

    [Fact]
    public void IsFirstRun_True_When_No_File()
    {
        var service = new JsonSettingsService(new UserSettings(), _path);

        service.IsFirstRun.Should().BeTrue();
        service.Current.ChatEdgeBaseUrl.Should().Be("http://localhost:4000");
    }

    [Fact]
    public void Save_Creates_File_And_Next_Load_Is_Not_First_Run()
    {
        var first = new JsonSettingsService(new UserSettings(), _path);
        var updated = first.Current.Clone();
        updated.Theme = "Light";
        updated.MinimizeToTray = false;
        first.Save(updated);

        var second = new JsonSettingsService(new UserSettings(), _path);

        second.IsFirstRun.Should().BeFalse();
        second.Current.Theme.Should().Be("Light");
        second.Current.MinimizeToTray.Should().BeFalse();
    }

    [Fact]
    public void New_Fields_RoundTrip()
    {
        var service = new JsonSettingsService(new UserSettings(), _path);
        var updated = service.Current.Clone();
        updated.StartWithWindows = true;
        updated.StartWithWindows = true;
        service.Save(updated);

        var reloaded = new JsonSettingsService(new UserSettings(), _path);
        reloaded.Current.StartWithWindows.Should().BeTrue();
        reloaded.Current.StartWithWindows.Should().BeTrue();
    }

    [Fact]
    public void SettingsChanged_Fires_On_Save()
    {
        var service = new JsonSettingsService(new UserSettings(), _path);
        var fired = 0;
        service.SettingsChanged += (_, _) => fired++;

        service.Save(service.Current.Clone());

        fired.Should().Be(1);
    }

    [Fact]
    public void Corrupt_File_Is_Backed_Up_Before_Reset()
    {
        Directory.CreateDirectory(_dir);
        File.WriteAllText(_path, "{ not valid json !!");

        var service = new JsonSettingsService(new UserSettings { Theme = "Light" }, _path);

        service.Current.Theme.Should().Be("Light"); // defaults, app still starts
        Directory.GetFiles(_dir, "settings.json.corrupt-*")
            .Should().ContainSingle("the unreadable file must be preserved, not discarded");
        File.ReadAllText(Directory.GetFiles(_dir, "settings.json.corrupt-*").Single())
            .Should().Be("{ not valid json !!");
    }

    [Fact]
    public void Save_Leaves_No_Tmp_File_Behind()
    {
        var service = new JsonSettingsService(new UserSettings(), _path);
        service.Save(service.Current.Clone());

        File.Exists(_path).Should().BeTrue();
        File.Exists(_path + ".tmp").Should().BeFalse();
    }

    public void Dispose()
    {
        if (Directory.Exists(_dir))
            Directory.Delete(_dir, recursive: true);
    }
}

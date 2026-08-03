using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Presentation;

/// <summary>FIX-A: with the dev bypass off (the Release default), a self-hosted
/// URL must pass Test Connection before Continue — a wrong URL slipping through
/// first-run is a permanent dead-end for a non-IT user.</summary>
public sealed class FirstRunGateTests : IDisposable
{
    private sealed class FakeSettings : ISettingsService
    {
        public UserSettings Current { get; } = new();
        public bool IsFirstRun => true;
        public event EventHandler? SettingsChanged;
        public void Save(UserSettings settings) => SettingsChanged?.Invoke(this, EventArgs.Empty);
    }

    private sealed class FakeStartup : IStartupManager
    {
        public bool IsEnabled => false;
        public void Set(bool enabled) { }
    }

    private readonly bool _originalBypass = FirstRunViewModel.AllowLocalDevBypass;

    public void Dispose() => FirstRunViewModel.AllowLocalDevBypass = _originalBypass;

    private static FirstRunViewModel CreateVm() => new(new FakeSettings(), new FakeStartup());

    [Fact]
    public void SelfHosted_Localhost_Cannot_Continue_Without_Passing_Test_When_Bypass_Off()
    {
        FirstRunViewModel.AllowLocalDevBypass = false;
        var vm = CreateVm(); // defaults: self-hosted selected, localhost URL

        vm.IsSelfHostedSelected.Should().BeTrue();
        vm.CanContinue.Should().BeFalse("a URL that never passed Test Connection must not proceed");
    }

    [Fact]
    public void SelfHosted_Can_Continue_After_Connection_Validated()
    {
        FirstRunViewModel.AllowLocalDevBypass = false;
        var vm = CreateVm();

        vm.IsConnectionValid = true;

        vm.CanContinue.Should().BeTrue();
    }

    [Fact]
    public void Editing_Url_Revokes_A_Previously_Valid_Connection()
    {
        FirstRunViewModel.AllowLocalDevBypass = false;
        var vm = CreateVm();
        vm.IsConnectionValid = true;

        vm.ServerBaseUrl = "https://typo.example.com";

        vm.CanContinue.Should().BeFalse("the edited URL has not been tested");
    }

    [Fact]
    public void Cloud_Preset_Is_Trusted_Without_A_Test()
    {
        FirstRunViewModel.AllowLocalDevBypass = false;
        var vm = CreateVm();

        vm.IsSelfHostedSelected = false;
        vm.IsCloudSelected = true;

        vm.CanContinue.Should().BeTrue();
    }

    [Fact]
    public void Dev_Bypass_Still_Lets_Localhost_Through_When_Enabled()
    {
        FirstRunViewModel.AllowLocalDevBypass = true;
        var vm = CreateVm();

        vm.CanContinue.Should().BeTrue("local dev with `make up` keeps working");
    }
}

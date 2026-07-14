using System.Collections.ObjectModel;
using System.IO;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Skill management (R9 Feature H).</summary>
public partial class SkillsViewModel : ObservableObject
{
    private readonly ISkillManager _skills;
    private readonly ILogger<SkillsViewModel> _logger;

    public ObservableCollection<SkillItemViewModel> Skills { get; } = new();

    [ObservableProperty] private string? _errorMessage;

    public SkillsViewModel(ISkillManager skills, ILogger<SkillsViewModel> logger)
    {
        _skills = skills;
        _logger = logger;
    }

    [RelayCommand]
    public async Task LoadAsync()
    {
        Skills.Clear();
        foreach (var skill in await _skills.GetSkillsAsync())
            Skills.Add(new SkillItemViewModel(this, _skills, skill));
    }

    [RelayCommand]
    private async Task InstallAsync()
    {
        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Title = LocaleService.Instance.T("skill.install"),
            Filter = "Skill packages (*.skill;*.zip)|*.skill;*.zip"
        };
        if (dialog.ShowDialog() != true)
            return;

        ErrorMessage = null;
        try
        {
            await _skills.InstallAsync(dialog.FileName);
            await LoadAsync();
        }
        catch (Exception ex) when (ex is InvalidDataException or IOException)
        {
            _logger.LogWarning(ex, "Skill install failed");
            ErrorMessage = ex.Message;
        }
    }

    internal void Remove(SkillItemViewModel item) => Skills.Remove(item);
}

/// <summary>One skill card.</summary>
public partial class SkillItemViewModel : ObservableObject
{
    private readonly SkillsViewModel _parent;
    private readonly ISkillManager _skills;
    private readonly Skill _skill;

    public string DisplayName => string.IsNullOrEmpty(_skill.Manifest.DisplayName)
        ? _skill.Manifest.Name : _skill.Manifest.DisplayName;
    public string Description => _skill.Manifest.Description;
    public string TriggersText =>
        $"{LocaleService.Instance.T("skill.triggers")}: {string.Join(", ", _skill.Manifest.Triggers.Take(5))}";
    public bool IsBuiltin => _skill.IsBuiltin;
    public string BadgeText => LocaleService.Instance.T(IsBuiltin ? "skill.builtin" : "skill.installed");

    [ObservableProperty] private bool _isEnabled;

    public SkillItemViewModel(SkillsViewModel parent, ISkillManager skills, Skill skill)
    {
        _parent = parent;
        _skills = skills;
        _skill = skill;
        _isEnabled = skill.IsEnabled;
    }

    partial void OnIsEnabledChanged(bool value) => _ = _skills.SetEnabledAsync(_skill, value);

    [RelayCommand]
    private async Task DeleteAsync()
    {
        if (IsBuiltin) return;
        var confirmed = System.Windows.MessageBox.Show(
            LocaleService.Instance.T("skill.deleteConfirm", DisplayName),
            LocaleService.Instance.T("skill.title"),
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Warning) == System.Windows.MessageBoxResult.Yes;
        if (!confirmed)
            return;

        await _skills.DeleteAsync(_skill);
        _parent.Remove(this);
    }
}

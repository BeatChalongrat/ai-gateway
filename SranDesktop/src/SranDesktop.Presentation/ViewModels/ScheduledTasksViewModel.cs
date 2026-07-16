using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Scheduled task management (R9 Feature G).</summary>
public partial class ScheduledTasksViewModel : ObservableObject
{
    private readonly IScheduledTaskStore _store;
    private readonly ITaskSchedulerService _scheduler;
    private readonly ILogger<ScheduledTasksViewModel> _logger;

    public ObservableCollection<ScheduledTaskItemViewModel> Tasks { get; } = new();

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsListEmpty))]
    private bool _hasLoaded;

    public bool IsListEmpty => HasLoaded && Tasks.Count == 0;

    private readonly IDialogService _dialogs;

    /// <summary>Themed confirm/error dialogs (Phase C.5), shared with item VMs.</summary>
    internal IDialogService Dialogs => _dialogs;

    public ScheduledTasksViewModel(
        IScheduledTaskStore store, ITaskSchedulerService scheduler,
        ILogger<ScheduledTasksViewModel> logger, IDialogService dialogs)
    {
        _store = store;
        _scheduler = scheduler;
        _logger = logger;
        _dialogs = dialogs;
    }

    [RelayCommand]
    public async Task LoadAsync()
    {
        Tasks.Clear();
        foreach (var task in await _store.GetAllAsync())
            Tasks.Add(new ScheduledTaskItemViewModel(this, _store, _scheduler, task));
        HasLoaded = true;
        OnPropertyChanged(nameof(IsListEmpty));
    }

    [RelayCommand]
    private async Task CreateAsync()
    {
        var dialog = new Views.Dialogs.ScheduledTaskFormDialog
        {
            Owner = System.Windows.Application.Current.MainWindow
        };
        if (dialog.ShowDialog() != true)
            return;

        var task = new ScheduledTask
        {
            Name = dialog.TaskName,
            Prompt = dialog.TaskPrompt,
            CronExpression = dialog.CronExpression,
            NextRunAt = _scheduler.NextOccurrence(dialog.CronExpression)
        };
        await _store.UpsertAsync(task);
        Tasks.Add(new ScheduledTaskItemViewModel(this, _store, _scheduler, task));
        OnPropertyChanged(nameof(IsListEmpty));
    }

    internal void Remove(ScheduledTaskItemViewModel item)
    {
        Tasks.Remove(item);
        OnPropertyChanged(nameof(IsListEmpty));
    }
}

/// <summary>One scheduled task row.</summary>
public partial class ScheduledTaskItemViewModel : ObservableObject
{
    private readonly ScheduledTasksViewModel _parent;
    private readonly IScheduledTaskStore _store;
    private readonly ITaskSchedulerService _scheduler;
    private readonly ScheduledTask _task;

    public string Id => _task.Id;

    [ObservableProperty] private string _name;
    [ObservableProperty] private string _prompt;
    [ObservableProperty] private string _cronExpression;
    [ObservableProperty] private bool _isEnabled;
    [ObservableProperty] private bool _isRunning;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(NextRunText))]
    [NotifyPropertyChangedFor(nameof(LastRunText))]
    [NotifyPropertyChangedFor(nameof(LastResult))]
    private int _refreshTick;

    public string NextRunText => _task.NextRunAt is { } next
        ? $"{LocaleService.Instance.T("task.nextRun")}: {next.LocalDateTime:g}"
        : $"{LocaleService.Instance.T("task.nextRun")}: {LocaleService.Instance.T("task.never")}";

    public string LastRunText => _task.LastRunAt is { } last
        ? $"{LocaleService.Instance.T("task.lastRun")}: {last.LocalDateTime:g}"
        : $"{LocaleService.Instance.T("task.lastRun")}: {LocaleService.Instance.T("task.never")}";

    public string? LastResult => _task.LastResult;
    public bool HasLastResult => !string.IsNullOrEmpty(_task.LastResult);

    public ScheduledTaskItemViewModel(
        ScheduledTasksViewModel parent, IScheduledTaskStore store,
        ITaskSchedulerService scheduler, ScheduledTask task)
    {
        _parent = parent;
        _store = store;
        _scheduler = scheduler;
        _task = task;
        _name = task.Name;
        _prompt = task.Prompt;
        _cronExpression = task.CronExpression;
        _isEnabled = task.IsEnabled;
    }

    partial void OnIsEnabledChanged(bool value)
    {
        _task.IsEnabled = value;
        if (value && _task.NextRunAt is null)
            _task.NextRunAt = _scheduler.NextOccurrence(_task.CronExpression);
        _ = _store.UpsertAsync(_task);
        RefreshTick++;
    }

    [RelayCommand]
    private async Task RunNowAsync()
    {
        if (IsRunning) return;
        IsRunning = true;
        try
        {
            await _scheduler.RunNowAsync(_task);
        }
        finally
        {
            IsRunning = false;
            RefreshTick++;
            OnPropertyChanged(nameof(HasLastResult));
        }
    }

    [RelayCommand]
    private async Task EditAsync()
    {
        var dialog = new Views.Dialogs.ScheduledTaskFormDialog(Name, Prompt, CronExpression)
        {
            Owner = System.Windows.Application.Current.MainWindow
        };
        if (dialog.ShowDialog() != true)
            return;

        _task.Name = Name = dialog.TaskName;
        _task.Prompt = Prompt = dialog.TaskPrompt;
        _task.CronExpression = CronExpression = dialog.CronExpression;
        _task.NextRunAt = _scheduler.NextOccurrence(dialog.CronExpression);
        await _store.UpsertAsync(_task);
        RefreshTick++;
    }

    [RelayCommand]
    private async Task DeleteAsync()
    {
        var confirmed = await _parent.Dialogs.ConfirmAsync(
            LocaleService.Instance.T("task.title"),
            LocaleService.Instance.T("task.deleteConfirm", Name));
        if (!confirmed)
            return;

        await _store.DeleteAsync(Id);
        _parent.Remove(this);
    }
}

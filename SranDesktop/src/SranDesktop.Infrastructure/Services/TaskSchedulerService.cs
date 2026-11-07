using Microsoft.Extensions.Logging;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Background scheduler (R9 Feature G): a 60s timer checks enabled tasks
/// against their cron schedule; due tasks send their prompt through chat-edge
/// (server persists the turn) and store a result preview. Runs even while the
/// window is minimized to tray — no dispatcher involvement.
/// </summary>
public sealed class TaskSchedulerService : ITaskSchedulerService, IDisposable
{
    private readonly IScheduledTaskStore _store;
    private readonly IChatEdgeApiClient _api;
    private readonly IChatService _chat;
    private readonly ILogger<TaskSchedulerService> _logger;

    private Timer? _timer;
    private int _ticking;

    public event EventHandler? TaskRunCompleted;

    public TaskSchedulerService(
        IScheduledTaskStore store,
        IChatEdgeApiClient api,
        IChatService chat,
        ILogger<TaskSchedulerService> logger)
    {
        _store = store;
        _api = api;
        _chat = chat;
        _logger = logger;
    }

    public void Start()
    {
        _timer ??= new Timer(_ => _ = TickAsync(), null,
            TimeSpan.FromSeconds(15), TimeSpan.FromSeconds(60));
    }

    public void Stop()
    {
        _timer?.Dispose();
        _timer = null;
    }

    public DateTimeOffset? NextOccurrence(string cronExpression) =>
        Application.Services.CronHelper.NextOccurrence(cronExpression);

    private async Task TickAsync()
    {
        // Never overlap ticks (a slow AI run can outlast the 60s period).
        if (Interlocked.Exchange(ref _ticking, 1) == 1)
            return;
        try
        {
            var tasks = await _store.GetAllAsync();
            var now = DateTimeOffset.Now;

            foreach (var task in tasks.Where(t => t.IsEnabled))
            {
                // Late-initialize the next-run marker for tasks created before
                // scheduling metadata existed.
                if (task.NextRunAt is null)
                {
                    task.NextRunAt = NextOccurrence(task.CronExpression);
                    await _store.UpsertAsync(task);
                    continue;
                }

                if (task.NextRunAt <= now)
                    await RunNowAsync(task);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Scheduler tick failed");
        }
        finally
        {
            Interlocked.Exchange(ref _ticking, 0);
        }
    }

    public async Task RunNowAsync(ScheduledTask task, CancellationToken ct = default)
    {
        _logger.LogInformation("Running scheduled task {Name} ({Id})", task.Name, task.Id);
        try
        {
            // Reuse the bound conversation, or create one titled after the task.
            var conversationId = task.ConversationId;
            if (conversationId is null)
            {
                var conversation = await _api.CreateConversationAsync("auto", ct);
                conversationId = conversation.Id;
                task.ConversationId = conversationId;
                try
                {
                    await _api.RenameConversationAsync(conversationId, $"⏰ {task.Name}", ct);
                }
                catch (HttpRequestException) { /* cosmetic only */ }
            }

            var response = new System.Text.StringBuilder();
            await foreach (var evt in _chat.SendMessageAsync(conversationId, task.Prompt, ct: ct))
            {
                switch (evt)
                {
                    case ContentDeltaEvent delta:
                        response.Append(delta.Text);
                        break;
                    case StreamErrorEvent error:
                        response.Clear();
                        response.Append("Error: ").Append(error.Message);
                        break;
                }
            }

            task.LastResult = Truncate(response.ToString(), 500);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or IOException)
        {
            _logger.LogWarning(ex, "Scheduled task {Id} failed", task.Id);
            task.LastResult = $"Error: {ex.Message}";
        }
        finally
        {
            task.LastRunAt = DateTimeOffset.Now;
            task.NextRunAt = NextOccurrence(task.CronExpression);
            await _store.UpsertAsync(task, CancellationToken.None);
            TaskRunCompleted?.Invoke(this, EventArgs.Empty);
        }
    }

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max] + "...";

    public void Dispose() => Stop();
}

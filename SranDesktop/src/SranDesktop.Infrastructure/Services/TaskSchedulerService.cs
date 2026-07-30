using Microsoft.Extensions.Logging;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Durable scheduler entry point. A firing creates or resumes an AgentTask with
/// TriggerType=Scheduler and then uses the same orchestrator, permission,
/// verification, and security path as interactive chat.
/// </summary>
public sealed class TaskSchedulerService : ITaskSchedulerService, IDisposable
{
    private readonly IScheduledTaskStore _store;
    private readonly IConversationStore _conversations;
    private readonly IChatOrchestrator _orchestrator;
    private readonly IDurableTaskRuntime _runtime;
    private readonly ISettingsService _settings;
    private readonly ILogger<TaskSchedulerService> _logger;

    private Timer? _timer;
    private int _ticking;

    public event EventHandler? TaskRunCompleted;

    public TaskSchedulerService(
        IScheduledTaskStore store,
        IConversationStore conversations,
        IChatOrchestrator orchestrator,
        IDurableTaskRuntime runtime,
        ISettingsService settings,
        ILogger<TaskSchedulerService> logger)
    {
        _store = store;
        _conversations = conversations;
        _orchestrator = orchestrator;
        _runtime = runtime;
        _settings = settings;
        _logger = logger;
    }

    public void Start()
    {
        _timer ??= new Timer(
            _ => _ = TickAsync(),
            null,
            TimeSpan.FromSeconds(15),
            TimeSpan.FromSeconds(60));
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
        if (Interlocked.Exchange(ref _ticking, 1) == 1)
            return;
        try
        {
            var tasks = await _store.GetAllAsync();
            var now = DateTimeOffset.Now;
            foreach (var task in tasks.Where(task => task.IsEnabled))
            {
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
        _logger.LogInformation("Running scheduled task {NameFingerprint} ({Id})",
            Application.Services.SensitiveDataRedactor.Fingerprint(task.Name),
            task.Id);
        try
        {
            var conversationId = task.ConversationId ?? $"schedule-{task.Id}";
            if (await _conversations.GetConversationAsync(conversationId, ct) is null)
            {
                await _conversations.UpsertConversationAsync(new Conversation
                {
                    Id = conversationId,
                    Title = task.Name,
                    ModelId = _settings.Current.DefaultModel,
                    CreatedAt = DateTimeOffset.UtcNow,
                    UpdatedAt = DateTimeOffset.UtcNow,
                    Source = ConversationSource.Local
                }, ct);
            }
            task.ConversationId = conversationId;

            var run = await _runtime.CreateRunAsync(new DurableRunRequest(
                conversationId,
                AgentTriggerType.Scheduler,
                task.Prompt,
                $"conversation:{conversationId}",
                SecurityContextJson: System.Text.Json.JsonSerializer.Serialize(new
                {
                    schedule_id = task.Id,
                    unattended = true,
                    consent_fallback = "deny"
                }),
                PermissionContextJson:
                    """{"unattended":true,"allow_stored_scope_only":true}""",
                ModelContextJson: System.Text.Json.JsonSerializer.Serialize(new
                {
                    model = _settings.Current.DefaultModel
                })), ct);
            task.LastRunId = run.TaskId;

            var messages = new List<GatewayMessage>
            {
                new() { Role = "user", Content = task.Prompt }
            };
            var response = new System.Text.StringBuilder();
            var model = string.IsNullOrWhiteSpace(_settings.Current.DefaultModel)
                ? "gpt-4o-mini"
                : _settings.Current.DefaultModel;
            await foreach (var evt in _orchestrator.RunAsync(
                               model,
                               messages,
                               conversationId,
                               ct: ct))
            {
                switch (evt)
                {
                    case AgentContentDeltaEvent delta:
                        response.Append(delta.Text);
                        break;
                    case AgentErrorEvent error:
                        response.Clear();
                        response.Append("Error: ").Append(error.Message);
                        break;
                }
            }
            task.LastResult = Truncate(response.ToString(), 500);
        }
        catch (Exception ex) when (ex is HttpRequestException
                                   or TaskCanceledException
                                   or IOException
                                   or InvalidOperationException
                                   or UnauthorizedAccessException)
        {
            _logger.LogWarning(ex, "Scheduled task {Id} failed", task.Id);
            task.LastResult =
                $"Error: {Application.Services.SensitiveDataRedactor.Redact(ex.Message)}";
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

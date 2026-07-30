using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Data.Sqlite;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Infrastructure.Storage;

public sealed class SqliteAgentTaskStore : IAgentTaskStore
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        Converters = { new JsonStringEnumConverter() }
    };

    private readonly string _connectionString;
    private readonly SemaphoreSlim _initLock = new(1, 1);
    private bool _initialized;

    public SqliteAgentTaskStore() : this(DefaultDbPath()) { }

    public SqliteAgentTaskStore(string dbPath)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(dbPath)!);
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            ForeignKeys = true,
            // Pooling off, deliberately. Every call site here opens a connection,
            // uses it, and disposes it; with pooling ON, "disposed" means
            // "returned to a pool that keeps the file handle open", so the
            // database stays locked for an unbounded time after the last logical
            // operation finished. That is invisible until something needs to
            // MOVE or DELETE the file — schema-drift recovery, a portable
            // install, an uninstall — and then it fails for no reason the caller
            // can see. Opening a local SQLite file is cheap; an unowned handle is
            // not. See SranDesktopStorePooling in the Phase 4 report.
            Pooling = false
        }.ToString();
    }

    public async Task InitializeAsync(CancellationToken ct = default)
    {
        if (_initialized) return;
        await _initLock.WaitAsync(ct);
        try
        {
            if (_initialized) return;
            await using var conn = await OpenAsync(ct);
            await SqliteMigrator.MigrateAsync(conn, SqliteConversationStore.Migrations, ct: ct);
            _initialized = true;
        }
        finally
        {
            _initLock.Release();
        }
    }

    public async Task CreateAsync(
        GoalSpec goal,
        AgentTask task,
        TaskPlan plan,
        TaskCheckpoint checkpoint,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);
        await SaveGoalCoreAsync(conn, tx, task.ConversationId, goal, ct);
        await UpsertTaskCoreAsync(conn, tx, task, ct);
        await SavePlanCoreAsync(conn, tx, plan, ct);
        await SaveCheckpointCoreAsync(conn, tx, checkpoint, ct);
        await tx.CommitAsync(ct);
    }

    public async Task<DurableTaskSnapshot?> GetActiveByConversationAsync(
        string conversationId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT task_id FROM agent_tasks
            WHERE conversation_id = $conversation
              AND status NOT IN ('Completed','Failed','Cancelled','Abandoned')
            ORDER BY updated_at DESC LIMIT 1
            """;
        cmd.Parameters.AddWithValue("$conversation", conversationId);
        var taskId = await cmd.ExecuteScalarAsync(ct) as string;
        return taskId is null ? null : await LoadSnapshotAsync(conn, taskId, ct);
    }

    public async Task<DurableTaskSnapshot?> GetLatestByConversationAsync(
        string conversationId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT task_id FROM agent_tasks
            WHERE conversation_id = $conversation
            ORDER BY updated_at DESC LIMIT 1
            """;
        cmd.Parameters.AddWithValue("$conversation", conversationId);
        var taskId = await cmd.ExecuteScalarAsync(ct) as string;
        return taskId is null ? null : await LoadSnapshotAsync(conn, taskId, ct);
    }

    public async Task<DurableTaskSnapshot?> GetAsync(string taskId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        return await LoadSnapshotAsync(conn, taskId, ct);
    }

    public async Task<IReadOnlyList<DurableTaskSnapshot>> GetIncompleteAsync(
        string? ownerScope = null,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT task_id FROM agent_tasks
            WHERE status NOT IN ('Completed','Failed','Cancelled','Abandoned')
              AND ($owner IS NULL OR owner_scope=$owner)
            ORDER BY created_at, task_id
            """;
        cmd.Parameters.AddWithValue("$owner", (object?)ownerScope ?? DBNull.Value);
        var ids = new List<string>();
        await using (var reader = await cmd.ExecuteReaderAsync(ct))
            while (await reader.ReadAsync(ct))
                ids.Add(reader.GetString(0));

        var result = new List<DurableTaskSnapshot>(ids.Count);
        foreach (var id in ids)
            if (await LoadSnapshotAsync(conn, id, ct) is { } snapshot)
                result.Add(snapshot);
        return result;
    }

    public async Task<int> CountChildrenAsync(
        string parentRunId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT COUNT(*) FROM agent_tasks WHERE parent_run_id=$parent";
        cmd.Parameters.AddWithValue("$parent", parentRunId);
        return Convert.ToInt32(await cmd.ExecuteScalarAsync(ct), CultureInfo.InvariantCulture);
    }

    public async Task<bool> UpdateTaskAsync(
        AgentTask task,
        int expectedVersion,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        var nextVersion = expectedVersion + 1;
        task.Version = nextVersion;
        task.UpdatedAtUtc = DateTimeOffset.UtcNow;
        await using var conn = await OpenAsync(ct);
        await EnsureTransitionAsync(conn, null, task.TaskId, task.Status, ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            UPDATE agent_tasks SET
                status=$status, current_plan_version=$plan_version,
                current_milestone_id=$milestone, current_step_id=$step,
                task_json=$json, version=$next_version,
                checkpoint_version=$checkpoint_version, updated_at=$updated,
                completed_at=$completed,parent_run_id=$parent_run,trigger_type=$trigger,
                security_context_json=$security_context,
                permission_context_json=$permission_context,
                workspace_context_json=$workspace_context,model_context_json=$model_context,
                cancellation_requested_at=$cancellation_requested,
                failure_summary=$failure_summary,final_result_reference=$final_result
            WHERE task_id=$id AND version=$expected_version
            """;
        BindTask(cmd, task);
        cmd.Parameters.AddWithValue("$next_version", nextVersion);
        cmd.Parameters.AddWithValue("$expected_version", expectedVersion);
        var changed = await cmd.ExecuteNonQueryAsync(ct) == 1;
        if (!changed) task.Version = expectedVersion;
        return changed;
    }

    public async Task SaveGoalAsync(GoalSpec goal, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        var conversationId = await ScalarStringAsync(
            conn, "SELECT conversation_id FROM goals WHERE goal_id=$id", "$id", goal.GoalId, ct)
            ?? throw new InvalidOperationException($"Goal '{goal.GoalId}' does not exist.");
        await SaveGoalCoreAsync(conn, null, conversationId, goal, ct);
    }

    public async Task SavePlanAsync(TaskPlan plan, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);
        await SavePlanCoreAsync(conn, tx, plan, ct);
        await tx.CommitAsync(ct);
    }

    public async Task SaveStepAsync(TaskStep step, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            UPDATE task_steps SET status=$status, side_effect_class=$side_effect,
                attempt_count=$attempts, failure_code=$failure, step_json=$json,
                parent_step_id=$parent_step,step_type=$step_type,tool_name=$tool_name,
                arguments_reference=$arguments_reference,idempotency_key=$step_idempotency,
                started_at=$started,completed_at=$step_completed,
                observation_ids_json=$observation_ids,
                verification_disposition=$verification_disposition,
                tool_outcome=$tool_outcome,failure_classification=$failure_classification
            WHERE step_id=$id
            """;
        BindStep(cmd, step);
        if (await cmd.ExecuteNonQueryAsync(ct) != 1)
            throw new InvalidOperationException($"Step '{step.StepId}' does not exist.");
    }

    public async Task SaveCheckpointAsync(TaskCheckpoint checkpoint, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await SaveCheckpointCoreAsync(conn, null, checkpoint, ct);
    }

    public async Task<TaskCheckpoint?> GetLatestCheckpointAsync(
        string taskId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        return await LoadLatestCheckpointAsync(conn, taskId, ct);
    }

    public async Task<bool> TryCreateActionAsync(TaskAction action, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO task_actions
                (action_id,task_id,plan_id,milestone_id,step_id,tool_call_id,tool_name,
                 arguments_fingerprint,idempotency_key,side_effect_class,status,attempt,
                 failure_code,failure_reason,action_json,created_at,updated_at)
            VALUES
                ($id,$task,$plan,$milestone,$step,$tool_call,$tool,$fingerprint,$key,
                 $side_effect,$status,$attempt,$failure_code,$failure_reason,$json,$created,$updated)
            """;
        BindAction(cmd, action);
        try
        {
            return await cmd.ExecuteNonQueryAsync(ct) == 1;
        }
        catch (SqliteException ex) when (ex.SqliteErrorCode == 19)
        {
            return false;
        }
    }

    public async Task<bool> CommitActionPreparationAsync(
        AgentTask task,
        int expectedTaskVersion,
        TaskStep step,
        TaskAction action,
        TaskCheckpoint checkpoint,
        TaskEvent taskEvent,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);
        try
        {
            await InsertActionCoreAsync(conn, tx, action, ct);
            await SaveStepCoreAsync(conn, tx, step, ct);
            if (!await UpdateTaskCoreAsync(conn, tx, task, expectedTaskVersion, ct))
            {
                await tx.RollbackAsync(ct);
                return false;
            }
            await SaveCheckpointCoreAsync(conn, tx, checkpoint, ct);
            await AppendEventCoreAsync(conn, tx, taskEvent, ct);
            await tx.CommitAsync(ct);
            return true;
        }
        catch (SqliteException ex) when (ex.SqliteErrorCode == 19)
        {
            await tx.RollbackAsync(ct);
            return false;
        }
    }

    public async Task<bool> CommitActionOutcomeAsync(
        AgentTask task,
        int expectedTaskVersion,
        TaskPlan plan,
        TaskStep step,
        TaskAction action,
        TaskEvidence evidence,
        WorldStateSnapshot worldState,
        TaskCheckpoint checkpoint,
        IReadOnlyList<TaskEvent> events,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);
        await UpdateActionCoreAsync(conn, tx, action, ct);
        await SavePlanCoreAsync(conn, tx, plan, ct);
        await SaveStepCoreAsync(conn, tx, step, ct);
        if (!await UpdateTaskCoreAsync(conn, tx, task, expectedTaskVersion, ct))
        {
            await tx.RollbackAsync(ct);
            return false;
        }
        await AddEvidenceCoreAsync(conn, tx, evidence, ct);
        await SaveWorldStateCoreAsync(conn, tx, worldState, ct);
        await SaveCheckpointCoreAsync(conn, tx, checkpoint, ct);
        foreach (var taskEvent in events)
            await AppendEventCoreAsync(conn, tx, taskEvent, ct);
        await tx.CommitAsync(ct);
        return true;
    }

    public async Task<TaskAction?> GetActionByIdempotencyKeyAsync(
        string taskId,
        string idempotencyKey,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT action_json FROM task_actions
            WHERE task_id=$task AND idempotency_key=$key
            """;
        cmd.Parameters.AddWithValue("$task", taskId);
        cmd.Parameters.AddWithValue("$key", idempotencyKey);
        return Deserialize<TaskAction>(await cmd.ExecuteScalarAsync(ct) as string);
    }

    public async Task<TaskAction?> GetActionAsync(string actionId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        return await LoadJsonAsync<TaskAction>(
            conn, "SELECT action_json FROM task_actions WHERE action_id=$id", actionId, ct);
    }

    public async Task<TaskAction?> GetLatestActionForStepAsync(
        string taskId,
        string stepId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT action_json FROM task_actions
            WHERE task_id=$task AND step_id=$step
            ORDER BY created_at DESC LIMIT 1
            """;
        cmd.Parameters.AddWithValue("$task", taskId);
        cmd.Parameters.AddWithValue("$step", stepId);
        return Deserialize<TaskAction>(await cmd.ExecuteScalarAsync(ct) as string);
    }

    public async Task<IReadOnlyList<TaskAction>> GetActionsForStepAsync(
        string taskId,
        string stepId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT action_json FROM task_actions
            WHERE task_id=$task AND step_id=$step
            ORDER BY created_at
            """;
        cmd.Parameters.AddWithValue("$task", taskId);
        cmd.Parameters.AddWithValue("$step", stepId);
        var result = new List<TaskAction>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            if (Deserialize<TaskAction>(reader.GetString(0)) is { } action)
                result.Add(action);
        }
        return result;
    }

    public async Task UpdateActionAsync(TaskAction action, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            UPDATE task_actions SET status=$status, failure_code=$failure_code,
                failure_reason=$failure_reason, action_json=$json, updated_at=$updated
            WHERE action_id=$id
            """;
        BindAction(cmd, action);
        if (await cmd.ExecuteNonQueryAsync(ct) != 1)
            throw new InvalidOperationException($"Action '{action.ActionId}' does not exist.");
    }

    public async Task SaveApprovalAsync(TaskApproval approval, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO task_approvals
                (approval_id,task_id,step_id,action_fingerprint,scope,status,
                 expires_at,approval_json,created_at)
            VALUES
                ($id,$task,$step,$fingerprint,$scope,$status,$expires,$json,$created)
            """;
        cmd.Parameters.AddWithValue("$id", approval.ApprovalId);
        cmd.Parameters.AddWithValue("$task", approval.TaskId);
        cmd.Parameters.AddWithValue("$step", approval.StepId);
        cmd.Parameters.AddWithValue("$fingerprint", approval.ActionFingerprint);
        cmd.Parameters.AddWithValue("$scope", SensitiveDataRedactor.Redact(approval.Scope));
        cmd.Parameters.AddWithValue("$status", approval.Status);
        cmd.Parameters.AddWithValue("$expires", Iso(approval.ExpiresAtUtc));
        cmd.Parameters.AddWithValue("$json", Serialize(approval));
        cmd.Parameters.AddWithValue("$created", Iso(approval.CreatedAtUtc));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<TaskApproval?> GetValidApprovalAsync(
        string taskId,
        string stepId,
        string actionFingerprint,
        DateTimeOffset atUtc,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT approval_json FROM task_approvals
            WHERE task_id=$task AND step_id=$step
              AND action_fingerprint=$fingerprint
              AND status='Granted' AND expires_at >= $at
            ORDER BY created_at DESC LIMIT 1
            """;
        cmd.Parameters.AddWithValue("$task", taskId);
        cmd.Parameters.AddWithValue("$step", stepId);
        cmd.Parameters.AddWithValue("$fingerprint", actionFingerprint);
        cmd.Parameters.AddWithValue("$at", Iso(atUtc));
        return Deserialize<TaskApproval>(await cmd.ExecuteScalarAsync(ct) as string);
    }

    public async Task<bool> HasGrantedApprovalAsync(
        string taskId,
        DateTimeOffset atUtc,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT COUNT(*) FROM task_approvals
            WHERE task_id=$task AND status='Granted' AND expires_at >= $at
            """;
        cmd.Parameters.AddWithValue("$task", taskId);
        cmd.Parameters.AddWithValue("$at", Iso(atUtc));
        return Convert.ToInt64(await cmd.ExecuteScalarAsync(ct), CultureInfo.InvariantCulture) > 0;
    }

    public async Task AddEvidenceAsync(TaskEvidence evidence, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO task_evidence
                (evidence_id,task_id,outcome_id,step_id,action_id,evidence_type,source,summary,
                 structured_data,artifact_reference,confidence,sensitivity,retention_policy,
                 trust_level,provenance_json,allowed_uses_json,prohibited_uses_json,created_at)
            VALUES
                ($id,$task,$outcome,$step,$action,$type,$source,$summary,$data,$artifact,
                 $confidence,$sensitivity,$retention,$trust,$provenance,$allowed,$prohibited,$created)
            """;
        cmd.Parameters.AddWithValue("$id", evidence.EvidenceId);
        cmd.Parameters.AddWithValue("$task", evidence.TaskId);
        cmd.Parameters.AddWithValue("$outcome", Db(evidence.OutcomeId));
        cmd.Parameters.AddWithValue("$step", Db(evidence.StepId));
        cmd.Parameters.AddWithValue("$action", Db(evidence.ActionId));
        cmd.Parameters.AddWithValue("$type", evidence.EvidenceType.ToString());
        cmd.Parameters.AddWithValue("$source", SensitiveDataRedactor.Redact(evidence.Source));
        cmd.Parameters.AddWithValue("$summary", SensitiveDataRedactor.Redact(evidence.Summary));
        cmd.Parameters.AddWithValue("$data", SensitiveDataRedactor.RedactJson(evidence.StructuredData));
        cmd.Parameters.AddWithValue(
            "$artifact",
            Db(evidence.ArtifactReference is null
                ? null
                : SensitiveDataRedactor.Redact(evidence.ArtifactReference)));
        cmd.Parameters.AddWithValue("$confidence", evidence.Confidence);
        cmd.Parameters.AddWithValue("$sensitivity", evidence.SensitiveDataClassification.ToString());
        cmd.Parameters.AddWithValue("$retention", evidence.RetentionPolicy);
        cmd.Parameters.AddWithValue("$trust", evidence.TrustLevel.ToString());
        cmd.Parameters.AddWithValue(
            "$provenance",
            SensitiveDataRedactor.RedactJson(evidence.ProvenanceJson));
        cmd.Parameters.AddWithValue("$allowed", Serialize(evidence.AllowedUses));
        cmd.Parameters.AddWithValue("$prohibited", Serialize(evidence.ProhibitedUses));
        cmd.Parameters.AddWithValue("$created", Iso(evidence.Timestamp));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task SaveVerificationAsync(
        TaskVerificationResult verification,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO task_verifications
                (verification_id,task_id,step_id,action_id,disposition,expected_state,
                 actual_state_summary,evidence_ids_json,suggested_recovery_action,
                 verification_json,created_at)
            VALUES
                ($id,$task,$step,$action,$disposition,$expected,$actual,$evidence,
                 $recovery,$json,$created)
            """;
        cmd.Parameters.AddWithValue("$id", verification.VerificationId);
        cmd.Parameters.AddWithValue("$task", verification.TaskId);
        cmd.Parameters.AddWithValue("$step", verification.StepId);
        cmd.Parameters.AddWithValue("$action", Db(verification.ActionId));
        cmd.Parameters.AddWithValue("$disposition", verification.Disposition.ToString());
        cmd.Parameters.AddWithValue(
            "$expected",
            SensitiveDataRedactor.Redact(verification.ExpectedState));
        cmd.Parameters.AddWithValue(
            "$actual",
            SensitiveDataRedactor.Redact(verification.ActualStateSummary));
        cmd.Parameters.AddWithValue("$evidence", Serialize(verification.EvidenceIds));
        cmd.Parameters.AddWithValue(
            "$recovery",
            Db(verification.SuggestedRecoveryAction is null
                ? null
                : SensitiveDataRedactor.Redact(verification.SuggestedRecoveryAction)));
        cmd.Parameters.AddWithValue("$json", Serialize(verification));
        cmd.Parameters.AddWithValue("$created", Iso(verification.CreatedAtUtc));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<IReadOnlyList<TaskEvidence>> GetEvidenceAsync(
        string taskId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT evidence_type,source,summary,structured_data,artifact_reference,
                   confidence,sensitivity,retention_policy,created_at,evidence_id,
                   step_id,action_id,outcome_id,trust_level,provenance_json,
                   allowed_uses_json,prohibited_uses_json
            FROM task_evidence WHERE task_id=$task ORDER BY created_at
            """;
        cmd.Parameters.AddWithValue("$task", taskId);
        var result = new List<TaskEvidence>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(new TaskEvidence
            {
                EvidenceId = reader.GetString(9),
                TaskId = taskId,
                StepId = reader.IsDBNull(10) ? null : reader.GetString(10),
                ActionId = reader.IsDBNull(11) ? null : reader.GetString(11),
                OutcomeId = reader.IsDBNull(12) ? null : reader.GetString(12),
                EvidenceType = Enum.Parse<EvidenceType>(reader.GetString(0)),
                Source = reader.GetString(1),
                Summary = reader.GetString(2),
                StructuredData = reader.GetString(3),
                ArtifactReference = reader.IsDBNull(4) ? null : reader.GetString(4),
                Confidence = reader.GetDouble(5),
                SensitiveDataClassification =
                    Enum.Parse<SensitiveDataClassification>(reader.GetString(6)),
                RetentionPolicy = reader.GetString(7),
                Timestamp = DateTimeOffset.Parse(
                    reader.GetString(8), CultureInfo.InvariantCulture),
                TrustLevel = Enum.Parse<ObservationTrust>(reader.GetString(13)),
                ProvenanceJson = reader.GetString(14),
                AllowedUses = Deserialize<List<string>>(reader.GetString(15)) ?? [],
                ProhibitedUses = Deserialize<List<string>>(reader.GetString(16)) ?? []
            });
        }
        return result;
    }

    public async Task SaveWorldStateAsync(WorldStateSnapshot snapshot, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO world_state_snapshots
                (world_state_id,task_id,version,source,confidence,observed_at,expires_at,snapshot_json)
            VALUES ($id,$task,$version,$source,$confidence,$observed,$expires,$json)
            """;
        cmd.Parameters.AddWithValue("$id", snapshot.WorldStateId);
        cmd.Parameters.AddWithValue("$task", snapshot.TaskId);
        cmd.Parameters.AddWithValue("$version", snapshot.Version);
        cmd.Parameters.AddWithValue("$source", SensitiveDataRedactor.Redact(snapshot.Source));
        cmd.Parameters.AddWithValue("$confidence", snapshot.Confidence);
        cmd.Parameters.AddWithValue("$observed", Iso(snapshot.ObservedAtUtc));
        cmd.Parameters.AddWithValue("$expires", Db(snapshot.ExpiresAtUtc is null ? null : Iso(snapshot.ExpiresAtUtc.Value)));
        cmd.Parameters.AddWithValue("$json", Serialize(snapshot));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task AppendEventAsync(TaskEvent taskEvent, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);
        taskEvent.Sequence = await NextEventSequenceAsync(conn, tx, taskEvent.TaskId, ct);
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT INTO task_events
                (event_id,task_id,event_type,plan_id,milestone_id,step_id,action_id,
                 outcome_id,parent_action_id,retry_id,tool_call_id,model_request_id,capture_id,
                 checkpoint_id,world_state_version,sequence_no,event_json,created_at)
            VALUES
                ($id,$task,$type,$plan,$milestone,$step,$action,$outcome,$parent_action,$retry,
                 $tool_call,$model_request,$capture,$checkpoint,$world_version,$sequence,$json,$created)
            """;
        BindEvent(cmd, taskEvent);
        await cmd.ExecuteNonQueryAsync(ct);
        await tx.CommitAsync(ct);
    }

    public async Task<IReadOnlyList<TaskEvent>> GetEventsAsync(
        string taskId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT event_json FROM task_events WHERE task_id=$task ORDER BY sequence_no,event_id";
        cmd.Parameters.AddWithValue("$task", taskId);
        var result = new List<TaskEvent>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
            if (Deserialize<TaskEvent>(reader.GetString(0)) is { } item) result.Add(item);
        return result;
    }

    public async Task SaveCapabilityHealthAsync(
        CapabilityHealth capability,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO capability_health
                (capability_id,provider,availability,health,capability_json,updated_at)
            VALUES ($id,$provider,$availability,$health,$json,$updated)
            ON CONFLICT(capability_id) DO UPDATE SET
                provider=excluded.provider,
                availability=excluded.availability,
                health=excluded.health,
                capability_json=excluded.capability_json,
                updated_at=excluded.updated_at
            """;
        cmd.Parameters.AddWithValue("$id", capability.CapabilityId);
        cmd.Parameters.AddWithValue("$provider", capability.Provider);
        cmd.Parameters.AddWithValue("$availability", capability.Availability.ToString());
        cmd.Parameters.AddWithValue("$health", capability.Health.ToString());
        cmd.Parameters.AddWithValue("$json", Serialize(capability));
        cmd.Parameters.AddWithValue("$updated", Iso(capability.UpdatedAtUtc));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<IReadOnlyList<CapabilityHealth>> GetCapabilityHealthAsync(
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT capability_json FROM capability_health ORDER BY capability_id";
        var result = new List<CapabilityHealth>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
            if (Deserialize<CapabilityHealth>(reader.GetString(0)) is { } capability)
                result.Add(capability);
        return result;
    }

    public async Task SaveMetricAsync(
        DurableMetric metric,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO task_metrics
                (metric_id,task_id,skill_id,name,value,dimensions_json,created_at)
            VALUES ($id,$task,$skill,$name,$value,$dimensions,$created)
            """;
        cmd.Parameters.AddWithValue("$id", metric.MetricId);
        cmd.Parameters.AddWithValue("$task", metric.TaskId);
        cmd.Parameters.AddWithValue("$skill", Db(metric.SkillId));
        cmd.Parameters.AddWithValue("$name", metric.Name);
        cmd.Parameters.AddWithValue("$value", metric.Value);
        cmd.Parameters.AddWithValue(
            "$dimensions",
            SensitiveDataRedactor.RedactJson(metric.DimensionsJson));
        cmd.Parameters.AddWithValue("$created", Iso(metric.TimestampUtc));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<IReadOnlyList<DurableMetric>> GetMetricsAsync(
        string taskId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT metric_id,skill_id,name,value,dimensions_json,created_at
            FROM task_metrics WHERE task_id=$task ORDER BY created_at
            """;
        cmd.Parameters.AddWithValue("$task", taskId);
        var result = new List<DurableMetric>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
            result.Add(new DurableMetric
            {
                MetricId = reader.GetString(0),
                TaskId = taskId,
                SkillId = reader.IsDBNull(1) ? null : reader.GetString(1),
                Name = reader.GetString(2),
                Value = reader.GetDouble(3),
                DimensionsJson = reader.GetString(4),
                TimestampUtc = DateTimeOffset.Parse(
                    reader.GetString(5), CultureInfo.InvariantCulture)
            });
        return result;
    }

    public async Task SaveCompensationAsync(
        CompensationLedgerEntry entry,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO compensation_ledger
                (compensation_id,task_id,step_id,action_id,target,reversible,status,
                 compensation_json,updated_at)
            VALUES ($id,$task,$step,$action,$target,$reversible,$status,$json,$updated)
            ON CONFLICT(compensation_id) DO UPDATE SET
                status=excluded.status,
                compensation_json=excluded.compensation_json,
                updated_at=excluded.updated_at
            """;
        cmd.Parameters.AddWithValue("$id", entry.CompensationId);
        cmd.Parameters.AddWithValue("$task", entry.TaskId);
        cmd.Parameters.AddWithValue("$step", entry.StepId);
        cmd.Parameters.AddWithValue("$action", entry.ActionId);
        cmd.Parameters.AddWithValue("$target", SensitiveDataRedactor.Redact(entry.Target));
        cmd.Parameters.AddWithValue("$reversible", entry.Reversible ? 1 : 0);
        cmd.Parameters.AddWithValue("$status", entry.Status.ToString());
        cmd.Parameters.AddWithValue("$json", Serialize(entry));
        cmd.Parameters.AddWithValue("$updated", Iso(entry.UpdatedAtUtc));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<IReadOnlyList<CompensationLedgerEntry>> GetCompensationsAsync(
        string taskId,
        CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT compensation_json FROM compensation_ledger
            WHERE task_id=$task ORDER BY updated_at
            """;
        cmd.Parameters.AddWithValue("$task", taskId);
        var result = new List<CompensationLedgerEntry>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
            if (Deserialize<CompensationLedgerEntry>(reader.GetString(0)) is { } entry)
                result.Add(entry);
        return result;
    }

    private async Task<DurableTaskSnapshot?> LoadSnapshotAsync(
        SqliteConnection conn,
        string taskId,
        CancellationToken ct)
    {
        var task = await LoadJsonAsync<AgentTask>(
            conn, "SELECT task_json FROM agent_tasks WHERE task_id=$id", taskId, ct);
        if (task is null) return null;
        var goal = await LoadJsonAsync<GoalSpec>(
            conn, "SELECT goal_json FROM goals WHERE goal_id=$id", task.GoalId, ct)
            ?? throw new InvalidDataException($"Task '{taskId}' references a missing goal.");
        var plan = await LoadJsonAsync<TaskPlan>(
            conn,
            "SELECT plan_json FROM task_plans WHERE task_id=$id ORDER BY version DESC LIMIT 1",
            taskId,
            ct) ?? throw new InvalidDataException($"Task '{taskId}' has no plan.");

        await HydrateStepsAsync(conn, plan, ct);
        var checkpoint = await LoadLatestCheckpointAsync(conn, taskId, ct);
        var world = await LoadJsonAsync<WorldStateSnapshot>(
            conn,
            "SELECT snapshot_json FROM world_state_snapshots WHERE task_id=$id ORDER BY version DESC LIMIT 1",
            taskId,
            ct);
        return new DurableTaskSnapshot(goal, task, plan, checkpoint, world);
    }

    private static async Task HydrateStepsAsync(
        SqliteConnection conn,
        TaskPlan plan,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT step_json FROM task_steps WHERE task_id=$task";
        cmd.Parameters.AddWithValue("$task", plan.TaskId);
        var byId = new Dictionary<string, TaskStep>(StringComparer.Ordinal);
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
            if (Deserialize<TaskStep>(reader.GetString(0)) is { } step) byId[step.StepId] = step;

        foreach (var milestone in plan.Milestones)
        {
            for (var i = 0; i < milestone.Steps.Count; i++)
                if (byId.TryGetValue(milestone.Steps[i].StepId, out var current))
                    milestone.Steps[i] = current;
        }
    }

    private static async Task SaveGoalCoreAsync(
        SqliteConnection conn,
        SqliteTransaction? tx,
        string conversationId,
        GoalSpec goal,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT INTO goals
                (goal_id,conversation_id,original_request,normalized_goal,desired_outcome,
                 risk_level,version,goal_json,created_at,updated_at)
            VALUES ($id,$conversation,$request,$goal,$outcome,$risk,$version,$json,$created,$updated)
            ON CONFLICT(goal_id) DO UPDATE SET
                normalized_goal=excluded.normalized_goal, desired_outcome=excluded.desired_outcome,
                risk_level=excluded.risk_level, version=excluded.version,
                goal_json=excluded.goal_json, updated_at=excluded.updated_at
            """;
        cmd.Parameters.AddWithValue("$id", goal.GoalId);
        cmd.Parameters.AddWithValue("$conversation", conversationId);
        cmd.Parameters.AddWithValue("$request", SensitiveDataRedactor.Redact(goal.OriginalUserRequest));
        cmd.Parameters.AddWithValue("$goal", SensitiveDataRedactor.Redact(goal.NormalizedGoal));
        cmd.Parameters.AddWithValue("$outcome", SensitiveDataRedactor.Redact(goal.DesiredOutcome));
        cmd.Parameters.AddWithValue("$risk", goal.RiskLevel.ToString());
        cmd.Parameters.AddWithValue("$version", goal.Version);
        cmd.Parameters.AddWithValue("$json", Serialize(goal));
        cmd.Parameters.AddWithValue("$created", Iso(goal.CreatedAtUtc));
        cmd.Parameters.AddWithValue("$updated", Iso(goal.UpdatedAtUtc));
        await cmd.ExecuteNonQueryAsync(ct);

        foreach (var item in goal.DefinitionOfDone)
        {
            await using var dod = conn.CreateCommand();
            dod.Transaction = tx;
            dod.CommandText = """
                INSERT INTO definition_of_done_items
                    (item_id,goal_id,description,verification_type,required_evidence,
                     required,status,verification_result,evidence_ids_json)
                VALUES ($id,$goal,$description,$type,$required_evidence,$required,
                        $status,$result,$evidence)
                ON CONFLICT(item_id) DO UPDATE SET
                    description=excluded.description, verification_type=excluded.verification_type,
                    required_evidence=excluded.required_evidence, required=excluded.required,
                    status=excluded.status, verification_result=excluded.verification_result,
                    evidence_ids_json=excluded.evidence_ids_json
                """;
            dod.Parameters.AddWithValue("$id", item.Id);
            dod.Parameters.AddWithValue("$goal", goal.GoalId);
            dod.Parameters.AddWithValue("$description", SensitiveDataRedactor.Redact(item.Description));
            dod.Parameters.AddWithValue("$type", SensitiveDataRedactor.Redact(item.VerificationType));
            dod.Parameters.AddWithValue(
                "$required_evidence",
                SensitiveDataRedactor.Redact(item.RequiredEvidence));
            dod.Parameters.AddWithValue("$required", item.Required ? 1 : 0);
            dod.Parameters.AddWithValue("$status", item.Status.ToString());
            dod.Parameters.AddWithValue(
                "$result",
                Db(item.VerificationResult is null
                    ? null
                    : SensitiveDataRedactor.Redact(item.VerificationResult)));
            dod.Parameters.AddWithValue("$evidence", Serialize(item.EvidenceIds));
            await dod.ExecuteNonQueryAsync(ct);
        }
    }

    private static async Task UpsertTaskCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        AgentTask task,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT INTO agent_tasks
                (task_id,conversation_id,goal_id,owner_scope,mode,status,current_plan_version,
                 current_milestone_id,current_step_id,task_json,version,checkpoint_version,
                 created_at,updated_at,completed_at,parent_run_id,trigger_type,
                 security_context_json,permission_context_json,workspace_context_json,
                 model_context_json,cancellation_requested_at,failure_summary,final_result_reference)
            VALUES
                ($id,$conversation,$goal,$owner,$mode,$status,$plan_version,$milestone,$step,
                 $json,$version,$checkpoint_version,$created,$updated,$completed,$parent_run,$trigger,
                 $security_context,$permission_context,$workspace_context,$model_context,
                 $cancellation_requested,$failure_summary,$final_result)
            """;
        BindTask(cmd, task);
        cmd.Parameters.AddWithValue("$conversation", task.ConversationId);
        cmd.Parameters.AddWithValue("$goal", task.GoalId);
        cmd.Parameters.AddWithValue("$owner", task.OwnerScope);
        cmd.Parameters.AddWithValue("$mode", task.Mode.ToString());
        cmd.Parameters.AddWithValue("$version", task.Version);
        cmd.Parameters.AddWithValue("$created", Iso(task.CreatedAtUtc));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private static async Task SavePlanCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        TaskPlan plan,
        CancellationToken ct)
    {
        await using (var cmd = conn.CreateCommand())
        {
            cmd.Transaction = tx;
            cmd.CommandText = """
                INSERT INTO task_plans
                    (plan_id,task_id,version,goal_version,status,planning_reason,
                     supersedes_plan_id,created_by,created_at,plan_json)
                VALUES ($id,$task,$version,$goal_version,$status,$reason,$supersedes,$created_by,$created,$json)
                ON CONFLICT(plan_id) DO UPDATE SET status=excluded.status, plan_json=excluded.plan_json
                """;
            cmd.Parameters.AddWithValue("$id", plan.PlanId);
            cmd.Parameters.AddWithValue("$task", plan.TaskId);
            cmd.Parameters.AddWithValue("$version", plan.Version);
            cmd.Parameters.AddWithValue("$goal_version", plan.GoalVersion);
            cmd.Parameters.AddWithValue("$status", plan.Status.ToString());
            cmd.Parameters.AddWithValue("$reason", SensitiveDataRedactor.Redact(plan.PlanningReason));
            cmd.Parameters.AddWithValue("$supersedes", Db(plan.SupersedesPlanId));
            cmd.Parameters.AddWithValue("$created_by", SensitiveDataRedactor.Redact(plan.CreatedBy));
            cmd.Parameters.AddWithValue("$created", Iso(plan.CreatedAtUtc));
            cmd.Parameters.AddWithValue("$json", Serialize(plan));
            await cmd.ExecuteNonQueryAsync(ct);
        }

        foreach (var outcome in plan.Outcomes)
        {
            await using var outcomeCmd = conn.CreateCommand();
            outcomeCmd.Transaction = tx;
            outcomeCmd.CommandText = """
                INSERT INTO task_outcomes
                    (outcome_id,task_id,plan_id,parent_outcome_id,kind,required,prohibited,
                     status,source_goal_version,outcome_json)
                VALUES
                    ($id,$task,$plan,$parent,$kind,$required,$prohibited,$status,$goal_version,$json)
                ON CONFLICT(outcome_id) DO UPDATE SET
                    status=excluded.status,outcome_json=excluded.outcome_json
                """;
            outcomeCmd.Parameters.AddWithValue("$id", outcome.OutcomeId);
            outcomeCmd.Parameters.AddWithValue("$task", outcome.TaskId);
            outcomeCmd.Parameters.AddWithValue("$plan", plan.PlanId);
            outcomeCmd.Parameters.AddWithValue("$parent", Db(outcome.ParentOutcomeId));
            outcomeCmd.Parameters.AddWithValue("$kind", outcome.Kind.ToString());
            outcomeCmd.Parameters.AddWithValue("$required", outcome.Required ? 1 : 0);
            outcomeCmd.Parameters.AddWithValue("$prohibited", outcome.Prohibited ? 1 : 0);
            outcomeCmd.Parameters.AddWithValue("$status", outcome.Status.ToString());
            outcomeCmd.Parameters.AddWithValue("$goal_version", outcome.SourceGoalVersion);
            outcomeCmd.Parameters.AddWithValue("$json", Serialize(outcome));
            await outcomeCmd.ExecuteNonQueryAsync(ct);
        }

        foreach (var milestone in plan.Milestones)
        {
            await using var cmd = conn.CreateCommand();
            cmd.Transaction = tx;
            cmd.CommandText = """
                INSERT INTO task_milestones
                    (milestone_id,task_id,plan_id,sequence,title,objective,status,milestone_json)
                VALUES ($id,$task,$plan,$sequence,$title,$objective,$status,$json)
                ON CONFLICT(milestone_id) DO UPDATE SET status=excluded.status,milestone_json=excluded.milestone_json
                """;
            cmd.Parameters.AddWithValue("$id", milestone.MilestoneId);
            cmd.Parameters.AddWithValue("$task", milestone.TaskId);
            cmd.Parameters.AddWithValue("$plan", milestone.PlanId);
            cmd.Parameters.AddWithValue("$sequence", milestone.Sequence);
            cmd.Parameters.AddWithValue("$title", SensitiveDataRedactor.Redact(milestone.Title));
            cmd.Parameters.AddWithValue("$objective", SensitiveDataRedactor.Redact(milestone.Objective));
            cmd.Parameters.AddWithValue("$status", milestone.Status.ToString());
            cmd.Parameters.AddWithValue("$json", Serialize(milestone));
            await cmd.ExecuteNonQueryAsync(ct);

            foreach (var step in milestone.Steps)
            {
                await using var stepCmd = conn.CreateCommand();
                stepCmd.Transaction = tx;
                stepCmd.CommandText = """
                    INSERT INTO task_steps
                        (step_id,task_id,milestone_id,status,side_effect_class,
                         attempt_count,failure_code,step_json,parent_step_id,step_type,
                         tool_name,arguments_reference,idempotency_key,started_at,completed_at,
                         observation_ids_json,verification_disposition,tool_outcome,
                         failure_classification)
                    VALUES ($id,$task,$milestone,$status,$side_effect,$attempts,$failure,$json,
                         $parent_step,$step_type,$tool_name,$arguments_reference,$step_idempotency,
                         $started,$step_completed,$observation_ids,$verification_disposition,
                         $tool_outcome,$failure_classification)
                    ON CONFLICT(step_id) DO UPDATE SET
                        status=excluded.status,side_effect_class=excluded.side_effect_class,
                        attempt_count=excluded.attempt_count,failure_code=excluded.failure_code,
                        step_json=excluded.step_json,parent_step_id=excluded.parent_step_id,
                        step_type=excluded.step_type,tool_name=excluded.tool_name,
                        arguments_reference=excluded.arguments_reference,
                        idempotency_key=excluded.idempotency_key,started_at=excluded.started_at,
                        completed_at=excluded.completed_at,
                        observation_ids_json=excluded.observation_ids_json,
                        verification_disposition=excluded.verification_disposition,
                        tool_outcome=excluded.tool_outcome,
                        failure_classification=excluded.failure_classification
                    """;
                BindStep(stepCmd, step);
                await stepCmd.ExecuteNonQueryAsync(ct);
            }
        }
    }

    private static async Task SaveCheckpointCoreAsync(
        SqliteConnection conn,
        SqliteTransaction? tx,
        TaskCheckpoint checkpoint,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT INTO task_checkpoints
                (checkpoint_id,task_id,version,plan_version,task_status,current_milestone_id,
                 current_step_id,pending_action_id,resume_safety_status,checksum,checkpoint_json,created_at)
            VALUES
                ($id,$task,$version,$plan_version,$status,$milestone,$step,$pending,
                 $resume,$checksum,$json,$created)
            """;
        cmd.Parameters.AddWithValue("$id", checkpoint.CheckpointId);
        cmd.Parameters.AddWithValue("$task", checkpoint.TaskId);
        cmd.Parameters.AddWithValue("$version", checkpoint.Version);
        cmd.Parameters.AddWithValue("$plan_version", checkpoint.PlanVersion);
        cmd.Parameters.AddWithValue("$status", checkpoint.TaskStatus.ToString());
        cmd.Parameters.AddWithValue("$milestone", Db(checkpoint.CurrentMilestoneId));
        cmd.Parameters.AddWithValue("$step", Db(checkpoint.CurrentStepId));
        cmd.Parameters.AddWithValue("$pending", Db(checkpoint.PendingActionId));
        cmd.Parameters.AddWithValue("$resume", checkpoint.ResumeSafetyStatus.ToString());
        cmd.Parameters.AddWithValue("$checksum", checkpoint.Checksum);
        cmd.Parameters.AddWithValue("$json", Serialize(checkpoint));
        cmd.Parameters.AddWithValue("$created", Iso(checkpoint.CreatedAtUtc));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private static async Task InsertActionCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        TaskAction action,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT INTO task_actions
                (action_id,task_id,plan_id,milestone_id,step_id,outcome_id,tool_call_id,tool_name,
                 arguments_fingerprint,idempotency_key,side_effect_class,status,attempt,
                 parent_action_id,retry_id,observation_version,recovery_strategy,
                 capability_change_reason,failure_code,failure_reason,action_json,created_at,updated_at)
            VALUES
                ($id,$task,$plan,$milestone,$step,$outcome,$tool_call,$tool,$fingerprint,$key,
                 $side_effect,$status,$attempt,$parent_action,$retry,$observation,$recovery,
                 $capability_change,$failure_code,$failure_reason,$json,$created,$updated)
            """;
        BindAction(cmd, action);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private static async Task UpdateActionCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        TaskAction action,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            UPDATE task_actions SET status=$status, failure_code=$failure_code,
                failure_reason=$failure_reason, action_json=$json, updated_at=$updated
            WHERE action_id=$id
            """;
        BindAction(cmd, action);
        if (await cmd.ExecuteNonQueryAsync(ct) != 1)
            throw new InvalidOperationException($"Action '{action.ActionId}' does not exist.");
    }

    private static async Task SaveStepCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        TaskStep step,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            UPDATE task_steps SET status=$status, side_effect_class=$side_effect,
                attempt_count=$attempts, failure_code=$failure, step_json=$json,
                parent_step_id=$parent_step,step_type=$step_type,tool_name=$tool_name,
                arguments_reference=$arguments_reference,idempotency_key=$step_idempotency,
                started_at=$started,completed_at=$step_completed,
                observation_ids_json=$observation_ids,
                verification_disposition=$verification_disposition,
                tool_outcome=$tool_outcome,failure_classification=$failure_classification
            WHERE step_id=$id
            """;
        BindStep(cmd, step);
        if (await cmd.ExecuteNonQueryAsync(ct) != 1)
            throw new InvalidOperationException($"Step '{step.StepId}' does not exist.");
    }

    private static async Task<bool> UpdateTaskCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        AgentTask task,
        int expectedVersion,
        CancellationToken ct)
    {
        var originalVersion = task.Version;
        await EnsureTransitionAsync(conn, tx, task.TaskId, task.Status, ct);
        task.Version = expectedVersion + 1;
        task.UpdatedAtUtc = DateTimeOffset.UtcNow;
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            UPDATE agent_tasks SET status=$status,current_plan_version=$plan_version,
                current_milestone_id=$milestone,current_step_id=$step,task_json=$json,
                version=$next_version,checkpoint_version=$checkpoint_version,
                updated_at=$updated,completed_at=$completed,parent_run_id=$parent_run,
                trigger_type=$trigger,security_context_json=$security_context,
                permission_context_json=$permission_context,
                workspace_context_json=$workspace_context,model_context_json=$model_context,
                cancellation_requested_at=$cancellation_requested,
                failure_summary=$failure_summary,final_result_reference=$final_result
            WHERE task_id=$id AND version=$expected_version
            """;
        BindTask(cmd, task);
        cmd.Parameters.AddWithValue("$next_version", task.Version);
        cmd.Parameters.AddWithValue("$expected_version", expectedVersion);
        var changed = await cmd.ExecuteNonQueryAsync(ct) == 1;
        if (!changed) task.Version = originalVersion;
        return changed;
    }

    private static async Task AddEvidenceCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        TaskEvidence evidence,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT INTO task_evidence
                (evidence_id,task_id,outcome_id,step_id,action_id,evidence_type,source,summary,
                 structured_data,artifact_reference,confidence,sensitivity,retention_policy,
                 trust_level,provenance_json,allowed_uses_json,prohibited_uses_json,created_at)
            VALUES
                ($id,$task,$outcome,$step,$action,$type,$source,$summary,$data,$artifact,
                 $confidence,$sensitivity,$retention,$trust,$provenance,$allowed,$prohibited,$created)
            """;
        cmd.Parameters.AddWithValue("$id", evidence.EvidenceId);
        cmd.Parameters.AddWithValue("$task", evidence.TaskId);
        cmd.Parameters.AddWithValue("$outcome", Db(evidence.OutcomeId));
        cmd.Parameters.AddWithValue("$step", Db(evidence.StepId));
        cmd.Parameters.AddWithValue("$action", Db(evidence.ActionId));
        cmd.Parameters.AddWithValue("$type", evidence.EvidenceType.ToString());
        cmd.Parameters.AddWithValue("$source", SensitiveDataRedactor.Redact(evidence.Source));
        cmd.Parameters.AddWithValue("$summary", SensitiveDataRedactor.Redact(evidence.Summary));
        cmd.Parameters.AddWithValue("$data", SensitiveDataRedactor.RedactJson(evidence.StructuredData));
        cmd.Parameters.AddWithValue(
            "$artifact",
            Db(evidence.ArtifactReference is null
                ? null
                : SensitiveDataRedactor.Redact(evidence.ArtifactReference)));
        cmd.Parameters.AddWithValue("$confidence", evidence.Confidence);
        cmd.Parameters.AddWithValue("$sensitivity", evidence.SensitiveDataClassification.ToString());
        cmd.Parameters.AddWithValue("$retention", evidence.RetentionPolicy);
        cmd.Parameters.AddWithValue("$trust", evidence.TrustLevel.ToString());
        cmd.Parameters.AddWithValue(
            "$provenance",
            SensitiveDataRedactor.RedactJson(evidence.ProvenanceJson));
        cmd.Parameters.AddWithValue("$allowed", Serialize(evidence.AllowedUses));
        cmd.Parameters.AddWithValue("$prohibited", Serialize(evidence.ProhibitedUses));
        cmd.Parameters.AddWithValue("$created", Iso(evidence.Timestamp));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private static async Task SaveWorldStateCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        WorldStateSnapshot snapshot,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT INTO world_state_snapshots
                (world_state_id,task_id,version,source,confidence,observed_at,expires_at,snapshot_json)
            VALUES ($id,$task,$version,$source,$confidence,$observed,$expires,$json)
            """;
        cmd.Parameters.AddWithValue("$id", snapshot.WorldStateId);
        cmd.Parameters.AddWithValue("$task", snapshot.TaskId);
        cmd.Parameters.AddWithValue("$version", snapshot.Version);
        cmd.Parameters.AddWithValue("$source", SensitiveDataRedactor.Redact(snapshot.Source));
        cmd.Parameters.AddWithValue("$confidence", snapshot.Confidence);
        cmd.Parameters.AddWithValue("$observed", Iso(snapshot.ObservedAtUtc));
        cmd.Parameters.AddWithValue("$expires", Db(snapshot.ExpiresAtUtc is null ? null : Iso(snapshot.ExpiresAtUtc.Value)));
        cmd.Parameters.AddWithValue("$json", Serialize(snapshot));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private static async Task AppendEventCoreAsync(
        SqliteConnection conn,
        SqliteTransaction tx,
        TaskEvent taskEvent,
        CancellationToken ct)
    {
        taskEvent.Sequence = await NextEventSequenceAsync(conn, tx, taskEvent.TaskId, ct);
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT INTO task_events
                (event_id,task_id,event_type,plan_id,milestone_id,step_id,action_id,
                 outcome_id,parent_action_id,retry_id,tool_call_id,model_request_id,capture_id,
                 checkpoint_id,world_state_version,sequence_no,event_json,created_at)
            VALUES
                ($id,$task,$type,$plan,$milestone,$step,$action,$outcome,$parent_action,$retry,
                 $tool_call,$model_request,$capture,$checkpoint,$world_version,$sequence,$json,$created)
            """;
        BindEvent(cmd, taskEvent);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private static async Task<TaskCheckpoint?> LoadLatestCheckpointAsync(
        SqliteConnection conn,
        string taskId,
        CancellationToken ct) =>
        await LoadJsonAsync<TaskCheckpoint>(
            conn,
            "SELECT checkpoint_json FROM task_checkpoints WHERE task_id=$id ORDER BY version DESC LIMIT 1",
            taskId,
            ct);

    private static void BindTask(SqliteCommand cmd, AgentTask task)
    {
        cmd.Parameters.AddWithValue("$id", task.TaskId);
        cmd.Parameters.AddWithValue("$status", task.Status.ToString());
        cmd.Parameters.AddWithValue("$plan_version", task.CurrentPlanVersion);
        cmd.Parameters.AddWithValue("$milestone", Db(task.CurrentMilestoneId));
        cmd.Parameters.AddWithValue("$step", Db(task.CurrentStepId));
        cmd.Parameters.AddWithValue("$json", Serialize(task));
        cmd.Parameters.AddWithValue("$checkpoint_version", task.CheckpointVersion);
        cmd.Parameters.AddWithValue("$updated", Iso(task.UpdatedAtUtc));
        cmd.Parameters.AddWithValue("$completed", Db(task.CompletedAtUtc is null ? null : Iso(task.CompletedAtUtc.Value)));
        cmd.Parameters.AddWithValue("$parent_run", Db(task.ParentRunId));
        cmd.Parameters.AddWithValue("$trigger", task.TriggerType.ToString());
        cmd.Parameters.AddWithValue(
            "$security_context",
            SensitiveDataRedactor.RedactJson(task.SecurityContextJson));
        cmd.Parameters.AddWithValue(
            "$permission_context",
            SensitiveDataRedactor.RedactJson(task.PermissionContextJson));
        cmd.Parameters.AddWithValue(
            "$workspace_context",
            SensitiveDataRedactor.RedactJson(task.WorkspaceContextJson));
        cmd.Parameters.AddWithValue(
            "$model_context",
            SensitiveDataRedactor.RedactJson(task.ModelContextJson));
        cmd.Parameters.AddWithValue(
            "$cancellation_requested",
            Db(task.CancellationRequestedAtUtc is null
                ? null
                : Iso(task.CancellationRequestedAtUtc.Value)));
        cmd.Parameters.AddWithValue(
            "$failure_summary",
            Db(task.FailureSummary is null
                ? null
                : SensitiveDataRedactor.Redact(task.FailureSummary)));
        cmd.Parameters.AddWithValue(
            "$final_result",
            Db(task.FinalResultReference is null
                ? null
                : SensitiveDataRedactor.Redact(task.FinalResultReference)));
    }

    private static void BindStep(SqliteCommand cmd, TaskStep step)
    {
        cmd.Parameters.AddWithValue("$id", step.StepId);
        cmd.Parameters.AddWithValue("$task", step.TaskId);
        cmd.Parameters.AddWithValue("$milestone", step.MilestoneId);
        cmd.Parameters.AddWithValue("$status", step.Status.ToString());
        cmd.Parameters.AddWithValue("$side_effect", step.SideEffectClass.ToString());
        cmd.Parameters.AddWithValue("$attempts", step.AttemptCount);
        cmd.Parameters.AddWithValue("$failure", Db(step.FailureCode));
        cmd.Parameters.AddWithValue("$json", Serialize(step));
        cmd.Parameters.AddWithValue("$parent_step", Db(step.ParentStepId));
        cmd.Parameters.AddWithValue("$step_type", step.StepType);
        cmd.Parameters.AddWithValue("$tool_name", Db(step.ToolName));
        cmd.Parameters.AddWithValue(
            "$arguments_reference",
            Db(step.StructuredArgumentsReference));
        cmd.Parameters.AddWithValue("$step_idempotency", Db(step.CurrentIdempotencyKey));
        cmd.Parameters.AddWithValue(
            "$started",
            Db(step.StartedAtUtc is null ? null : Iso(step.StartedAtUtc.Value)));
        cmd.Parameters.AddWithValue(
            "$step_completed",
            Db(step.CompletedAtUtc is null ? null : Iso(step.CompletedAtUtc.Value)));
        cmd.Parameters.AddWithValue("$observation_ids", Serialize(step.ObservationIds));
        cmd.Parameters.AddWithValue(
            "$verification_disposition",
            Db(step.VerificationDisposition?.ToString()));
        cmd.Parameters.AddWithValue("$tool_outcome", Db(step.ToolOutcome?.ToString()));
        cmd.Parameters.AddWithValue(
            "$failure_classification",
            Db(step.FailureClassification?.ToString()));
    }

    private static void BindAction(SqliteCommand cmd, TaskAction action)
    {
        cmd.Parameters.AddWithValue("$id", action.ActionId);
        cmd.Parameters.AddWithValue("$task", action.TaskId);
        cmd.Parameters.AddWithValue("$plan", action.PlanId);
        cmd.Parameters.AddWithValue("$milestone", action.MilestoneId);
        cmd.Parameters.AddWithValue("$step", action.StepId);
        cmd.Parameters.AddWithValue("$outcome", Db(action.OutcomeId));
        cmd.Parameters.AddWithValue("$tool_call", action.ToolCallId);
        cmd.Parameters.AddWithValue("$tool", action.ToolName);
        cmd.Parameters.AddWithValue("$fingerprint", action.ArgumentsFingerprint);
        cmd.Parameters.AddWithValue("$key", action.IdempotencyKey);
        cmd.Parameters.AddWithValue("$side_effect", action.SideEffectClass.ToString());
        cmd.Parameters.AddWithValue("$status", action.Status.ToString());
        cmd.Parameters.AddWithValue("$attempt", action.Attempt);
        cmd.Parameters.AddWithValue("$parent_action", Db(action.ParentActionId));
        cmd.Parameters.AddWithValue("$retry", Db(action.RetryId));
        cmd.Parameters.AddWithValue("$observation", action.ObservationVersion);
        cmd.Parameters.AddWithValue(
            "$recovery",
            Db(action.RecoveryStrategy is null
                ? null
                : SensitiveDataRedactor.Redact(action.RecoveryStrategy)));
        cmd.Parameters.AddWithValue(
            "$capability_change",
            Db(action.CapabilityChangeReason is null
                ? null
                : SensitiveDataRedactor.Redact(action.CapabilityChangeReason)));
        cmd.Parameters.AddWithValue("$failure_code", Db(action.FailureCode));
        cmd.Parameters.AddWithValue(
            "$failure_reason",
            Db(action.FailureReason is null
                ? null
                : SensitiveDataRedactor.Redact(action.FailureReason)));
        cmd.Parameters.AddWithValue("$json", Serialize(action));
        cmd.Parameters.AddWithValue("$created", Iso(action.CreatedAtUtc));
        cmd.Parameters.AddWithValue("$updated", Iso(action.UpdatedAtUtc));
    }

    private static void BindEvent(SqliteCommand cmd, TaskEvent taskEvent)
    {
        cmd.Parameters.AddWithValue("$id", taskEvent.EventId);
        cmd.Parameters.AddWithValue("$task", taskEvent.TaskId);
        cmd.Parameters.AddWithValue("$type", taskEvent.EventType);
        cmd.Parameters.AddWithValue("$plan", Db(taskEvent.PlanId));
        cmd.Parameters.AddWithValue("$milestone", Db(taskEvent.MilestoneId));
        cmd.Parameters.AddWithValue("$step", Db(taskEvent.StepId));
        cmd.Parameters.AddWithValue("$action", Db(taskEvent.ActionId));
        cmd.Parameters.AddWithValue("$outcome", Db(taskEvent.OutcomeId));
        cmd.Parameters.AddWithValue("$parent_action", Db(taskEvent.ParentActionId));
        cmd.Parameters.AddWithValue("$retry", Db(taskEvent.RetryId));
        cmd.Parameters.AddWithValue("$tool_call", Db(taskEvent.ToolCallId));
        cmd.Parameters.AddWithValue("$model_request", Db(taskEvent.ModelRequestId));
        cmd.Parameters.AddWithValue("$capture", Db(taskEvent.CaptureId));
        cmd.Parameters.AddWithValue("$checkpoint", Db(taskEvent.CheckpointId));
        cmd.Parameters.AddWithValue("$world_version", Db(taskEvent.WorldStateVersion));
        cmd.Parameters.AddWithValue("$sequence", taskEvent.Sequence);
        cmd.Parameters.AddWithValue("$json", Serialize(taskEvent));
        cmd.Parameters.AddWithValue("$created", Iso(taskEvent.TimestampUtc));
    }

    private static async Task<long> NextEventSequenceAsync(
        SqliteConnection connection,
        SqliteTransaction transaction,
        string taskId,
        CancellationToken ct)
    {
        await using var cmd = connection.CreateCommand();
        cmd.Transaction = transaction;
        cmd.CommandText =
            "SELECT COALESCE(MAX(sequence_no),0)+1 FROM task_events WHERE task_id=$task";
        cmd.Parameters.AddWithValue("$task", taskId);
        return Convert.ToInt64(await cmd.ExecuteScalarAsync(ct), CultureInfo.InvariantCulture);
    }

    private static async Task EnsureTransitionAsync(
        SqliteConnection connection,
        SqliteTransaction? transaction,
        string taskId,
        AgentTaskStatus nextStatus,
        CancellationToken ct)
    {
        await using var cmd = connection.CreateCommand();
        cmd.Transaction = transaction;
        cmd.CommandText = "SELECT status FROM agent_tasks WHERE task_id=$id";
        cmd.Parameters.AddWithValue("$id", taskId);
        if (await cmd.ExecuteScalarAsync(ct) is not string currentText
            || !Enum.TryParse<AgentTaskStatus>(currentText, out var current))
            return;
        AgentRunStateMachine.EnsureTransition(current, nextStatus);
    }

    private static async Task<T?> LoadJsonAsync<T>(
        SqliteConnection conn,
        string sql,
        string id,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        cmd.Parameters.AddWithValue("$id", id);
        return Deserialize<T>(await cmd.ExecuteScalarAsync(ct) as string);
    }

    private static async Task<string?> ScalarStringAsync(
        SqliteConnection conn,
        string sql,
        string parameter,
        string value,
        CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        cmd.Parameters.AddWithValue(parameter, value);
        return await cmd.ExecuteScalarAsync(ct) as string;
    }

    private async Task<SqliteConnection> OpenAsync(CancellationToken ct)
    {
        var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync(ct);
        return conn;
    }

    // Durable state is a persistence boundary. Redaction happens before the
    // SQLite parameter is created; display-time masking would leave plaintext
    // secrets recoverable from the database and WAL.
    private static string Serialize<T>(T value) =>
        SensitiveDataRedactor.RedactJson(JsonSerializer.Serialize(value, JsonOptions));
    private static T? Deserialize<T>(string? value) =>
        value is null ? default : JsonSerializer.Deserialize<T>(value, JsonOptions);
    private static object Db(object? value) => value ?? DBNull.Value;
    private static string Iso(DateTimeOffset value) =>
        value.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture);
    private static string DefaultDbPath() =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "SRAN Desktop", "data.db");
}

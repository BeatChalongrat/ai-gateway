using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Text.Json;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Services;

/// <summary>
/// Runs user-requested image/video generation as a durable external action.
/// Raw prompts and media never enter runtime metadata or security audit events.
/// </summary>
public sealed class DurableMediaTurnService : IDurableMediaTurnService
{
    private readonly IChatService _chat;
    private readonly IDurableTaskRuntime _runtime;
    private readonly ISecurityEventLogger? _securityEvents;

    public DurableMediaTurnService(
        IChatService chat,
        IDurableTaskRuntime runtime,
        ISecurityEventLogger? securityEvents = null)
    {
        _chat = chat;
        _runtime = runtime;
        _securityEvents = securityEvents;
    }

    public async IAsyncEnumerable<ChatStreamEvent> RunAsync(
        string conversationId,
        string userContent,
        IReadOnlyList<string>? fileIds,
        bool imageMode,
        bool videoMode,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        if (imageMode == videoMode)
        {
            yield return new StreamErrorEvent(
                "InvalidMediaMode",
                "Exactly one media generation mode must be selected.");
            yield break;
        }

        var kind = videoMode ? "video" : "image";
        var toolName = videoMode ? "generate_video" : "generate_image";
        var turnId = Guid.NewGuid().ToString("N");
        var toolCallId = $"media_{turnId}";
        var ownerScope = $"conversation:{conversationId}";
        var sensitivity = SensitiveDataRedactor.Classify(userContent);
        var argumentsJson = JsonSerializer.Serialize(new
        {
            media_kind = kind,
            prompt_length = userContent.Length,
            attachment_count = fileIds?.Count ?? 0,
            destination = "configured_model_provider",
            external_transmission = true,
            explicit_user_intent = true
        });

        DurableTurnContext? context = null;
        DurableActionPreparation? preparation = null;
        StreamErrorEvent? initializationError = null;
        try
        {
            context = await _runtime.CreateRunAsync(new DurableRunRequest(
                conversationId,
                AgentTriggerType.Chat,
                $"Generate a {kind} from the user-authorized prompt.",
                ownerScope,
                SecurityContextJson: JsonSerializer.Serialize(new
                {
                    source = "interactive_media_turn",
                    sensitivity = sensitivity.ToString(),
                    external_transmission = true
                }),
                PermissionContextJson: JsonSerializer.Serialize(new
                {
                    action = $"Media.Generate{char.ToUpperInvariant(kind[0])}{kind[1..]}",
                    destination = "configured_model_provider",
                    scope = "this_turn",
                    explicit_user_intent = true
                }),
                ModelContextJson: JsonSerializer.Serialize(new
                {
                    media_kind = kind,
                    prompt_length = userContent.Length,
                    attachment_count = fileIds?.Count ?? 0
                })), ct);

            await _runtime.RecordApprovalAsync(
                context,
                toolCallId,
                toolName,
                argumentsJson,
                "explicit_user_media_turn",
                granted: true,
                DateTimeOffset.UtcNow.AddMinutes(1),
                ct);

            preparation = await _runtime.PrepareActionAsync(
                context,
                toolCallId,
                toolName,
                ToolCategory.Network,
                argumentsJson,
                ct);
            if (!preparation.Proceed)
            {
                _securityEvents?.Write(new SecurityAuditEvent(
                    "media.egress.authorization",
                    "denied",
                    toolName,
                    $"Media.Generate{kind}",
                    sensitivity.ToString(),
                    "user_prompt",
                    "configured_model_provider",
                    true,
                    false,
                    preparation.FailureCode,
                    CorrelationId: toolCallId));
                initializationError = new StreamErrorEvent(
                    preparation.FailureCode ?? "DurableRuntimeDenied",
                    preparation.FailureReason ?? "The durable runtime denied media generation.");
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            initializationError = new StreamErrorEvent(
                "DurableRuntimeUnavailable",
                $"Media generation was blocked because durable execution could not start: {ex.Message}");
        }
        if (initializationError is not null)
        {
            yield return initializationError;
            yield break;
        }
        if (context is null || preparation is null)
            throw new InvalidOperationException("Durable media initialization produced no context.");

        _securityEvents?.Write(new SecurityAuditEvent(
            "media.egress.authorization",
            "allow_once",
            toolName,
            $"Media.Generate{kind}",
            sensitivity.ToString(),
            "user_prompt",
            "configured_model_provider",
            true,
            null,
            CorrelationId: toolCallId));

        var stopwatch = Stopwatch.StartNew();
        var completed = false;
        var mediaReferenceCount = 0;
        string? streamError = null;
        StreamErrorEvent? disconnectedError = null;
        await using var enumerator = _chat.SendMessageAsync(
                conversationId,
                userContent,
                fileIds,
                imageMode,
                videoMode,
                ct)
            .GetAsyncEnumerator(ct);
        while (true)
        {
            ChatStreamEvent? evt = null;
            var moved = false;
            try
            {
                moved = await enumerator.MoveNextAsync();
                if (moved)
                    evt = enumerator.Current;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                await _runtime.RecordUnknownOutcomeAsync(
                    context,
                    preparation,
                    "The media provider stream was cancelled after dispatch.",
                    CancellationToken.None);
                _securityEvents?.Write(new SecurityAuditEvent(
                    "media.execution",
                    "unknown_outcome",
                    toolName,
                    $"Media.Generate{kind}",
                    sensitivity.ToString(),
                    "user_prompt",
                    "configured_model_provider",
                    true,
                    false,
                    "CancelledAfterDispatch",
                    stopwatch.ElapsedMilliseconds,
                    CorrelationId: toolCallId));
                throw;
            }
            catch (Exception ex)
            {
                await _runtime.RecordUnknownOutcomeAsync(
                    context,
                    preparation,
                    "The media provider stream failed after dispatch.",
                    CancellationToken.None);
                _securityEvents?.Write(new SecurityAuditEvent(
                    "media.execution",
                    "unknown_outcome",
                    toolName,
                    $"Media.Generate{kind}",
                    sensitivity.ToString(),
                    "user_prompt",
                    "configured_model_provider",
                    true,
                    false,
                    "ProviderStreamDisconnected",
                    stopwatch.ElapsedMilliseconds,
                    CorrelationId: toolCallId));
                disconnectedError = new StreamErrorEvent(
                    "ProviderStreamDisconnected",
                    $"The media stream disconnected after dispatch and requires reconciliation: {ex.Message}");
            }

            if (disconnectedError is not null || !moved)
                break;
            if (evt is null)
                continue;
            switch (evt)
            {
                case StreamCompletedEvent:
                    completed = true;
                    break;
                case StreamErrorEvent error:
                    streamError = error.Code ?? "ProviderStreamError";
                    break;
                case MessageFilesEvent files:
                    mediaReferenceCount += files.Files.Count(file =>
                        file.ContentType.StartsWith(
                            videoMode ? "video/" : "image/",
                            StringComparison.OrdinalIgnoreCase)
                        && !string.IsNullOrWhiteSpace(file.Id));
                    break;
            }
            yield return evt;
        }
        if (disconnectedError is not null)
        {
            yield return disconnectedError;
            yield break;
        }

        stopwatch.Stop();
        ToolResult result;
        if (completed && mediaReferenceCount > 0 && streamError is null)
        {
            result = ToolResult.Verified(
                $"The provider returned {mediaReferenceCount} verified {kind} reference(s).",
                [
                    "provider_stream_completed=true",
                    $"media_reference_count={mediaReferenceCount}",
                    $"media_kind={kind}"
                ],
                ["external_media_generated=true"]);
        }
        else if (mediaReferenceCount > 0)
        {
            result = ToolResult.ExecutedUnverified(
                "The provider returned media references without a clean completed stream.",
                "UnknownOutcome",
                "Media may have been generated, but provider completion was not verified.",
                retryable: false,
                evidence: [$"media_reference_count={mediaReferenceCount}"],
                sideEffects: ["external_media_may_have_been_generated=true"]);
        }
        else
        {
            result = ToolResult.FailedEffect(
                streamError ?? "MediaArtifactMissing",
                streamError is null
                    ? "The provider stream ended without a verifiable media artifact reference."
                    : "The provider reported a media generation error.",
                retryable: false);
        }

        var durableOutcome = await _runtime.RecordResultAsync(
            context,
            preparation,
            result,
            stopwatch.ElapsedMilliseconds,
            CancellationToken.None);
        await _runtime.EvaluateCompletionAsync(
            durableOutcome.Context,
            CancellationToken.None);

        _securityEvents?.Write(new SecurityAuditEvent(
            "media.execution",
            result.Effect?.Verified == true ? "verified" : "failed",
            toolName,
            $"Media.Generate{kind}",
            sensitivity.ToString(),
            "user_prompt",
            "configured_model_provider",
            true,
            result.Effect?.Verified == true,
            result.Effect?.FailureCode,
            stopwatch.ElapsedMilliseconds,
            CorrelationId: toolCallId));

        if (result.Effect?.Verified != true && streamError is null)
            yield return new StreamErrorEvent(
                result.Effect?.FailureCode ?? "MediaVerificationFailed",
                result.Effect?.FailureReason
                ?? "Media generation did not produce independent verification evidence.");
    }
}

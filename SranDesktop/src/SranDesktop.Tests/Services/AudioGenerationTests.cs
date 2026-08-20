using System.Net.Http;
using System.Text;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 14 Task 3 — audio generation on the desktop.
///
/// ARCHITECTURE NOTE, and the reason this is a form rather than a composer
/// toggle: chat-edge's tool registry has generate_image and generate_video but
/// NO generate_audio (internal/service/tools.go). Audio exists only as direct
/// dispatch — POST /audio/generations → poll GET /jobs/{id} → GET
/// /jobs/{id}/output. An "audio mode" toggle like the image/video ones would
/// bias the model toward a tool that does not exist and quietly produce
/// nothing, so the desktop drives the job itself, exactly as the web
/// AudioGenForm does.
/// </summary>
public sealed class AudioGenerationServiceTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();

    /// <summary>No real waiting — the poll loop runs at full speed.</summary>
    private static Task NoDelay(TimeSpan _, CancellationToken __) => Task.CompletedTask;

    private AudioGenerationService Create(
        Func<MediaGenSubmitResponse, CancellationToken, Task<bool>>? confirmPremium = null) =>
        new(_api.Object, NullLogger.Instance, NoDelay, confirmPremium);

    private static AudioGenerationRequest Request(string mode = "tts") =>
        new("hello there", "tts-1", mode);

    private void Submitted(string status = "queued", bool requiresConfirmation = false) =>
        _api.Setup(a => a.SubmitAudioGenerationAsync(
                It.IsAny<AudioGenerationRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MediaGenSubmitResponse(
                "job-1", status,
                EstimatedCostUsd: requiresConfirmation ? "$2.50" : null,
                RequiresConfirmation: requiresConfirmation));

    private void Polls(params string[] statuses)
    {
        var i = 0;
        _api.Setup(a => a.GetMediaJobAsync("job-1", It.IsAny<CancellationToken>()))
            .ReturnsAsync(() => new MediaJobStatus(
                "job-1", statuses[Math.Min(i++, statuses.Length - 1)]));
    }

    private void Output(byte[] bytes) =>
        _api.Setup(a => a.DownloadMediaJobOutputAsync("job-1", It.IsAny<CancellationToken>()))
            .ReturnsAsync(bytes);

    [Fact]
    public async Task Submits_Polls_Then_Fetches_The_Audio()
    {
        Submitted();
        Polls("processing", "processing", "done");
        Output(Encoding.UTF8.GetBytes("ID3-audio"));

        var result = await Create().GenerateAsync(Request());

        result.Success.Should().BeTrue();
        result.Audio.Should().NotBeEmpty();
        result.ContentType.Should().Be("audio/mpeg");
        _api.Verify(a => a.DownloadMediaJobOutputAsync("job-1", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task A_Job_That_Is_Already_Done_Skips_Polling_Entirely()
    {
        Submitted("done");
        Output(new byte[] { 1, 2, 3 });

        var result = await Create().GenerateAsync(Request());

        result.Success.Should().BeTrue();
        _api.Verify(a => a.GetMediaJobAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task A_Failed_Job_Surfaces_The_Server_Message()
    {
        Submitted();
        _api.Setup(a => a.GetMediaJobAsync("job-1", It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MediaJobStatus("job-1", "failed", ErrorMessage: "voice unavailable"));

        var result = await Create().GenerateAsync(Request());

        result.Success.Should().BeFalse();
        result.ErrorMessage.Should().Be("voice unavailable");
        _api.Verify(a => a.DownloadMediaJobOutputAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task A_Failed_Submit_Never_Starts_Polling()
    {
        _api.Setup(a => a.SubmitAudioGenerationAsync(
                It.IsAny<AudioGenerationRequest>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("402 insufficient balance"));

        var result = await Create().GenerateAsync(Request());

        result.Success.Should().BeFalse();
        result.ErrorMessage.Should().Contain("402");
        _api.Verify(a => a.GetMediaJobAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    // ── Batch 1 D1 — premium-cost confirmation ──────────────────────────
    // requires_confirmation=true is the server saying "reserved, but do not
    // proceed without consent". Declining (or having no confirm hook wired)
    // must cancel the job so the reservation refunds; confirming proceeds.

    [Fact]
    public async Task Premium_Job_Declined_Cancels_The_Job_And_Reports_UserCancelled()
    {
        Submitted(requiresConfirmation: true);

        var result = await Create(confirmPremium: (_, _) => Task.FromResult(false))
            .GenerateAsync(Request());

        result.Success.Should().BeFalse();
        result.UserCancelled.Should().BeTrue();
        result.ErrorMessage.Should().BeNull();
        _api.Verify(a => a.CancelMediaJobAsync("job-1", It.IsAny<CancellationToken>()), Times.Once);
        _api.Verify(a => a.GetMediaJobAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Premium_Job_Confirmed_Proceeds_To_Completion()
    {
        Submitted(requiresConfirmation: true);
        Polls("processing", "done");
        Output(new byte[] { 7 });

        var result = await Create(confirmPremium: (submitted, _) =>
        {
            submitted.EstimatedCostUsd.Should().Be("$2.50");
            return Task.FromResult(true);
        }).GenerateAsync(Request());

        result.Success.Should().BeTrue();
        _api.Verify(a => a.CancelMediaJobAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Premium_Job_With_No_Confirm_Hook_Is_Declined_Safely()
    {
        // Headless callers that never wired a dialog must not silently start
        // a job the server said needs consent.
        Submitted(requiresConfirmation: true);

        var result = await Create().GenerateAsync(Request());

        result.UserCancelled.Should().BeTrue();
        _api.Verify(a => a.CancelMediaJobAsync("job-1", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task NonPremium_Job_Never_Invokes_The_Confirm_Hook()
    {
        Submitted("done");
        Output(new byte[] { 1 });
        var hookCalls = 0;

        var result = await Create(confirmPremium: (_, _) =>
        {
            hookCalls++;
            return Task.FromResult(true);
        }).GenerateAsync(Request());

        result.Success.Should().BeTrue();
        hookCalls.Should().Be(0);
        _api.Verify(a => a.CancelMediaJobAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task A_Transient_Poll_Failure_Does_Not_Abort_The_Job()
    {
        // A dropped poll is a network blip, not a job failure — the job is
        // still running server-side and giving up would strand the user's
        // (already charged) generation.
        Submitted();
        var calls = 0;
        _api.Setup(a => a.GetMediaJobAsync("job-1", It.IsAny<CancellationToken>()))
            .ReturnsAsync(() =>
            {
                calls++;
                if (calls == 1) throw new HttpRequestException("blip");
                return new MediaJobStatus("job-1", "done");
            });
        Output(new byte[] { 9 });

        var result = await Create().GenerateAsync(Request());

        result.Success.Should().BeTrue();
        calls.Should().BeGreaterThan(1);
    }

    [Fact]
    public async Task An_Empty_Artifact_Is_A_Failure_Not_A_Silent_Success()
    {
        Submitted("done");
        Output(Array.Empty<byte>());

        var result = await Create().GenerateAsync(Request());

        result.Success.Should().BeFalse();
    }

    [Fact]
    public async Task A_Never_Finishing_Job_Gives_Up_Instead_Of_Polling_Forever()
    {
        Submitted();
        Polls("processing");

        var result = await Create().GenerateAsync(Request());

        result.Success.Should().BeFalse();
        _api.Verify(a => a.GetMediaJobAsync("job-1", It.IsAny<CancellationToken>()),
            Times.Exactly(AudioGenerationService.MaxPolls));
    }

    [Fact]
    public async Task The_Filename_Reflects_The_Mode()
    {
        Submitted("done");
        Output(new byte[] { 1 });

        (await Create().GenerateAsync(Request("tts"))).Filename.Should().StartWith("speech-");
        (await Create().GenerateAsync(Request("music"))).Filename.Should().StartWith("music-");
    }

    [Theory]
    [InlineData("done", true, false)]
    [InlineData("completed", true, false)]
    [InlineData("failed", false, true)]
    [InlineData("cancelled", false, true)]
    [InlineData("processing", false, false)]
    [InlineData("queued", false, false)]
    [InlineData(null, false, false)]
    public void Job_States_Are_Classified_Correctly(string? status, bool done, bool failed)
    {
        MediaJobState.IsDone(status).Should().Be(done);
        MediaJobState.IsFailed(status).Should().Be(failed);
        MediaJobState.IsTerminal(status).Should().Be(done || failed);
    }
}

/// <summary>Phase 14 Task 3 — the form's own rules.</summary>
public sealed class AudioGenViewModelTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();

    private static Task NoDelay(TimeSpan _, CancellationToken __) => Task.CompletedTask;

    private AudioGenViewModel Create(string? conversationId = "c1") =>
        new(_api.Object, NullLogger.Instance, "tts-1", conversationId, NoDelay);

    private void HappyPath()
    {
        _api.Setup(a => a.SubmitAudioGenerationAsync(
                It.IsAny<AudioGenerationRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MediaGenSubmitResponse("job-1", "done"));
        _api.Setup(a => a.DownloadMediaJobOutputAsync("job-1", It.IsAny<CancellationToken>()))
            .ReturnsAsync(new byte[] { 1, 2, 3 });
    }

    [Fact]
    public void An_Empty_Prompt_Cannot_Be_Submitted()
    {
        var vm = Create();
        vm.CanSubmit.Should().BeFalse();

        vm.Prompt = "   ";
        vm.CanSubmit.Should().BeFalse();

        vm.Prompt = "read this";
        vm.CanSubmit.Should().BeTrue();
    }

    [Fact]
    public async Task Speech_Mode_Sends_A_Voice_And_No_Duration()
    {
        HappyPath();
        var vm = Create();
        vm.IsSpeech = true;
        vm.Prompt = "สวัสดีครับ";

        await vm.GenerateCommand.ExecuteAsync(null);

        _api.Verify(a => a.SubmitAudioGenerationAsync(
            It.Is<AudioGenerationRequest>(r =>
                r.Mode == "tts" && r.VoiceId != null && r.DurationSec == null
                && r.Prompt == "สวัสดีครับ" && r.ConversationId == "c1"),
            It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Music_Mode_Sends_A_Duration_And_No_Voice()
    {
        HappyPath();
        var vm = Create();
        vm.IsSpeech = false;
        vm.DurationSec = 45;
        vm.Prompt = "calm piano";

        await vm.GenerateCommand.ExecuteAsync(null);

        _api.Verify(a => a.SubmitAudioGenerationAsync(
            It.Is<AudioGenerationRequest>(r =>
                r.Mode == "music" && r.VoiceId == null && r.DurationSec == 45),
            It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Success_Raises_Completed_So_The_Chat_Can_Keep_The_Track()
    {
        HappyPath();
        var vm = Create();
        vm.Prompt = "hello";
        AudioGenerationResult? completed = null;
        vm.Completed += (_, r) => completed = r;

        await vm.GenerateCommand.ExecuteAsync(null);

        completed.Should().NotBeNull();
        completed!.Success.Should().BeTrue();
        vm.HasResult.Should().BeTrue();
        vm.IsBusy.Should().BeFalse();
    }

    [Fact]
    public async Task A_Failure_Shows_A_Message_And_Leaves_The_Form_Usable()
    {
        _api.Setup(a => a.SubmitAudioGenerationAsync(
                It.IsAny<AudioGenerationRequest>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));
        var vm = Create();
        vm.Prompt = "hello";

        await vm.GenerateCommand.ExecuteAsync(null);

        vm.HasResult.Should().BeFalse();
        vm.StatusMessage.Should().NotBeNullOrEmpty();
        vm.IsBusy.Should().BeFalse();
        vm.CanSubmit.Should().BeTrue("the user must be able to retry");
    }

    [Fact]
    public async Task An_Over_Length_Prompt_Is_Refused_Before_It_Is_Sent()
    {
        var vm = Create();
        vm.Prompt = new string('x', AudioGenViewModel.MaxPromptChars + 1);

        await vm.GenerateCommand.ExecuteAsync(null);

        _api.Verify(a => a.SubmitAudioGenerationAsync(
            It.IsAny<AudioGenerationRequest>(), It.IsAny<CancellationToken>()), Times.Never);
        vm.StatusMessage.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public void Voices_Are_Offered_And_One_Is_Preselected()
    {
        var vm = Create();

        vm.Voices.Should().HaveCount(AudioGenViewModel.FallbackVoiceIds.Count);
        vm.SelectedVoice.Should().NotBeNull();
    }

    [Fact]
    public void Audio_Strings_Exist_In_Both_Languages()
    {
        foreach (var key in new[]
                 {
                     "media.audioTitle", "media.audioSubtitle", "media.modeSpeech",
                     "media.modeMusic", "media.tts.placeholder", "media.music.placeholder",
                     "media.voice", "media.voice.default", "media.voice.narrator",
                     "media.duration", "media.generateSpeech", "media.generateMusic",
                     "media.generatingSpeech", "media.generatingMusic", "media.genFailed",
                     "media.promptTooLong", "media.audioReady",
                 })
        {
            SranDesktop.Presentation.Localization.Dict.En.Should().ContainKey(key);
            SranDesktop.Presentation.Localization.Dict.Th.Should().ContainKey(key);
        }
    }
}

/// <summary>Phase 14 Task 3 — when the composer offers audio, and what happens
/// to the finished track.</summary>
public sealed class AudioGenComposerGateTests
{
    private static ModelInfo Model(string id, string? kind = "chat", params string[] capabilities) =>
        new(id, id, "openai", kind, true, Capabilities: capabilities.ToList());

    private static ChatViewModel Create()
    {
        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());
        return new ChatViewModel(
            Mock.Of<IChatEdgeApiClient>(), Mock.Of<IGatewayApiClient>(),
            Mock.Of<IConversationStore>(), Mock.Of<IFileService>(),
            Mock.Of<IChatOrchestrator>(), Mock.Of<IChatService>(),
            Mock.Of<ISkillManager>(), Mock.Of<ISpeechService>(),
            settings.Object, Mock.Of<IAgentConversationSyncService>(),
            NullLogger<ChatViewModel>.Instance);
    }

    private static ConversationItemViewModel Conversation(string modelId) =>
        new(new Conversation
        {
            Id = "c1", Title = "chat", ModelId = modelId,
            CreatedAt = DateTimeOffset.UtcNow, UpdatedAt = DateTimeOffset.UtcNow,
        });

    [Fact]
    public void Offered_For_An_Audio_Capable_Model()
    {
        var vm = Create();
        vm.AvailableModels.Add(Model("music-1", "audio", "audio_generation"));
        vm.Conversation = Conversation("music-1");

        vm.CurrentModelSupportsAudio.Should().BeTrue();
    }

    [Fact]
    public void Offered_For_A_Tts_Model_Kind()
    {
        var vm = Create();
        vm.AvailableModels.Add(Model("tts-1", "tts"));
        vm.Conversation = Conversation("tts-1");

        vm.CurrentModelSupportsAudio.Should().BeTrue();
    }

    [Fact]
    public void Hidden_For_An_Ordinary_Chat_Model()
    {
        var vm = Create();
        vm.AvailableModels.Add(Model("gpt-4o-mini"));
        vm.Conversation = Conversation("gpt-4o-mini");

        vm.CurrentModelSupportsAudio.Should().BeFalse();
    }

    [Fact]
    public void Hidden_On_Auto_Unlike_Image_And_Video()
    {
        // Image/video are TOOLS, so Auto can still land on a model that
        // supports them. Audio is a direct dispatch that must name a concrete
        // audio model, so Auto has nothing to send.
        var vm = Create();
        vm.AvailableModels.Add(Model("music-1", "audio", "audio_generation"));
        vm.Conversation = Conversation("auto");

        vm.CurrentModelSupportsAudio.Should().BeFalse();
        vm.CurrentModelSupportsImage.Should().BeTrue("image generation IS Auto-friendly");
    }

    [Fact]
    public void The_Finished_Track_Lands_In_The_Transcript()
    {
        var vm = Create();
        var before = vm.Messages.Count;

        vm.AppendGeneratedAudio(new AudioGenerationResult(
            true, new byte[] { 1, 2, 3 }, "speech-1.mp3", "audio/mpeg", null));

        vm.Messages.Should().HaveCount(before + 1);
        var attachment = vm.Messages[^1].InlineAttachments.Should().ContainSingle().Subject;
        attachment.Filename.Should().Be("speech-1.mp3");
        attachment.IsDocument.Should().BeTrue(
            "audio is not inline-rendered media on desktop — it shows as a saveable chip");
    }
}

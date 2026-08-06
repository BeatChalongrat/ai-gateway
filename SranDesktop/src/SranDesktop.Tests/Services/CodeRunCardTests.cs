using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 13 Task 2 — the dedicated run_python card.
///
/// The card re-derives code + stdout + stderr from the text RunPythonTool
/// shapes for the model, because that string is all the message history
/// keeps. These tests pin BOTH ends of that contract: the exact format the
/// tool writes today, and the forgiving fallback for anything else — a
/// parser that silently swallowed output would be worse than no card.
/// </summary>
public sealed class CodeRunParserTests
{
    /// <summary>Byte-for-byte what RunPythonTool.Shape produces on success.</summary>
    private const string Typical = """
        Exit code: 0

        stdout:
        42
        done
        """;

    [Fact]
    public void Reads_Exit_Code_And_Stdout()
    {
        var r = CodeRunParser.Parse(Typical);

        r.ExitCode.Should().Be(0);
        r.Stdout.Should().Be("42\ndone");
        r.HasStderr.Should().BeFalse();
        r.IsSilent.Should().BeFalse();
    }

    [Fact]
    public void Splits_Stdout_From_Stderr()
    {
        var r = CodeRunParser.Parse("""
            Exit code: 1

            stdout:
            partial

            stderr:
            Traceback (most recent call last):
            ZeroDivisionError
            """);

        r.ExitCode.Should().Be(1);
        r.Stdout.Should().Be("partial");
        r.Stderr.Should().Contain("ZeroDivisionError");
        r.HasStderr.Should().BeTrue();
    }

    [Fact]
    public void Recognises_A_Timeout()
    {
        var r = CodeRunParser.Parse("[The code timed out and was stopped.]\nExit code: 137");

        r.TimedOut.Should().BeTrue();
        r.IsSilent.Should().BeFalse("a timeout is its own notice, not 'no output'");
    }

    [Fact]
    public void Recognises_Truncation()
    {
        var r = CodeRunParser.Parse("Exit code: 0\n\nstdout:\nlots\n[output truncated]");

        r.Truncated.Should().BeTrue();
        r.Stdout.Should().Be("lots", "the marker is a flag, not output");
    }

    [Fact]
    public void A_Silent_Run_Is_Reported_As_Silent()
    {
        var r = CodeRunParser.Parse("Exit code: 0\n(no output was printed)");

        r.IsSilent.Should().BeTrue();
        r.Stdout.Should().BeEmpty();
    }

    [Fact]
    public void Keeps_Generated_Artifact_Lines_Out_Of_Stdout()
    {
        var r = CodeRunParser.Parse("""
            Exit code: 0

            stdout:
            plotted

            Generated image(s): chart.png
            """);

        r.Stdout.Should().Be("plotted");
        r.GeneratedNote.Should().Contain("chart.png");
    }

    [Fact]
    public void An_Unrecognised_Result_Is_Still_Shown_In_Full()
    {
        // e.g. ToolResult.Fail("Code interpreter call failed: ...") — this must
        // land somewhere visible rather than vanishing into an empty card.
        var r = CodeRunParser.Parse("Code interpreter call failed: 503 sandbox unavailable");

        r.Stdout.Should().Contain("503 sandbox unavailable");
        r.ExitCode.Should().BeNull();
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   \n ")]
    public void Empty_Input_Parses_To_An_Empty_Silent_Result(string? text)
    {
        var r = CodeRunParser.Parse(text);

        r.Stdout.Should().BeEmpty();
        r.Stderr.Should().BeEmpty();
        r.IsSilent.Should().BeTrue();
    }
}

/// <summary>The card view-model side: which body renders, and when.</summary>
public sealed class CodeRunCardViewModelTests
{
    private static ToolExecutionItemViewModel Card(string tool, string args) =>
        new("call-1", tool, args);

    [Fact]
    public void Only_Run_Python_Gets_The_Code_Body()
    {
        Card("run_python", """{"code":"print(1)"}""").IsCodeRun.Should().BeTrue();
        Card("run_command", """{"command":"dir"}""").IsCodeRun.Should().BeFalse();
    }

    [Fact]
    public void The_Code_Pane_Shows_The_Source_Verbatim()
    {
        var card = Card("run_python", """{"code":"import math\nprint(math.pi)"}""");

        card.CodeText.Should().Be("import math\nprint(math.pi)");
        card.HasCodeText.Should().BeTrue();
    }

    [Fact]
    public void Malformed_Arguments_Leave_The_Code_Pane_Hidden_Not_Filled_With_Json()
    {
        var card = Card("run_python", "not json at all");

        card.CodeText.Should().BeEmpty();
        card.HasCodeText.Should().BeFalse();
    }

    [Fact]
    public void The_Card_Opens_While_The_Code_Runs_And_Folds_When_It_Finishes()
    {
        var card = Card("run_python", """{"code":"print(1)"}""");
        card.IsExpanded.Should().BeTrue("that is when the user wants to watch it");
        card.ShowCodeDetails.Should().BeTrue();
        card.ShowGenericDetails.Should().BeFalse();

        card.Complete(new AgentToolResultEvent(
            "call-1", "run_python", """{"code":"print(1)"}""", true,
            "Exit code: 0", 120, "Exit code: 0\n\nstdout:\n1"));

        card.IsExpanded.Should().BeFalse("it folds to the summary line, like the web card");
        card.CodeStdout.Should().Be("1");
    }

    [Fact]
    public void A_Non_Code_Card_Still_Starts_Collapsed_With_The_Generic_Body()
    {
        var card = Card("read_file", """{"path":"C:\\x.txt"}""");

        card.IsExpanded.Should().BeFalse();
        card.IsExpanded = true;
        card.ShowGenericDetails.Should().BeTrue();
        card.ShowCodeDetails.Should().BeFalse();
    }

    [Fact]
    public void Failure_Output_Reaches_The_Stderr_Pane()
    {
        var card = Card("run_python", """{"code":"1/0"}""");

        card.Complete(new AgentToolResultEvent(
            "call-1", "run_python", """{"code":"1/0"}""", false,
            "Exit code: 1", 90, "Exit code: 1\n\nstderr:\nZeroDivisionError"));

        card.IsFailed.Should().BeTrue();
        card.HasCodeStderr.Should().BeTrue();
        card.CodeStderr.Should().Contain("ZeroDivisionError");
    }

    [Fact]
    public void A_Silent_Run_Says_So_Instead_Of_Showing_An_Empty_Box()
    {
        var card = Card("run_python", """{"code":"x = 1"}""");

        card.Complete(new AgentToolResultEvent(
            "call-1", "run_python", """{"code":"x = 1"}""", true,
            "Exit code: 0", 40, "Exit code: 0\n(no output was printed)"));

        card.HasCodeStdout.Should().BeFalse();
        card.HasCodeNotice.Should().BeTrue();
        card.CodeNotice.Should().NotBeNullOrWhiteSpace();
    }

    [Fact]
    public void The_Full_Result_Is_Parsed_Not_The_Short_Preview()
    {
        // The preview is a truncated header line; parsing it would drop stdout.
        var card = Card("run_python", """{"code":"print('x')"}""");

        card.Complete(new AgentToolResultEvent(
            "call-1", "run_python", """{"code":"print('x')"}""", true,
            "Exit code: 0", 10, "Exit code: 0\n\nstdout:\nx"));

        card.CodeStdout.Should().Be("x");
    }

    [Fact]
    public void Card_Strings_Exist_In_Both_Languages()
    {
        foreach (var key in new[]
                 {
                     "coderun.code", "coderun.output", "coderun.noOutput",
                     "coderun.timedOut", "coderun.truncated",
                 })
        {
            SranDesktop.Presentation.Localization.Dict.En.Should().ContainKey(key);
            SranDesktop.Presentation.Localization.Dict.Th.Should().ContainKey(key);
        }
    }
}

/// <summary>
/// Phase 13.1 — what a model picker row exposes.
///
/// The pricing/context meta line added in Phase 13 was removed: SRAN runs on a
/// shared internal account, so per-token costs were numbers nobody acts on.
/// The catalogue still SENDS them (ModelInfo keeps the fields for the context
/// budget in ChatOrchestrator), so these tests pin that the picker row exposes
/// only the description — a reinstated meta line fails here rather than
/// quietly showing users prices again.
/// </summary>
public sealed class ModelPickerRowTests
{
    private static SranDesktop.Application.Models.ModelInfo Model(
        string? description = null, double? input = 2.5, double? output = 10,
        int? context = 128_000) =>
        new("m1", "Model One", "OpenAI", "chat", true,
            Description: description,
            ContextWindow: context, InputPricePer1M: input, OutputPricePer1M: output);

    [Fact]
    public void Shows_The_Model_Name_And_Description()
    {
        var vm = new ModelItemViewModel(Model(description: "Fast everyday model"), false);

        vm.DisplayName.Should().Be("Model One");
        vm.Description.Should().Be("Fast everyday model");
        vm.HasDescription.Should().BeTrue();
    }

    [Fact]
    public void A_Model_Without_A_Description_Renders_No_Second_Line()
    {
        new ModelItemViewModel(Model(description: null), false)
            .HasDescription.Should().BeFalse();
        new ModelItemViewModel(Model(description: "   "), false)
            .HasDescription.Should().BeFalse("whitespace is not a description");
    }

    [Fact]
    public void Capability_Badges_Still_Come_From_The_Catalogue()
    {
        // The meta line went; the badges the row shows beside it did not.
        var vm = new ModelItemViewModel(
            new SranDesktop.Application.Models.ModelInfo(
                "m2", "Thinker", "OpenAI", "chat", true,
                Capabilities: new List<string> { "image_generation" },
                Reasoning: new SranDesktop.Application.Models.ReasoningCapability(true, null, null, false)),
            false);

        vm.SupportsImage.Should().BeTrue();
        vm.SupportsThinking.Should().BeTrue();
    }

    [Fact]
    public void The_Pricing_String_Is_Gone_From_Both_Dictionaries()
    {
        // An orphaned key is dead weight, and its presence would be the first
        // sign someone had put the price line back.
        SranDesktop.Presentation.Localization.Dict.En.Should().NotContainKey("model.pricePerM");
        SranDesktop.Presentation.Localization.Dict.Th.Should().NotContainKey("model.pricePerM");
    }
}

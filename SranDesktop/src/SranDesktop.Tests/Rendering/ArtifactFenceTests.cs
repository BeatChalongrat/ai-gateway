using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using FluentAssertions;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// The desktop must never depend on the model producing perfectly-formed
/// markup. Models drop newlines and close fences late; the renderer's job is
/// to recognise what it can and show the rest VERBATIM.
///
/// Live evidence (1.2.5): asked for a mermaid flowchart, the model emitted the
/// whole `:::artifact[...]` directive on ONE line with no closing `:::`. The
/// fence regex needs a newline after `]` and a closing `:::` on its own line,
/// so nothing matched — and Markdig's CustomContainer extension (which owns
/// the `:::` grammar under UseAdvancedExtensions) swallowed the marker and
/// produced a block type RenderBlock has no case for, so the text vanished
/// instead of degrading to plain prose.
///
/// WPF is thread-affine, so the render bodies run on their own STA thread —
/// same pattern as MarkdownTableRenderTests.
/// </summary>
public class ArtifactFenceTests
{
    // The exact directive from the failing 1.2.5 reply.
    private const string OneLineDirective =
        ":::artifact[title=\"ผังงานการประมวลผลคำขอ\" type=\"mermaid\"] " +
        "flowchart TD A[รับคำขอ] --> B[ตรวจสิทธิ์] B --> C [ประมวลผล] C --> D[ตอบกลับ]";

    private const string WellFormed =
        ":::artifact[title=\"Flow\" type=\"mermaid\"]\nflowchart TD\nA --> B\n:::";

    private const string Unclosed =
        ":::artifact[title=\"Flow\" type=\"mermaid\"]\nflowchart TD\nA --> B";

    private const string MermaidFence =
        "```mermaid\nflowchart TD\nA --> B\n```";

    private static void RunSta(Action action)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try { action(); }
            catch (Exception ex) { failure = ex; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure is not null)
            throw failure;
    }

    /// <summary>Every character the renderer put on screen, flattened.</summary>
    private static string VisibleText(string markdown)
    {
        var sb = new StringBuilder();
        Collect(new MarkdownRenderer().Render(markdown), sb);
        return sb.ToString();
    }

    private static void Collect(DependencyObject? o, StringBuilder sb)
    {
        switch (o)
        {
            case null:
                return;
            case TextBlock tb:
                if (!string.IsNullOrEmpty(tb.Text)) sb.Append(tb.Text).Append('\n');
                foreach (var inline in tb.Inlines)
                    if (inline is Run run) sb.Append(run.Text);
                sb.Append('\n');
                return;
            case Panel panel:
                foreach (var child in panel.Children) Collect(child as DependencyObject, sb);
                return;
            case Decorator dec:
                Collect(dec.Child, sb);
                return;
            case ContentControl cc:
                Collect(cc.Content as DependencyObject, sb);
                return;
        }
    }

    // ── (b) content must never disappear ────────────────────────────────────

    [Fact]
    public void OneLine_Malformed_Directive_Still_Shows_Every_Character() => RunSta(() =>
    {
        var shown = VisibleText(OneLineDirective);

        // The user must see what the model sent, even though the fence is
        // malformed — not an empty bubble and not a mangled prefix.
        shown.Should().Contain("artifact[title=", "the directive text must survive");
        shown.Should().Contain("flowchart TD", "the diagram source must survive");
        shown.Should().Contain("ตอบกลับ", "the tail of the line must survive");
    });

    [Fact]
    public void Unclosed_Fence_Still_Shows_Its_Text() => RunSta(() =>
    {
        var shown = VisibleText(Unclosed);

        shown.Should().Contain("flowchart TD");
        shown.Should().Contain("A --> B");
    });

    [Fact]
    public void A_Bare_Triple_Colon_Line_Is_Not_Swallowed() => RunSta(() =>
    {
        // Any ::: construct the app does not itself understand must degrade to
        // plain text rather than being consumed by the markdown parser.
        var shown = VisibleText("before\n\n:::note\nkeep me\n:::\n\nafter");

        shown.Should().Contain("keep me");
        shown.Should().Contain("before");
        shown.Should().Contain("after");
    });

    // ── (a) grammar tolerance ───────────────────────────────────────────────

    [Fact]
    public void Regex_Accepts_The_One_Line_Form()
    {
        var m = FencePatterns.ArtifactFenceRegex.Match(OneLineDirective);

        m.Success.Should().BeTrue("a one-line directive is what the model actually emits");
        m.Groups["attrs"].Value.Should().Contain("type=\"mermaid\"");
        m.Groups["code"].Value.Should().Contain("flowchart TD");
        m.Groups["code"].Value.Should().Contain("ตอบกลับ");
    }

    [Fact]
    public void Regex_Still_Accepts_The_Well_Formed_Block()
    {
        var m = FencePatterns.ArtifactFenceRegex.Match(WellFormed);

        m.Success.Should().BeTrue();
        m.Groups["attrs"].Value.Should().Contain("title=\"Flow\"");
        m.Groups["code"].Value.Trim().Should().Be("flowchart TD\nA --> B");
    }

    [Fact]
    public void Regex_Accepts_A_Fence_The_Model_Never_Closed()
    {
        var m = FencePatterns.ArtifactFenceRegex.Match(Unclosed);

        m.Success.Should().BeTrue("a missing closer must still yield the diagram");
        m.Groups["code"].Value.Trim().Should().Be("flowchart TD\nA --> B");
    }

    // ── the grammar models emit by default, and web already accepts ─────────

    [Fact]
    public void Plain_Mermaid_Fence_Renders_As_A_Canvas_Eligible_Code_Block() => RunSta(() =>
    {
        var shown = VisibleText(MermaidFence);

        shown.Should().Contain("flowchart TD", "the diagram source is shown");
        shown.Should().Contain("mermaid", "the language label is shown");
        shown.Should().Contain("Open in Canvas", "the canvas affordance must be offered");
    });
}

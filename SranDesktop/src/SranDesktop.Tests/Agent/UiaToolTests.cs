using FluentAssertions;
using SranDesktop.Infrastructure.Native.Uia;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>UIA layer: element-line formatting, listing cap, and snapshot
/// staleness — all against plain data records, no real UI Automation.</summary>
public class UiaToolTests
{
    private static UiaElementInfo Button(string name = "Send reply") =>
        new("button", name, IsEnabled: true, X: 912, Y: 640, Width: 80, Height: 28);

    // ── formatting ────────────────────────────────────────────────────────────

    [Fact]
    public void FormatElement_Renders_The_Compact_Indexed_Line()
    {
        UiaFormatter.FormatElement(3, Button())
            .Should().Be("[3] button \"Send reply\" enabled bounds=(912,640,80,28)");
    }

    [Fact]
    public void FormatElement_Includes_Value_And_Toggle_State_When_Present()
    {
        var edit = new UiaElementInfo("edit", "Search customer", true, 120, 88, 240, 24, Value: "");
        UiaFormatter.FormatElement(4, edit)
            .Should().Be("[4] edit \"Search customer\" enabled value=\"\" bounds=(120,88,240,24)");

        var checkbox = new UiaElementInfo("checkbox", "Remember me", true, 10, 10, 20, 20, Toggled: false);
        UiaFormatter.FormatElement(7, checkbox)
            .Should().Contain("checked=false");

        var disabled = new UiaElementInfo("button", "OK", false, 0, 0, 10, 10);
        UiaFormatter.FormatElement(0, disabled).Should().Contain(" disabled ");
    }

    [Fact]
    public void FormatElement_Truncates_Long_Values_And_Flattens_Newlines()
    {
        var doc = new UiaElementInfo("document", "Body", true, 0, 0, 100, 100,
            Value: new string('x', 200) + "\nsecond line");

        var line = UiaFormatter.FormatElement(0, doc);

        line.Should().Contain("…");
        line.Should().NotContain("\n" + "second"); // value newlines flattened
        line.Length.Should().BeLessThan(220);
    }

    [Fact]
    public void BuildListing_Caps_Output_And_Notes_The_Omission()
    {
        var elements = Enumerable.Range(0, 10).Select(i => Button($"b{i}")).ToList();

        var listing = UiaFormatter.BuildListing(elements, totalFound: 450, cap: 10);

        listing.Should().Contain("[9] button \"b9\"");
        listing.Should().Contain("(+440 more elements omitted");
    }

    [Fact]
    public void BuildListing_Without_Overflow_Has_No_Note()
    {
        var listing = UiaFormatter.BuildListing(new[] { Button() }, totalFound: 1);

        listing.Should().NotContain("omitted");
        listing.Should().Be("[0] button \"Send reply\" enabled bounds=(912,640,80,28)");
    }

    // ── snapshot staleness ────────────────────────────────────────────────────

    [Fact]
    public void Resolving_Before_Any_Snapshot_Reports_Stale()
    {
        var store = new UiaSnapshotStore();

        store.TryGet(0, out _, out var error).Should().BeFalse();
        error.Should().Be(UiaSnapshotStore.StaleError);
    }

    [Fact]
    public void Resolving_An_Out_Of_Range_Id_Reports_Stale()
    {
        var store = new UiaSnapshotStore();
        store.Set(new object[] { new(), new() });

        store.TryGet(2, out _, out var error).Should().BeFalse();
        error.Should().Be(UiaSnapshotStore.StaleError);
        store.TryGet(-1, out _, out _).Should().BeFalse();
    }

    [Fact]
    public void A_New_Snapshot_Replaces_The_Old_One_Wholesale()
    {
        var store = new UiaSnapshotStore();
        var first = new object();
        store.Set(new[] { first, new object(), new object() });
        var second = new object();
        store.Set(new[] { second });

        store.TryGet(0, out var resolved, out _).Should().BeTrue();
        resolved.Should().BeSameAs(second);
        store.TryGet(1, out _, out var error).Should().BeFalse("old ids must not survive re-read");
        error.Should().Be(UiaSnapshotStore.StaleError);
    }
}

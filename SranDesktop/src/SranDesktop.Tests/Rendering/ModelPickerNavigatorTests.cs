using FluentAssertions;
using SranDesktop.Presentation.Services;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>Phase C PART 4: model picker roving-focus index math (the actual
/// focus move + render is a visual check).</summary>
public class ModelPickerNavigatorTests
{
    [Theory]
    [InlineData(0, PickerNavKey.Down, 1)]
    [InlineData(4, PickerNavKey.Down, 0)]   // wraps to top
    [InlineData(0, PickerNavKey.Up, 4)]     // wraps to bottom
    [InlineData(2, PickerNavKey.Up, 1)]
    [InlineData(3, PickerNavKey.Home, 0)]
    [InlineData(1, PickerNavKey.End, 4)]
    [InlineData(2, PickerNavKey.None, 2)]   // no-op
    public void Wrapping_Navigation(int current, PickerNavKey key, int expected)
    {
        ModelPickerNavigator.NextIndex(current, count: 5, key).Should().Be(expected);
    }

    [Fact]
    public void From_No_Selection_Down_Goes_First_Up_Goes_Last()
    {
        ModelPickerNavigator.NextIndex(-1, 5, PickerNavKey.Down).Should().Be(0);
        ModelPickerNavigator.NextIndex(-1, 5, PickerNavKey.Up).Should().Be(4);
    }

    [Fact]
    public void Clamping_Mode_Does_Not_Wrap()
    {
        ModelPickerNavigator.NextIndex(4, 5, PickerNavKey.Down, wrap: false).Should().Be(4);
        ModelPickerNavigator.NextIndex(0, 5, PickerNavKey.Up, wrap: false).Should().Be(0);
    }

    [Fact]
    public void Empty_List_Returns_Negative()
    {
        ModelPickerNavigator.NextIndex(0, 0, PickerNavKey.Down).Should().Be(-1);
    }
}

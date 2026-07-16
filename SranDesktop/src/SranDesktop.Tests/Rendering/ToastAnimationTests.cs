using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using FluentAssertions;
using SranDesktop.Presentation.Controls;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>FIX 8 BUG 1: copying a message crashed the app — the toast card's
/// entrance animation called BeginAnimation on the DataTemplate's
/// TranslateTransform, which WPF freezes and shares across template instances.
/// These tests exercise the exact crash input (a frozen transform) against the
/// fixed path. WPF UIElements are thread-affine to STA, so each body runs on a
/// dedicated STA thread.</summary>
public class ToastAnimationTests
{
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

    private static Border CardWithFrozenTransform()
    {
        var transform = new TranslateTransform();
        transform.Freeze();
        return new Border { RenderTransform = transform };
    }

    [Fact]
    public void Frozen_template_transform_is_replaced_with_an_unfrozen_one() => RunSta(() =>
    {
        var card = CardWithFrozenTransform();

        var translate = ToastHost.PrepareEntranceTransform(card);

        translate.IsFrozen.Should().BeFalse();
        card.RenderTransform.Should().BeSameAs(translate);
    });

    [Fact]
    public void Unfrozen_transform_is_kept_as_is() => RunSta(() =>
    {
        var existing = new TranslateTransform();
        var card = new Border { RenderTransform = existing };

        ToastHost.PrepareEntranceTransform(card).Should().BeSameAs(existing);
    });

    [Fact]
    public void Non_translate_transform_gets_a_fresh_animatable_one() => RunSta(() =>
    {
        // Transform.Identity (the RenderTransform default) is frozen too.
        var card = new Border { RenderTransform = Transform.Identity };

        var translate = ToastHost.PrepareEntranceTransform(card);

        translate.IsFrozen.Should().BeFalse();
        card.RenderTransform.Should().BeSameAs(translate);
    });

    [Fact]
    public void Real_toast_template_card_survives_the_entrance_animation() => RunSta(() =>
    {
        // Instantiate the REAL ToastHost XAML and stamp its actual card
        // DataTemplate — the exact object graph the crash came from.
        var host = new ToastHost();
        var items = (ItemsControl)host.FindName("Items")!;
        var card = (Border)items.ItemTemplate.LoadContent();

        // Precondition of the live crash: WPF hands out the template's
        // constant <TranslateTransform /> as a frozen shared instance.
        card.RenderTransform.Should().BeOfType<TranslateTransform>()
            .Which.IsFrozen.Should().BeTrue(
                "the template transform being frozen is what crashed copy");

        var act = () => ToastHost.RunEntranceAnimation(card);

        act.Should().NotThrow();
        card.RenderTransform.Should().BeOfType<TranslateTransform>()
            .Which.IsFrozen.Should().BeFalse();
    });

    [Fact]
    public void Entrance_animation_never_throws_on_a_frozen_transform() => RunSta(() =>
    {
        // The pre-fix code threw InvalidOperationException here ("Cannot
        // animate the 'Y' property ... because the object is sealed or
        // frozen"), which took down the whole app on copy.
        var card = CardWithFrozenTransform();

        var act = () => ToastHost.RunEntranceAnimation(card);

        act.Should().NotThrow();
        card.RenderTransform.Should().BeOfType<TranslateTransform>()
            .Which.IsFrozen.Should().BeFalse();
    });
}

using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.ViewModels;
using Xunit;
using WpfApp = System.Windows.Application;

namespace SranDesktop.UiTests;

/// <summary>
/// STA visual-tree gate for the tool-execution card in <c>MessageBubble.xaml</c>.
/// Closes gap #1 from the round-1 gate tests: those assert VM state, but would stay
/// green if a binding (e.g. <c>{Binding HumanError}</c>) were deleted. This test loads
/// the REAL <c>ToolExecutionsList</c> ItemTemplate, applies it to a card VM, realizes
/// the visual tree in memory, and asserts the bound TEXT/effective visibility — so
/// removing or renaming those bindings turns it red before the round-2 rewrite.
///
/// WPF traps handled: (1) all WPF objects are built on a dedicated STA thread with a
/// single Application whose resources merge the same dictionaries App.xaml loads
/// (brushes / Geo_* / styles); (2) the card is Measure/Arrange/UpdateLayout'd so the
/// template is applied; (3) visibility is read as EFFECTIVE visibility by walking
/// ancestors — an off-screen (never-shown) element always reports IsVisible=false, so
/// that property is unusable here.
/// </summary>
public sealed class MessageBubbleBindingTests : IClassFixture<StaWpfApplicationFixture>
{
    private readonly StaWpfApplicationFixture _sta;

    public MessageBubbleBindingTests(StaWpfApplicationFixture sta) => _sta = sta;

    // ── Case 1: failed → human error visible with resolved text + fail glyph ──

    [Fact]
    public void Failed_Card_Shows_Localized_HumanError_And_Fail_Glyph()
        => _sta.Invoke(() =>
        {
            var original = LocaleService.Instance.Locale;
            try
            {
                LocaleService.Instance.Locale = "th";
                var vm = new ToolExecutionItemViewModel("c1", "computer_type", "{}");
                vm.Complete(FailedFocusLost());
                vm.IsFailed.Should().BeTrue();
                vm.HumanError.Should().NotBeNullOrWhiteSpace();

                var card = RealizeCard(vm);
                var texts = Descendants<TextBlock>(card).ToList();

                // Regression guard: a TextBlock actually surfaces HumanError's resolved
                // value (deleting {Binding HumanError} makes this fail).
                texts.Should().Contain(
                    t => t.Text == vm.HumanError && EffectivelyVisible(t, card),
                    "the failed card must surface HumanError text");

                // Fail glyph (Geo_Close) is visible; the amber "verifying" label is not.
                HasVisibleIcon(card, "Geo_Close").Should().BeTrue();
                var verifying = LocaleService.Instance.T("tool.verifying");
                texts.Where(t => t.Text == verifying)
                     .Any(t => EffectivelyVisible(t, card)).Should().BeFalse();
            }
            finally { LocaleService.Instance.Locale = original; }
        });

    // ── Case 2: verification_pending → amber label, no fail glyph, no human error ──

    [Fact]
    public void VerificationPending_Card_Shows_Amber_Verifying_Label_Only()
        => _sta.Invoke(() =>
        {
            var original = LocaleService.Instance.Locale;
            try
            {
                LocaleService.Instance.Locale = "th";
                var vm = new ToolExecutionItemViewModel("c1", "computer_type", "{}");
                vm.Complete(Unverified()); // Success=true, no UnifiedResult → verification_pending
                vm.IsVerificationPending.Should().BeTrue();
                vm.HumanError.Should().BeNull();

                var card = RealizeCard(vm);
                var texts = Descendants<TextBlock>(card).ToList();

                var verifying = LocaleService.Instance.T("tool.verifying");
                texts.Should().Contain(
                    t => t.Text == verifying && EffectivelyVisible(t, card),
                    "an amber card shows the localized verifying label");
                HasVisibleIcon(card, "Geo_Close").Should().BeFalse("amber is not a failure");
            }
            finally { LocaleService.Instance.Locale = original; }
        });

    // ── Case 3: verified → green check, no amber label, no human error ──

    [Fact]
    public void Verified_Card_Shows_Check_Not_Amber_Or_Error()
        => _sta.Invoke(() =>
        {
            var vm = new ToolExecutionItemViewModel("c1", "read_file", "{}");
            vm.Complete(Ok(), requireVerification: false); // real nested-child path → verified
            vm.IsOk.Should().BeTrue();

            var card = RealizeCard(vm);
            var texts = Descendants<TextBlock>(card).ToList();

            HasVisibleIcon(card, "Geo_Check").Should().BeTrue();
            HasVisibleIcon(card, "Geo_Close").Should().BeFalse();
            var verifying = LocaleService.Instance.T("tool.verifying");
            texts.Where(t => t.Text == verifying)
                 .Any(t => EffectivelyVisible(t, card)).Should().BeFalse();
        });

    // ── Case 4: raw code (FullResult) reachable in the expanded state ──

    [Fact]
    public void Expanded_Failed_Card_Exposes_Raw_Code_In_Output()
        => _sta.Invoke(() =>
        {
            var vm = new ToolExecutionItemViewModel("c1", "computer_type", "{}");
            vm.Complete(FailedFocusLost());
            vm.IsExpanded = true; // progressive disclosure opened

            var card = RealizeCard(vm);
            var texts = Descendants<TextBlock>(card).ToList();

            // The Output block binds FullResult (raw "Error: FocusLost: …"); only the
            // technical text carries "FocusLost", so it is an unambiguous marker.
            texts.Should().Contain(
                t => t.Text != null && t.Text.Contains("FocusLost") && EffectivelyVisible(t, card),
                "the raw code must stay reachable when the card is expanded");
        });

    // ── Case 5 (2b): retrying → amber hint visible, no red X ──────────────────

    [Fact]
    public void Retrying_Card_Shows_Amber_Hint_Not_Fail_Glyph()
        => _sta.Invoke(() =>
        {
            var original = LocaleService.Instance.Locale;
            try
            {
                LocaleService.Instance.Locale = "en";
                var vm = new ToolExecutionItemViewModel("c1", "computer_type", "{}");
                vm.BeginRetry(2, 3, "FocusLost");
                vm.IsRetrying.Should().BeTrue();

                var card = RealizeCard(vm);
                var texts = Descendants<TextBlock>(card).ToList();

                // Regression guard: a TextBlock surfaces RetryHint (deleting the binding fails this).
                texts.Should().Contain(
                    t => t.Text == vm.RetryHint && EffectivelyVisible(t, card),
                    "the retrying card must surface the RetryHint");
                HasVisibleIcon(card, "Geo_Close").Should().BeFalse("retrying is amber, not a failure");
            }
            finally { LocaleService.Instance.Locale = original; }
        });

    // ── realization + tree helpers (run on the STA thread) ──────────────────────

    /// <summary>Extract the real ToolExecutions ItemTemplate from MessageBubble.xaml and
    /// apply it to <paramref name="vm"/> via a ContentControl, then lay it out in memory.</summary>
    private static FrameworkElement RealizeCard(ToolExecutionItemViewModel vm)
    {
        var bubble = new MessageBubble();
        var list = bubble.FindName("ToolExecutionsList") as ItemsControl
            ?? throw new InvalidOperationException(
                "ToolExecutionsList not found — the x:Name test seam was removed.");
        var template = list.ItemTemplate
            ?? throw new InvalidOperationException("ToolExecutionsList has no ItemTemplate.");

        var host = new ContentControl { ContentTemplate = template, Content = vm };
        var root = new Border { Child = host, Width = 700, Height = 500 };
        root.Measure(new Size(700, 500));
        root.Arrange(new Rect(0, 0, 700, 500));
        root.UpdateLayout();
        return host;
    }

    private static IEnumerable<T> Descendants<T>(DependencyObject root) where T : DependencyObject
    {
        var count = VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is T typed) yield return typed;
            foreach (var d in Descendants<T>(child)) yield return d;
        }
    }

    /// <summary>Effective visibility for an in-memory (never-shown) tree: the element and
    /// every ancestor up to the host must be Visibility.Visible. (IsVisible is always
    /// false without a live PresentationSource, so it cannot be used.)</summary>
    private static bool EffectivelyVisible(DependencyObject element, DependencyObject host)
    {
        for (DependencyObject? cur = element; cur is not null; cur = VisualTreeHelper.GetParent(cur))
        {
            if (cur is UIElement ue && ue.Visibility != Visibility.Visible) return false;
            if (ReferenceEquals(cur, host)) break;
        }
        return true;
    }

    /// <summary>True when an IconView carrying the named geometry resource is effectively
    /// visible — the status glyph binds its Visibility to the card state.</summary>
    private static bool HasVisibleIcon(DependencyObject host, string geometryKey)
    {
        var geometry = WpfApp.Current.Resources[geometryKey] as Geometry;
        geometry.Should().NotBeNull($"resource {geometryKey} must resolve");
        return Descendants<IconView>(host)
            .Any(iv => ReferenceEquals(iv.Geometry, geometry) && EffectivelyVisible(iv, host));
    }

    // ── event builders ──────────────────────────────────────────────────────────

    private static AgentToolResultEvent FailedFocusLost() => new(
        "c1", "computer_type", "{}",
        Success: false,
        ResultPreview: "Error: FocusLost: the expected receiver lost foreground before dispatch.",
        DurationMs: 5,
        FullResult: "Error: FocusLost: the expected receiver lost foreground before dispatch.");

    private static AgentToolResultEvent Unverified() => new(
        "c1", "computer_type", "{}",
        Success: true, ResultPreview: "Input accepted; awaiting confirmation.",
        DurationMs: 8, FullResult: "Input accepted; awaiting confirmation.");

    private static AgentToolResultEvent Ok() => new(
        "c1", "read_file", "{}",
        Success: true, ResultPreview: "ok", DurationMs: 3, FullResult: "ok");
}

/// <summary>
/// Owns a single STA thread with one WPF <see cref="Application"/> whose resources merge
/// the same dictionaries App.xaml loads, so controls built on it resolve brushes, Geo_*
/// geometries and styles. Test bodies run via <see cref="Invoke"/> to stay on that thread.
/// </summary>
public sealed class StaWpfApplicationFixture : IDisposable
{
    private readonly Thread _thread;
    private Dispatcher _dispatcher = null!;
    private readonly SemaphoreSlim _ready = new(0, 1);

    public StaWpfApplicationFixture()
    {
        _thread = new Thread(Main) { IsBackground = true, Name = "sta-wpf-test" };
        _thread.SetApartmentState(ApartmentState.STA);
        _thread.Start();
        _ready.Wait();
    }

    public void Invoke(Action action) => _dispatcher.Invoke(action);

    private void Main()
    {
        // One Application per process; create it if no prior test did, then merge the
        // App.xaml dictionaries (creating the Application also registers the pack scheme
        // the ResourceDictionary Source URIs need).
        var app = WpfApp.Current ?? new WpfApp { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        foreach (var source in new[]
        {
            "pack://application:,,,/SranDesktop.Presentation;component/Resources/Themes/Dark.xaml",
            "pack://application:,,,/SranDesktop.Presentation;component/Resources/Styles.xaml",
            "pack://application:,,,/SranDesktop.Presentation;component/Resources/Icons.xaml",
        })
        {
            app.Resources.MergedDictionaries.Add(
                new ResourceDictionary { Source = new Uri(source, UriKind.Absolute) });
        }

        _dispatcher = Dispatcher.CurrentDispatcher;
        _ready.Release();
        Dispatcher.Run();
    }

    public void Dispose()
    {
        _dispatcher.InvokeShutdown();
        _thread.Join(TimeSpan.FromSeconds(5));
    }
}

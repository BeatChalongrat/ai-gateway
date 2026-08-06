using System.Collections.Generic;
using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// The shared chat composer (single rounded box). Bound to a <see cref="ChatViewModel"/>;
/// used both in an open conversation and in the empty state.
/// Two instances coexist (home screen + ChatView) sharing the same ViewModel.
/// Popup open/close is managed here so each instance independently controls its own popup.
/// </summary>
public partial class ComposerControl : UserControl
{
    public ComposerControl()
    {
        InitializeComponent();
        DataContextChanged += (_, e) =>
        {
            if (e.OldValue is ChatViewModel oldVm)
            {
                oldVm.ComposerFocusRequested -= OnComposerFocusRequested;
                oldVm.PropertyChanged -= OnViewModelPropertyChanged;
            }
            if (e.NewValue is ChatViewModel newVm)
            {
                newVm.ComposerFocusRequested += OnComposerFocusRequested;
                newVm.PropertyChanged += OnViewModelPropertyChanged;
            }
        };
    }

    /// <summary>
    /// Timestamp of the last model-pill click on THIS instance. The user can
    /// only click the on-screen composer, so a recent click identifies this
    /// instance as the one that must open its popup — even when IsVisible
    /// briefly reads false mid-layout (the home-screen R8 bug).
    /// </summary>
    private long _modelPillClickTick;

    private const int ClickClaimWindowMs = 2000;

    private void ModelSelectorButton_Click(object sender, RoutedEventArgs e)
        => _modelPillClickTick = Environment.TickCount64;

    /// <summary>
    /// Only the on-screen ComposerControl instance opens its popup when the
    /// shared ViewModel sets IsModelPopupOpen = true. "On-screen" = IsVisible,
    /// or (fallback) the instance whose pill was just clicked. The open is
    /// deferred a frame so WPF finishes layout/measure first.
    /// </summary>
    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName != nameof(ChatViewModel.IsModelPopupOpen)) return;
        if (sender is not ChatViewModel vm) return;

        if (vm.IsModelPopupOpen)
        {
            if (!IsVisible && !WasJustClicked())
                return; // the other (hidden) instance — not ours to open

            Dispatcher.InvokeAsync(() =>
            {
                if (vm.IsModelPopupOpen && (IsVisible || WasJustClicked()))
                    ModelSelectorPopup.IsOpen = true;
            }, System.Windows.Threading.DispatcherPriority.Loaded);
        }
        else
        {
            ModelSelectorPopup.IsOpen = false;
        }
    }

    private bool WasJustClicked() =>
        _modelPillClickTick != 0
        && Environment.TickCount64 - _modelPillClickTick < ClickClaimWindowMs;

    private void OnComposerFocusRequested(object? sender, EventArgs e)
    {
        // Only the visible composer should take focus.
        if (!IsVisible) return;
        Dispatcher.InvokeAsync(() =>
        {
            InputBox.Focus();
            InputBox.CaretIndex = InputBox.Text.Length;
        }, System.Windows.Threading.DispatcherPriority.Input);
    }

    private void AttachButton_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is not ChatViewModel vm)
            return;

        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Multiselect = true,
            Title = "Attach files"
        };
        if (dialog.ShowDialog() == true)
            _ = vm.AttachFilesAsync(dialog.FileNames);
    }

    private void ModelSelectorPopup_Opened(object? sender, EventArgs e)
    {
        // Type-to-filter straight away, like the web model picker.
        Dispatcher.InvokeAsync(() => ModelSearchBox.Focus(),
            System.Windows.Threading.DispatcherPriority.Input);
    }

    /// <summary>
    /// When StaysOpen="False" closes the popup (click outside), sync back to ViewModel.
    /// </summary>
    private void ModelSelectorPopup_Closed(object? sender, EventArgs e)
    {
        if (DataContext is ChatViewModel vm && vm.IsModelPopupOpen)
            vm.IsModelPopupOpen = false;
    }

    /// <summary>Phase C PART 4: arrow-key / Home-End / Esc navigation of the model
    /// picker (the rows are Buttons; Enter/Space selects natively when focused).</summary>
    private void ModelPopup_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape)
        {
            if (DataContext is ChatViewModel vm) vm.IsModelPopupOpen = false;
            e.Handled = true;
            return;
        }

        var nav = e.Key switch
        {
            Key.Down => Services.PickerNavKey.Down,
            Key.Up => Services.PickerNavKey.Up,
            Key.Home => Services.PickerNavKey.Home,
            Key.End => Services.PickerNavKey.End,
            _ => Services.PickerNavKey.None,
        };
        if (nav == Services.PickerNavKey.None)
            return;

        var rows = ModelRowButtons();
        if (rows.Count == 0)
            return;

        var focused = Keyboard.FocusedElement as DependencyObject;
        var current = focused is null ? -1 : rows.FindIndex(b => ReferenceEquals(b, focused));
        var next = Services.ModelPickerNavigator.NextIndex(current, rows.Count, nav);
        if (next >= 0)
        {
            rows[next].Focus();
            e.Handled = true;
        }
    }

    /// <summary>The picker row Buttons in display order (across all groups).</summary>
    private List<Button> ModelRowButtons()
    {
        var result = new List<Button>();
        CollectModelRowButtons(ModelPopupRoot, result);
        return result;
    }

    private static void CollectModelRowButtons(DependencyObject root, List<Button> into)
    {
        var count = System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(root, i);
            if (child is Button { DataContext: ViewModels.ModelItemViewModel } b)
                into.Add(b);
            CollectModelRowButtons(child, into);
        }
    }

    private void InputBox_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        // Phase 12 — the slash menu owns the arrow keys + Enter/Tab/Esc while
        // it is open, so the user can drive it without leaving the keyboard.
        // Everything below (Enter-to-send, Shift+Enter, Ctrl+V) is untouched
        // when the menu is closed, which is every keystroke of normal typing.
        if (DataContext is ChatViewModel slashVm && slashVm.IsSlashMenuOpen)
        {
            switch (e.Key)
            {
                case Key.Down:
                    e.Handled = true;
                    slashVm.MoveSlashSelection(1);
                    return;
                case Key.Up:
                    e.Handled = true;
                    slashVm.MoveSlashSelection(-1);
                    return;
                case Key.Escape:
                    e.Handled = true;
                    slashVm.DismissSlashMenu();
                    return;
                case Key.Enter:
                case Key.Tab:
                    // An IME committing a candidate reports ImeProcessedKey;
                    // that Enter means "accept the candidate", never "run the
                    // command" — same guard as web's isComposing check.
                    if (e.Key == Key.Enter && e.ImeProcessedKey == Key.ImeProcessed)
                        break;
                    if (slashVm.RunSelectedSlashCommand())
                    {
                        e.Handled = true;
                        return;
                    }
                    break;
            }
        }

        // Enter sends, Shift+Enter inserts a newline.
        if (e.Key == Key.Enter && !Keyboard.IsKeyDown(Key.LeftShift) && !Keyboard.IsKeyDown(Key.RightShift))
        {
            e.Handled = true;
            if (DataContext is ChatViewModel vm && vm.SendCommand.CanExecute(null))
                vm.SendCommand.Execute(null);
            return;
        }

        // Ctrl+V with an image on the clipboard pastes it as an attachment (R9 D1).
        // The image takes precedence over any text/HTML alternative shipped with
        // it (browser and Word copies carry both) — see ClipboardPasteDecider.
        if (e.Key == Key.V && Keyboard.Modifiers == ModifierKeys.Control
            && Services.ClipboardPasteDecider.Decide(
                   Clipboard.ContainsImage(), Clipboard.ContainsText()) == Services.PasteMode.Image)
        {
            if (Clipboard.GetImage() is { } image && DataContext is ChatViewModel vm)
            {
                e.Handled = true;
                _ = vm.AttachClipboardImageAsync(image);
            }
        }
    }

    // Drag-and-drop straight onto the composer box (R9 D2).
    private void ComposerBorder_DragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(DataFormats.FileDrop)
            ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled = true;
    }

    private void ComposerBorder_Drop(object sender, DragEventArgs e)
    {
        if (!e.Data.GetDataPresent(DataFormats.FileDrop)
            || e.Data.GetData(DataFormats.FileDrop) is not string[] files
            || DataContext is not ChatViewModel vm)
            return;
        e.Handled = true;
        _ = vm.AttachFilesAsync(files);
    }
}

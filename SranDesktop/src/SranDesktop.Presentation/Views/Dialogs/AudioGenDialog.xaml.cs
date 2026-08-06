using System.Windows;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>
/// Audio generation form (Phase 14 Task 3) — speech or music, submitted
/// directly to the media endpoint and polled to completion.
/// </summary>
public partial class AudioGenDialog : Window
{
    private readonly AudioGenViewModel _viewModel;

    public AudioGenDialog(AudioGenViewModel viewModel)
    {
        InitializeComponent();
        _viewModel = viewModel;
        DataContext = viewModel;
        // Close as soon as the audio lands — the result is attached to the
        // conversation, so keeping the form open would just be in the way.
        viewModel.Completed += (_, _) => Dispatcher.Invoke(() => DialogResult = true);
    }

    /// <summary>The music radio is bound one-way (IsMusic is derived), so the
    /// flip back to music happens here.</summary>
    private void MusicMode_Checked(object sender, RoutedEventArgs e) =>
        _viewModel.IsSpeech = false;
}

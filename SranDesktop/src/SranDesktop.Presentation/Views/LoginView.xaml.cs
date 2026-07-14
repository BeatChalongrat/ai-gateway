using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

public partial class LoginView : UserControl
{
    private LoginViewModel? _viewModel;

    public LoginView()
    {
        InitializeComponent();
        DataContextChanged += LoginView_DataContextChanged;
    }

    private void LoginView_DataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (_viewModel is not null)
            _viewModel.PropertyChanged -= ViewModel_PropertyChanged;
        _viewModel = e.NewValue as LoginViewModel;
        if (_viewModel is not null)
            _viewModel.PropertyChanged += ViewModel_PropertyChanged;
    }

    private void ViewModel_PropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (_viewModel is null)
            return;

        // Keep the PasswordBox in sync when the reveal TextBox edited the value
        // (PasswordBox has no bindable Password) and clear it after login.
        if (e.PropertyName == nameof(LoginViewModel.Password)
            && PasswordInput.Password != _viewModel.Password)
        {
            PasswordInput.Password = _viewModel.Password;
        }
    }

    // ── Entrance: fade in each time the overlay becomes visible (startup
    //    without a stored session, and again after logout). ──────────────────

    private void LoginView_IsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (e.NewValue is not true)
            return;

        var fade = new DoubleAnimation(0, 1, TimeSpan.FromSeconds(0.4))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
        };
        BeginAnimation(OpacityProperty, fade);

        Dispatcher.BeginInvoke(() =>
        {
            if (_viewModel?.IsPasswordHidden is false)
                PasswordRevealBox.Focus();
            else
                EmailBox.Focus();
        }, System.Windows.Threading.DispatcherPriority.Input);
    }

    // ── Input focus highlight (blue ring on the wrapping border) ────────────

    private void EmailBox_GotFocus(object sender, RoutedEventArgs e) =>
        SetFocusRing(EmailBorder, focused: true);

    private void EmailBox_LostFocus(object sender, RoutedEventArgs e) =>
        SetFocusRing(EmailBorder, focused: false);

    private void PasswordField_GotFocus(object sender, RoutedEventArgs e) =>
        SetFocusRing(PasswordBorder, focused: true);

    private void PasswordField_LostFocus(object sender, RoutedEventArgs e) =>
        SetFocusRing(PasswordBorder, focused: false);

    private void SetFocusRing(Border border, bool focused) =>
        border.BorderBrush = FindResource(focused ? "AccentBlueBrush" : "BorderBrush") as Brush;

    // ── Password plumbing ────────────────────────────────────────────────────

    private void PasswordInput_PasswordChanged(object sender, RoutedEventArgs e)
    {
        if (DataContext is LoginViewModel vm)
            vm.Password = ((PasswordBox)sender).Password;
    }

    private void PasswordInput_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && DataContext is LoginViewModel vm && vm.LoginCommand.CanExecute(null))
            vm.LoginCommand.Execute(null);
    }
}

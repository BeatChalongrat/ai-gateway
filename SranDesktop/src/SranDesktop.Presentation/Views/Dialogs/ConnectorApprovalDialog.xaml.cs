using System.Windows;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>
/// The screen that decides whether a connector may run.
///
/// Two things make it a decision rather than a formality. Closing it any other
/// way is a denial — a dialog dismissed by a stray Escape must not leave a
/// connector approved. And the evidence attached to the outcome is minted here,
/// at the moment of the click, through <see
/// cref="UserApprovalEvidence.FromUserGesture"/>, which refuses while the agent
/// is driving the desktop. That is the attack this guards against: the agent has
/// a click tool, and this window has an Allow button.
/// </summary>
public partial class ConnectorApprovalDialog : Window
{
    public ConnectorApprovalDialog(ConnectorApprovalRequest request)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        DataContext = new ConnectorApprovalViewModel(request);
    }

    /// <summary>The decision, defaulting to denial.</summary>
    public ConnectorApprovalDecision Decision { get; private set; } =
        ConnectorApprovalDecision.Deny("The approval window was closed without a decision.");

    /// <summary>Set when the user clicked but the gesture could not be accepted —
    /// shown to them rather than swallowed, because a decision that silently did
    /// nothing is worse than one that says why.</summary>
    public string? RejectedGestureReason { get; private set; }

    private void Approve_Click(object sender, RoutedEventArgs e) =>
        Decide(ConnectorApprovalOutcome.Approved, "Allow button");

    private void ApproveVersion_Click(object sender, RoutedEventArgs e) =>
        Decide(ConnectorApprovalOutcome.ApprovedForThisVersion, "Allow this version button");

    private void Disable_Click(object sender, RoutedEventArgs e) =>
        Decide(ConnectorApprovalOutcome.Disabled, "Disable button");

    private void Revoke_Click(object sender, RoutedEventArgs e) =>
        Decide(ConnectorApprovalOutcome.Revoked, "Revoke trust button");

    private void Deny_Click(object sender, RoutedEventArgs e) => Deny("The user declined.");

    /// <summary>
    /// Escape means the same as pressing Don't allow, and is recorded the same
    /// way. Leaving it to WPF's Cancel handling would set DialogResult to false,
    /// which the caller reads as "dismissed" and stores nothing — so the user's
    /// refusal would leave no trace and the next launch would re-ask from
    /// scratch with no record that they had already said no.
    /// </summary>
    protected override void OnKeyDown(System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == System.Windows.Input.Key.Escape && DialogResult is null)
        {
            Deny("The user dismissed the approval window.");
            e.Handled = true;
            return;
        }
        base.OnKeyDown(e);
    }

    private void Deny(string reason)
    {
        Decision = ConnectorApprovalDecision.Deny(reason);
        DialogResult = true;
    }

    private void Decide(ConnectorApprovalOutcome outcome, string gesture)
    {
        try
        {
            var evidence = UserApprovalEvidence.FromUserGesture("user:desktop", gesture);
            Decision = new ConnectorApprovalDecision(outcome, evidence.Principal);
            Evidence = evidence;
            DialogResult = true;
        }
        catch (UnauthorizedAccessException ex)
        {
            // The click arrived while the agent was working the UI. Refuse it and
            // leave the window open so a person can decide for themselves.
            RejectedGestureReason = ex.Message;
            Decision = ConnectorApprovalDecision.Deny(ex.Message);
        }
    }

    /// <summary>Proof of the gesture behind <see cref="Decision"/>. Null for a
    /// denial, which needs none.</summary>
    public UserApprovalEvidence? Evidence { get; private set; }
}

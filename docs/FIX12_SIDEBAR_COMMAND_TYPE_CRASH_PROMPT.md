# FIX 12 — Sidebar "Something went wrong": RelayCommand<T> crashes on mixed-type sidebar rows

Live crash on the running desktop app (dialog: "SRAN Desktop hit an unexpected error"):

```
System.ArgumentException: Parameter "parameter" (object) cannot be of type
SranDesktop.Presentation.ViewModels.SidebarGroupHeaderRow, as the command type requires
an argument of type SranDesktop.Presentation.ViewModels.ConversationItemViewModel.
  at CommunityToolkit.Mvvm.Input.RelayCommand`1.ThrowArgumentExceptionForInvalidCommandArgument
  at CommunityToolkit.Mvvm.Input.RelayCommand`1.CanExecute
  at System.Windows.Controls.Primitives.ButtonBase.UpdateCanExecute()
```

Desktop-only (`SranDesktop/`). Do NOT `git push`, do NOT edit to force a test pass.

## Root cause (verified in code — re-confirm lines in R0)
- `ConversationListViewModel.SidebarRows` is `ObservableCollection<object>` mixing 4 row types (ConversationItemViewModel / FolderItemViewModel / FolderConversationRow / SidebarGroupHeaderRow) — `ConversationListViewModel.cs:140`, built by `SidebarRowsBuilder.Build` and refreshed via `RebuildSidebarRowsNow()` (`:229-241`) which does **`Clear()` + re-`Add()` of every row**.
- `SidebarView.xaml` templates bind typed commands with `CommandParameter="{Binding}"` (e.g. `SelectConversationCommand` :89-91, plus TogglePin/Rename/Delete :97-146, OpenMessageHit :438, RenameFolder/DeleteFolder :474-489).
- The commands are generated `RelayCommand<T>` from typed methods (`SelectConversation(ConversationItemViewModel?)` :606, `Rename` :818, `DeleteAsync` :840, `TogglePinAsync` :889, `RenameFolder` :474, `DeleteFolderAsync` :506, `OpenMessageHitAsync` :368, `MoveToNewFolderAsync` :571).
- During the Clear+Add rebuild, WPF re-uses/re-binds item containers across row types; `ButtonBase.UpdateCanExecute` transiently calls `CanExecute` with a wrong-type row (a group header lands on a conversation-row button). **CommunityToolkit's `RelayCommand<T>.CanExecute` THROWS on a non-null wrong-type parameter instead of returning false** — that's the crash. Timing-dependent → looks random (fires on sidebar refresh after a turn/sync/search).

## Fix (make it structurally impossible, all commands in one pass)
**Part A — never throw on wrong-type parameters (the crash killer).**
For EVERY `[RelayCommand]` in `ConversationListViewModel` whose command is bound with `CommandParameter="{Binding}"` from the mixed `SidebarRows` template (list above): change the method signature to accept `object?` and pattern-match inside — `if (parameter is not ConversationItemViewModel item) return;` — so `CanExecute`/`Execute` on a transient wrong-type row is a silent no-op, not an exception. Keep nullability/behavior otherwise identical. (Alternative if you find a cleaner repo-consistent idiom: a small `SafeRelayCommand<T>` wrapper — but the object-parameter pattern is the standard CommunityToolkit answer; pick one and apply it consistently.) Grep the WHOLE Presentation project for other `RelayCommand<T>` + `CommandParameter="{Binding}"` combos inside mixed/heterogeneous ItemsControls and fix any with the same exposure (the message-search hits list :438 and folder rows :474-489 are already on your list; check ChatViewModel/MessageBubble templates too — single-type lists are safe, only mixed collections crash).

**Part B — reduce the churn that triggers it (cheap hardening, not a rewrite).**
`RebuildSidebarRowsNow` Clear+Add-all forces full container rebind on every refresh (also causes visible flicker). If low-risk, do an in-place diff (skip update when the built rows are element-equal, or replace only changed spans). If that's more than ~an hour of risk, skip it — Part A alone fully fixes the crash; note the churn as a perf follow-up.

## Verify
1. `dotnet build` 0/0 + `dotnet test` green. New test: each fixed command's `CanExecute`/`Execute` with a `SidebarGroupHeaderRow` (and other wrong-type rows) returns/no-ops without throwing.
2. **Live (rebuild + restart the binary first — stale builds fooled us twice):** run the app, force sidebar rebuilds repeatedly (send turns, rename, pin/unpin, search, let server sync repaint) while hovering/clicking rows — no "Something went wrong". The original repro was organic/timing-based, so hammer it: if you can, script rapid `RebuildSidebarRowsNow` calls in a UI test harness with a hovered button. If the timing window can't be provoked live, state UNVERIFIED-live but the type-guard makes the throw impossible by construction.
3. No behavior change: clicking a conversation still opens it; pin/rename/delete/move/folder ops still work (click through each once).

## Report
R0 (confirmed lines) → per-command list of what changed → test results → live-proof (or UNVERIFIED-live + the by-construction argument). One local commit, nothing pushed.

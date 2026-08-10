# DESKTOP FIX — PHASE C.5: Replace native MessageBox with themed dialogs (finish Phase C PART 5)

Bounded cleanup — the one residual from Phase C. ~15 native `System.Windows.MessageBox.Show` calls across 8 ViewModels break the themed aesthetic (default Windows chrome, ignores dark/light). Replace them with the app's own themed dialog. Desktop-only, fully verifiable with `dotnet build` + `dotnet test`. Do not regress Phases 1–16 + A/B/C. Do NOT `git push`.

## Verified call sites (the exact sweep target)
Two kinds, both in `SranDesktop/src/SranDesktop.Presentation/ViewModels/`:

**Confirmations (Yes/No — the important ones):**
- `ConversationListViewModel.cs:481, 760, 821` (delete conversation / folder)
- `KnowledgeBaseViewModel.cs:199, 343` (delete KB / document)
- `MemoryViewModel.cs:157, 337` (delete / clear memory)
- `ProjectViewModel.cs:97` (delete project)
- `ScheduledTasksViewModel.cs:168` (delete task)
- `SkillsViewModel.cs:94` (delete/uninstall skill)

**Error notices (info-only):**
- `ConversationListViewModel.cs:752`, `InlineAttachmentViewModel.cs:101`, `KnowledgeBaseViewModel.cs:245`, `PlanBlockViewModel.cs:297` (error message display)

Existing themed dialogs to mirror for style/behavior: `Views/Dialogs/InputDialog.xaml(.cs)`, `ShareDialog`, `ToolConsentDialog` (custom window chrome, theme brushes, `DarkTitleBarHelper`).

## Ground rules
- Read the existing dialogs (`InputDialog.xaml.cs`, `ShareDialog.xaml.cs`) IN FULL first to match the pattern (window chrome, owner, theming, how they return a result, how VMs invoke them). Follow it exactly — don't invent a new style.
- New strings localized EN/TH (`DictParityTests`). Keep the existing confirmation text/meaning (don't change what's being confirmed).
- Finish: `dotnet build` 0/0, `dotnet test` green, existing suites unmodified.

## Task

1. **New reusable `ConfirmDialog`** (`Views/Dialogs/ConfirmDialog.xaml(.cs)`) mirroring `InputDialog`: themed window chrome, title + message, a destructive-styled confirm button (red, matching the app's Danger button style) + a cancel button, returns a bool. Optional: a variant/flag for destructive vs neutral confirm so the confirm button color/label fits (e.g. "Delete" red vs "OK"). Owner = the active window; centered; Esc = cancel; Enter = confirm (or the safe default — cancel for destructive; state your choice).
2. **A small themed message/notice** path for the info-only error calls — either a lightweight `ConfirmDialog` in OK-only mode, OR route them to the existing **toast system** (Phase 8 `ToastService`) if these are non-blocking notices (an error toast is actually MORE consistent with modern apps than a modal — prefer toast for the info-only error cases unless the flow needs a blocking acknowledgment). Decide per call site and justify.
3. **Invocation from ViewModels:** the VMs currently call `MessageBox.Show` directly (a View concern leaking into the VM). Introduce a minimal abstraction — an `IDialogService`/`IConfirmService` (Application or Presentation interface) with `Task<bool> ConfirmAsync(title, message, destructive)` and `NotifyError(message)` — injected into the VMs, implemented in Presentation to show the themed dialog / toast on the UI thread. This both themes the dialogs AND removes the direct `System.Windows` dependency from the ViewModels (cleaner MVVM + makes the confirm logic unit-testable). Wire it in `AppHostBuilder`.
4. Replace all ~15 call sites to use the injected service. Preserve exact behavior: a "No"/cancel still cancels the action; the confirmed path is unchanged.
5. **Reduced-motion / theme:** the dialog uses theme brushes (`DynamicResource`) so it recolors in light/dark and honors the Phase-8/Phase-C System theme; no hardcoded `Brushes.*`.

## Tests
- `IConfirmService` is now mockable → unit-test that each delete flow calls confirm and only proceeds on true (e.g. `DeleteConversation` with a mocked service returning false does NOT delete; true deletes). This is a real testability win the native `MessageBox` blocked.
- `DictParityTests` green for new strings.
- The dialog window itself (XAML) is a visual check — state it; the logic (service calls, confirm-gates-action) is unit-tested via the abstraction.

## Final verification
1. `dotnet build` 0/0; `dotnet test` green (new confirm-flow tests + `DictParityTests`; A/B/C + earlier suites unmodified).
2. Grep proof: `grep -rn 'MessageBox.Show' SranDesktop/src/SranDesktop.Presentation` returns EMPTY (all 15 replaced) — state it.
3. Confirm every delete/destructive flow still gates on confirmation (behavior unchanged); info-only errors show as themed toast/dialog; ViewModels no longer reference `System.Windows.MessageBox`.
4. Summarize changes file by file. This closes Phase C PART 5's residual and completes desktop Track-1. Next: push the accumulated desktop work (Track-1: Phases A/B/C/C.5 + the storage-gitignore fix) to GitLab so the remote is complete and buildable.

# RS-001 & Regression Matrix — Real-GUI Runbook (isolated VM only)

> **Status: NOT RUNTIME VERIFIED.** Everything below is prepared but has **not**
> been executed. Real-GUI automation types into, clicks, closes and reads *live*
> windows. It must run **only** on a throwaway, isolated Windows VM — **never on
> a primary desktop**. A prior smoke test on a live desktop overwrote a real
> Notepad tab; this runbook and its guard exist so that cannot recur.

This runbook drives the **existing** user-facing path
(`ChatViewModel → ChatOrchestrator → DurableTaskRuntime → real MCP tools →
real Windows apps → independent verifier`) through the already-wired
`RealRuntimeIntegrationRunner`. It does **not** introduce a parallel Computer-Use
pipeline.

---

## 0. Safety model — `RealGuiEnvironmentGuard`

`SranDesktop.Infrastructure.Native.RealGuiEnvironmentGuard` **denies by default**
and permits real-GUI automation only when **three independent signals agree**:

| # | Signal | Env var / file | Required value |
|---|--------|----------------|----------------|
| 1 | Exact opt-in token | `SRAN_ALLOW_REAL_GUI_AUTOMATION` | `I-UNDERSTAND-THIS-DESTROYS-GUI-STATE` |
| 2 | Declared sandbox host = this machine | `SRAN_GUI_SANDBOX_HOST` | the VM's `MachineName` |
| 3 | On-disk sentinel marker | file at `SRAN_GUI_SANDBOX_MARKER` | text containing the token **and** the machine name |

A bare `1`/`true` is deliberately rejected. A stray inherited env var cannot
trigger it because the marker file must be authored on the VM on purpose, and the
declared host must equal the current machine. **Every real-GUI runner must call
`guard.EnsurePermitted()` before touching a window.**

The guard's behaviour (deny-by-default + every partial-signal deny path) is unit
tested in `RealGuiEnvironmentGuardTests` and runs in the normal suite — proving
the block holds on any non-isolated machine, including CI and the dev desktop.

---

## 1. Provision the isolated VM

1. Create a fresh Windows 11 VM (Hyper-V / VirtualBox / a disposable cloud
   image). **No real user files, no signed-in accounts, no production data.**
2. Enable an **interactive GUI session** kept unlocked for the run (real UIA
   needs a visible desktop — automation cannot drive a locked/headless session).
3. Install the .NET 8 SDK and the SranDesktop build output on the VM.
4. Note the VM's machine name: `powershell -c "$env:COMPUTERNAME"`.

## 2. Arm the guard (on the VM only)

```powershell
$env:SRAN_ALLOW_REAL_GUI_AUTOMATION = "I-UNDERSTAND-THIS-DESTROYS-GUI-STATE"
$env:SRAN_GUI_SANDBOX_HOST          = $env:COMPUTERNAME
$env:SRAN_GUI_SANDBOX_MARKER        = "C:\sran-sandbox\marker.txt"

New-Item -ItemType Directory -Force C:\sran-sandbox | Out-Null
"I-UNDERSTAND-THIS-DESTROYS-GUI-STATE host=$($env:COMPUTERNAME)" |
  Set-Content -Encoding utf8 C:\sran-sandbox\marker.txt
```

Verify the guard now permits **on the VM** (and only there):

```powershell
# expect Permitted = True on the VM; False anywhere else
dotnet test --filter "FullyQualifiedName~RealGuiEnvironmentGuardTests"
```

## 3. Baseline — deterministic RS-001 (safe anywhere, already green)

These exercise the same runtime contract with fake tools (no real windows) and
must be green **before** any real-GUI run:

```powershell
dotnet test --filter "FullyQualifiedName~RS001RegressionTests"       # 8 tests
dotnet test --filter "FullyQualifiedName~DurableTaskRuntimeTests"    # runtime invariants
```

## 4. Live real-GUI RS-001

Drive the real path through `RealRuntimeIntegrationRunner` inside the VM. Wrap the
call with the guard so it aborts unless the VM is armed:

```csharp
new RealGuiEnvironmentGuard().EnsurePermitted();   // throws off the sandbox

// Resolve the app's real DI graph (AppHostBuilder) so the orchestrator uses the
// real MCP tools (open_application, uia_click, read_screen_elements,
// uia_set_value) against real Calculator/Notepad.
var runner = host.Services.GetRequiredService<RealRuntimeIntegrationRunner>();

var report = await runner.RunAsync(
    prompt: "open Calculator and calculate 125 x 48 then write the result into Notepad",
    model: "<configured-model>",
    mode: RuntimeIntegrationMode.DeterministicOrchestration, // then LiveModel
    attempts: 20,
    outputDirectory: @"C:\sran-sandbox\rs001",
    resetAttempt: async (attempt, ct) =>
    {
        // Close any Calculator/Notepad from the prior attempt so each run starts
        // clean; NEVER save Notepad — discard changes.
        foreach (var name in new[] { "Calculator", "Notepad" })
            foreach (var p in Process.GetProcessesByName(name)) { p.CloseMainWindow(); }
        await Task.Delay(500, ct);
    });
```

**Ground truth must come from the machine, not the model or the runtime's own
claims.** For each attempt, independently read via UIA:

* Calculator display element text == `6000`.
* Notepad edit control text == `6000`.

Cross-check these against `report.Attempts[i]` (`VerifiedOutcomes == 5`,
`Completed`, `UnrelatedActions == 0`). A mismatch between UIA ground truth and the
runtime's verdict is a **hard failure** — record it, do not "grade to pass".

Run both modes:
* **DeterministicOrchestration** — scripted model decisions (isolates runtime).
* **LiveModel** — the configured model (measures goal interpretation).

## 5. Regression matrix (Phase 11 / 13)

Repeat the pattern per group, each into its own `outputDirectory`, resetting app
state per attempt and verifying by UIA/DOM/process/file — never model self-grading:

* **B — primitives** (≥50 each): open app, locate window, readiness, focus, UIA
  invoke, click, type, key sequence, read-back, switch app, close app.
* **C — categories** (≥20 each): Calculator, Notepad, local browser harness,
  local UIA app, local terminal command.
* **D — cross-app** (≥20 each): Calc→Notepad, browser form→text report,
  terminal→editor, file-selection→browser upload, Calc→browser form.
* **E — perturbations**: already-open, multi-window, focus stolen, startup delay,
  minimized, moved/resized, multi-monitor, DPI scaling, popup/modal, stale UIA,
  user takeover, keyboard layout/IME.
* **F — recovery**: invalid key schema, target missing, focus lost, app not ready,
  effect mismatch, verifier timeout, provider timeout, unknown outcome,
  capability degradation, plan drift.

RS-002…RS-010 (Phase 13) run the same way — note RS-002/004 **must stop before
Submit**, RS-003 **must not save the file**, RS-006 targets only the
`Test Target` window, RS-007/008/009 inject focus-steal / slow-control /
stale-UIA perturbations, RS-010 pauses after dispatch then resumes with no
duplicate action.

## 6. Cleanup

```powershell
Remove-Item -Recurse -Force C:\sran-sandbox
Remove-Item Env:\SRAN_ALLOW_REAL_GUI_AUTOMATION, Env:\SRAN_GUI_SANDBOX_HOST, Env:\SRAN_GUI_SANDBOX_MARKER
```

Then **discard the VM snapshot.** Never reuse the automated session for real work.

## 7. Pass gates (report PASS / FAIL / NOT VERIFIED per line)

* RS-001 deterministic = **20/20**; live model ≥ **19/20**.
* False step completion = 0; false task completion = 0.
* Wrong receiver = 0; unrelated action = 0; blind retry = 0.
* Calculator-only ≥ 99%; text-editor-only ≥ 99%; browser harness ≥ 98%.
* Cross-app 20-step workflow ≥ 95%.
* UIA/DOM ground truth agrees with the runtime verdict on **every** attempt.
* Full trace reconstruction from SQLite = 100% (`GetEventsAsync(taskId)`).

Until this runbook has actually been executed on a VM, every gate above remains
**NOT RUNTIME VERIFIED** — do not report them as passed from unit/deterministic
tests alone.

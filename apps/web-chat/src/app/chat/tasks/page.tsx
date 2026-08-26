"use client";

// Scheduled Tasks — desktop-only screen, a React port of the WPF
// ScheduledTasksViewModel. Talks ONLY to the host over the typed
// hostApi bridge; there is no HTTP surface for these ops.
//
// IMPORTANT: scheduled tasks run via an in-process timer inside the
// host, so they only ever fire while the SRAN Desktop app is open.
// A task with a nightly cron will not run if the app was closed at
// that time — this is faithful to the current host behavior and is
// surfaced to the user in the header note below.

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { HOSTED_MODE } from "@/lib/auth";
import { hostApi, type TaskDto, type TaskRunDto } from "@/lib/hostApi";

export default function ScheduledTasksPage() {
  if (!HOSTED_MODE) {
    return (
      <div className="flex flex-1 items-center justify-center bg-bg-base px-6">
        <p className="max-w-sm text-center text-sm text-text-muted">
          This screen is available in the SRAN Desktop app.
        </p>
      </div>
    );
  }
  return <ScheduledTasksScreen />;
}

function ScheduledTasksScreen() {
  const [tasks, setTasks] = useState<TaskDto[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<TaskDto | null>(null);
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [historyTask, setHistoryTask] = useState<TaskDto | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const rows = await hostApi.listTasks();
        if (alive) setTasks(rows);
      } catch {
        toast("Couldn't load scheduled tasks.");
      } finally {
        if (alive) setLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(task: TaskDto) {
    setEditing(task);
    setFormOpen(true);
  }

  async function handleRun(task: TaskDto) {
    setRunning((r) => ({ ...r, [task.id]: true }));
    try {
      const res = await hostApi.runTask(task.id);
      if (!res.ok || !res.data) {
        toast(res.error ?? "Task run failed.");
        return;
      }
      const updated = res.data;
      setTasks((list) => list.map((t) => (t.id === updated.id ? updated : t)));
      toast(updated.lastResult ?? "Task finished.");
    } catch {
      toast("Task run failed.");
    } finally {
      setRunning((r) => ({ ...r, [task.id]: false }));
    }
  }

  async function handleDelete(task: TaskDto) {
    if (!window.confirm(`Delete "${task.name}"?`)) return;
    try {
      const res = await hostApi.deleteTask(task.id);
      if (!res.ok) {
        toast(res.error ?? "Couldn't delete task.");
        return;
      }
      setTasks((list) => list.filter((t) => t.id !== task.id));
    } catch {
      toast("Couldn't delete task.");
    }
  }

  async function handleToggle(task: TaskDto, enabled: boolean) {
    // Optimistic — flip locally first, revert on refusal.
    setTasks((list) =>
      list.map((t) => (t.id === task.id ? { ...t, isEnabled: enabled } : t)),
    );
    try {
      const res = await hostApi.setTaskEnabled(task.id, enabled);
      if (!res.ok) {
        setTasks((list) =>
          list.map((t) =>
            t.id === task.id ? { ...t, isEnabled: !enabled } : t,
          ),
        );
        toast(res.error ?? "Couldn't update task.");
        return;
      }
      if (res.data) {
        const updated = res.data;
        setTasks((list) =>
          list.map((t) => (t.id === updated.id ? updated : t)),
        );
      }
    } catch {
      setTasks((list) =>
        list.map((t) => (t.id === task.id ? { ...t, isEnabled: !enabled } : t)),
      );
      toast("Couldn't update task.");
    }
  }

  function handleSaved(task: TaskDto, wasEditing: boolean) {
    setTasks((list) =>
      wasEditing
        ? list.map((t) => (t.id === task.id ? task : t))
        : [task, ...list],
    );
    setFormOpen(false);
    setEditing(null);
  }

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-8 pb-12 md:px-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <svg
            width={14}
            height={14}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M15 6l-6 6 6 6" />
          </svg>
          Back to chat
        </Link>

        <div className="mt-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
              Scheduled tasks
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              Prompts that run on a schedule inside the desktop app.
            </p>
            <p className="mt-2 text-xs text-text-muted">
              Scheduled tasks run only while SRAN Desktop is open (including
              minimized to the tray). If the app is closed at the scheduled
              time, the task runs at the next check after you reopen it — it is
              not run in your absence. A run missed while the app was closed is
              caught up <span className="font-medium text-text-secondary">once</span>, for the
              most recent missed time (within the last 7 days); older missed runs are skipped, not stacked up.
            </p>
          </div>
          {loaded && tasks.length > 0 ? (
            <button
              type="button"
              onClick={openCreate}
              className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border bg-transparent px-3.5 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
            >
              <PlusIcon />
              New task
            </button>
          ) : null}
        </div>

        <div className="mt-8">
          {!loaded ? (
            <div className="flex items-center justify-center py-10 text-text-secondary">
              <Spinner size={20} />
            </div>
          ) : tasks.length === 0 ? (
            <EmptyState onCreate={openCreate} />
          ) : (
            <div className="flex flex-col gap-3">
              {tasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  running={running[task.id] ?? false}
                  onRun={() => void handleRun(task)}
                  onEdit={() => openEdit(task)}
                  onDelete={() => void handleDelete(task)}
                  onToggle={(enabled) => void handleToggle(task, enabled)}
                  onHistory={() => setHistoryTask(task)}
                />
              ))}
            </div>
          )}
        </div>
      </main>

      <TaskFormModal
        open={formOpen}
        task={editing}
        onClose={() => {
          setFormOpen(false);
          setEditing(null);
        }}
        onSaved={handleSaved}
      />

      <HistoryModal task={historyTask} onClose={() => setHistoryTask(null)} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// FIX8 A1.4 — run history (every past attempt, including skips + why)
// ---------------------------------------------------------------------------

const OUTCOME_LABEL: Record<string, string> = {
  ok: "Ran", error: "Failed", catchup: "Caught up", skipped: "Skipped",
};
const REASON_LABEL: Record<string, string> = {
  asleep: "the machine was asleep",
  prev_running: "the previous run was still going",
  others_running: "other tasks were already running",
};

function HistoryModal({ task, onClose }: { task: TaskDto | null; onClose: () => void }) {
  const [runs, setRuns] = useState<TaskRunDto[] | null>(null);
  useEffect(() => {
    if (!task) {
      setRuns(null);
      return;
    }
    setRuns(null);
    void (async () => {
      try {
        setRuns(await hostApi.taskRuns(task.id));
      } catch {
        setRuns([]);
      }
    })();
  }, [task]);

  return (
    <Modal open={task !== null} onClose={onClose} title={task ? `History — ${task.name}` : "History"} description="Every past attempt, including skipped runs and why.">
      {runs === null ? (
        <p className="text-sm text-text-muted">Loading…</p>
      ) : runs.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid="history-empty">No runs yet.</p>
      ) : (
        <ul className="flex flex-col gap-2" data-testid="history-list">
          {runs.map((r) => (
            <li key={r.id} className="rounded-md border border-border bg-bg-secondary/50 px-3 py-2 text-xs" data-outcome={r.outcome}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-text-primary">{OUTCOME_LABEL[r.outcome] ?? r.outcome}</span>
                <span className="text-text-muted">{formatDate(r.ranAt)}</span>
              </div>
              {r.reason ? (
                <p className="mt-0.5 text-text-secondary" title={r.reason}>Skipped because {REASON_LABEL[r.reason] ?? r.reason}.</p>
              ) : null}
              {r.result ? <p className="mt-0.5 line-clamp-2 text-text-muted">{r.result}</p> : null}
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-transparent px-8 py-16 text-center">
      <svg
        width={48}
        height={48}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className="mx-auto mb-6 text-text-muted"
      >
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </svg>
      <h2 className="text-base font-semibold tracking-tight text-text-primary">
        No scheduled tasks
      </h2>
      <p className="mx-auto mt-2 max-w-sm text-sm text-text-secondary">
        Create a task to have a prompt run automatically on a cron schedule.
      </p>
      <button
        type="button"
        onClick={onCreate}
        className="focus-ring mt-6 inline-flex items-center gap-1.5 rounded-lg border border-border bg-transparent px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
      >
        <PlusIcon />
        New task
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Task row
// ---------------------------------------------------------------------------

function TaskRow({
  task,
  running,
  onRun,
  onEdit,
  onDelete,
  onToggle,
  onHistory,
}: {
  task: TaskDto;
  running: boolean;
  onRun: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
  onHistory: () => void;
}) {
  const modeLabel = task.permissionMode === "skip" ? "Never asks" : task.permissionMode === "auto" ? "Auto-approves safe" : "Asks each action";
  return (
    <section className="rounded-xl border border-border bg-bg-surface p-5 transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-medium text-text-primary">
            {task.name}
          </h3>
          <p className="mt-1 font-mono text-xs text-text-secondary">
            {task.cron}
          </p>
          <p className="mt-3 text-xs text-text-muted">
            Next run: {formatDate(task.nextRunAt)}
            {" · "}
            Last run: {formatDate(task.lastRunAt)}
            {" · "}
            <span title="Per-task permission mode">{modeLabel}</span>
          </p>
          {task.lastResult ? (
            <p className="mt-2 line-clamp-2 text-xs text-text-muted">
              {task.lastResult}
            </p>
          ) : null}
        </div>
        <Toggle
          enabled={task.isEnabled}
          onChange={onToggle}
          label={`Enable ${task.name}`}
        />
      </div>

      <div className="mt-4 flex items-center gap-2">
        <Button
          variant="secondary"
          onClick={onRun}
          loading={running}
          disabled={running}
        >
          Run now
        </Button>
        <Button variant="ghost" onClick={onEdit}>
          Edit
        </Button>
        <Button variant="ghost" onClick={onHistory}>
          History
        </Button>
        <button
          type="button"
          onClick={onDelete}
          className="ml-auto rounded-md px-2.5 py-1.5 text-sm text-status-danger transition-colors hover:bg-status-danger/10"
        >
          Delete
        </button>
      </div>
    </section>
  );
}

function Toggle({
  enabled,
  onChange,
  label,
}: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={label}
      onClick={() => onChange(!enabled)}
      className={`focus-ring relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
        enabled ? "bg-status-success" : "bg-border-strong"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-bg-base transition-transform ${
          enabled ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Create / edit modal
// ---------------------------------------------------------------------------

function TaskFormModal({
  open,
  task,
  onClose,
  onSaved,
}: {
  open: boolean;
  task: TaskDto | null;
  onClose: () => void;
  onSaved: (task: TaskDto, wasEditing: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [cron, setCron] = useState("");
  const [permissionMode, setPermissionMode] = useState("manual");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setName(task?.name ?? "");
      setPrompt(task?.prompt ?? "");
      setCron(task?.cron ?? "");
      setPermissionMode(task?.permissionMode ?? "manual");
      setError(null);
      setSubmitting(false);
    }
  }, [open, task]);

  async function submit() {
    const n = name.trim();
    const p = prompt.trim();
    const c = cron.trim();
    if (!n || !p || !c) {
      setError("Name, prompt, and cron are all required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = task
        ? await hostApi.updateTask(task.id, n, p, c, permissionMode)
        : await hostApi.createTask(n, p, c, permissionMode);
      if (!res.ok || !res.data) {
        setError(
          res.error === "invalid"
            ? "Invalid cron expression or fields."
            : (res.error ?? "Couldn't save task."),
        );
        return;
      }
      onSaved(res.data, task !== null);
    } catch {
      setError("Couldn't save task.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={task ? "Edit task" : "New task"}
      description="Runs the prompt on the schedule below"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={submitting}>
            {task ? "Save" : "Create"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Input
          label="Name"
          placeholder="Morning briefing"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="task-prompt"
            className="text-xs font-medium uppercase tracking-wider text-text-secondary"
          >
            Prompt
          </label>
          <textarea
            id="task-prompt"
            rows={4}
            placeholder="Summarize today's unread alerts…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            className="scrollbar-thin resize-y rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Input
            label="Cron expression"
            placeholder="0 6 * * *"
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            className="font-mono"
          />
          <p className="text-[11px] text-text-muted">
            Standard 5-field cron. e.g. 0 6 * * * = daily 06:00
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="task-perm" className="text-xs font-medium uppercase tracking-wider text-text-secondary">
            Permissions
          </label>
          <select
            id="task-perm"
            value={permissionMode}
            onChange={(e) => setPermissionMode(e.target.value)}
            data-testid="task-permission-mode"
            className="rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
          >
            <option value="manual">Ask before each action (safest)</option>
            <option value="auto">Auto-approve safe actions, ask for risky ones</option>
            <option value="skip">Never ask (only for tasks you fully trust)</option>
          </select>
          <p className="text-[11px] text-text-muted">
            Tip: run the task once with “Run now” so any permission prompts appear and can be granted — later
            runs then stop stalling.
          </p>
        </div>
        {/* FIX8 A2 — the missed-run guardrail, where the prompt is written. */}
        <p className="rounded-md border border-border bg-bg-secondary/50 px-3 py-2 text-[11px] text-text-muted">
          Because a task runs only while the app is open, a run missed while it was closed is caught up
          <span className="font-medium text-text-secondary"> once, at the next launch</span> — so a task meant
          for 9am can run at 11pm if the machine was off all day. Write the prompt so a late catch-up is still
          safe (avoid “send now”, “post immediately”); older missed runs are skipped, not stacked up.
        </p>
        {error ? (
          <p className="rounded-md border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-status-danger">
            {error}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Helpers + icons
// ---------------------------------------------------------------------------

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

function PlusIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

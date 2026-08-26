"use client";

// Cloud scheduled tasks — the SERVER-SIDE counterpart to the desktop-only
// LOCAL tasks (page.tsx / hostApi). These run on our servers on a schedule,
// so they fire EVEN WHEN THE COMPUTER IS OFF — but they cannot touch local
// files and run with a read-only tool set only. Managed over plain
// cookie-authenticated HTTP (api.*CloudTask*), so this section renders in
// BOTH the browser and the desktop shell (NOT gated on HOSTED_MODE).

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { api } from "@/lib/api";
import type { CloudTask, CloudTaskRun, Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

// The MINIMUM cadence is one hour — cloud tasks run at most once per hour.
const CLOUD_INTERVALS: { label: string; seconds: number }[] = [
  { label: "Every hour", seconds: 3600 },
  { label: "Every 6 hours", seconds: 21600 },
  { label: "Every day", seconds: 86400 },
];

export function CloudTasksSection({
  openCreateNonce,
  onRequestCreate,
}: {
  /** Bumped by the page's kind chooser when the user picks "Cloud" — opens
   *  the create form (already past the kind step). Ignored at its initial 0. */
  openCreateNonce: number;
  /** Empty-state "New task" → route back through the page's kind chooser. */
  onRequestCreate: () => void;
}) {
  const [tasks, setTasks] = useState<CloudTask[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [historyTask, setHistoryTask] = useState<CloudTask | null>(null);

  // The active-conversation source: cloud tasks need a conversation_id to run
  // against. We prefer the currently-open conversation (the store's
  // activeConversationId, set whenever a chat is opened), then fall back to a
  // picker over the conversation list so a task can always be attached.
  // TODO: on the /chat/tasks route the user may not have a chat open, so
  // activeConversationId can be stale or null — the picker below is the
  // guaranteed-correct path; revisit if a dedicated "task target" is added.
  const conversations = useChatStore((s) => s.conversations);
  const conversationsLoaded = useChatStore((s) => s.conversationsLoaded);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const loadConversations = useChatStore((s) => s.loadConversations);

  useEffect(() => {
    if (!conversationsLoaded) void loadConversations();
  }, [conversationsLoaded, loadConversations]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await api.listCloudTasks();
        if (alive) setTasks(res.tasks);
      } catch {
        toast("Couldn't load cloud tasks.");
      } finally {
        if (alive) setLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Open the create form when the page's chooser routes here.
  useEffect(() => {
    if (openCreateNonce > 0) setFormOpen(true);
  }, [openCreateNonce]);

  async function handleToggle(task: CloudTask, enabled: boolean) {
    // Optimistic — enabling also clears any server pause reason locally.
    setTasks((list) =>
      list.map((t) =>
        t.id === task.id
          ? { ...t, enabled, paused_reason: enabled ? null : t.paused_reason }
          : t,
      ),
    );
    try {
      await api.setCloudTaskEnabled(task.id, enabled);
    } catch {
      setTasks((list) =>
        list.map((t) =>
          t.id === task.id
            ? { ...t, enabled: !enabled, paused_reason: task.paused_reason }
            : t,
        ),
      );
      toast("Couldn't update task.");
    }
  }

  async function handleDelete(task: CloudTask) {
    if (!window.confirm(`Delete "${task.name}"?`)) return;
    try {
      await api.deleteCloudTask(task.id);
      setTasks((list) => list.filter((t) => t.id !== task.id));
    } catch {
      toast("Couldn't delete task.");
    }
  }

  function handleCreated(task: CloudTask) {
    setTasks((list) => [task, ...list]);
    setFormOpen(false);
  }

  const defaultConversationId =
    activeConversationId &&
    conversations.some((c) => c.id === activeConversationId)
      ? activeConversationId
      : conversations[0]?.id ?? null;

  return (
    <section className="mt-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-text-primary">
            Cloud
            <KindBadge kind="cloud" />
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            Runs on our servers on schedule, even when your computer is off.
          </p>
          <p className="mt-2 text-xs text-text-muted">
            Cloud tasks cannot use local files and run with a read-only tool
            set. They run at most once per hour.
          </p>
        </div>
        {loaded && tasks.length > 0 ? (
          <button
            type="button"
            onClick={onRequestCreate}
            className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border bg-transparent px-3.5 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
          >
            <PlusIcon />
            New task
          </button>
        ) : null}
      </div>

      <div className="mt-6">
        {!loaded ? (
          <div className="flex items-center justify-center py-10 text-text-secondary">
            <Spinner size={20} />
          </div>
        ) : tasks.length === 0 ? (
          <CloudEmptyState onCreate={onRequestCreate} />
        ) : (
          <div className="flex flex-col gap-3">
            {tasks.map((task) => (
              <CloudTaskRow
                key={task.id}
                task={task}
                onToggle={(enabled) => void handleToggle(task, enabled)}
                onDelete={() => void handleDelete(task)}
                onHistory={() => setHistoryTask(task)}
              />
            ))}
          </div>
        )}
      </div>

      <CloudTaskFormModal
        open={formOpen}
        conversations={conversations}
        defaultConversationId={defaultConversationId}
        onClose={() => setFormOpen(false)}
        onCreated={handleCreated}
      />

      <CloudHistoryModal
        task={historyTask}
        onClose={() => setHistoryTask(null)}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Kind badge — makes Cloud vs On-this-device distinguishable at a glance.
// ---------------------------------------------------------------------------

export function KindBadge({ kind }: { kind: "cloud" | "local" }) {
  const cloud = kind === "cloud";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${
        cloud
          ? "border-border bg-bg-elevated text-text-secondary"
          : "border-border bg-transparent text-text-muted"
      }`}
      title={
        cloud
          ? "Runs on our servers on schedule, even when your computer is off"
          : "Runs only while the SRAN Desktop app is open"
      }
    >
      {cloud ? <CloudIcon /> : <DeviceIcon />}
      {cloud ? "Cloud" : "On this device"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Task row — with the cost / paused surface (why a task stopped).
// ---------------------------------------------------------------------------

function CloudTaskRow({
  task,
  onToggle,
  onDelete,
  onHistory,
}: {
  task: CloudTask;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
  onHistory: () => void;
}) {
  return (
    <section className="rounded-xl border border-border bg-bg-surface p-5 transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-base font-medium text-text-primary">
              {task.name}
            </h3>
            <KindBadge kind="cloud" />
          </div>
          <p className="mt-1 text-xs text-text-secondary">
            {intervalLabel(task.interval_seconds)}
          </p>
          <p className="mt-3 text-xs text-text-muted">
            Next run: {formatDate(task.next_run_at)}
            {" · "}
            Last run: {formatDate(task.last_run_at ?? null)}
            {" · "}
            Runs: {task.run_count}
            {task.max_runs != null ? ` / ${task.max_runs}` : ""}
          </p>
        </div>
        <Toggle
          enabled={task.enabled}
          onChange={onToggle}
          label={`Enable ${task.name}`}
        />
      </div>

      <PausedNotice task={task} onReEnable={() => onToggle(true)} />

      <div className="mt-4 flex items-center gap-2">
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

/** The cost / paused surface: a user must be able to LEARN why their task
 *  stopped from the list, not silently. */
function PausedNotice({
  task,
  onReEnable,
}: {
  task: CloudTask;
  onReEnable: () => void;
}) {
  if (!task.paused_reason) return null;
  if (task.paused_reason === "no_balance") {
    return (
      <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-status-danger">
        <span>Paused — out of credits. Top up, then re-enable.</span>
        <button
          type="button"
          onClick={onReEnable}
          className="focus-ring ml-auto rounded-md border border-status-danger/40 px-2.5 py-1 font-medium text-status-danger transition-colors hover:bg-status-danger/10"
        >
          Re-enable
        </button>
      </div>
    );
  }
  const text =
    task.paused_reason === "max_runs"
      ? "Paused — reached its run limit."
      : "Paused.";
  return (
    <p className="mt-3 rounded-md border border-border bg-bg-secondary/50 px-3 py-2 text-xs text-text-secondary">
      {text}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Run history — the "wall" (tool withheld / refusals) must be visible.
// ---------------------------------------------------------------------------

function CloudHistoryModal({
  task,
  onClose,
}: {
  task: CloudTask | null;
  onClose: () => void;
}) {
  const [runs, setRuns] = useState<CloudTaskRun[] | null>(null);

  useEffect(() => {
    if (!task) {
      setRuns(null);
      return;
    }
    setRuns(null);
    void (async () => {
      try {
        const res = await api.listCloudTaskRuns(task.id);
        setRuns(res.runs);
      } catch {
        setRuns([]);
      }
    })();
  }, [task]);

  return (
    <Modal
      open={task !== null}
      onClose={onClose}
      title={task ? `History — ${task.name}` : "History"}
      description="Every past run, including refusals and why."
    >
      {runs === null ? (
        <p className="text-sm text-text-muted">Loading…</p>
      ) : runs.length === 0 ? (
        <p className="text-sm text-text-muted">No runs yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {runs.map((r) => (
            <li
              key={r.id}
              className="rounded-md border border-border bg-bg-secondary/50 px-3 py-2 text-xs"
              data-outcome={r.outcome}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-text-primary">
                  {runHeadline(r)}
                </span>
                <span className="text-text-muted">{formatDate(r.fired_at)}</span>
              </div>
              {r.outcome === "error" && r.error_detail ? (
                <p className="mt-0.5 text-status-danger">{r.error_detail}</p>
              ) : null}
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                {r.policy_action ? (
                  <Tag>DLP: {r.policy_action}</Tag>
                ) : null}
                {r.tokens != null ? <Tag>{r.tokens} tokens</Tag> : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

/** Turn an outcome (+ skip_reason) into a plain sentence — a silently worse
 *  answer must never hide the fact that the run hit a wall. */
function runHeadline(r: CloudTaskRun): string {
  if (r.outcome === "ok" && r.skip_reason === "tool_withheld") {
    return "Ran, but a tool it wanted isn't available to cloud tasks";
  }
  switch (r.outcome) {
    case "ok":
      return "Ran";
    case "refused_revoked":
      return "Skipped — account not active.";
    case "refused_no_balance":
      return "Skipped — out of credits.";
    case "error":
      return "Failed";
    default:
      return r.outcome;
  }
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-border bg-bg-elevated px-1.5 py-0.5 text-[11px] text-text-muted">
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function CloudEmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-transparent px-8 py-14 text-center">
      <div className="mx-auto mb-5 text-text-muted">
        <CloudIcon size={40} />
      </div>
      <h3 className="text-base font-semibold tracking-tight text-text-primary">
        No cloud tasks
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-sm text-text-secondary">
        Create a task to have a prompt run on a schedule on our servers — even
        when your computer is off.
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
// Create form
// ---------------------------------------------------------------------------

function CloudTaskFormModal({
  open,
  conversations,
  defaultConversationId,
  onClose,
  onCreated,
}: {
  open: boolean;
  conversations: Conversation[];
  defaultConversationId: string | null;
  onClose: () => void;
  onCreated: (task: CloudTask) => void;
}) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState(3600);
  const [maxRuns, setMaxRuns] = useState("");
  const [conversationId, setConversationId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setName("");
      setPrompt("");
      setIntervalSeconds(3600);
      setMaxRuns("");
      setConversationId(defaultConversationId ?? "");
      setError(null);
      setSubmitting(false);
    }
  }, [open, defaultConversationId]);

  const noConversations = conversations.length === 0;

  async function submit() {
    const n = name.trim();
    const p = prompt.trim();
    if (!n || !p) {
      setError("Name and prompt are both required.");
      return;
    }
    if (n.length > 120) {
      setError("Name must be 120 characters or fewer.");
      return;
    }
    if (p.length > 4000) {
      setError("Prompt must be 4000 characters or fewer.");
      return;
    }
    if (!conversationId) {
      setError("Choose a conversation for this task to run in.");
      return;
    }
    let parsedMaxRuns: number | null = null;
    if (maxRuns.trim()) {
      const m = Number(maxRuns);
      if (!Number.isInteger(m) || m < 1) {
        setError("Max runs must be a whole number of 1 or more.");
        return;
      }
      parsedMaxRuns = m;
    }
    setSubmitting(true);
    setError(null);
    try {
      const task = await api.createCloudTask({
        conversationId,
        name: n,
        prompt: p,
        intervalSeconds,
        maxRuns: parsedMaxRuns,
      });
      onCreated(task);
    } catch {
      setError("Couldn't create task.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New cloud task"
      description="Runs the prompt on our servers, on the schedule below."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={submitting}
            disabled={noConversations}
          >
            Create
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Input
          label="Name"
          placeholder="Daily digest"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="cloud-task-prompt"
            className="text-xs font-medium uppercase tracking-wider text-text-secondary"
          >
            Prompt
          </label>
          <textarea
            id="cloud-task-prompt"
            rows={4}
            maxLength={4000}
            placeholder="Summarize what's new since yesterday…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            className="scrollbar-thin resize-y rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="cloud-task-interval"
            className="text-xs font-medium uppercase tracking-wider text-text-secondary"
          >
            Schedule
          </label>
          <select
            id="cloud-task-interval"
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(Number(e.target.value))}
            className="rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
          >
            {CLOUD_INTERVALS.map((opt) => (
              <option key={opt.seconds} value={opt.seconds}>
                {opt.label}
              </option>
            ))}
          </select>
          <p className="text-[11px] text-text-muted">
            Cloud tasks run at most once per hour.
          </p>
        </div>
        <Input
          label="Max runs (optional)"
          type="number"
          min={1}
          placeholder="Leave blank for no limit"
          value={maxRuns}
          onChange={(e) => setMaxRuns(e.target.value)}
        />
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="cloud-task-conversation"
            className="text-xs font-medium uppercase tracking-wider text-text-secondary"
          >
            Conversation
          </label>
          {noConversations ? (
            <p className="rounded-md border border-border bg-bg-secondary/50 px-3 py-2 text-xs text-text-muted">
              A cloud task runs inside a conversation. Start a chat first, then
              come back to schedule it.
            </p>
          ) : (
            <>
              <select
                id="cloud-task-conversation"
                value={conversationId}
                onChange={(e) => setConversationId(e.target.value)}
                className="rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
              >
                {conversations.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title || "Untitled"}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-text-muted">
                The task runs as a new turn in this conversation.
              </p>
            </>
          )}
        </div>
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
// Shared bits (kept local so this section is self-contained)
// ---------------------------------------------------------------------------

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

function intervalLabel(seconds: number): string {
  const match = CLOUD_INTERVALS.find((o) => o.seconds === seconds);
  if (match) return match.label;
  if (seconds % 86400 === 0) return `Every ${seconds / 86400} days`;
  if (seconds % 3600 === 0) return `Every ${seconds / 3600} hours`;
  return `Every ${seconds}s`;
}

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

function CloudIcon({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M17.5 19a4.5 4.5 0 0 0 0-9 6 6 0 0 0-11.5 1.5A3.5 3.5 0 0 0 6.5 19z" />
    </svg>
  );
}

function DeviceIcon({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="3" y="4" width="18" height="12" rx="1.5" />
      <path d="M8 20h8M12 16v4" />
    </svg>
  );
}

"use client";

// Skills management page — desktop-only.
//
// A React port of the WPF SkillsViewModel. Lists every skill the host
// knows about (built-in + user-installed), lets the user toggle each on
// or off, install a new skill via the host's native file picker, and
// delete non-builtin skills. All I/O goes through the typed `hostApi`
// bridge client — this page never names a filesystem path; the host
// collects the skill file through an OS dialog inside installSkill().

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { useHostedMode } from "@/hooks/useHostedMode";
import { hostApi, type SkillDto } from "@/lib/hostApi";

export default function SkillsPage() {
  const hosted = useHostedMode();
  if (!hosted) {
    return (
      <div className="flex flex-1 items-center justify-center bg-bg-base px-6 text-center">
        <p className="max-w-sm text-sm text-text-muted">
          This screen is available in the SRAN Desktop app.
        </p>
      </div>
    );
  }
  return <SkillsScreen />;
}

function SkillsScreen() {
  const [skills, setSkills] = useState<SkillDto[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [installing, setInstalling] = useState(false);

  async function reload() {
    try {
      const list = await hostApi.listSkills();
      setSkills(list);
    } catch {
      toast("Couldn't load skills.");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  // Optimistic toggle: flip locally, call the host, revert on refusal.
  async function handleToggle(skill: SkillDto) {
    const next = !skill.isEnabled;
    setSkills((prev) =>
      prev.map((s) => (s.id === skill.id ? { ...s, isEnabled: next } : s)),
    );
    const res = await hostApi.setSkillEnabled(skill.id, next);
    if (!res.ok) {
      setSkills((prev) =>
        prev.map((s) =>
          s.id === skill.id ? { ...s, isEnabled: skill.isEnabled } : s,
        ),
      );
      toast("Couldn't update skill.");
    }
  }

  async function handleInstall() {
    setInstalling(true);
    try {
      const res = await hostApi.installSkill();
      if (!res.ok) {
        toast(res.error ?? "Couldn't install skill.");
        return;
      }
      const data = res.data;
      if (data?.cancelled) return;
      if (data?.installed) {
        toast(
          data.name ? `Installed "${data.name}".` : "Skill installed.",
        );
        await reload();
      }
    } finally {
      setInstalling(false);
    }
  }

  async function handleDelete(skill: SkillDto) {
    if (!window.confirm(`Delete "${skill.name}"? This can't be undone.`)) {
      return;
    }
    const res = await hostApi.deleteSkill(skill.id);
    if (!res.ok) {
      toast(res.error ?? "Couldn't delete skill.");
      return;
    }
    setSkills((prev) => prev.filter((s) => s.id !== skill.id));
    toast(`Deleted "${skill.name}".`);
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
              Skills
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              Capabilities the assistant can invoke. Toggle them on or off, or
              install your own.
            </p>
          </div>
          <Button
            onClick={() => void handleInstall()}
            loading={installing}
            className="shrink-0"
          >
            <span className="inline-flex items-center gap-1.5">
              <PlusIcon />
              Install skill
            </span>
          </Button>
        </div>

        <div className="mt-8">
          {!loaded ? (
            <div className="flex items-center justify-center py-10 text-text-secondary">
              <Spinner size={20} />
            </div>
          ) : skills.length === 0 ? (
            <EmptySkillsState />
          ) : (
            <div className="flex flex-col gap-3">
              {skills.map((skill) => (
                <SkillCard
                  key={skill.id}
                  skill={skill}
                  onToggle={() => void handleToggle(skill)}
                  onDelete={() => void handleDelete(skill)}
                />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptySkillsState() {
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
        <path d="M12 2l2.4 5.4 5.9.5-4.5 3.9 1.4 5.8L12 20l-5.2 2.6 1.4-5.8-4.5-3.9 5.9-.5z" />
      </svg>
      <h2 className="text-base font-semibold tracking-tight text-text-primary">
        No skills installed yet
      </h2>
      <p className="mx-auto mt-2 max-w-sm text-sm text-text-secondary">
        Install a skill file to give the assistant a new capability.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skill card
// ---------------------------------------------------------------------------

function SkillCard({
  skill,
  onToggle,
  onDelete,
}: {
  skill: SkillDto;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const triggers = skill.triggers.slice(0, 5).join(", ");
  return (
    <section className="rounded-xl border border-border bg-bg-surface p-5 transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-base font-medium text-text-primary">
              {skill.name}
            </h3>
            <Badge builtin={skill.isBuiltin} />
          </div>
          {skill.description ? (
            <p className="mt-1 text-sm text-text-secondary">
              {skill.description}
            </p>
          ) : null}
          {triggers ? (
            <p className="mt-3 truncate text-xs text-text-muted">
              Triggers: {triggers}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Toggle
            checked={skill.isEnabled}
            onChange={onToggle}
            label={`${skill.isEnabled ? "Disable" : "Enable"} ${skill.name}`}
          />
          {!skill.isBuiltin ? (
            <button
              type="button"
              onClick={onDelete}
              title="Delete skill"
              aria-label={`Delete ${skill.name}`}
              className="tap-44 rounded p-1.5 text-text-muted hover:bg-status-danger/10 hover:text-status-danger"
            >
              <TrashIcon />
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function Badge({ builtin }: { builtin: boolean }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider ${
        builtin
          ? "border-border text-text-muted"
          : "border-status-success/40 text-status-success"
      }`}
    >
      {builtin ? "Built-in" : "Installed"}
    </span>
  );
}

// A minimal token-styled switch. Native checkbox for a11y + keyboard,
// visually rendered as a sliding track.
function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
}) {
  return (
    <label className="tap-44 inline-flex cursor-pointer items-center">
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        onChange={onChange}
        aria-label={label}
        className="peer sr-only"
      />
      <span
        aria-hidden
        className={`relative inline-block h-5 w-9 rounded-full border transition-colors ${
          checked
            ? "border-status-success bg-status-success"
            : "border-border-strong bg-bg-elevated"
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${
            checked ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </span>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

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

function TrashIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

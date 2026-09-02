"use client";

// Skills management page.
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
import { Switch } from "@/components/ui/Switch";
import { toast } from "@/components/ui/Toast";
import { tOutside, useT } from "@/lib/i18n";
import { useHostedMode } from "@/hooks/useHostedMode";
import { api, type WebSkill } from "@/lib/api";
import { hostApi, type SkillDto } from "@/lib/hostApi";

export default function SkillsPage() {
  const hosted = useHostedMode();
  // FIX32 Part 4.3 — skills are instruction text chat-edge already stores and
  // folds into the prompt (FIX12 Item 3, layer 10b). Nothing about them needs
  // a host, so the web surface manages them through chat-edge directly; the
  // desktop keeps its host-backed screen (its skills live in the sidecar and
  // include installable packages, which ARE a host capability).
  if (!hosted) return <WebSkillsScreen />;
  return <SkillsScreen />;
}

// ---------------------------------------------------------------------------
// Web surface — chat-edge-backed (FIX32 Part 4.3)
// ---------------------------------------------------------------------------

function WebSkillsScreen() {
  const [skills, setSkills] = useState<WebSkill[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState({ name: "", description: "", triggers: "", instructions: "" });
  const t = useT();

  async function reload() {
    try {
      const r = await api.listSkills();
      setSkills(r.skills);
    } catch {
      toast(tOutside("skills.toast.loadFailed"));
    } finally {
      setLoaded(true);
    }
  }
  useEffect(() => {
    void reload();
  }, []);

  async function handleCreate() {
    const name = draft.name.trim();
    if (!name || !draft.instructions.trim()) {
      toast(tOutside("skills.toast.needNameInstr"));
      return;
    }
    setSaving(true);
    try {
      await api.createSkill({
        name,
        description: draft.description.trim(),
        triggers: draft.triggers.split(",").map((t) => t.trim()).filter(Boolean),
        instructions: draft.instructions.trim(),
      });
      toast(tOutside("skills.toast.created", { name }));
      setDraft({ name: "", description: "", triggers: "", instructions: "" });
      setCreating(false);
      await reload();
    } catch (e) {
      // FIX22 §1a — chat-edge refuses instructions that would trip the guardrail.
      toast(e instanceof Error && e.message ? e.message : tOutside("skills.toast.createFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(skill: WebSkill) {
    const next = !skill.enabled;
    setSkills((prev) => prev.map((s) => (s.id === skill.id ? { ...s, enabled: next } : s)));
    try {
      await api.setSkillEnabled(skill.id, next);
    } catch {
      setSkills((prev) => prev.map((s) => (s.id === skill.id ? { ...s, enabled: skill.enabled } : s)));
      toast(tOutside("skills.toast.updateFailed"));
    }
  }

  async function handleDelete(skill: WebSkill) {
    if (!window.confirm(tOutside("skills.confirmDelete", { name: skill.name }))) return;
    try {
      await api.deleteSkill(skill.id);
      setSkills((prev) => prev.filter((s) => s.id !== skill.id));
      toast(tOutside("skills.toast.deleted", { name: skill.name }));
    } catch {
      toast(tOutside("skills.toast.deleteFailed"));
    }
  }

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-8 pb-12 md:px-6">
        <Link href="/chat" className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary">
          ← {t("skills.back")}
        </Link>
        <div className="mt-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">{t("skills.title")}</h1>
            <p className="mt-1 text-sm text-text-secondary">
              {t("skills.web.subtitle")}
            </p>
          </div>
          <Button variant="secondary" onClick={() => setCreating((v) => !v)} aria-expanded={creating}>
            {t("skills.create")}
          </Button>
        </div>

        {creating ? (
          <form
            className="mt-6 flex flex-col gap-3 rounded-xl border border-border bg-bg-surface p-4"
            onSubmit={(e) => {
              e.preventDefault();
              void handleCreate();
            }}
            aria-label={tOutside("skills.create")}
          >
            <p className="text-sm text-text-secondary">
              {t("skills.form.intro")}
            </p>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-text-secondary">{t("skills.field.name")}</span>
              <input className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-text-primary" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="สรุปแบบสั้น" required />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-text-secondary">{t("skills.field.description")}</span>
              <input className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-text-primary" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-text-secondary">{t("skills.field.triggers")}</span>
              <input className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-text-primary" value={draft.triggers} onChange={(e) => setDraft({ ...draft, triggers: e.target.value })} placeholder="สรุปแบบสั้น, สรุปสั้นๆ" />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-text-secondary">{t("skills.field.instructions")}</span>
              <textarea className="min-h-[120px] rounded-md border border-border bg-bg-elevated px-3 py-2 text-text-primary" value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} required />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" type="button" onClick={() => setCreating(false)}>{t("skills.form.cancel")}</Button>
              <Button type="submit" loading={saving}>{t("skills.form.save")}</Button>
            </div>
          </form>
        ) : null}

        <div className="mt-8">
          {!loaded ? (
            <div className="flex items-center justify-center py-10 text-text-secondary">
              <Spinner size={20} />
            </div>
          ) : skills.length === 0 ? (
            <EmptySkillsState />
          ) : (
            <div className="flex flex-col gap-3" data-testid="web-skills-list">
              {skills.map((skill) => (
                <section key={skill.id} className="flex items-start justify-between gap-4 rounded-xl border border-border bg-bg-surface p-4" data-testid="web-skill-card">
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate text-base font-medium text-text-primary">{skill.name}</h3>
                    {skill.description ? <p className="mt-1 text-sm text-text-secondary">{skill.description}</p> : null}
                    {skill.triggers.length > 0 ? (
                      <p className="mt-1 truncate text-xs text-text-muted">{t("skills.triggers", { list: skill.triggers.join(" · ") })}</p>
                    ) : (
                      <p className="mt-1 text-xs text-text-muted">{t("skills.appliesAll")}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button variant="ghost" onClick={() => void handleDelete(skill)} aria-label={t("skills.delete.aria", { name: skill.name })}>
                      {t("skills.delete")}
                    </Button>
                    <Switch
                      checked={skill.enabled}
                      onChange={() => void handleToggle(skill)}
                      label={`${skill.name} ${skill.enabled ? t("skills.state.on") : t("skills.state.off")}`}
                    />
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function SkillsScreen() {
  const [skills, setSkills] = useState<SkillDto[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [installing, setInstalling] = useState(false);
  // FIX28 §3 (G3): write a skill from a form — no .zip/.skill archive needed.
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: "", displayName: "", triggers: "", instructions: "" });
  const [saving, setSaving] = useState(false);
  const t = useT();

  async function handleCreate() {
    const name = draft.name.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
    if (!name || !draft.instructions.trim()) {
      toast(tOutside("skills.toast.needIdInstr"));
      return;
    }
    setSaving(true);
    try {
      const res = await hostApi.createSkill({
        name,
        displayName: draft.displayName.trim() || undefined,
        triggers: draft.triggers.split(",").map((t) => t.trim()).filter(Boolean),
        instructions: draft.instructions.trim(),
      });
      if (!res.ok) {
        toast(tOutside("skills.toast.createFailed"));
        return;
      }
      toast(tOutside("skills.toast.created", { name: draft.displayName.trim() || name }));
      setDraft({ name: "", displayName: "", triggers: "", instructions: "" });
      setCreating(false);
      await reload();
    } finally {
      setSaving(false);
    }
  }

  async function reload() {
    try {
      const list = await hostApi.listSkills();
      setSkills(list);
    } catch {
      toast(tOutside("skills.toast.loadFailed"));
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
      toast(tOutside("skills.toast.updateFailed"));
    }
  }

  async function handleInstall() {
    setInstalling(true);
    try {
      const res = await hostApi.installSkill();
      if (!res.ok) {
        toast(res.error ?? tOutside("skills.toast.installFailed"));
        return;
      }
      const data = res.data;
      if (data?.cancelled) return;
      if (data?.installed) {
        toast(
          data.name ? tOutside("skills.toast.installedNamed", { name: data.name }) : tOutside("skills.toast.installed"),
        );
        await reload();
      }
    } finally {
      setInstalling(false);
    }
  }

  async function handleDelete(skill: SkillDto) {
    if (!window.confirm(tOutside("skills.confirmDelete", { name: skill.name }))) {
      return;
    }
    const res = await hostApi.deleteSkill(skill.id);
    if (!res.ok) {
      toast(res.error ?? tOutside("skills.toast.deleteFailed"));
      return;
    }
    setSkills((prev) => prev.filter((s) => s.id !== skill.id));
    toast(tOutside("skills.toast.deleted", { name: skill.name }));
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
          {t("skills.back")}
        </Link>

        <div className="mt-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
              {t("skills.title")}
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              {t("skills.desktop.subtitle")}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button variant="secondary" onClick={() => setCreating((v) => !v)} aria-expanded={creating}>
              <span className="inline-flex items-center gap-1.5">
                <PlusIcon />
                {t("skills.create")}
              </span>
            </Button>
            <Button
              onClick={() => void handleInstall()}
              loading={installing}
              className="shrink-0"
            >
              {t("skills.install")}
            </Button>
          </div>
        </div>

        {creating ? (
          <form
            className="mt-6 flex flex-col gap-3 rounded-xl border border-border bg-bg-surface p-4"
            onSubmit={(e) => {
              e.preventDefault();
              void handleCreate();
            }}
            aria-label={tOutside("skills.create")}
          >
            <p className="text-sm text-text-secondary">
              {t("skills.form.intro")}
            </p>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-text-secondary">{t("skills.field.id")}</span>
              <input className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-text-primary" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="summarize-short" required />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-text-secondary">{t("skills.field.displayName")}</span>
              <input className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-text-primary" value={draft.displayName} onChange={(e) => setDraft({ ...draft, displayName: e.target.value })} placeholder="สรุปแบบสั้น" />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-text-secondary">{t("skills.field.triggers")}</span>
              <input className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-text-primary" value={draft.triggers} onChange={(e) => setDraft({ ...draft, triggers: e.target.value })} placeholder="สรุปแบบสั้น, สรุปสั้นๆ" />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-text-secondary">{t("skills.field.instructions")}</span>
              <textarea className="min-h-[120px] rounded-md border border-border bg-bg-elevated px-3 py-2 text-text-primary" value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} placeholder="ตอบเป็นข้อๆ ไม่เกิน 3 ข้อเสมอ ห้ามเกิน" required />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" type="button" onClick={() => setCreating(false)}>{t("skills.form.cancel")}</Button>
              <Button type="submit" loading={saving}>{t("skills.form.save")}</Button>
            </div>
          </form>
        ) : null}

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
  const t = useT();
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
        {t("skills.empty.title")}
      </h2>
      <p className="mx-auto mt-2 max-w-sm text-sm text-text-secondary">
        {t("skills.empty.desc")}
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
  const t = useT();
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
              {t("skills.triggers", { list: triggers })}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Toggle
            checked={skill.isEnabled}
            onChange={onToggle}
            label={skill.isEnabled ? t("skills.disable.aria", { name: skill.name }) : t("skills.enable.aria", { name: skill.name })}
          />
          {!skill.isBuiltin ? (
            <button
              type="button"
              onClick={onDelete}
              title={t("skills.delete.title")}
              aria-label={t("skills.delete.aria", { name: skill.name })}
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
  const t = useT();
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider ${
        builtin
          ? "border-border text-text-muted"
          : "border-status-success/40 text-status-success"
      }`}
    >
      {builtin ? t("skills.badge.builtin") : t("skills.badge.installed")}
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
            ? "border-status-success-solid bg-status-success-solid"
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

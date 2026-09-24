"use client";

// FIX132 F30 — say which project you are in. NIGHT3 p-12/p-13: "แชทใหม่ใน
// โปรเจกต์นี้" landed on the generic welcome screen, and after the first send
// only the sidebar nested the chat under the project. Two places now name it:
//   · the chat header — "โปรเจกต์: <name>", a link to the project;
//   · the new-chat screen — "แชทใหม่ในโปรเจกต์ <name>", with a way to take
//     the new chat out of the project before it is sent.
// (ChatGPT shows the project as a breadcrumb over the chat and as the title
// of the new-chat screen; this is the same fact in a chip.)

import Link from "next/link";
import { FolderKanban, X } from "lucide-react";

import { useT } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";

export function ProjectChip({
  projectId,
  variant = "chat",
  onClear,
}: {
  projectId: string | null | undefined;
  variant?: "chat" | "newChat";
  /** newChat only: take the next chat out of the project. */
  onClear?: () => void;
}) {
  const t = useT();
  const project = useChatStore((s) => (projectId ? s.projects.find((p) => p.id === projectId) : undefined));
  if (!project) return null;
  const label = variant === "newChat" ? t("project.chip.newChat", { name: project.name }) : t("project.chip.in", { name: project.name });
  return (
    <div data-testid="project-chip" className="inline-flex min-w-0 max-w-full items-center gap-1 rounded-full border border-border-subtle bg-bg-surface text-xs text-text-secondary">
      <Link
        href={`/chat/projects?project=${project.id}`}
        className="focus-ring inline-flex min-w-0 items-center gap-1.5 rounded-full py-1 pl-2.5 pr-2 hover:text-text-primary"
        title={project.name}
      >
        <FolderKanban className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="truncate">{label}</span>
      </Link>
      {variant === "newChat" && onClear ? (
        <button
          type="button"
          onClick={onClear}
          aria-label={t("project.chip.leave")}
          title={t("project.chip.leave")}
          className="focus-ring -ml-1 mr-1 shrink-0 rounded-full p-0.5 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
        >
          <X className="h-3 w-3" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

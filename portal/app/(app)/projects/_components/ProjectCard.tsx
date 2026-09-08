"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  FolderOpen,
  Key,
  MoreVertical,
  Pencil,
  Trash2,
} from "lucide-react";

import { relativeTime } from "@/lib/format";
import type { Project } from "@/lib/types";

// Per-project request counts aren't available from the backend yet, so the
// card shows only the key count (a fabricated "0 requests" was removed — F6).
export type ProjectStats = { keys: number };

type Props = {
  project: Project;
  stats: ProjectStats;
  onRename: () => void;
  onDelete: () => void;
  onOpen: () => void;
};

export default function ProjectCard({
  project,
  stats,
  onRename,
  onDelete,
  onOpen,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className="card-hover group relative p-4 cursor-pointer"
      style={{
        background:
          "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
        border: "1px solid var(--border-default)",
        borderRadius: 10,
      }}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3 min-w-0">
          <div
            className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{
              background: "rgba(99,102,241,0.14)",
              color: "var(--accent-text)",
            }}
          >
            <FolderOpen className="w-5 h-5" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <h3
              className="font-heading truncate"
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: "var(--text-primary)",
                margin: 0,
              }}
            >
              {project.name}
            </h3>
            <div
              style={{
                fontSize: 11,
                color: "var(--text-tertiary)",
                marginTop: 2,
              }}
            >
              Created {relativeTime(project.created_at)}
            </div>
          </div>
        </div>

        {/* Dropdown — stop propagation so clicks don't navigate the card */}
        <div ref={menuRef} className="relative" onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            aria-label="Project actions"
            className="p-1 -m-1 rounded"
            style={{
              color: "var(--text-tertiary)",
              background: "transparent",
              border: "none",
              cursor: "pointer",
            }}
          >
            <MoreVertical className="w-4 h-4" />
          </button>
          {menuOpen ? (
            <div
              className="absolute right-0 top-7 z-10 w-40 rounded-lg overflow-hidden"
              style={{
                background: "var(--bg-surface)",
                border: "1px solid var(--border-default)",
                boxShadow:
                  "0 8px 24px rgba(0,0,0,0.1), 0 2px 6px rgba(0,0,0,0.04)",
              }}
            >
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  onRename();
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-left"
                style={{
                  fontSize: 13,
                  color: "var(--text-secondary)",
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                }}
              >
                <Pencil className="w-3.5 h-3.5" /> Rename
              </button>
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  onDelete();
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-left"
                style={{
                  fontSize: 13,
                  color: "var(--accent-danger)",
                  background: "transparent",
                  border: "none",
                  borderTop: "1px solid var(--border-subtle)",
                  cursor: "pointer",
                }}
              >
                <Trash2 className="w-3.5 h-3.5" /> Delete
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <div
        className="flex items-center gap-4 py-3"
        style={{
          borderTop: "1px solid var(--border-subtle)",
          fontSize: 13,
          color: "var(--text-secondary)",
        }}
      >
        <div className="flex items-center gap-1.5">
          <Key
            className="w-3.5 h-3.5"
            style={{ color: "var(--text-tertiary)" }}
          />
          <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
            {stats.keys}
          </span>
          <span style={{ color: "var(--text-tertiary)" }}>
            {stats.keys === 1 ? "key" : "keys"}
          </span>
        </div>
      </div>

      <Link
        href={`/projects/${project.id}`}
        onClick={(e) => e.stopPropagation()}
        className="btn-premium mt-2 inline-flex items-center justify-center gap-1.5 w-full py-2 px-4 rounded-lg"
        style={{
          background: "var(--bg-sidebar)",
          color: "#FFFFFF",
          fontSize: 13,
          fontWeight: 500,
          textDecoration: "none",
        }}
      >
        Open project <ArrowRight size={14} />
      </Link>
    </div>
  );
}

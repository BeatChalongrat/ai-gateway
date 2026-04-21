"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  Activity,
  FolderOpen,
  Key,
  MoreVertical,
  Pencil,
  Trash2,
} from "lucide-react";

import { relativeTime } from "@/lib/format";
import type { Project } from "@/lib/types";

export type ProjectStats = { keys: number; requests: number };

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
      className="group relative bg-white rounded-xl border border-gray-200 hover:border-violet-300 hover:shadow-md transition-all p-5 cursor-pointer"
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 rounded-lg bg-violet-50 text-violet-600 flex items-center justify-center flex-shrink-0">
            <FolderOpen className="w-5 h-5" />
          </div>
          <div className="min-w-0">
            <h3 className="font-heading font-semibold text-gray-900 truncate">
              {project.name}
            </h3>
            <div className="text-xs text-gray-500 mt-0.5">
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
            className="text-gray-400 hover:text-gray-700 p-1 -m-1 rounded hover:bg-gray-100 transition-colors"
          >
            <MoreVertical className="w-4 h-4" />
          </button>
          {menuOpen ? (
            <div className="absolute right-0 top-7 z-10 w-40 rounded-lg bg-white shadow-lg border border-gray-200 overflow-hidden">
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  onRename();
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 text-left"
              >
                <Pencil className="w-3.5 h-3.5" /> Rename
              </button>
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  onDelete();
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-brand-red hover:bg-red-50 text-left border-t border-gray-100"
              >
                <Trash2 className="w-3.5 h-3.5" /> Delete
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <div className="flex items-center gap-4 py-3 border-t border-gray-100 text-sm text-gray-600">
        <div className="flex items-center gap-1.5">
          <Key className="w-3.5 h-3.5 text-gray-400" />
          <span className="font-medium text-gray-900">{stats.keys}</span>
          <span className="text-gray-500">
            {stats.keys === 1 ? "key" : "keys"}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <Activity className="w-3.5 h-3.5 text-gray-400" />
          <span className="font-medium text-gray-900">
            {stats.requests.toLocaleString()}
          </span>
          <span className="text-gray-500">
            {stats.requests === 1 ? "request" : "requests"}
          </span>
        </div>
      </div>

      <Link
        href={`/projects/${project.id}`}
        onClick={(e) => e.stopPropagation()}
        className="mt-3 inline-flex items-center justify-center w-full py-2 px-4 rounded-lg bg-gray-900 text-white text-sm font-medium hover:bg-black transition-colors"
      >
        Open project →
      </Link>
    </div>
  );
}

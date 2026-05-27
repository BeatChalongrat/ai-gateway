"use client";

// Phase 4 Session 5 — Artifact Workspace slide-over drawer.
//
// Mounted at the conversation page level. When `open`, it renders a
// right-side drawer listing every generated-file lineage in the
// conversation. Click a card → VersionTimeline takes over the panel
// body with the lineage's full history + compare/rollback controls.

import { useEffect, useState } from "react";

import { Download, FolderOpen, History, X } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type { GeneratedFileEntry } from "@/lib/types";

import { VersionTimeline } from "./VersionTimeline";
import { fileTypeIcon, humanSize, timeAgo } from "./workspaceUtil";

interface Props {
  convId: string;
  open: boolean;
  onClose: () => void;
}

interface SelectedLineage {
  fileId: string;
  filename: string;
  contentType?: string;
}

export function WorkspacePanel({ convId, open, onClose }: Props) {
  const [files, setFiles] = useState<GeneratedFileEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedLineage | null>(null);

  async function fetchFiles() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listGeneratedFiles(convId);
      setFiles(res.files ?? []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load files.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open) {
      void fetchFiles();
    } else {
      // Reset the selected-lineage view when the panel closes so the
      // next open lands on the file list, not on a stale timeline.
      setSelected(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, convId]);

  // ESC to close — matches the existing modal patterns in the app.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (selected) setSelected(null);
        else onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, selected, onClose]);

  const totalFiles = files?.length ?? 0;
  const totalVersions = files?.reduce((acc, f) => acc + (f.version_count ?? 1), 0) ?? 0;

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-200 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
        aria-hidden
      />
      {/* Drawer */}
      <aside
        className={`fixed right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l border-border-default bg-bg-surface shadow-2xl transition-transform duration-200 ease-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        role="dialog"
        aria-label="Workspace"
      >
        <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-3">
          <FolderOpen className="h-5 w-5 text-blue-500" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-text-primary">Workspace</div>
            <div className="text-xs text-text-tertiary">
              {totalFiles} file{totalFiles === 1 ? "" : "s"} ·{" "}
              {totalVersions} version{totalVersions === 1 ? "" : "s"}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring rounded p-1 text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary"
            aria-label="Close workspace"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3">
          {selected ? (
            <VersionTimeline
              convId={convId}
              fileId={selected.fileId}
              filename={selected.filename}
              contentType={selected.contentType}
              onBack={() => {
                setSelected(null);
                // Refresh so a rollback that just happened surfaces
                // in the file list with the new leaf.
                void fetchFiles();
              }}
            />
          ) : (
            <FileList
              files={files}
              loading={loading}
              error={error}
              onPick={(f) =>
                setSelected({
                  fileId: f.id,
                  filename: f.filename,
                  contentType: f.content_type,
                })
              }
            />
          )}
        </div>
      </aside>
    </>
  );
}

function FileList({
  files,
  loading,
  error,
  onPick,
}: {
  files: GeneratedFileEntry[] | null;
  loading: boolean;
  error: string | null;
  onPick: (f: GeneratedFileEntry) => void;
}) {
  if (loading && files == null) {
    return <div className="text-xs text-text-tertiary">Loading workspace…</div>;
  }
  if (error) {
    return (
      <div className="rounded-md border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-status-danger">
        {error}
      </div>
    );
  }
  if (!files || files.length === 0) {
    return (
      <div className="rounded-md border border-border-subtle bg-bg-base/40 px-3 py-6 text-center text-xs text-text-tertiary">
        No generated files yet. Ask the AI to create or edit a workbook,
        document, or presentation and they'll appear here.
      </div>
    );
  }
  return (
    <ul className="space-y-2">
      {files.map((f) => (
        <li
          key={f.id}
          className="rounded-lg border border-border-subtle bg-bg-base/30 px-3 py-2.5"
        >
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 flex-shrink-0">
              {fileTypeIcon(f.filename, f.content_type)}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-sm font-semibold text-text-primary">
                  {f.filename}
                </div>
                {f.version ? (
                  <span className="flex-shrink-0 rounded-full bg-blue-500/15 px-2 py-0.5 text-[10px] font-semibold text-blue-500">
                    v{f.version}
                  </span>
                ) : null}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-text-tertiary">
                <span>{humanSize(f.file_size_bytes)}</span>
                <span>·</span>
                <span>{timeAgo(f.created_at)}</span>
                {f.parent_filename ? (
                  <>
                    <span>·</span>
                    <span className="truncate">from {f.parent_filename}</span>
                  </>
                ) : null}
              </div>
              <div className="mt-2 flex items-center gap-1.5">
                <a
                  href={api.downloadUrl(f.id)}
                  download={f.filename}
                  className="focus-ring inline-flex items-center gap-1 rounded-md border border-border-default bg-bg-elevated px-2 py-1 text-xs font-medium text-text-primary transition-colors hover:bg-bg-surface"
                >
                  <Download className="h-3 w-3" />
                  Download
                </a>
                <button
                  type="button"
                  onClick={() => onPick(f)}
                  className="focus-ring inline-flex items-center gap-1 rounded-md border border-border-default bg-bg-elevated px-2 py-1 text-xs font-medium text-text-primary transition-colors hover:bg-bg-surface"
                >
                  <History className="h-3 w-3" />
                  History ({f.version_count})
                </button>
              </div>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

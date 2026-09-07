"use client";

// Phase 4 Session 5 — Artifact Workspace slide-over drawer.
//
// Mounted at the conversation page level. When `open`, it renders a
// right-side pane listing every generated-file lineage in the
// conversation. Click a card → VersionTimeline takes over the panel
// body with the lineage's full history + compare/rollback controls.
// FIX76: the frame is the shared SidePane (docked beside the thread at
// ≥ lg, a slide-over dialog below); `open` comes from the page's one
// side-pane slot.

import { useEffect, useState } from "react";

import { Download, FolderOpen, History } from "lucide-react";

import { SidePane } from "@/components/ui/SidePane";
import { api, ApiError } from "@/lib/api";
import { tOutside, useT } from "@/lib/i18n";
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
  const t = useT();
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
      setError(err instanceof ApiError ? err.message : tOutside("ws.loadFilesFailed"));
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

  // Esc inside a lineage view goes BACK to the file list (capture phase,
  // stopped before SidePane's own Esc handler would close the pane).
  useEffect(() => {
    if (!open || !selected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopImmediatePropagation();
      setSelected(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, selected]);

  const totalFiles = files?.length ?? 0;
  const totalVersions = files?.reduce((acc, f) => acc + (f.version_count ?? 1), 0) ?? 0;

  return (
    <SidePane
      open={open}
      widthKey="workspace"
      label={t("ws.panelTitle")}
      subtitle={
        <>
          {totalFiles} {t(totalFiles === 1 ? "unit.file" : "unit.files")} ·{" "}
          {totalVersions} {t(totalVersions === 1 ? "unit.version" : "unit.versions")}
        </>
      }
      icon={<FolderOpen className="h-5 w-5 text-text-tertiary" />}
      maxWidth={448}
      closeLabel={t("ws.close")}
      onClose={onClose}
      testId="workspace-panel"
    >

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
    </SidePane>
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
  const t = useT();
  if (loading && files == null) {
    return <div className="text-xs text-text-tertiary">{t("ws.loading")}</div>;
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
        {t("ws.empty")}
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
                  <span className="flex-shrink-0 rounded-full border border-border-default px-2 py-0.5 text-[10px] font-semibold text-text-tertiary">
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
                    <span className="truncate">
                      {t("ws.fromParent", { name: f.parent_filename })}
                    </span>
                  </>
                ) : null}
              </div>
              <div className="mt-2 flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void api.downloadFile(f.id, f.filename)}
                  className="focus-ring inline-flex items-center gap-1 rounded-md border border-border-default bg-bg-elevated px-2 py-1 text-xs font-medium text-text-primary transition-colors hover:bg-bg-surface"
                >
                  <Download className="h-3 w-3" />
                  {t("common.download")}
                </button>
                <button
                  type="button"
                  onClick={() => onPick(f)}
                  className="focus-ring inline-flex items-center gap-1 rounded-md border border-border-default bg-bg-elevated px-2 py-1 text-xs font-medium text-text-primary transition-colors hover:bg-bg-surface"
                >
                  <History className="h-3 w-3" />
                  {t("ws.history", { n: String(f.version_count) })}
                </button>
              </div>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

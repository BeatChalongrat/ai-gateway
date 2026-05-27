"use client";

// Per-lineage version timeline shown inside the WorkspacePanel
// after the user clicks a file. Vertical dotted timeline with
// per-version cards; download / compare / rollback inline.

import { useEffect, useState } from "react";

import { ArrowLeft, Download, GitCompare, RotateCcw } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type {
  ComparisonResponse,
  FileVersion,
} from "@/lib/types";

import { ComparisonCard } from "./ComparisonCard";
import { fileTypeIcon, humanSize, timeAgo } from "./workspaceUtil";

interface Props {
  convId: string;
  fileId: string;
  filename: string;
  contentType?: string;
  onBack: () => void;
}

export function VersionTimeline({ convId, fileId, filename, contentType, onBack }: Props) {
  const [versions, setVersions] = useState<FileVersion[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [comparison, setComparison] = useState<ComparisonResponse | null>(null);

  async function fetchVersions() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listFileVersions(convId, fileId);
      // Newest first for the timeline.
      const sorted = [...(res.versions ?? [])].sort((a, b) => (b.version ?? 0) - (a.version ?? 0));
      setVersions(sorted);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load versions.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void fetchVersions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId, fileId]);

  async function handleCompare(olderId: string, newerId: string) {
    setBusyAction(`compare:${olderId}-${newerId}`);
    setComparison(null);
    try {
      const res = await api.compareVersions(convId, olderId, newerId);
      setComparison(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Compare failed.");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleRollback(target: FileVersion) {
    const ok = window.confirm(
      `Roll back to v${target.version}? A new version (v${
        (versions?.[0]?.version ?? 0) + 1
      }) will be created with v${target.version}'s content. No versions are deleted.`,
    );
    if (!ok) return;
    setBusyAction(`rollback:${target.id}`);
    setComparison(null);
    try {
      await api.rollbackFile(convId, fileId, target.id);
      await fetchVersions();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Rollback failed.");
    } finally {
      setBusyAction(null);
    }
  }

  const list = versions ?? [];
  const total = list.length;

  return (
    <div className="flex flex-col gap-3">
      <button
        type="button"
        onClick={onBack}
        className="focus-ring inline-flex items-center gap-1 self-start text-xs font-medium text-text-tertiary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to files
      </button>

      <div className="flex items-center gap-2">
        <span className="flex-shrink-0">{fileTypeIcon(filename, contentType)}</span>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-text-primary">{filename}</div>
          <div className="text-xs text-text-tertiary">
            {total} version{total === 1 ? "" : "s"}
          </div>
        </div>
      </div>

      {loading ? (
        <div className="text-xs text-text-tertiary">Loading versions…</div>
      ) : null}
      {error ? (
        <div className="rounded-md border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-status-danger">
          {error}
        </div>
      ) : null}

      <ol className="relative ml-3 space-y-3 border-l border-border-default pl-5">
        {list.map((v, i) => {
          const olderNeighbor = list[i + 1];
          const compareKey = olderNeighbor
            ? `compare:${olderNeighbor.id}-${v.id}`
            : null;
          return (
            <li key={v.id} className="relative">
              <span
                className={`absolute -left-[1.4rem] top-3 inline-block h-2.5 w-2.5 rounded-full ring-2 ring-bg-surface ${
                  v.is_current ? "bg-blue-500" : "bg-text-tertiary"
                }`}
                aria-hidden
              />
              <div className="rounded-lg border border-border-subtle bg-bg-base/30 px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-text-primary">
                    v{v.version}
                    {v.is_current ? (
                      <span className="ml-2 text-[10px] font-medium uppercase tracking-wider text-blue-500">
                        current
                      </span>
                    ) : null}
                  </span>
                  <span className="text-xs text-text-tertiary">
                    {timeAgo(v.created_at)}
                  </span>
                </div>
                <div className="mt-0.5 text-xs text-text-tertiary">
                  {humanSize(v.file_size_bytes)}
                </div>
                {v.change_log && v.change_log.length > 0 ? (
                  <div className="mt-1.5 line-clamp-2 text-xs text-text-secondary">
                    {v.change_log[0]}
                  </div>
                ) : null}
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <a
                    href={api.downloadUrl(v.id)}
                    download={v.filename}
                    className="focus-ring inline-flex items-center gap-1 rounded-md border border-border-default bg-bg-elevated px-2 py-1 text-xs font-medium text-text-primary transition-colors hover:bg-bg-surface"
                  >
                    <Download className="h-3 w-3" />
                    Download
                  </a>
                  {olderNeighbor ? (
                    <button
                      type="button"
                      onClick={() => handleCompare(olderNeighbor.id, v.id)}
                      disabled={busyAction === compareKey}
                      className="focus-ring inline-flex items-center gap-1 rounded-md border border-border-default bg-bg-elevated px-2 py-1 text-xs font-medium text-text-primary transition-colors hover:bg-bg-surface disabled:opacity-60"
                    >
                      <GitCompare className="h-3 w-3" />
                      Compare with v{olderNeighbor.version}
                    </button>
                  ) : null}
                  {!v.is_current ? (
                    <button
                      type="button"
                      onClick={() => handleRollback(v)}
                      disabled={busyAction === `rollback:${v.id}`}
                      className="focus-ring inline-flex items-center gap-1 rounded-md border border-status-warning/40 bg-status-warning/10 px-2 py-1 text-xs font-medium text-status-warning transition-colors hover:bg-status-warning/20 disabled:opacity-60"
                    >
                      <RotateCcw className="h-3 w-3" />
                      Roll back
                    </button>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
        {!loading && list.length === 0 ? (
          <li className="text-xs text-text-tertiary">No versions in this lineage.</li>
        ) : null}
      </ol>

      {comparison ? (
        <ComparisonCard result={comparison} onClose={() => setComparison(null)} />
      ) : null}
    </div>
  );
}

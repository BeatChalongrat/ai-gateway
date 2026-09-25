"use client";

// Files staged for the agent: each one uploads the moment it is added
// (POST /installs/{id}/files) and keeps its server id for the message that
// sends it. A chip removed mid-upload aborts its request.

import { useCallback, useEffect, useRef, useState } from "react";

import { amApi, errorMessage, newClientId } from "../api";
import { tAm } from "../i18n";

export const DEFAULT_ACCEPT = [".xlsx", ".xlsm", ".csv", ".pdf"];
export const DEFAULT_MAX_MB = 20;

export interface Staged {
  localId: string;
  name: string;
  size: number;
  status: "uploading" | "done" | "error";
  progress: number;
  fileId?: string;
  error?: string;
}

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}

/** Why a file cannot be sent, or null. */
export function rejectReason(file: File, accept: string[], maxMb: number): string | null {
  if (accept.length && !accept.includes(extOf(file.name))) return tAm("error.fileType", { types: accept.join(" ") });
  if (file.size > maxMb * 1024 * 1024) return tAm("error.fileTooLarge", { mb: String(maxMb) });
  return null;
}

export function useUploads(installId: string | null, opts: { accept?: string[]; maxMb?: number } = {}) {
  const accept = opts.accept ?? DEFAULT_ACCEPT;
  const maxMb = opts.maxMb ?? DEFAULT_MAX_MB;
  const [items, setItems] = useState<Staged[]>([]);
  const aborts = useRef(new Map<string, AbortController>());

  useEffect(() => {
    const map = aborts.current;
    return () => {
      for (const c of map.values()) c.abort();
      map.clear();
    };
  }, []);

  const patch = useCallback((localId: string, p: Partial<Staged>) => {
    setItems((cur) => cur.map((s) => (s.localId === localId ? { ...s, ...p } : s)));
  }, []);

  const add = useCallback(
    (files: File[]) => {
      if (!installId) return;
      for (const file of files) {
        const localId = newClientId();
        const reason = rejectReason(file, accept, maxMb);
        const base: Staged = { localId, name: file.name, size: file.size, status: reason ? "error" : "uploading", progress: 0 };
        if (reason) base.error = reason;
        setItems((cur) => [...cur, base]);
        if (reason) continue;
        const ctrl = new AbortController();
        aborts.current.set(localId, ctrl);
        amApi
          .upload(installId, file, {
            clientId: localId,
            signal: ctrl.signal,
            onProgress: (pct) => patch(localId, { progress: pct }),
          })
          .then(
            (f) => patch(localId, { status: "done", progress: 100, fileId: f.id }),
            (err) => {
              if (ctrl.signal.aborted) return;
              patch(localId, { status: "error", error: errorMessage(err) });
            },
          )
          .finally(() => aborts.current.delete(localId));
      }
    },
    // accept is a fresh array each render when defaulted; its contents are what matter
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [installId, maxMb, accept.join(","), patch],
  );

  const remove = useCallback((localId: string) => {
    aborts.current.get(localId)?.abort();
    aborts.current.delete(localId);
    setItems((cur) => cur.filter((s) => s.localId !== localId));
  }, []);

  const clear = useCallback(() => {
    for (const c of aborts.current.values()) c.abort();
    aborts.current.clear();
    setItems([]);
  }, []);

  const uploading = items.some((s) => s.status === "uploading");
  const failed = items.some((s) => s.status === "error");
  const ids = items.filter((s) => s.status === "done" && s.fileId).map((s) => s.fileId as string);

  return { items, add, remove, clear, uploading, failed, ids, accept, maxMb };
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { EMPTY_ARTIFACT_DRAFT, type ArtifactDraft, type ArtifactDraftBody } from "@/lib/artifactWorkspace";

// One user/file identity, synchronous local crash recovery, debounced server CAS.
// Server drafts survive a device switch; local drafts never cross account keys.
export function useArtifactDraft(fileId: string, sha?: string) {
  const user = useAuthStore(s => s.user);
  const key = user ? `sran-artifact-draft:${user.tenant_id}:${user.user_id}:${fileId}` : null;
  const [value, setValue] = useState<ArtifactDraftBody>(EMPTY_ARTIFACT_DRAFT);
  const [status, setStatus] = useState<"loading" | "saved" | "saving" | "local" | "unsaved" | "conflict">("loading");
  const [recovery, setRecovery] = useState<ArtifactDraft>();
  const [undoCount, setUndoCount] = useState(0); const [redoCount, setRedoCount] = useState(0);
  const history = useRef<ArtifactDraftBody[]>([]); const future = useRef<ArtifactDraftBody[]>([]);
  const state = useRef({ value: EMPTY_ARTIFACT_DRAFT, revision: 0, ready: false, modified: false, key, fileId, sha, conflict: false });
  const serial = useRef<Promise<void>>(Promise.resolve()); const generation = useRef(0); const lastEdit = useRef({ group: "", time: 0 });
  const persistLocal = (s: typeof state.current) => { if (!s.key || !s.sha) return false; try { localStorage.setItem(s.key, JSON.stringify({ sha256: s.sha, revision: s.revision, body: s.value, updated_at: new Date().toISOString() })); return true; } catch { return false; } };
  const flush = useCallback(() => {
    const captured = state.current; const gen = generation.current;
    serial.current = serial.current.catch(() => undefined).then(async () => {
      if (!captured.ready || !captured.modified || captured.conflict || !captured.sha) return;
      const snapshot = captured.value;
      if (gen === generation.current) setStatus("saving");
      try {
        const result = await api.saveArtifactDraft(captured.fileId, { sha256: captured.sha, revision: captured.revision, body: snapshot });
        captured.revision = result.draft.revision; captured.modified = captured.value !== snapshot;
        persistLocal(captured);
        if (gen === generation.current) setStatus(captured.modified ? "saving" : "saved");
      } catch (e) {
        const conflict = (e as { status?: number }).status === 409 || /another client|draft changed/i.test(String(e));
        captured.conflict = conflict;
        if (gen === generation.current) setStatus(conflict ? "conflict" : persistLocal(captured) ? "local" : "unsaved");
      }
    });
    return serial.current;
  }, []);
  useEffect(() => {
    generation.current++; const gen = generation.current;
    const next = { value: EMPTY_ARTIFACT_DRAFT, revision: 0, ready: false, modified: false, key, fileId, sha, conflict: false };
    state.current = next; setValue(next.value); setRecovery(undefined); setStatus("loading"); history.current = []; future.current = []; setUndoCount(0); setRedoCount(0);
    if (!sha) return;
    let local: ArtifactDraft | null = null;
    try { local = key ? JSON.parse(localStorage.getItem(key) || "null") : null; } catch { /* corrupt local cache */ }
    void (async () => {
      let remote: ArtifactDraft | null = null; let offline = false;
      try { remote = (await api.getArtifactDraft(fileId)).draft; } catch { offline = true; }
      if (gen !== generation.current) return;
      next.revision = remote?.revision ?? local?.revision ?? 0;
      const valid = (d: ArtifactDraft | null): d is ArtifactDraft => !!d?.body?.changes && !!d.body.image && Number.isInteger(d.body.page);
      const chosen = valid(local) && (!remote || Date.parse(local.updated_at ?? "") > Date.parse(remote.updated_at ?? "")) ? local : remote;
      if (valid(local) && valid(remote) && local.revision !== remote.revision && JSON.stringify(local.body) !== JSON.stringify(remote.body)) {
        next.value = local.sha256 === sha ? local.body : EMPTY_ARTIFACT_DRAFT;
        next.conflict = true; next.modified = true; next.ready = true;
        setRecovery(local); setValue(next.value); setStatus("conflict"); return;
      }
      if (valid(chosen) && chosen.sha256 === sha) { next.value = chosen.body; next.modified = chosen !== remote; }
      else if (valid(chosen)) { setRecovery(chosen); next.conflict = true; }
      next.ready = true; setValue(next.value); setStatus(next.conflict ? "conflict" : offline ? valid(local) ? "local" : "unsaved" : next.modified ? "saving" : "saved");
    })();
    return () => { void flush(); generation.current++; };
  }, [fileId, sha, key, flush]);
  useEffect(() => { const timer = setTimeout(() => void flush(), 700); return () => clearTimeout(timer); }, [value, flush]);
  const update = useCallback((mutate: (old: ArtifactDraftBody) => ArtifactDraftBody, group = "") => {
    const s = state.current; if (!s.ready) return;
    const next = mutate(s.value); if (JSON.stringify(next) === JSON.stringify(s.value)) return;
    const now = Date.now(); if (!group || group !== lastEdit.current.group || now - lastEdit.current.time > 700) { history.current = [...history.current.slice(-79), s.value]; }
    lastEdit.current = { group, time: now }; future.current = []; s.value = next; s.modified = true;
    setValue(next); setUndoCount(history.current.length); setRedoCount(0); setStatus(s.conflict ? "conflict" : "saving"); persistLocal(s);
  }, []);
  const travel = (back: boolean) => { const from = back ? history.current : future.current; const to = back ? future.current : history.current; const next = from.pop(); if (!next) return; const s = state.current; to.push(s.value); s.value = next; s.modified = true; lastEdit.current = { group: "", time: 0 }; setValue(next); setUndoCount(history.current.length); setRedoCount(future.current.length); persistLocal(s); };
  const clear = async () => {
    await flush(); const s = state.current;
    try { if (s.revision) await api.deleteArtifactDraft(s.fileId, s.revision); } catch { setStatus("conflict"); return false; }
    if (s.key) localStorage.removeItem(s.key); s.value = EMPTY_ARTIFACT_DRAFT; s.revision = 0; s.modified = false; s.conflict = false;
    history.current = []; future.current = []; setUndoCount(0); setRedoCount(0); setValue(s.value); setRecovery(undefined); setStatus("saved"); return true;
  };
  return { value, update, status, ready: state.current.ready, recovery, undo: () => travel(true), redo: () => travel(false), undoCount, redoCount, flush, clear,
    exportRecovery: () => JSON.stringify(recovery ?? { sha256: sha, body: state.current.value }, null, 2),
    reloadRemote: async () => { await flush(); const remote = (await api.getArtifactDraft(fileId)).draft; const s = state.current; if (s.modified) setRecovery({ sha256: s.sha ?? "", revision: s.revision, body: s.value }); s.revision = remote?.revision ?? 0; s.value = remote && remote.sha256 === sha ? remote.body : EMPTY_ARTIFACT_DRAFT; s.conflict = false; s.modified = false; setValue(s.value); setStatus("saved"); persistLocal(s); },
  };
}

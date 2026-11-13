"use client";
import { useT } from "@/lib/i18n";
import type { useArtifactDraft } from "@/hooks/useArtifactDraft";

export function DraftControls({ draft, disabled }: { draft: ReturnType<typeof useArtifactDraft>; disabled: boolean }) {
  const t = useT();
  return <div className="flex flex-wrap items-center gap-3 text-xs" aria-live="polite">
    <span>{t(`artifact.draft.${draft.status}`)}</span>
    <button disabled={disabled || !draft.undoCount} onClick={draft.undo}>{t("artifact.undo")}</button>
    <button disabled={disabled || !draft.redoCount} onClick={draft.redo}>{t("artifact.redo")}</button>
    {(draft.status === "local" || draft.status === "unsaved") && <button onClick={() => void draft.flush()}>{t("artifact.sync")}</button>}
    {(draft.status === "conflict" || draft.status === "unsaved" || draft.recovery) && <>
      <button onClick={() => { const url = URL.createObjectURL(new Blob([draft.exportRecovery()], { type: "application/json" })); const a = document.createElement("a"); a.href = url; a.download = "draft-recovery.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>{t("artifact.recover")}</button>
      <button onClick={() => void draft.reloadRemote().catch(() => undefined)}>{t("artifact.reload")}</button>
    </>}
  </div>;
}

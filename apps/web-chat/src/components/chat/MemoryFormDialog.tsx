"use client";

// MemoryFormDialog — combined add + edit dialog for a single memory.
//
// One component, two modes:
//   - mode="create": empty form; submit calls createMemory()
//   - mode="edit":   form pre-filled from `initial`; submit calls
//                    updateMemory(initial.id, ...)
//
// Mirrors the design language of VoiceSettingsModal: Modal shell +
// inverted-primary save button + outlined cancel.

import { useEffect, useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { toast } from "@/components/ui/Toast";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import type { Memory } from "@/lib/types";

// Order intentional — fact first (most common), file_reference last
// (most niche). inferred_preference is omitted from the manual-add
// dropdown because it's Phase 4 background-inference territory.
const TYPE_OPTIONS: { value: string; labelKey: DictKey }[] = [
  { value: "explicit_fact", labelKey: "memory.form.opt.fact" },
  { value: "response_preference", labelKey: "memory.form.opt.preference" },
  { value: "ongoing_interest", labelKey: "memory.form.opt.interest" },
  { value: "confirmed_instruction", labelKey: "memory.form.opt.instruction" },
  { value: "artifact_reference", labelKey: "memory.form.opt.fileReference" },
];

const TYPE_DESCRIBE_KEYS: Record<string, DictKey> = {
  explicit_fact: "memory.form.describe.fact",
  response_preference: "memory.form.describe.preference",
  ongoing_interest: "memory.form.describe.interest",
  confirmed_instruction: "memory.form.describe.instruction",
  artifact_reference: "memory.form.describe.fileReference",
};

const MAX_CONTENT_CHARS = 1500;

interface Props {
  open: boolean;
  mode: "create" | "edit";
  initial?: Memory | null;
  onClose: () => void;
  onSaved: (m: Memory) => void;
}

export function MemoryFormDialog({
  open,
  mode,
  initial,
  onClose,
  onSaved,
}: Props) {
  const t = useT();
  const [content, setContent] = useState("");
  const [memoryType, setMemoryType] = useState("explicit_fact");
  const [category, setCategory] = useState("");
  const [saving, setSaving] = useState(false);

  // Reset / seed the form whenever the dialog opens. Without this,
  // an edit followed by an add would prefill the add dialog with
  // the previous memory's content.
  useEffect(() => {
    if (!open) return;
    if (mode === "edit" && initial) {
      setContent(initial.content);
      setMemoryType(initial.memory_type || "explicit_fact");
      setCategory(initial.category ?? "");
    } else {
      setContent("");
      setMemoryType("explicit_fact");
      setCategory("");
    }
  }, [open, mode, initial]);

  async function handleSave() {
    const trimmed = content.trim();
    if (!trimmed) {
      toast(t("memory.form.errorEmpty"));
      return;
    }
    if (trimmed.length > MAX_CONTENT_CHARS) {
      toast(t("memory.form.errorTooLong", { max: String(MAX_CONTENT_CHARS) }));
      return;
    }
    setSaving(true);
    try {
      let saved: Memory;
      if (mode === "edit" && initial) {
        saved = await api.updateMemory(initial.id, {
          content: trimmed,
          memory_type: memoryType,
          category: category.trim(),
        });
      } else {
        saved = await api.createMemory({
          memory_type: memoryType,
          content: trimmed,
          category: category.trim(),
        });
      }
      onSaved(saved);
      toast(mode === "edit" ? t("memory.form.toastUpdated") : t("memory.form.toastSaved"));
      onClose();
    } catch (err) {
      if (err instanceof ApiError) {
        // The backend returns a tailored message for DLP rejects /
        // duplicates — surface it directly so the user sees WHY the
        // save was rejected.
        toast(err.message || t("memory.form.toastError"));
      } else {
        toast(t("memory.form.toastError"));
      }
    } finally {
      setSaving(false);
    }
  }

  const remaining = MAX_CONTENT_CHARS - content.length;
  const title =
    mode === "edit" ? t("memory.form.titleEdit") : t("memory.form.titleAdd");
  const description =
    mode === "edit"
      ? t("memory.form.descriptionEdit")
      : t("memory.form.descriptionAdd");
  const describeKey = TYPE_DESCRIBE_KEYS[memoryType];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      maxWidth={520}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring rounded-lg border border-border bg-transparent px-4 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            {t("memory.form.cancel")}
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !content.trim()}
            className="focus-ring rounded-lg bg-text-primary px-6 py-2 text-sm font-medium text-bg-base transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {saving
              ? t("memory.form.saving")
              : mode === "edit"
                ? t("memory.form.saveEdit")
                : t("memory.form.saveNew")}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t("memory.form.contentLabel")}>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={t("memory.form.contentPlaceholder")}
            rows={3}
            maxLength={MAX_CONTENT_CHARS}
            autoFocus
            className="scrollbar-thin min-h-[88px] resize-y rounded-lg border border-border bg-bg-surface px-3 py-2.5 text-sm text-text-primary placeholder:text-text-placeholder focus:border-border-strong focus:outline-none"
          />
          <span
            className={`text-right text-xs ${
              remaining < 0 ? "text-status-danger" : "text-text-muted"
            }`}
          >
            {content.length}/{MAX_CONTENT_CHARS}
          </span>
        </Field>

        <Field label={t("memory.form.typeLabel")}>
          <select
            value={memoryType}
            onChange={(e) => setMemoryType(e.target.value)}
            className="rounded-lg border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary focus:border-border-strong focus:outline-none"
          >
            {TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {t(opt.labelKey)}
              </option>
            ))}
          </select>
          <span className="text-xs text-text-muted">
            {describeKey ? t(describeKey) : memoryType}
          </span>
        </Field>

        <Field label={t("memory.form.categoryLabel")}>
          <input
            type="text"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder={t("memory.form.categoryPlaceholder")}
            maxLength={50}
            className="rounded-lg border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-placeholder focus:border-border-strong focus:outline-none"
          />
        </Field>
      </div>
    </Modal>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium uppercase tracking-wider text-text-muted">
        {label}
      </label>
      {children}
    </div>
  );
}

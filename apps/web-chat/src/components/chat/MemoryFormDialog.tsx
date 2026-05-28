"use client";

// MemoryFormDialog — combined add + edit dialog for a single memory.
//
// One component, two modes:
//   - mode="create": empty form; submit calls createMemory()
//   - mode="edit":   form pre-filled from `initial`; submit calls
//                    updateMemory(initial.id, ...)
//
// Mirrors the design language of CustomInstructionsModal /
// VoiceSettingsModal: Modal shell + inverted-primary save button
// + outlined cancel.

import { useEffect, useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { toast } from "@/components/ui/Toast";
import { api, ApiError } from "@/lib/api";
import type { Memory } from "@/lib/types";

import { MEMORY_TYPE_LABELS } from "./MemoryCard";

// Order intentional — fact first (most common), file_reference last
// (most niche). inferred_preference is omitted from the manual-add
// dropdown because it's Phase 4 background-inference territory.
const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "explicit_fact", label: "Fact" },
  { value: "response_preference", label: "Preference" },
  { value: "ongoing_interest", label: "Interest" },
  { value: "confirmed_instruction", label: "Instruction" },
  { value: "artifact_reference", label: "File reference" },
];

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
      toast("Please enter what should be remembered.");
      return;
    }
    if (trimmed.length > MAX_CONTENT_CHARS) {
      toast(`Memory text is limited to ${MAX_CONTENT_CHARS} characters.`);
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
      toast(mode === "edit" ? "Memory updated." : "Memory saved.");
      onClose();
    } catch (err) {
      if (err instanceof ApiError) {
        // The backend returns a tailored message for DLP rejects /
        // duplicates — surface it directly so the user sees WHY the
        // save was rejected.
        toast(err.message || "Couldn't save the memory.");
      } else {
        toast("Couldn't save the memory.");
      }
    } finally {
      setSaving(false);
    }
  }

  const remaining = MAX_CONTENT_CHARS - content.length;
  const title = mode === "edit" ? "Edit memory" : "Add memory";
  const description =
    mode === "edit"
      ? "Update the saved fact, preference, or instruction."
      : "Save a fact or preference so it persists across all chats.";

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
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !content.trim()}
            className="focus-ring rounded-lg bg-text-primary px-6 py-2 text-sm font-medium text-bg-base transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Saving…" : mode === "edit" ? "Save changes" : "Save memory"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="What should I remember?">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="e.g. My name is Beat"
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

        <Field label="Type">
          <select
            value={memoryType}
            onChange={(e) => setMemoryType(e.target.value)}
            className="rounded-lg border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary focus:border-border-strong focus:outline-none"
          >
            {TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <span className="text-xs text-text-muted">
            {describeType(memoryType)}
          </span>
        </Field>

        <Field label="Category (optional)">
          <input
            type="text"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="identity, language, coding, work…"
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

function describeType(t: string): string {
  switch (t) {
    case "explicit_fact":
      return "A personal fact — name, role, identity, etc.";
    case "response_preference":
      return "How you want the AI to respond (tone, length, language).";
    case "ongoing_interest":
      return "What you're currently working on or interested in.";
    case "confirmed_instruction":
      return "A standing rule to follow in every conversation.";
    case "artifact_reference":
      return "Something about a file the AI should remember.";
    default:
      return MEMORY_TYPE_LABELS[t] ?? t;
  }
}

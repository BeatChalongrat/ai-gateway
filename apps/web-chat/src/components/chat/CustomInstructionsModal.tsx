"use client";

import { useEffect, useRef, useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { toast } from "@/components/ui/Toast";
import { api } from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
}

const MAX_CHARS = 1500;

export function CustomInstructionsModal({ open, onClose }: Props) {
  const [about, setAbout] = useState("");
  const [respond, setRespond] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // Debounced auto-save handle. We still expose an explicit Save
  // button for clarity, but typing also persists silently after a
  // 1s idle so the user doesn't lose changes if they close via
  // backdrop click.
  const autoSaveHandle = useRef<number | null>(null);
  const dirtyRef = useRef(false);

  // Lazy-load on open. We re-fetch every time so a save from
  // another tab / device flows through, but only once per open
  // cycle (loaded gate).
  useEffect(() => {
    if (!open || loaded) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const p = await api.getPreferences();
        if (cancelled) return;
        setAbout(p.custom_instructions_about ?? "");
        setRespond(p.custom_instructions_respond ?? "");
      } catch {
        if (!cancelled) toast("Couldn't load preferences.");
      } finally {
        if (!cancelled) {
          setLoading(false);
          setLoaded(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, loaded]);

  // Reset the loaded gate whenever the modal closes so the next
  // open re-fetches.
  useEffect(() => {
    if (!open) setLoaded(false);
  }, [open]);

  // Debounced background save. Fires 1s after the last edit while
  // the modal is open + dirty. The visible Save button is still
  // available for users who want an explicit confirmation.
  useEffect(() => {
    if (!open || !loaded || !dirtyRef.current) return;
    if (autoSaveHandle.current != null) {
      window.clearTimeout(autoSaveHandle.current);
    }
    autoSaveHandle.current = window.setTimeout(() => {
      autoSaveHandle.current = null;
      if (!dirtyRef.current) return;
      dirtyRef.current = false;
      void api
        .putPreferences({
          custom_instructions_about: about,
          custom_instructions_respond: respond,
        })
        .catch(() => {
          /* surface only on explicit save */
        });
    }, 1000);
    return () => {
      if (autoSaveHandle.current != null) {
        window.clearTimeout(autoSaveHandle.current);
        autoSaveHandle.current = null;
      }
    };
  }, [about, respond, open, loaded]);

  async function save() {
    if (about.length > MAX_CHARS || respond.length > MAX_CHARS) {
      toast(`Each field is limited to ${MAX_CHARS} characters.`);
      return;
    }
    setSaving(true);
    try {
      await api.putPreferences({
        custom_instructions_about: about,
        custom_instructions_respond: respond,
      });
      toast("Custom instructions saved.");
      onClose();
    } catch {
      toast("Couldn't save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Custom instructions"
      description="Shape how the AI responds in every conversation."
      maxWidth={560}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring rounded-md border border-border px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving || loading}
            className="focus-ring rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <Field
          label="What would you like the AI to know about you?"
          placeholder="e.g. I'm a software developer working with Python and Go. I prefer concise answers with code examples."
          value={about}
          onChange={(v) => {
            dirtyRef.current = true;
            setAbout(v);
          }}
          loading={loading}
        />
        <Field
          label="How would you like the AI to respond?"
          placeholder="e.g. Be concise and technical. Use code blocks. Explain trade-offs."
          value={respond}
          onChange={(v) => {
            dirtyRef.current = true;
            setRespond(v);
          }}
          loading={loading}
        />
      </div>
    </Modal>
  );
}

function Field({
  label,
  placeholder,
  value,
  onChange,
  loading,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  loading: boolean;
}) {
  const remaining = MAX_CHARS - value.length;
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-text-secondary">{label}</span>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onInput={(e) => {
          // dirtyRef lives in the parent component; toggling it
          // here lets the debounced auto-save effect know there's
          // unsynced content.
          void e;
        }}
        placeholder={placeholder}
        rows={5}
        disabled={loading}
        maxLength={MAX_CHARS}
        className="scrollbar-thin resize-y rounded-md border border-border bg-bg-base/60 px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:opacity-50"
      />
      <span
        className={`text-right text-[10px] ${
          remaining < 0 ? "text-status-danger" : "text-text-muted"
        }`}
      >
        {value.length}/{MAX_CHARS}
      </span>
    </label>
  );
}

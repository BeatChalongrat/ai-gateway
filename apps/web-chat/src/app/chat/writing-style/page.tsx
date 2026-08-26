"use client";

// Writing Style page — desktop-only.
//
// A React port of the WPF WritingStyleViewModel. It reads and writes the
// local `writingStyleProfile` setting through the typed `hostApi` bridge:
// the profile is a short, natural-language description of how the user
// writes that the assistant follows when drafting on their behalf.
//
// The profile field itself is FULLY wired — load on mount via getSettings,
// persist / clear via setSetting("writingStyleProfile", ...). "Learn my
// style" (Analyze) is deliberately left as a marked TODO: in the WPF app it
// streamed a gateway completion to distill samples into a profile, but on the
// hosted page all model access flows through the agent turn, which is out of
// scope for a settings screen. Rather than fake a model call, Analyze surfaces
// a toast noting the wiring is pending. The user can still hand-write or paste
// a profile below and save it.

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { useHostedMode } from "@/hooks/useHostedMode";
import { hostApi } from "@/lib/hostApi";

const MIN_SAMPLE_CHARS = 100;

export default function WritingStylePage() {
  const hosted = useHostedMode();
  if (!hosted) {
    return (
      <div className="flex flex-1 items-center justify-center bg-bg-base px-6 text-center">
        <p className="max-w-sm text-sm text-text-muted">
          This screen is available in the SRAN Desktop app.
        </p>
      </div>
    );
  }
  return <WritingStyleScreen />;
}

function WritingStyleScreen() {
  const [profile, setProfile] = useState("");
  const [samples, setSamples] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const settings = await hostApi.getSettings();
        if (alive) setProfile(settings.writingStyleProfile ?? "");
      } catch {
        if (alive) toast("Couldn't load your writing style.");
      } finally {
        if (alive) setLoaded(true);
      }
    })().catch(() => {
      /* handled above */
    });
    return () => {
      alive = false;
    };
  }, []);

  // TODO(host-wiring): the WPF WritingStyleViewModel streamed a chat-edge
  // completion here to distill `samples` into a short style profile, then
  // wrote the result into the profile field. On the hosted page, model access
  // is owned by the agent turn (not reachable from a settings screen), so the
  // distillation call is deferred. Until it lands, Analyze only signals intent;
  // the profile field below stays fully editable and save/clear work.
  function handleAnalyze() {
    toast("Style analysis runs through the host — wiring pending.");
  }

  async function persist(value: string, successMsg: string) {
    setSaving(true);
    try {
      const res = await hostApi.setSetting("writingStyleProfile", value);
      if (res.ok) {
        setProfile(value);
        toast(successMsg);
      } else {
        // FIX22 §1a: the host refuses a profile that would trip the gateway's
        // content guardrail on every turn, and says WHICH phrase — show it.
        toast(res.error && res.error.startsWith("Error:") ? res.error : "Couldn't save.");
      }
    } catch {
      toast("Couldn't save.");
    } finally {
      setSaving(false);
    }
  }

  const canAnalyze = samples.trim().length >= MIN_SAMPLE_CHARS;

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-8 pb-12 md:px-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <BackIcon />
          Back to chat
        </Link>

        <div className="mt-6">
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            Writing style
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            Teach the assistant to draft in your voice. It follows this profile
            whenever it writes on your behalf.
          </p>
        </div>

        {!loaded ? (
          <div className="mt-10 flex items-center justify-center py-10 text-text-secondary">
            <Spinner size={20} />
          </div>
        ) : (
          <div className="mt-8 flex flex-col gap-8">
            {/* Section 1 — Learn my style (Analyze is a marked TODO) */}
            <section className="rounded-xl border border-border bg-bg-surface p-5">
              <h2 className="text-base font-medium text-text-primary">
                Learn my style
              </h2>
              <p className="mt-1 text-sm text-text-secondary">
                Paste a few paragraphs you&apos;ve written. Analyze distills a
                short style profile the assistant follows when drafting.
              </p>
              <textarea
                value={samples}
                onChange={(e) => setSamples(e.target.value)}
                rows={8}
                placeholder="Paste your writing samples here…"
                className="scrollbar-thin mt-4 w-full resize-y rounded-lg border border-border bg-bg-base px-3 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none"
              />
              <div className="mt-3 flex items-center justify-between gap-3">
                <span className="text-xs text-text-muted">
                  {samples.trim().length < MIN_SAMPLE_CHARS
                    ? `Add at least ${MIN_SAMPLE_CHARS} characters to analyze.`
                    : "Ready to analyze."}
                </span>
                <Button
                  variant="secondary"
                  onClick={handleAnalyze}
                  disabled={!canAnalyze}
                  className="shrink-0"
                >
                  Analyze
                </Button>
              </div>
            </section>

            {/* Section 2 — Style profile (fully wired read/save/clear) */}
            <section className="rounded-xl border border-border bg-bg-surface p-5">
              <h2 className="text-base font-medium text-text-primary">
                Style profile
              </h2>
              <p className="mt-1 text-sm text-text-secondary">
                A short description of your voice. Edit it directly, then save.
              </p>
              <textarea
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
                rows={7}
                placeholder="e.g. Concise and direct. Prefers plain words over jargon. Short paragraphs, active voice, no exclamation marks."
                className="scrollbar-thin mt-4 w-full resize-y rounded-lg border border-border bg-bg-base px-3 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none"
              />
              <div className="mt-3 flex items-center justify-end gap-2">
                <Button
                  variant="ghost"
                  onClick={() => void persist("", "Cleared.")}
                  disabled={saving || profile.trim().length === 0}
                >
                  Clear
                </Button>
                <Button
                  onClick={() => void persist(profile.trim(), "Saved.")}
                  loading={saving}
                >
                  Save
                </Button>
              </div>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

function BackIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}

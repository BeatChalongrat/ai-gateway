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
//
// FIX33 B7: every visible string comes from lib/i18n/dict.ts (ws.* keys);
// i18n.hardcoded.test.tsx pins this file so English can't creep back.

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { useDesktopOnlyRoute } from "@/hooks/useDesktopOnlyRoute";
import { hostApi } from "@/lib/hostApi";
import { tOutside, useT } from "@/lib/i18n";

const MIN_SAMPLE_CHARS = 100;

export default function WritingStylePage() {
  // FIX34.1 Part F — on the web this route lands on /chat, never on a wall.
  const hosted = useDesktopOnlyRoute("/chat");
  if (!hosted) return null;
  return <WritingStyleScreen />;
}

function WritingStyleScreen() {
  const t = useT();
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
        if (alive) toast(tOutside("ws.toast.loadFailed"));
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
    toast(tOutside("ws.toast.analyzePending"));
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
        toast(res.error && res.error.startsWith("Error:") ? res.error : tOutside("ws.toast.saveFailed"));
      }
    } catch {
      toast(tOutside("ws.toast.saveFailed"));
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
          {t("ws.back")}
        </Link>

        <div className="mt-6">
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">{t("ws.title")}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t("ws.subtitle")}</p>
        </div>

        {!loaded ? (
          <div className="mt-10 flex items-center justify-center py-10 text-text-secondary">
            <Spinner size={20} />
          </div>
        ) : (
          <div className="mt-8 flex flex-col gap-8">
            {/* Section 1 — Learn my style (Analyze is a marked TODO) */}
            <section className="rounded-xl border border-border bg-bg-surface p-5">
              <h2 className="text-base font-medium text-text-primary">{t("ws.learn.title")}</h2>
              <p className="mt-1 text-sm text-text-secondary">{t("ws.learn.desc")}</p>
              <textarea
                value={samples}
                onChange={(e) => setSamples(e.target.value)}
                rows={8}
                placeholder={t("ws.learn.placeholder")}
                className="scrollbar-thin mt-4 w-full resize-y rounded-lg border border-border bg-bg-base px-3 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none"
              />
              <div className="mt-3 flex items-center justify-between gap-3">
                <span className="text-xs text-text-muted">
                  {samples.trim().length < MIN_SAMPLE_CHARS
                    ? t("ws.learn.need", { n: String(MIN_SAMPLE_CHARS) })
                    : t("ws.learn.ready")}
                </span>
                <Button
                  variant="secondary"
                  onClick={handleAnalyze}
                  disabled={!canAnalyze}
                  className="shrink-0"
                >
                  {t("ws.learn.analyze")}
                </Button>
              </div>
            </section>

            {/* Section 2 — Style profile (fully wired read/save/clear) */}
            <section className="rounded-xl border border-border bg-bg-surface p-5">
              <h2 className="text-base font-medium text-text-primary">{t("ws.profile.title")}</h2>
              <p className="mt-1 text-sm text-text-secondary">{t("ws.profile.desc")}</p>
              <textarea
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
                rows={7}
                placeholder={t("ws.profile.placeholder")}
                className="scrollbar-thin mt-4 w-full resize-y rounded-lg border border-border bg-bg-base px-3 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none"
              />
              <div className="mt-3 flex items-center justify-end gap-2">
                <Button
                  variant="ghost"
                  onClick={() => void persist("", tOutside("ws.toast.cleared"))}
                  disabled={saving || profile.trim().length === 0}
                >
                  {t("ws.clear")}
                </Button>
                <Button
                  onClick={() => void persist(profile.trim(), tOutside("ws.toast.saved"))}
                  loading={saving}
                >
                  {t("ws.save")}
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

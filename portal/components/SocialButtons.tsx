"use client";

import { signIn } from "next-auth/react";

// Env-gated: when NEXT_PUBLIC_OAUTH_ENABLED !== "true" we render
// nothing (Phase 1 dropped the old "coming soon" banner — the email
// form is the whole story until the flag flips). The flag is baked at
// BUILD time; flipping it requires an image rebuild.
//
// Google-only (Phase 1, finding L2): the GitHub button is intentionally
// not rendered and its OAuth app credentials must stay unset — user
// login is Google Workspace SSO only. The signIn("github") plumbing
// still exists in git history if that decision is ever reversed.

const enabled = process.env.NEXT_PUBLIC_OAUTH_ENABLED === "true";

// callbackUrl routes to the OAuth-complete page, which POSTs to the
// server-side cookie bridge to turn the NextAuth session into the
// portal's HttpOnly cookies.
const CALLBACK = "/oauth-complete";

export function SocialButtons() {
  if (!enabled) {
    return null;
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => signIn("google", { callbackUrl: CALLBACK })}
        className="w-full inline-flex items-center justify-center gap-3 rounded-lg border border-gray-300 bg-white hover:bg-gray-50 py-2.5 px-4 text-sm font-medium text-gray-800"
      >
        <GoogleIcon className="w-5 h-5" />
        Continue with Google
      </button>
    </div>
  );
}

export function OrDivider({ label = "or sign up with email" }: { label?: string }) {
  return (
    <div className="relative my-4">
      <div className="absolute inset-0 flex items-center" aria-hidden>
        <div className="w-full border-t border-gray-200" />
      </div>
      <div className="relative flex justify-center">
        <span className="bg-white px-3 text-xs text-gray-500 uppercase tracking-widest">
          {label}
        </span>
      </div>
    </div>
  );
}

// Google's brand guidelines require a specific colored icon rather
// than a monochrome glyph. Reproduced inline from the official marks.
function GoogleIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden>
      <path
        d="M22.5 12.27c0-.78-.07-1.53-.2-2.27H12v4.51h5.92a5.06 5.06 0 01-2.2 3.32v2.76h3.56c2.08-1.92 3.28-4.74 3.28-8.32z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.56-2.76c-.99.66-2.25 1.05-3.72 1.05-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A10.99 10.99 0 0012 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.1a6.6 6.6 0 010-4.2V7.06H2.18a11 11 0 000 9.88l3.66-2.84z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.07.56 4.22 1.65l3.15-3.15C17.46 2.09 14.97 1 12 1A10.99 10.99 0 002.18 7.06l3.66 2.84C6.71 7.3 9.14 5.38 12 5.38z"
        fill="#EA4335"
      />
    </svg>
  );
}

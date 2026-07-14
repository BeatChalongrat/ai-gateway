"use client";

// Renders a video served by the authenticated chat-edge file endpoint.
// Same reasoning as AuthedImage: auth is the HttpOnly portal cookie
// (no Bearer token exists in this app), and a bare <video src> can't
// recover from access-token expiry. We fetch the bytes through the
// cookie-authed client (api.fileObjectUrl, with 401 → refresh → retry)
// and play the resulting object URL, then revoke it on unmount. Used
// for AI-generated videos delivered via message.files[].

import { useEffect, useState } from "react";

import { VideoOff } from "lucide-react";

import { api } from "@/lib/api";

interface Props {
  fileId: string;
  /** Display name (for the a11y label / failure tooltip). */
  filename?: string;
  className?: string;
}

export function VideoPlayer({ fileId, filename, className }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setUrl(null);
    setFailed(false);
    api
      .fileObjectUrl(fileId)
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileId]);

  if (failed) {
    return (
      <div
        className={`flex aspect-video w-full max-w-[480px] items-center justify-center rounded-lg border border-border bg-bg-elevated text-text-tertiary ${className ?? ""}`}
        title={filename ?? "video"}
        aria-label={filename ?? "video"}
      >
        <VideoOff className="h-6 w-6" aria-hidden />
      </div>
    );
  }
  if (!url) {
    return (
      <div
        className={`aspect-video w-full max-w-[480px] animate-pulse rounded-lg bg-bg-elevated ${className ?? ""}`}
        aria-label={`Loading ${filename ?? "video"}`}
      />
    );
  }
  return (
    <video
      src={url}
      controls
      preload="metadata"
      className={`mt-1 w-full max-w-[480px] rounded-lg border border-border bg-black ${className ?? ""}`}
      aria-label={filename ?? "Generated video"}
    />
  );
}

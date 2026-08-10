"use client";

// Renders an image served by the authenticated chat-edge file
// endpoint. Auth is the HttpOnly ``portal_access_token`` cookie (there
// is no Bearer token in this app). A plain <img src> would send that
// cookie too, but it can't recover when the ~1h access token lapses —
// the load just 401s into a broken-image icon. We instead fetch the
// bytes through the authed client (api.fileObjectUrl → credentials:
// "include" + the 401 → silent-refresh → retry contract) and render
// the resulting object URL, then revoke it on unmount. In production
// the cookie reaches the chat-edge origin only when the deploy sets
// the documented cross-origin cookie envs (AUTH_COOKIE_DOMAIN etc.) —
// see backend/app/services/portal/cookies.py.

import { useEffect, useRef, useState } from "react";

import { ImageOff } from "lucide-react";

import { api } from "@/lib/api";

import { useT } from "@/lib/i18n";

interface Props {
  fileId: string;
  alt: string;
  className?: string;
  /** Receives the loaded object URL once — lets a parent reuse the
   *  same blob (e.g. hand it to the lightbox) without re-fetching. */
  onReady?: (objectUrl: string) => void;
}

export function AuthedImage({ fileId, alt, className, onReady }: Props) {
  const t = useT();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

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
        onReadyRef.current?.(u);
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
        className={`flex items-center justify-center bg-bg-elevated text-text-tertiary ${className ?? ""}`}
        title={alt}
        aria-label={alt}
      >
        <ImageOff className="h-5 w-5" aria-hidden />
      </div>
    );
  }
  if (!url) {
    return (
      <div
        className={`animate-pulse bg-bg-elevated ${className ?? ""}`}
        aria-label={t("a11y.loadingImage", { name: alt })}
      />
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={url}
      alt={alt}
      decoding="async"
      className={`img-fade-in ${className ?? ""}`}
    />
  );
}

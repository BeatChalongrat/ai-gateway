"use client";

// Renders an image served by the authenticated chat-edge file
// endpoint. That route is behind RequireAuth and accepts the Portal
// JWT via the Authorization header — but a plain <img src> can't send
// a header, so it would fall back to the portal_access_token cookie,
// which expires after ~1h and is dropped on cross-site subresource
// loads in production. We instead fetch the bytes with the Bearer
// token (api.fileObjectUrl) and render the resulting object URL, then
// revoke it on unmount. This is why uploaded images now show instead
// of a broken-image icon.

import { useEffect, useRef, useState } from "react";

import { ImageOff } from "lucide-react";

import { api, ApiError } from "@/lib/api";
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
  // "failed" = generic load error; "gone" = blob no longer in storage
  // (HTTP 410). We surface "gone" with an explicit label so an expired
  // upload reads as "File unavailable" instead of a mute broken icon.
  const [failed, setFailed] = useState(false);
  const [gone, setGone] = useState(false);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setUrl(null);
    setFailed(false);
    setGone(false);
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
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 410) {
          setGone(true);
        } else {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileId]);

  if (gone) {
    const label = t("file.unavailable");
    return (
      <div
        className={`flex flex-col items-center justify-center gap-1 bg-bg-elevated px-2 text-center text-text-tertiary ${className ?? ""}`}
        title={label}
        aria-label={label}
      >
        <ImageOff className="h-5 w-5" aria-hidden />
        <span className="text-[10px] leading-tight">{label}</span>
      </div>
    );
  }
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
        aria-label={`Loading ${alt}`}
      />
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={url} alt={alt} decoding="async" className={className} />;
}

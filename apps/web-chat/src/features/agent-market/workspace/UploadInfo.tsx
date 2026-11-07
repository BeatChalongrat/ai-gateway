"use client";

// AM4 — said where people upload: a 300 DPI scan beats a phone photo (for
// agents that read scans), how long files are kept, that this is not the
// company's document archive, and a warning before storage becomes a surprise.

import { useEffect, useState } from "react";

import { formatBytes } from "@/lib/fileUtil";

import { amApi } from "../api";
import { useAmT } from "../i18n";
import type { Manifest, StorageInfo } from "../types";
import { cls } from "../ui";
import { useWorkspace } from "./context";
import { acceptFromManifest } from "./useUploads";

const SCAN_TYPES = [".pdf", ".jpg", ".jpeg", ".png"];

export function readsScans(manifest: Manifest | null | undefined): boolean {
  const caps = [...(manifest?.capabilities?.required ?? []), ...(manifest?.capabilities?.optional ?? [])];
  return caps.includes("model.vision") || acceptFromManifest(manifest).some((a) => SCAN_TYPES.includes(a) && a !== ".pdf");
}

/** The install's storage and retention (read once per workspace). */
export function useStorage(installId: string | null): StorageInfo | null {
  const [info, setInfo] = useState<StorageInfo | null>(null);
  useEffect(() => {
    if (!installId) return;
    let alive = true;
    amApi.storage(installId).then(
      (r) => alive && setInfo(r),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId]);
  return info;
}

export function UploadInfo({ manifest, center = false, storage }: { manifest: Manifest | null | undefined; center?: boolean; storage?: StorageInfo | null }) {
  const t = useAmT();
  const ws = useWorkspace();
  const own = useStorage(storage === undefined ? ws?.installId ?? null : null);
  const info = storage === undefined ? own : storage;
  const years = info?.retention_years ?? 5;
  return (
    <div className={cls("flex flex-col gap-0.5 text-[11px] text-text-muted", center ? "items-center text-center" : "")} data-testid="am-upload-info">
      {readsScans(manifest) ? <p>{t("upload.scanTip")}</p> : null}
      <p>{`${t("upload.keepYears", { years: String(years) })} · ${t("upload.notArchive")}`}</p>
      {info?.storage.warn ? (
        <p className="text-status-warning" role="status">
          {t("storage.warn", { limit: formatBytes(info.storage.warn_bytes) })}
        </p>
      ) : null}
    </div>
  );
}

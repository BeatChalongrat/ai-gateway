"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/Button";
import { Modal } from "@/components/Modal";

interface Props {
  open: boolean;
  imageSrc: string | null;
  onCancel: () => void;
  onConfirm: (blob: Blob) => Promise<void> | void;
}

// Square crop — 256x256 preview + export. We draw onto a hidden 256x256
// canvas on confirm and hand the blob back to the caller. Pan + zoom
// with a single slider keep the UI small without pulling in a full
// react-image-crop dependency; the backend still enforces a square
// crop + format/size guardrails so a crafted client can't slip a bigger
// image through.
const PREVIEW = 256;

export function AvatarCropModal({ open, imageSrc, onCancel, onConfirm }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [busy, setBusy] = useState(false);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  // Reset zoom/offset whenever a new image is picked.
  useEffect(() => {
    if (!open) return;
    setZoom(1);
    setOffset({ x: 0, y: 0 });
  }, [open, imageSrc]);

  // Load the source into an offscreen image so we can measure it and
  // redraw as zoom/pan change without DOM churn.
  useEffect(() => {
    if (!open || !imageSrc) {
      imgRef.current = null;
      return;
    }
    const im = new Image();
    im.crossOrigin = "anonymous";
    im.onload = () => {
      imgRef.current = im;
      draw();
    };
    im.src = imageSrc;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, imageSrc]);

  useEffect(() => {
    draw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom, offset]);

  function draw() {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, PREVIEW, PREVIEW);

    // Fit-short-side so the smallest dimension of the source covers
    // PREVIEW at zoom=1 — gives the user a sensible starting crop.
    const fitScale = PREVIEW / Math.min(img.width, img.height);
    const scale = fitScale * zoom;
    const drawW = img.width * scale;
    const drawH = img.height * scale;
    const dx = (PREVIEW - drawW) / 2 + offset.x;
    const dy = (PREVIEW - drawH) / 2 + offset.y;

    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, dx, dy, drawW, drawH);
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
    drag.current = {
      x: e.clientX,
      y: e.clientY,
      ox: offset.x,
      oy: offset.y,
    };
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drag.current) return;
    setOffset({
      x: drag.current.ox + (e.clientX - drag.current.x),
      y: drag.current.oy + (e.clientY - drag.current.y),
    });
  }

  function onPointerUp() {
    drag.current = null;
  }

  async function handleConfirm() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    setBusy(true);
    try {
      const blob: Blob | null = await new Promise((resolve) =>
        canvas.toBlob((b) => resolve(b), "image/webp", 0.9)
      );
      if (!blob) return;
      await onConfirm(blob);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : onCancel}
      title="Crop profile picture"
      widthClass="max-w-md"
      footer={
        <>
          <Button
            type="button"
            variant="secondary"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button type="button" onClick={handleConfirm} loading={busy}>
            Save
          </Button>
        </>
      }
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 16,
        }}
      >
        <div
          style={{
            width: PREVIEW,
            height: PREVIEW,
            borderRadius: "50%",
            overflow: "hidden",
            border: "1px solid var(--border-default)",
            background: "var(--bg-elevated)",
            position: "relative",
            touchAction: "none",
          }}
        >
          <canvas
            ref={canvasRef}
            width={PREVIEW}
            height={PREVIEW}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            style={{
              width: "100%",
              height: "100%",
              cursor: drag.current ? "grabbing" : "grab",
              display: "block",
            }}
          />
        </div>

        <div style={{ width: "100%" }}>
          <label
            htmlFor="avatar-zoom"
            style={{
              display: "block",
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--text-tertiary)",
              fontWeight: 600,
              marginBottom: 6,
            }}
          >
            Zoom
          </label>
          <input
            id="avatar-zoom"
            type="range"
            min={1}
            max={3}
            step={0.05}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
            style={{ width: "100%", accentColor: "var(--accent-primary)" }}
          />
        </div>

        <p
          style={{
            fontSize: 12,
            color: "var(--text-tertiary)",
            margin: 0,
            textAlign: "center",
          }}
        >
          Drag to reposition. The preview above is exactly what we&apos;ll save.
        </p>
      </div>
    </Modal>
  );
}

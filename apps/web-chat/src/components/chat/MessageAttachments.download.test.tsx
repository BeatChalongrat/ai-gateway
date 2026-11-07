// Batch 1 W2 — image thumbnail download.
//
// Pins: the hover download button on an image attachment saves the
// SAME object URL AuthedImage already loaded (no second fetch), under
// the attachment's filename; opening the lightbox forwards that
// filename so its download control works too.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockDownload } = vi.hoisted(() => ({ mockDownload: vi.fn() }));

vi.mock("@/lib/fileUtil", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/fileUtil")>();
  return { ...actual, downloadFromObjectUrl: mockDownload };
});

// AuthedImage fetches through the authed client; stand in with a bare
// <img> that reports a loaded object URL immediately.
vi.mock("./AuthedImage", () => ({
  AuthedImage: ({
    alt,
    onReady,
    className,
  }: {
    alt: string;
    onReady?: (u: string) => void;
    className?: string;
  }) => {
    onReady?.("blob:thumb-url");
    // eslint-disable-next-line @next/next/no-img-element
    return <img src="blob:thumb-url" alt={alt} className={className} />;
  },
}));

import { useI18nStore } from "@/lib/i18n";
import type { FileAttachment } from "@/lib/types";
import { useLightboxStore } from "@/components/ui/ImageLightbox";

import { MessageAttachments } from "./MessageAttachments";

const imageFile: FileAttachment = {
  id: "f1",
  filename: "generated-cat.png",
  content_type: "image/png",
  file_size_bytes: 12345,
  extraction_status: "completed",
  created_at: "2026-08-18T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useLightboxStore.getState().close();
});
afterEach(cleanup);

describe("MessageAttachments image download", () => {
  it("downloads the loaded object URL under the attachment filename", () => {
    render(<MessageAttachments files={[imageFile]} />);

    fireEvent.click(screen.getByTestId("image-thumb-download"));

    expect(mockDownload).toHaveBeenCalledTimes(1);
    expect(mockDownload).toHaveBeenCalledWith(
      "blob:thumb-url",
      "generated-cat.png",
    );
  });

  it("opens the lightbox with the filename so its download works too", () => {
    render(<MessageAttachments files={[imageFile]} />);

    fireEvent.click(screen.getByTitle(/generated-cat\.png/));

    const lb = useLightboxStore.getState();
    expect(lb.src).toBe("blob:thumb-url");
    expect(lb.filename).toBe("generated-cat.png");
  });
});

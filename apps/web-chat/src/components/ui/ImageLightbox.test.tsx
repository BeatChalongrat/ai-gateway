// Batch 1 W2 — lightbox download + zoom controls.
//
// Pins: the download button saves the ALREADY-LOADED object URL under
// the opener-provided filename (no re-fetch), wheel zoom transforms
// the image and `0` / the reset control return to fit, and Escape
// still closes with the new controls present.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockDownload } = vi.hoisted(() => ({ mockDownload: vi.fn() }));

vi.mock("@/lib/fileUtil", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/fileUtil")>();
  return { ...actual, downloadFromObjectUrl: mockDownload };
});

import { useI18nStore } from "@/lib/i18n";

import { ImageLightbox, openLightbox, useLightboxStore } from "./ImageLightbox";

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useLightboxStore.getState().close();
});
afterEach(cleanup);

describe("ImageLightbox", () => {
  it("downloads the loaded object URL under the opener's filename", () => {
    render(<ImageLightbox />);
    act(() => openLightbox("blob:cat-url", "cat.png", "cat.png"));

    fireEvent.click(screen.getByTestId("lightbox-download"));

    expect(mockDownload).toHaveBeenCalledTimes(1);
    expect(mockDownload).toHaveBeenCalledWith("blob:cat-url", "cat.png");
  });

  it("hides the download control when no filename was provided", () => {
    render(<ImageLightbox />);
    act(() => openLightbox("blob:bare-url", "alt only"));

    expect(screen.queryByTestId("lightbox-download")).toBeNull();
  });

  it("wheel-zooms the image and `0` resets to fit", () => {
    render(<ImageLightbox />);
    act(() => openLightbox("blob:z", "z.png", "z.png"));
    const img = screen.getByAltText("z.png") as HTMLImageElement;

    expect(img.style.transform).toContain("scale(1)");
    fireEvent.wheel(img, { deltaY: -120 });
    expect(img.style.transform).toContain("scale(1.2)");

    fireEvent.keyDown(document, { key: "0" });
    expect(img.style.transform).toContain("scale(1)");
  });

  it("the reset control appears while zoomed and returns to fit", () => {
    render(<ImageLightbox />);
    act(() => openLightbox("blob:z", "z.png", "z.png"));
    const img = screen.getByAltText("z.png") as HTMLImageElement;

    expect(screen.queryByTestId("lightbox-zoom-reset")).toBeNull();
    fireEvent.wheel(img, { deltaY: -120 });
    fireEvent.click(screen.getByTestId("lightbox-zoom-reset"));

    expect(img.style.transform).toContain("scale(1)");
    expect(screen.queryByTestId("lightbox-zoom-reset")).toBeNull();
  });

  it("still closes on Escape with the new controls present", () => {
    render(<ImageLightbox />);
    act(() => openLightbox("blob:esc", "esc.png", "esc.png"));
    expect(screen.getByTestId("lightbox-download")).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(useLightboxStore.getState().src).toBeNull();
    expect(screen.queryByTestId("lightbox-download")).toBeNull();
  });
});

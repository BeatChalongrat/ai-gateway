// AM6 — Button gained a size ("sm") and a link form (ButtonLink) and Modal an
// optional test id, so that Agent Market could drop its own buttons and
// dialog. A caller that passes none of them must get exactly what it got
// before. The markup below is what the components produced before the change,
// written out by hand, not computed from the code under test.
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";

import { Button, ButtonLink } from "./Button";
import { Modal } from "./Modal";

const BASE = "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-border-strong";
const SMALL = "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-border-strong";
const LOOK = {
  primary: "bg-text-primary hover:opacity-90 text-bg-base shadow-sm disabled:opacity-40 disabled:cursor-not-allowed",
  secondary: "bg-bg-secondary hover:bg-bg-elevated text-text-primary border border-border disabled:opacity-50",
  ghost: "bg-transparent hover:bg-bg-elevated text-text-secondary hover:text-text-primary",
  danger: "bg-status-danger-solid hover:bg-status-danger-solid/90 text-white disabled:opacity-50",
} as const;

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const html = (ui: React.ReactElement) => render(ui).container.innerHTML;

describe("Button — a caller that names no size gets the button it always got", () => {
  it("with no props at all", () => {
    expect(html(<Button>Save</Button>)).toBe(`<button class="${BASE} ${LOOK.primary} ">Save</button>`);
  });

  for (const variant of ["primary", "secondary", "ghost", "danger"] as const) {
    it(`${variant}, with the caller's own class and attributes`, () => {
      expect(
        html(
          <Button variant={variant} className="mt-4" type="button" data-testid="x">
            Save
          </Button>,
        ),
      ).toBe(`<button class="${BASE} ${LOOK[variant]} mt-4" type="button" data-testid="x">Save</button>`);
    });
  }

  it("loading: disabled, with the same spinner", () => {
    expect(html(<Button loading>Save</Button>)).toBe(
      `<button disabled="" class="${BASE} ${LOOK.primary} "><span class="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" aria-hidden="true"></span>Save</button>`,
    );
  });

  it("disabled", () => {
    expect(html(<Button variant="secondary" disabled>Save</Button>)).toBe(`<button disabled="" class="${BASE} ${LOOK.secondary} ">Save</button>`);
  });
});

describe("Button — what AM6 added", () => {
  it('size "sm" changes the padding, the gap and the text size, nothing else', () => {
    expect(html(<Button size="sm" variant="secondary">Save</Button>)).toBe(`<button class="${SMALL} ${LOOK.secondary} ">Save</button>`);
  });

  it('size "sm", loading: a spinner that fits the smaller text', () => {
    expect(html(<Button size="sm" loading>Save</Button>)).toBe(
      `<button disabled="" class="${SMALL} ${LOOK.primary} "><span class="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" aria-hidden="true"></span>Save</button>`,
    );
  });

  it("ButtonLink is a link in the Button's classes", () => {
    const { container } = render(
      <ButtonLink href="/chat/agents" variant="secondary" data-testid="x">
        Open
      </ButtonLink>,
    );
    const link = container.firstElementChild as HTMLAnchorElement;
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("/chat/agents");
    expect(link.getAttribute("data-testid")).toBe("x");
    expect(link.className).toBe(`${BASE} ${LOOK.secondary} `);
    expect(container.querySelector("button")).toBeNull();
  });
});

describe("Modal — a caller that names no test id gets the dialog it always got", () => {
  const dialog = (ui: React.ReactElement) => render(ui).getByRole("dialog");

  it("no test id attribute unless one is asked for", () => {
    const plain = dialog(
      <Modal open onClose={() => {}} title="Delete chat" description="This cannot be undone">
        body
      </Modal>,
    );
    expect(plain.getAttributeNames().sort()).toEqual(["aria-label", "aria-modal", "class", "role"]);
    expect(plain.className).toBe("fixed inset-0 z-40 flex items-center justify-center bg-black/65 px-4 backdrop-blur-sm");
    expect((plain.firstElementChild as HTMLElement).getAttribute("style")).toBe("max-width: 480px;");
  });

  it("the test id, when asked for, is on the dialog itself", () => {
    const named = dialog(
      <Modal open onClose={() => {}} title="Delete chat" testId="delete-dialog">
        body
      </Modal>,
    );
    expect(named.getAttribute("data-testid")).toBe("delete-dialog");
    expect(named.getAttribute("aria-label")).toBe("Delete chat");
  });

  it("the same on a phone, where the dialog is a sheet", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
    const plain = dialog(
      <Modal open onClose={() => {}} title="Delete chat">
        body
      </Modal>,
    );
    expect(plain.getAttributeNames().sort()).toEqual(["aria-label", "aria-modal", "class", "role"]);
    expect(plain.className).toBe("fixed inset-0 z-40 flex items-end justify-center bg-black/65 backdrop-blur-sm");
    cleanup();
    const named = dialog(
      <Modal open onClose={() => {}} title="Delete chat" testId="delete-dialog">
        body
      </Modal>,
    );
    expect(named.getAttribute("data-testid")).toBe("delete-dialog");
  });
});

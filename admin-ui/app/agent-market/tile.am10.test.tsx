// AM10 B — an agent's tile on the admin page. The icon ships inside the agent
// package and is painted with the agent's accent, as in the chat app. An agent
// that is not published is drawn quietly — but with its own icon: the walk on
// the built admin app found every unpublished agent wearing a stand-in glyph,
// which is all five on a stack where nothing is published yet.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AgentTile } from "./_sections/shared";

const look = {
  icon_svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 20V10l8-6 8 6v10"/></svg>',
  accent: { light: "#1D4ED8", dark: "#8FB0FF" },
};

afterEach(cleanup);

describe("an agent's tile on the admin page", () => {
  it("is the package's own icon in the agent's colour", () => {
    render(<AgentTile look={look} />);
    const tile = screen.getByTestId("am-tile");
    expect(tile.getAttribute("data-icon")).toBe("own");
    expect([look.accent.light, look.accent.dark]).toContain(tile.getAttribute("data-accent"));
    expect(tile.getAttribute("data-quiet")).toBeNull();
  });

  it("keeps the package's own icon when the agent is not published — drawn quietly, not replaced", () => {
    render(<AgentTile icon="bank" look={look} quiet />);
    const tile = screen.getByTestId("am-tile");
    expect(tile.getAttribute("data-icon")).toBe("own");
    expect(tile.getAttribute("data-quiet")).toBe("true");
    expect((tile.firstElementChild as HTMLElement).style.background).toContain("--text-muted");
  });

  it("falls back to a stand-in only for a package that carries no icon", () => {
    render(<AgentTile icon="bank" />);
    expect(screen.getByTestId("am-tile").getAttribute("data-icon")).toBe("standin");
  });

  it("refuses markup that is not a plain shape", () => {
    render(<AgentTile look={{ ...look, icon_svg: '<svg viewBox="0 0 24 24"><script>alert(1)</script></svg>' }} />);
    expect(screen.getByTestId("am-tile").getAttribute("data-icon")).toBe("standin");
  });
});

// FIX31 §1.1 — a sub-agent's consent card says who is asking, and with what.
//
// The user is approving something they did not directly ask for. The card
// must say so plainly, name the sub-agent, and show the task the assistant
// wrote for it — labelled as the assistant's words — above the exact command.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import type { PendingConsent } from "@/lib/types";

import { ConsentCard } from "./ConsentCard";

const base: PendingConsent = {
  promptId: "consent-1",
  tool: "run_command",
  description: "run_command (Shell)",
  category: "Shell",
  argumentSummary: "command=Get-Date -Format o",
  argumentDetail: '{\n  "command": "Get-Date -Format o"\n}',
  dangerClass: "danger.shell",
};

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

describe("ConsentCard — sub-agent attribution", () => {
  it("an unattributed card reads as the assistant's own request", () => {
    render(<ConsentCard consent={base} onDecision={() => {}} />);
    expect(screen.getByText("The assistant wants to use a tool")).toBeTruthy();
    expect(screen.queryByTestId("consent-requester")).toBeNull();
    expect(screen.getByTestId("consent-card").getAttribute("data-requested-by")).toBeNull();
  });

  it("a sub-agent's card names the sub-agent, says the user did not ask for it, and quotes the task as the assistant's words", () => {
    const onDecision = vi.fn();
    render(
      <ConsentCard
        consent={{ ...base, requestedBy: { kind: "subagent", subagentId: "subagent-abc", label: "date", task: "get the current date with a shell command" } }}
        onDecision={onDecision}
      />,
    );
    expect(screen.getByText("A sub-agent wants to use a tool")).toBeTruthy();
    const req = screen.getByTestId("consent-requester");
    expect(req.textContent).toContain("Requested by sub-agent “date” — a helper the assistant started on its own. You did not ask for this directly.");
    expect(req.textContent).toContain("The task the assistant gave it (the assistant’s words, not yours):");
    expect(req.textContent).toContain("get the current date with a shell command");
    expect(screen.getByTestId("consent-card").getAttribute("data-requested-by")).toBe("subagent");
    // The exact command is still the thing being approved, and the danger banner still shows.
    expect(screen.getByText(/"command": "Get-Date -Format o"/)).toBeTruthy();
    expect(screen.getByText(/runs a command on your computer/)).toBeTruthy();
    // The three answers are the user's, unchanged.
    fireEvent.click(screen.getByTestId("consent-deny"));
    expect(onDecision).toHaveBeenCalledWith("deny", "once");
  });

  it("an unnamed sub-agent still reads as a sub-agent", () => {
    render(<ConsentCard consent={{ ...base, requestedBy: { kind: "subagent", subagentId: "subagent-abc", label: "", task: "ping the server" } }} onDecision={() => {}} />);
    expect(screen.getByTestId("consent-requester").textContent).toContain("Requested by a sub-agent — a helper the assistant started on its own.");
  });

  it("Thai locale", () => {
    useI18nStore.setState({ locale: "th" });
    render(<ConsentCard consent={{ ...base, requestedBy: { kind: "subagent", subagentId: "s", label: "date", task: "t" } }} onDecision={() => {}} />);
    expect(screen.getByText("Sub-agent ต้องการใช้เครื่องมือ")).toBeTruthy();
    expect(screen.getByTestId("consent-requester").textContent).toContain("คุณไม่ได้สั่งสิ่งนี้โดยตรง");
  });
});

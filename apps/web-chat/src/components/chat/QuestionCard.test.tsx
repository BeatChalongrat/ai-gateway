// FIX73 — the question card (desktop shell). Pins:
//   • renders every question with its header chip and the model's options,
//     plus the client-added "Something else" row;
//   • single-select: one label goes back verbatim; multi-select joins many;
//   • "Something else" returns the user's OWN text, never the word Other;
//   • the free reply box answers instead of the options ("response");
//   • Answer is disabled until every question has an answer (or a reply);
//   • Skip posts outcome "skipped" with nothing chosen;
//   • the card answers once — a second click does nothing.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import type { PendingQuestion } from "@/lib/types";

import { QuestionCard } from "./QuestionCard";

const one: PendingQuestion = {
  promptId: "question-1",
  questions: [{ question: "Which format?", header: "Format", multiSelect: false, options: [{ label: "Summary", description: "Brief overview" }, { label: "Detailed", description: "Full explanation" }] }],
};
const two: PendingQuestion = {
  promptId: "question-2",
  questions: [
    { question: "Which format?", header: "Format", multiSelect: false, options: [{ label: "Summary", description: "" }, { label: "Detailed", description: "" }] },
    { question: "Which sections?", header: "Sections", multiSelect: true, options: [{ label: "Intro", description: "" }, { label: "Body", description: "" }, { label: "Appendix", description: "" }] },
  ],
};

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

describe("QuestionCard", () => {
  it("renders the header chip, the question, the model's options and the client's 'Something else' row", () => {
    render(<QuestionCard question={one} onAnswer={() => {}} />);
    expect(screen.getByTestId("question-card").getAttribute("data-prompt-id")).toBe("question-1");
    expect(screen.getByTestId("question-header").textContent).toBe("Format");
    expect(screen.getByText("Which format?")).toBeTruthy();
    // FIX77 D4 — options carry their selection number.
    expect(screen.getAllByTestId("question-option").map((b) => b.textContent)).toEqual(["1SummaryBrief overview", "2DetailedFull explanation"]);
    expect(screen.getByTestId("question-other").textContent).toBe("3Something else");
    expect((screen.getByTestId("question-submit") as HTMLButtonElement).disabled).toBe(true);
  });

  it("FIX77 D1 — the model-written header chip renders as written (no uppercase transform)", () => {
    const mixed: PendingQuestion = {
      promptId: "question-3",
      questions: [{ question: "ข้อไหน?", header: "ควิช tools", multiSelect: false, options: [{ label: "ก", description: "" }, { label: "ข", description: "" }] }],
    };
    render(<QuestionCard question={mixed} onAnswer={() => {}} />);
    const chip = screen.getByTestId("question-header");
    expect(chip.textContent).toBe("ควิช tools");
    expect(chip.className).not.toMatch(/uppercase/);
  });

  it("FIX77 D4 — number keys choose options; typing in a field never triggers them", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard question={one} onAnswer={onAnswer} />);
    fireEvent.keyDown(window, { key: "2" });
    expect(screen.getAllByTestId("question-option")[1]!.getAttribute("aria-checked")).toBe("true");
    // "3" = the Something-else row; its text box opens and typing digits there must not toggle anything
    fireEvent.keyDown(window, { key: "3" });
    const box = screen.getByTestId("question-other-text");
    fireEvent.keyDown(box, { key: "1" });
    expect(screen.getAllByTestId("question-option")[0]!.getAttribute("aria-checked")).toBe("false");
  });

  it("single-select posts the chosen label verbatim as the answer", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard question={one} onAnswer={onAnswer} />);
    fireEvent.click(screen.getAllByTestId("question-option")[1]!);
    fireEvent.click(screen.getByTestId("question-submit"));
    expect(onAnswer).toHaveBeenCalledWith({ promptId: "question-1", outcome: "answered", answers: { "Which format?": ["Detailed"] }, response: null });
  });

  it("multi-select joins every chosen label; Answer stays disabled until EVERY question has an answer", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard question={two} onAnswer={onAnswer} />);
    const opts = screen.getAllByTestId("question-option");
    fireEvent.click(opts[2]!); // Intro
    fireEvent.click(opts[4]!); // Appendix
    expect((screen.getByTestId("question-submit") as HTMLButtonElement).disabled).toBe(true); // format not answered yet
    fireEvent.click(opts[0]!); // Summary
    fireEvent.click(screen.getByTestId("question-submit"));
    expect(onAnswer).toHaveBeenCalledWith({ promptId: "question-2", outcome: "answered", answers: { "Which format?": ["Summary"], "Which sections?": ["Intro", "Appendix"] }, response: null });
  });

  it("'Something else' returns the user's own text — never the word Other", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard question={one} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByTestId("question-other"));
    fireEvent.change(screen.getByTestId("question-other-text"), { target: { value: "a slide deck" } });
    fireEvent.click(screen.getByTestId("question-submit"));
    const payload = onAnswer.mock.calls[0]![0] as { answers: Record<string, string[]> };
    expect(payload.answers).toEqual({ "Which format?": ["a slide deck"] });
    expect(JSON.stringify(payload)).not.toMatch(/other/i);
  });

  it("a free reply answers instead of the options and rides as `response`", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard question={two} onAnswer={onAnswer} />);
    // FIX77 D3 — the free reply hides behind its toggle until asked for.
    fireEvent.click(screen.getByTestId("question-reply-toggle"));
    fireEvent.change(screen.getByTestId("question-reply"), { target: { value: "just do both, short" } });
    expect((screen.getByTestId("question-submit") as HTMLButtonElement).disabled).toBe(false);
    fireEvent.keyDown(screen.getByTestId("question-reply"), { key: "Enter" });
    expect(onAnswer).toHaveBeenCalledWith({ promptId: "question-2", outcome: "answered", answers: {}, response: "just do both, short" });
  });

  it("Skip posts outcome 'skipped' with nothing chosen, and the card answers only once", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard question={one} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByTestId("question-skip"));
    fireEvent.click(screen.getByTestId("question-skip"));
    fireEvent.click(screen.getByTestId("question-submit"));
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(onAnswer).toHaveBeenCalledWith({ promptId: "question-1", outcome: "skipped", answers: {}, response: null });
  });

  it("speaks Thai when the locale is Thai", () => {
    useI18nStore.setState({ locale: "th" });
    render(<QuestionCard question={one} onAnswer={() => {}} />);
    expect(screen.getByText("ผู้ช่วยมีคำถาม")).toBeTruthy();
    expect(screen.getByTestId("question-other").textContent).toBe("3อย่างอื่น");
    expect(screen.getByTestId("question-skip").textContent).toBe("ข้าม");
  });
});

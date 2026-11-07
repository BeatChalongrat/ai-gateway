// AM22 — the agents that read scans now have a model write their commentary too. The files card says both:
// which model read the scans, and which wrote the words — or that the agent's rules did, because no model
// could answer (the working paper says the same on its commentary sheet). An agent whose words are still the
// rules' (an older round, no written_by_rules) says only who read the scans, as before.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";

import type { OutputsModel } from "../types";
import { SeenBy } from "./OutputsCard";

const summary = { models: [{ model: "qwen3.8-27b-fp8", self_hosted: true, documents: 2 }] };
const card = (model: OutputsModel) => <SeenBy outputs={{ run_id: "r-1", artifacts: [], model, summary }} />;
const lines = () => Array.from(screen.getByTestId("am-outputs-seen-by").querySelectorAll("p")).map((p) => p.textContent ?? "");

describe("a scan-reading agent's card says who wrote its commentary", () => {
  afterEach(cleanup);

  it("a model wrote: said beside what read the scans, in Thai and in English, naming no model (AM23)", () => {
    useI18nStore.setState({ locale: "th" });
    render(card({ uses: "vision", choice: "auto", model: "qwen3.8-27b-fp8", self_hosted: true, written_by_rules: false }));
    expect(lines()).toHaveLength(2);
    expect(lines()[0]).toContain("อ่านโดยผู้ช่วย AI ขององค์กร (ในองค์กร)");
    expect(lines()[1]).toContain("คำอธิบายรอบนี้ร่างโดยผู้ช่วย AI ขององค์กร (ในองค์กร)");
    expect(lines().join(" ")).not.toContain("qwen");
    cleanup();
    useI18nStore.setState({ locale: "en" });
    render(card({ uses: "vision", choice: "auto", model: "qwen3.8-27b-fp8", self_hosted: true, written_by_rules: false }));
    expect(lines()[1]).toContain("commentary was drafted by the organisation's AI assistant (in the organisation)");
    expect(lines().join(" ")).not.toContain("qwen");
    expect(lines().join(" ")).not.toMatch(/[฀-๿]/);
  });

  it("the rules wrote: said so, and never that a model wrote", () => {
    useI18nStore.setState({ locale: "th" });
    render(card({ uses: "vision", choice: "auto", model: "", written_by_rules: true }));
    expect(lines()[1]).toContain("เรียบเรียงจากกฎของระบบ");
    expect(lines().join(" ")).not.toContain("ร่างโดย");
  });

  it("a round from before AM22 (no word on who wrote) says only who read the scans", () => {
    useI18nStore.setState({ locale: "th" });
    render(card({ uses: "vision", choice: "auto", model: "" }));
    expect(lines()).toHaveLength(1);
  });
});

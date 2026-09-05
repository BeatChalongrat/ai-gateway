// FIX62 — the composer's image-intent detector is chat-edge's list,
// verbatim; these pin the phrases the owner's screen used and the
// false-positive guards the Go side documents.
import { describe, expect, it } from "vitest";
import { detectImageIntent } from "./imageIntent";

describe("detectImageIntent", () => {
  it("catches the Thai request from the owner's screen", () => {
    expect(detectImageIntent("ทำรูปแมว")).toBe(true);
    expect(detectImageIntent("ช่วยวาดแมวให้หน่อย")).toBe(true);
    expect(detectImageIntent("สร้างภาพโลโก้บริษัท")).toBe(true);
  });

  it("catches explicit English generation phrases, case-insensitively", () => {
    expect(detectImageIntent("Generate an image of a cat")).toBe(true);
    expect(detectImageIntent("DRAW ME a castle")).toBe(true);
    expect(detectImageIntent("please make a picture of the sea")).toBe(true);
  });

  it("leaves ordinary sentences alone (draw a conclusion, create a plan)", () => {
    expect(detectImageIntent("draw a conclusion from these numbers")).toBe(false);
    expect(detectImageIntent("create a plan for the sprint")).toBe(false);
    expect(detectImageIntent("คุณคือใคร")).toBe(false);
    expect(detectImageIntent("ไง")).toBe(false);
    expect(detectImageIntent("")).toBe(false);
  });
});

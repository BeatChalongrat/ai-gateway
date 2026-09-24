// FIX132 F20 — search snippets showed raw markdown: NIGHT3 j2-06 read
// "**ศาลเจ้าฟูชิมิอินาริ** — เปิด 24 ชั่วโมง …". A snippet is plain text.
import { describe, expect, it } from "vitest";

import { snippetText } from "./snippetText";

describe("snippetText — markdown to one plain line", () => {
  it("drops emphasis, code, headings, list and quote markers; keeps link text", () => {
    expect(snippetText("**ศาลเจ้าฟูชิมิอินาริ** — เปิด 24 ชั่วโมง")).toBe("ศาลเจ้าฟูชิมิอินาริ — เปิด 24 ชั่วโมง");
    expect(snippetText("## หัวข้อ\n- ข้อหนึ่ง\n- *ข้อสอง*\n> อ้างอิง")).toBe("หัวข้อ ข้อหนึ่ง ข้อสอง อ้างอิง");
    expect(snippetText("ใช้ `npm ci` แล้วดู [คู่มือ](https://example.com/a_b) ![รูป](x.png)")).toBe("ใช้ npm ci แล้วดู คู่มือ รูป");
    expect(snippetText("```python\nprint('x')\n```\nจบ")).toBe("print('x') จบ");
    expect(snippetText("| a | b |\n|---|---|\n| 1 | 2 |")).toBe("a b 1 2");
  });
  it("keeps ordinary punctuation and snake_case words", () => {
    expect(snippetText("ราคา 5*3 = 15 และ file_name_v2")).toBe("ราคา 5*3 = 15 และ file_name_v2");
  });
  it("caps the length with an ellipsis", () => {
    const s = snippetText("ก".repeat(200), 140);
    expect(s).toHaveLength(141);
    expect(s.endsWith("…")).toBe(true);
  });
});

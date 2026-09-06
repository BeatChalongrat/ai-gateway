// FIX77 ② — toolSubject: the ONE argument a transcript tool row may show
// next to its friendly label. summarizeArgs (the raw dump) stays for the
// consent card and the expanded detail; the row itself gets a filename, a
// host or a search term — or nothing. These tests go red if toolSubject
// starts dumping arguments again (the complaint this round fixes).
import { describe, expect, it } from "vitest";
import { toolSubject } from "../src/main/permissions";

describe("toolSubject", () => {
  it("file tools show the basename, not the full path", () => {
    expect(toolSubject("read_document", { path: "C:\\Users\\x\\Desktop\\งบการเงิน 2569.xlsx" })).toBe("งบการเงิน 2569.xlsx");
    expect(toolSubject("read_file", { path: "/home/x/notes/todo.md" })).toBe("todo.md");
    expect(toolSubject("write_file", { path: "C:\\out\\report.docx", content: "..." })).toBe("report.docx");
    expect(toolSubject("list_directory", { path: "C:\\Users\\x\\Documents\\" })).toBe("Documents");
    expect(toolSubject("move_file", { path: "a.txt", destination: "C:\\dst\\b.txt" })).toBe("b.txt");
  });

  it("url tools show only the host", () => {
    expect(toolSubject("browser_navigate", { url: "https://www.youtube.com/results?search_query=secret" })).toBe("www.youtube.com");
    expect(toolSubject("open_url", { url: "http://localhost:3002/c/abc" })).toBe("localhost:3002");
    expect(toolSubject("web_fetch", { url: "not a url" })).toBeNull();
  });

  it("search tools show the query", () => {
    expect(toolSubject("web_search", { query: "ข่าว Fed วันนี้" })).toBe("ข่าว Fed วันนี้");
    expect(toolSubject("search_past_conversations", { query: "excel plan", scope: "all" })).toBe("excel plan");
    expect(toolSubject("browser_find", { query: "ราคา" })).toBe("ราคา");
  });

  it("run_command shows the first line of the command, cut at a word", () => {
    expect(toolSubject("run_command", { command: "Get-Date" })).toBe("Get-Date");
    const long = "Get-ChildItem -Recurse -Filter *.log | Select-String -Pattern error -CaseSensitive";
    const s = toolSubject("run_command", { command: long })!;
    expect(s.length).toBeLessThanOrEqual(61);
    expect(s.endsWith("…")).toBe(true);
    // never cut mid-word: the char before the ellipsis ends a whole token
    expect(long).toContain(s.slice(0, -1).trim());
    expect(long.charAt(s.length - 1)).toBe(" ");
  });

  it("code, typed text, questions and unknown MCP tools get NO subject — and never a dump", () => {
    expect(toolSubject("run_python", { code: "print('x')" })).toBeNull();
    expect(toolSubject("browser_type", { text: "4111 1111 1111 1111" })).toBeNull();
    expect(toolSubject("browser_click", { x: 10, y: 20 })).toBeNull();
    expect(toolSubject("ask_user_question", { questions: [{ question: "?" }] })).toBeNull();
    expect(toolSubject("some_mcp_tool", { payload: { a: 1 } })).toBeNull();
    expect(toolSubject("remember", { note: "private" })).toBeNull();
  });

  it("missing or empty args mean no subject, not an empty chip", () => {
    expect(toolSubject("read_file", {})).toBeNull();
    expect(toolSubject("web_search", { query: "   " })).toBeNull();
    expect(toolSubject("run_command", {})).toBeNull();
  });
});

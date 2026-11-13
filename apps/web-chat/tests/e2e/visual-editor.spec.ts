import { expect, test } from "@playwright/test";
import { installScrollFixture } from "./scroll-fixture";

test("visual attachment editor saves typed text, formatting and revision in the real browser", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1000 });
  await installScrollFixture(page, 1);
  const conversationId = "00000000-0000-4000-8000-000000000077";
  const file = { id: "visual-source", filename: "report.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", file_size_bytes: 15000, extraction_status: "completed", version: 1, created_at: "2026-11-13T00:00:00Z" };
  const revision = { ...file, id: "visual-revision", version: 2, parent_file_id: file.id };
  let saved: Record<string, unknown> | undefined;
  const calls: string[] = [];
  await page.route("**/api/v1/**", async route => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    const headers = { "access-control-allow-origin": req.headers().origin || "*", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type, authorization, x-user-id, x-csrf-token", "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS" };
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    let body: unknown;
    if (path.endsWith("/draft")) body = { draft: req.method() === "PUT" ? { ...req.postDataJSON(), revision: 1 } : null };
    else if (path.endsWith("/work")) body = { work: null };
    else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/files")) body = { files: [file] };
    else if (path.endsWith(`/conversations/${conversationId}`)) body = { id: conversationId, title: "Visual editor review", model_id: "scroll-model", message_count: 1, created_at: file.created_at, updated_at: file.created_at };
    else if (path.endsWith("/messages")) body = { messages: [{ id: "visual-message", role: "assistant", content: "Office result", status: "completed", created_at: file.created_at, files: [file] }], has_more: false };
    else if (path.endsWith("/preview-data")) return route.fulfill({ status: 415, headers, body: "unsupported" });
    else if (path.endsWith("/text")) body = { text: "Original report", extraction_status: "completed" };
    else if (path.endsWith("/editor")) {
      const request = req.postDataJSON(); calls.push(request.mode);
      if (request.mode === "history") body = { versions: saved ? [file, revision] : [file] };
      else if (request.mode === "visual_inspect") body = { visual: { kind: "docx", sha256: "source-sha", width: 595, height: 842, page_count: 1, page: 0, margins: { left: 42, top: 42, right: 42, bottom: 42 }, fields: [{ id: "body/p/0", value: saved ? "Edited report รายงาน" : "Original report", style: { size: saved ? 22 : 14, bold: !!saved } }], blocks: [{ kind: "paragraph", id: "body/p/0" }], ai_available: true } };
      else if (request.mode === "visual_edit") { saved = request; body = { file: revision }; }
      else body = {};
    } else return route.fallback();
    return route.fulfill({ status: 200, headers, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto(`/chat/${conversationId}`);
  await page.getByTitle("Open · report.docx").click();

  const editor = page.getByRole("textbox", { name: "body/p/0" });
  await expect(editor).toHaveText("Original report");
  await expect(page.getByText("Draft saved", { exact: true })).toBeVisible();
  await editor.fill("Edited report รายงาน");
  await page.getByRole("button", { name: "Bold", exact: true }).click();
  await page.getByLabel("Font size", { exact: true }).fill("22");
  await expect(page.getByRole("button", { name: "AI inspect and repair", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Save new version", exact: true }).click();
  await expect.poll(() => saved).toBeTruthy();
  expect(saved).toMatchObject({ mode: "visual_edit", sha256: "source-sha", changes: [{ id: "body/p/0", value: "Edited report รายงาน", style: { size: 22, bold: true } }] });
  await expect(editor).toHaveText("Edited report รายงาน");
  await expect(editor).toHaveCSS("font-size", "22px");
  expect(calls).not.toContain("ai_repair");
  await page.screenshot({ path: "test-results/visual-editor.png", fullPage: true });
});

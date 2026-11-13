import { expect, test } from "@playwright/test";
import { installScrollFixture } from "./scroll-fixture";

test("mock workspace recovers drafts, reviews selected changes, searches library and creates templates", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1000 }); await installScrollFixture(page, 1);
  const conversation = "00000000-0000-4000-8000-000000000077";
  const file = { id: "workspace-source", filename: "workspace.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", file_size_bytes: 1234, extraction_status: "completed", created_at: "2026-11-13T00:00:00Z", version: 1 };
  const revision = { ...file, id: "workspace-revision", parent_file_id: file.id, version: 2 };
  const starter = { ...file, id: "workspace-starter", filename: "My report.docx" };
  let draft: unknown = null; let accepted = false; let proposed = false; let acceptIds: string[] = []; let template: Record<string, unknown> | undefined;
  const requests: string[] = [];
  const fields = ["a", "b"].map(id => ({ id, value: `Original ${id}`, style: { size: 14 } }));
  const work = { id: "workspace-job", file_id: file.id, status: "ready", stage: "ready", updated_at: file.created_at, changes: fields.map(f => ({ id: f.id, before: f, after: { ...f, style: { size: 18 } } })) };
  await page.route("**/api/v1/**", async route => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    const headers = { "access-control-allow-origin": req.headers().origin || "*", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type, authorization, x-user-id, x-csrf-token", "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS" };
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    let body: unknown;
    if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/draft")) { if (req.method() === "PUT") draft = { ...req.postDataJSON(), revision: 1, updated_at: new Date().toISOString() }; if (req.method() === "DELETE") draft = null; body = { draft }; }
    else if (path.endsWith("/work")) { if (req.method() === "POST") { proposed = true; requests.push(req.postDataJSON().action); } body = { work: proposed && path.includes(file.id) && !accepted ? work : null }; }
    else if (path.endsWith("/file-work/workspace-job")) { const data = req.postDataJSON(); expect(data.action).toBe("accept"); accepted = true; acceptIds = data.ids; body = { file: revision }; }
    else if (path.endsWith("/files/library")) { requests.push(`search:${url.searchParams.get("q")}`); body = { items: [{ file: accepted ? revision : file, conversation_id: conversation }], has_more: false }; }
    else if (path.endsWith("/templates")) { template = req.postDataJSON(); body = { file: starter }; }
    else if (path.endsWith("/files")) body = { files: [file, ...(accepted ? [revision] : [])] };
    else if (path.endsWith(`/conversations/${conversation}`)) body = { id: conversation, title: "Workspace mock", model_id: "scroll-model", message_count: 1, created_at: file.created_at, updated_at: file.created_at };
    else if (path.endsWith("/messages")) body = { messages: [{ id: "workspace-message", role: "assistant", content: "Your report", status: "completed", created_at: file.created_at, files: [file] }], has_more: false };
    else if (path.endsWith("/editor")) {
      const input = req.postDataJSON();
      if (input.mode === "history") body = { versions: path.includes(starter.id) ? [starter] : accepted ? [file, revision] : [file] };
      else if (input.mode === "visual_inspect") body = { visual: { kind: "docx", sha256: "hash", width: 595, height: 842, page_count: 1, page: 0, fields, blocks: fields.map(f => ({ kind: "paragraph", id: f.id })), ai_available: true } };
      else body = {};
    } else return route.fallback();
    return route.fulfill({ status: 200, headers, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto(`/chat/${conversation}`); await page.getByTitle("Open · workspace.docx").click();
  await expect(page.getByText("Draft saved", { exact: true })).toBeVisible();
  const field = page.getByRole("textbox", { name: "a", exact: true }); await field.fill("Recovered draft");
  await expect.poll(() => draft).toBeTruthy();
  await page.getByRole("button", { name: "Undo", exact: true }).click(); await expect(field).toHaveText("Original a");
  await page.getByRole("button", { name: "Redo", exact: true }).click(); await expect(field).toHaveText("Recovered draft");
  await expect(page.getByText("Draft saved", { exact: true })).toBeVisible();
  await page.reload(); await page.getByTitle("Open · workspace.docx").click(); await expect(field).toHaveText("Recovered draft");
  await page.getByRole("button", { name: "Discard changes", exact: true }).click(); await expect(field).toHaveText("Original a");
  await page.getByRole("button", { name: "AI inspect and repair", exact: true }).click();
  await expect(page.getByText("Ready for review", { exact: true })).toBeVisible(); expect(accepted).toBe(false);
  await page.getByRole("checkbox").nth(1).uncheck(); await page.getByRole("button", { name: "Accept 1 changes", exact: true }).click();
  await expect.poll(() => acceptIds).toEqual(["a"]);
  await page.getByRole("button", { name: "Artifacts", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Artifacts", exact: true }); await expect(dialog).toBeVisible();
  await dialog.getByRole("textbox", { name: "Search filenames" }).fill("workspace"); await expect.poll(() => requests.includes("search:workspace")).toBe(true);
  await dialog.getByRole("button", { name: "Templates", exact: true }).click();
  await expect(dialog.locator("img")).toHaveCount(12);
  await expect.poll(() => dialog.locator("img").first().evaluate(img => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.screenshot({ path: "test-results/template-gallery.png", fullPage: true });
  await dialog.getByRole("button", { name: /Business report.*docx/ }).click();
  await dialog.getByLabel("Document title", { exact: true }).fill("My report"); await dialog.getByLabel("Executive summary", { exact: true }).fill("Only supplied facts.");
  await dialog.getByRole("button", { name: "Create and open", exact: true }).click();
  await expect.poll(() => template).toMatchObject({ template: "docx.business_report", data: { title: "My report", sections: [{ heading: "Executive summary", body: "Only supplied facts." }] } });
  await expect(dialog).not.toBeVisible(); await expect(page.getByRole("navigation", { name: "Open artifacts" })).toContainText("My report.docx");
  await page.screenshot({ path: "test-results/artifact-workspace.png", fullPage: true });
});

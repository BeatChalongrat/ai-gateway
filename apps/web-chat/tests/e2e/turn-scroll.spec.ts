import { expect, test, type Page } from "@playwright/test";
import { completeScrollReply, emitScrollEvent, installScrollFixture, scrollSnapshot } from "./scroll-fixture";

async function openThread(page: Page, height = 900, count = 8) {
  await page.setViewportSize({ width: 1280, height });
  await installScrollFixture(page, count);
  await page.goto("/chat/scroll-test");
  await expect(page.locator("textarea")).toBeVisible();
  if (count) await expect(page.locator(`[data-msg-index="${count - 1}"]`)).toBeAttached();
}

async function send(page: Page, prompt = "A short reply, please.") {
  const before = await page.evaluate(() => (window as unknown as { __scrollFixture: { streams: unknown[] } }).__scrollFixture.streams.length);
  await page.locator("textarea").fill(prompt);
  await page.locator("textarea").press("Enter");
  await expect.poll(() => page.evaluate(() => (window as unknown as { __scrollFixture: { streams: unknown[] } }).__scrollFixture.streams.length)).toBe(before + 1);
  await expect(page.getByRole("button", { name: "Stop generation", exact: true })).toBeVisible();
}

async function delta(page: Page, text: string) {
  await emitScrollEvent(page, "content_delta", { delta: text });
  await expect(page.locator('[data-msg-index]').last()).toContainText(text.trim().split("\n").at(-1)!.slice(-30));
}

async function terminalAssertions(page: Page) {
  await expect(page.getByRole("button", { name: "Stop generation", exact: true })).toHaveCount(0);
  // Tail visibility, reservation, and affordance are independent assertions.
  await expect.poll(async () => (await page.evaluate(scrollSnapshot)).spacerRendered).toBe(0);
  await expect.poll(async () => {
    const s = await page.evaluate(scrollSnapshot); return s.lastBottom - s.H;
  }).toBeLessThanOrEqual(1);
  await expect.poll(async () => {
    const s = await page.evaluate(scrollSnapshot); return s.plainArrow || s.newPill;
  }).toBe(false);
  const s = await page.evaluate(scrollSnapshot);
  expect(s.spacerStyle).toBe(0);
  await expect.poll(async () => {
    const current = await page.evaluate(scrollSnapshot);
    return Math.abs(current.T - Math.max(0, current.C - current.H));
  }).toBeLessThanOrEqual(1);
}

async function wheel(page: Page, dy: number) {
  const box = await page.locator('[data-testid="turn-spacer"]').evaluate((sp) => {
    const r = sp.parentElement!.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + 100 };
  });
  await page.mouse.move(box.x, box.y);
  const before = await page.evaluate(scrollSnapshot);
  await page.mouse.wheel(0, dy);
  await expect.poll(async () => Math.abs((await page.evaluate(scrollSnapshot)).T - before.T)).toBeGreaterThan(1);
}

for (const height of [936, 836, 900, 800, 640]) {
  test(`short reply repays its reservation at window height ${height}`, async ({ page }) => {
    await openThread(page, height);
    await send(page);
    await delta(page, "A short answer that is complete.");
    await expect.poll(async () => {
      const s = await page.evaluate(scrollSnapshot); return Math.abs(s.T - Number(s.A));
    }).toBeLessThanOrEqual(1);
    expect((await page.evaluate(scrollSnapshot)).spacerRendered).toBeGreaterThan(0);
    await completeScrollReply(page);
    await terminalAssertions(page);
  });
}

test("a plain arrow from earlier reading expires after sending", async ({ page }) => {
  await openThread(page);
  await wheel(page, -500);
  await expect(page.getByRole("button", { name: "Scroll to latest", exact: true })).toBeVisible();
  await send(page);
  await delta(page, "The whole answer is visible.");
  await completeScrollReply(page);
  await terminalAssertions(page);
});

test("completion preserves a reader 40 px above the natural bottom", async ({ page }) => {
  await openThread(page);
  await send(page);
  await delta(page, "A longer answer with several paragraphs.\n\n".repeat(70));
  await expect.poll(async () => (await page.evaluate(scrollSnapshot)).spacerRendered).toBe(0);
  // Keep this case's terminal row size stable; shrink/clamp has its own case.
  await page.locator('[data-msg-index]').last().evaluate((row) => {
    (row as HTMLElement).style.minHeight = `${row.getBoundingClientRect().height}px`;
  });
  await wheel(page, -40);
  const reading = await page.evaluate(scrollSnapshot);
  await completeScrollReply(page);
  await expect(page.getByRole("button", { name: "Stop generation", exact: true })).toHaveCount(0);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const done = await page.evaluate(scrollSnapshot);
  // Reader preservation is the fourth assertion, independent of tail visibility.
  expect(Math.abs(done.T - reading.T)).toBeLessThanOrEqual(1);
  expect(done.spacerRendered).toBe(0);
  expect(done.plainArrow || done.newPill).toBe(done.lastBottom > done.H + 1);
  expect(done.newPill).toBe(false);
});

test("an unreachable reader offset clamps only as far as the new natural bottom", async ({ page }) => {
  await openThread(page);
  await send(page);
  await delta(page, "A longer answer with several paragraphs.\n\n".repeat(70));
  await expect.poll(async () => (await page.evaluate(scrollSnapshot)).follow).toBe(true);
  await wheel(page, -40);
  const reading = await page.evaluate(scrollSnapshot);
  await completeScrollReply(page);
  await expect(page.getByRole("button", { name: "Stop generation", exact: true })).toHaveCount(0);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const done = await page.evaluate(scrollSnapshot);
  expect(Math.abs(done.T - Math.min(reading.T, Math.max(0, done.C - done.H)))).toBeLessThanOrEqual(1);
  expect(done.follow).toBe(false);
  expect(done.spacerRendered).toBe(0);
  await page.mouse.wheel(0, 40);
  await expect.poll(async () => (await page.evaluate(scrollSnapshot)).follow).toBe(true);
});

for (const difference of [-2, 0, 2]) {
  test(`handover at ${difference} px from the hold threshold`, async ({ page }) => {
    await openThread(page);
    await send(page);
    await delta(page, "The beginning.");
    await expect.poll(async () => {
      const s = await page.evaluate(scrollSnapshot); return Math.abs(s.T - Number(s.A));
    }).toBeLessThanOrEqual(1);
    const held = await page.evaluate(scrollSnapshot);
    await page.locator('[data-msg-index]').last().evaluate((row, target) => {
      const height = row.getBoundingClientRect().height;
      (row as HTMLElement).style.height = `${height + target}px`;
      (row as HTMLElement).style.overflow = "hidden";
    }, Number(held.A) + held.H - held.C + difference);
    await expect.poll(async () => (await page.evaluate(scrollSnapshot)).anchorActive).toBe(difference < -1);
    const current = await page.evaluate(scrollSnapshot);
    expect(current.spacerRendered).toBe(difference < -1 ? 2 : 0);
    if (difference >= -1) {
      await page.locator('[data-msg-index]').last().evaluate((row) => { (row as HTMLElement).style.height = "20px"; });
      await expect.poll(async () => (await page.evaluate(scrollSnapshot)).spacerRendered).toBe(0);
      expect((await page.evaluate(scrollSnapshot)).anchorActive).toBe(false);
    }
    await completeScrollReply(page);
    await terminalAssertions(page);
  });
}

test("regenerating a completed short reply preserves following without another send anchor", async ({ page }) => {
  await openThread(page);
  await send(page);
  await delta(page, "The first answer.");
  await completeScrollReply(page);
  await terminalAssertions(page);
  await page.locator('[data-msg-index]').last().hover();
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __scrollFixture: { streams: unknown[] } }).__scrollFixture.streams.length)).toBe(2);
  await delta(page, "A fresh answer.\n\n".repeat(70));
  expect((await page.evaluate(scrollSnapshot)).anchorActive).toBe(false);
  await completeScrollReply(page);
  await terminalAssertions(page);
});

test("edit-and-resend anchors even though the edited user's key is preserved", async ({ page }) => {
  await openThread(page);
  await send(page);
  await delta(page, "First answer.");
  await completeScrollReply(page);
  await terminalAssertions(page);
  const user = page.locator('[data-msg-index="8"]');
  await user.hover();
  await user.getByRole("button", { name: "Edit message", exact: true }).click();
  await user.locator("textarea").fill("An edited question");
  await user.locator("textarea").press("Control+Enter");
  await expect.poll(() => page.evaluate(() => (window as unknown as { __scrollFixture: { streams: unknown[] } }).__scrollFixture.streams.length)).toBe(2);
  await delta(page, "Edited answer.");
  await expect.poll(async () => (await page.evaluate(scrollSnapshot)).anchorActive).toBe(true);
  await completeScrollReply(page);
  await terminalAssertions(page);
});

test("scrolling away while holding repays immediately and unseen deltas raise the new-content pill", async ({ page }) => {
  await openThread(page);
  await send(page);
  await delta(page, "The opening of this answer.");
  await expect.poll(async () => Math.abs((await page.evaluate(scrollSnapshot)).T - Number((await page.evaluate(scrollSnapshot)).A))).toBeLessThanOrEqual(1);
  await wheel(page, -550);
  const reading = await page.evaluate(scrollSnapshot);
  expect(reading.spacerRendered).toBe(0);
  await delta(page, " More actual new content below the reader.".repeat(100));
  await expect.poll(async () => (await page.evaluate(scrollSnapshot)).newPill).toBe(true);
  await completeScrollReply(page);
  await expect.poll(async () => (await page.evaluate(scrollSnapshot)).spacerRendered).toBe(0);
  expect(Math.abs((await page.evaluate(scrollSnapshot)).T - reading.T)).toBeLessThanOrEqual(1);
  await page.getByRole("button", { name: "New messages", exact: true }).click();
  await terminalAssertions(page);
});

test("first-turn underflow uses natural extent and has no terminal scroll range", async ({ page }) => {
  await openThread(page, 936, 0);
  await send(page);
  await delta(page, "Short.");
  await expect.poll(async () => Math.abs((await page.evaluate(scrollSnapshot)).T - Number((await page.evaluate(scrollSnapshot)).A))).toBeLessThanOrEqual(1);
  await completeScrollReply(page);
  await terminalAssertions(page);
  expect((await page.evaluate(scrollSnapshot)).T).toBe(0);
});

test("an immediate terminal reply and an error before tokens both release the hold", async ({ page }) => {
  await openThread(page);
  await send(page);
  await completeScrollReply(page);
  await terminalAssertions(page);
  await send(page, "Another prompt");
  await emitScrollEvent(page, "error", { code: "provider_error", message: "Fixture stopped" });
  await completeScrollReply(page);
  await terminalAssertions(page);
});

test("Stop before the first token releases the reservation without a later event", async ({ page }) => {
  await openThread(page);
  await send(page);
  await expect.poll(async () => (await page.evaluate(scrollSnapshot)).spacerRendered).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Stop generation", exact: true }).click();
  await terminalAssertions(page);
});

test("viewport resize, late growth, and collapse keep completed turns spacer-free", async ({ page }) => {
  await openThread(page);
  await send(page);
  await delta(page, "A short answer.");
  await page.setViewportSize({ width: 1280, height: 1000 });
  await expect.poll(async () => {
    const s = await page.evaluate(scrollSnapshot); return Math.abs(s.spacerRendered - Math.max(0, Number(s.A) + s.H - s.C));
  }).toBeLessThanOrEqual(1);
  await completeScrollReply(page);
  await terminalAssertions(page);
  // Deliberately delayed geometry, independent of React or streamed text.
  await page.waitForTimeout(2100);
  await page.locator('[data-msg-index]').last().evaluate((row) => {
    const block = document.createElement("div"); block.dataset.lateBlock = "true";
    block.style.height = "800px"; block.textContent = "Late rendered content"; row.append(block);
  });
  await terminalAssertions(page);
  await page.locator('[data-late-block]').evaluate((block) => { (block as HTMLElement).style.height = "0px"; block.textContent = ""; });
  await terminalAssertions(page);
  await page.setViewportSize({ width: 850, height: 700 });
  await terminalAssertions(page);
});

test("crossing the virtualization boundary still releases the terminal spacer", async ({ page }) => {
  await openThread(page, 900, 48);
  await send(page);
  await delta(page, "A complete answer in the virtualized thread.");
  await completeScrollReply(page);
  await terminalAssertions(page);
});

test("growth above a reader preserves a visible row instead of its old numeric scrollTop", async ({ page }) => {
  await openThread(page);
  await wheel(page, -300);
  const before = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="turn-spacer"]')!.parentElement!;
    const row = [...el.querySelectorAll<HTMLElement>('[data-msg-index]')].find((r) => r.getBoundingClientRect().bottom > el.getBoundingClientRect().top + 1)!;
    return { index: row.dataset.msgIndex!, top: row.getBoundingClientRect().top };
  });
  await page.locator('[data-msg-index="0"]').evaluate((row) => { (row as HTMLElement).style.paddingTop = "180px"; });
  await expect.poll(async () => page.locator(`[data-msg-index="${before.index}"]`).evaluate((row) => row.getBoundingClientRect().top)).toBeCloseTo(before.top, 0);
});

test("loading older history across virtualization preserves the row and a plain arrow", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await installScrollFixture(page, 48, "http://localhost:3102", 20);
  await page.goto("/chat/scroll-test");
  await expect(page.locator('[data-msg-index="47"]')).toBeAttached();
  await wheel(page, -20000);
  const row = page.locator('[data-msg-index="0"]');
  await expect(row).toBeVisible();
  const before = await row.evaluate((r) => r.getBoundingClientRect().top);
  await page.getByRole("button", { name: "Load earlier messages", exact: true }).click();
  await expect(page.locator('[data-msg-index="20"]')).toContainText("Previous question 0");
  await expect.poll(async () => Math.abs(await page.locator('[data-msg-index="20"]').evaluate((r) => r.getBoundingClientRect().top) - before)).toBeLessThanOrEqual(1);
  const current = await page.evaluate(scrollSnapshot);
  expect(current.newPill).toBe(false);
  expect(current.plainArrow).toBe(true);
  expect(current.spacerRendered).toBe(0);
});

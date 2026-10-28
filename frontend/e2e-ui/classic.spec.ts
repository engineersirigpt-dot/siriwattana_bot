// The classic chat page — the rollback target of V5 — against the mock API.
// Covers what the old tests/ specs checked (those need a live backend and
// OpenAI): login guard, ask → streamed answer, new chat, delete a chat.
import { expect, test } from "@playwright/test";
import { isMobile, resetMock, signIn } from "./helpers";

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(isMobile(testInfo), "the classic page is the desktop rollback");
  await resetMock(page, { ui: { 1: false } });
});

test("no login → the login page", async ({ page }) => {
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login/);
});

test("classic: ask and get a streamed answer", async ({ page }) => {
  await signIn(page);
  await page.goto("/chat");
  await page.getByPlaceholder("พิมพ์คำถาม…").fill("บริษัทเปิดกี่โมง");
  await page.getByRole("button", { name: "ส่ง" }).click();
  await expect(page.locator("strong", { hasText: "ตัวหนา" })).toBeVisible();
  // the `session` event the backend now sends doesn't upset the classic page
  await expect(page.getByText("บริษัทเปิดกี่โมง").first()).toBeVisible();
});

test("classic: new chat clears the screen", async ({ page }) => {
  await signIn(page);
  await page.goto("/chat");
  await page.getByText("รายงานยอดขายไตรมาส 1").first().click();
  await expect(page.getByText("สรุปยอดขายไตรมาส 1/2569").first()).toBeVisible();
  await page.getByRole("button", { name: "แชทใหม่" }).first().click();
  await expect(page.getByText("พิมพ์คำถามเกี่ยวกับบริษัทหรือคำถามทั่วไปได้เลย")).toBeVisible();
});

test("classic: delete a chat from the sidebar", async ({ page }) => {
  await signIn(page);
  await page.goto("/chat");
  const row = page.locator("div.group", { hasText: "ร่างอีเมลแจ้งลูกค้า 2" }).first();
  await row.hover();
  await row.locator('button[title="ลบ"]').click();
  await expect(page.getByText("ลบบทสนทนานี้?")).toBeVisible();
  await page.getByRole("button", { name: "ลบ", exact: true }).last().click();
  await expect(page.getByText("ร่างอีเมลแจ้งลูกค้า 2", { exact: true })).toHaveCount(0);
});

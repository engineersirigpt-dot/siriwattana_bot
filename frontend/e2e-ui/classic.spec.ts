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

test("classic: switching chats mid-answer doesn't write it into the chat opened", async ({ page }) => {
  await signIn(page);
  await page.goto("/chat");
  await page.getByPlaceholder("พิมพ์คำถาม…").fill("ตอบช้า ๆ ยาว ๆ");
  await page.locator("form").getByRole("button", { name: "ส่ง" }).click();
  await expect(page.getByText(/คำตอบยาว/).first()).toBeVisible();
  await page.getByText("ขั้นตอนเบิกค่าเดินทาง 1", { exact: true }).first().click();
  await expect(page.getByText(/ทองคำแท่งขายออก/).first()).toBeVisible(); // chat 200's own answer
  const posted = page.waitForRequest((r) => r.url().endsWith("/chat/stream"));
  await page.getByPlaceholder(/พิมพ์คำถาม/).fill("สรุปแชทนี้ให้ 3 บรรทัด");
  await page.locator("form").getByRole("button", { name: "ส่ง" }).click();
  expect((await posted).postData() ?? "").toMatch(/name="session_id"\r\n\r\n200\r\n/);
  await page.waitForTimeout(1500);
  await expect(page.locator("main")).not.toContainText("คำตอบยาว");
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

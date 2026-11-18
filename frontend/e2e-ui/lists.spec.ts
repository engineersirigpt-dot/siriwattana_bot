// Answer lists look like ChatGPT's: each nesting level has its own marker
// (• ◦ ▪ for bullets, 1. a. for numbers) in the text colour — on the V5 page
// and on the classic page.
import { expect, test, type Locator } from "@playwright/test";
import { isMobile, resetMock, signIn } from "./helpers";

const listStyle = (l: Locator) => l.evaluate((el) => getComputedStyle(el).listStyleType);

async function expectLevels(answer: Locator) {
  const top = answer.locator(":scope ul").first();
  await expect.poll(() => listStyle(top)).toBe("disc");
  await expect.poll(() => listStyle(top.locator(":scope > li > ul").first())).toBe("circle");
  await expect.poll(() => listStyle(top.locator(":scope > li > ul > li > ul").first())).toBe("square");
  const ol = answer.locator(":scope ol").first();
  await expect.poll(() => listStyle(ol)).toBe("decimal");
  await expect.poll(() => listStyle(ol.locator(":scope > li > ul").first())).toBe("disc");
  await expect.poll(() => listStyle(ol.locator(":scope > li > ol").first())).toBe("lower-alpha");
}

test("V5: nested lists get their own markers", async ({ page }, testInfo) => {
  await resetMock(page);
  await signIn(page);
  await page.goto("/chat");
  await page.getByLabel("ข้อความ").fill("ขอคำตอบแบบรายการซ้อน");
  await page.getByTestId("send").click();
  const bot = page.getByTestId("msg-bot").last();
  await expect(bot).toHaveAttribute("data-streaming", "0");
  await expectLevels(bot.getByTestId("md"));
  if (process.env.SHOTS_DIR && !isMobile(testInfo)) {
    await bot.screenshot({ path: `${process.env.SHOTS_DIR}/lists-v5-light.png` });
  }
});

test("V5 dark: markers stay readable", async ({ page }, testInfo) => {
  test.skip(!process.env.SHOTS_DIR || isMobile(testInfo), "picture only");
  await resetMock(page);
  await signIn(page, "admin", undefined, "dark");
  await page.goto("/chat");
  await page.getByLabel("ข้อความ").fill("ขอคำตอบแบบรายการซ้อน");
  await page.getByTestId("send").click();
  const bot = page.getByTestId("msg-bot").last();
  await expect(bot).toHaveAttribute("data-streaming", "0");
  await bot.screenshot({ path: `${process.env.SHOTS_DIR}/lists-v5-dark.png` });
});

test("classic: nested lists get their own markers", async ({ page }, testInfo) => {
  test.skip(isMobile(testInfo), "the classic page is the desktop rollback");
  await resetMock(page, { ui: { 1: false } });
  await signIn(page);
  await page.goto("/chat");
  await page.getByPlaceholder("พิมพ์คำถาม…").fill("ขอคำตอบแบบรายการซ้อน");
  await page.getByRole("button", { name: "ส่ง" }).click();
  const answer = page.locator(".md-lists").last();
  await expect(answer.locator("ol")).toBeVisible();
  await expectLevels(answer);
});

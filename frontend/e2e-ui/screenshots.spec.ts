// Before (classic) / after (V5) pictures of the same chat, for review.
//   SHOTS_DIR=<folder> npx playwright test -c e2e-ui screenshots
import { expect, test } from "@playwright/test";
import path from "node:path";
import { isMobile, openSession, resetMock, signIn } from "./helpers";

const DIR = process.env.SHOTS_DIR;
test.skip(!DIR, "set SHOTS_DIR to take the pictures");

// keep the next-dev "N" badge out of the pictures (it isn't in production)
test.beforeEach(async ({ page }) => {
  page.on("load", () => page.addStyleTag({ content: "nextjs-portal{display:none!important}" }).catch(() => {}));
});

for (const ui of ["classic", "v5"] as const) {
  test(`chat — ${ui}`, async ({ page }, testInfo) => {
    await resetMock(page, { ui: { 1: ui === "v5" } });
    await signIn(page);
    await page.goto("/chat");
    const name = `${testInfo.project.name}-${ui}`;
    if (ui === "v5") {
      await expect(page.locator('[data-ui="v5"]')).toBeVisible();
      await openSession(page, testInfo, "รายงานยอดขายไตรมาส 1");
      await expect(page.getByTestId("file-card").first()).toBeVisible();
    } else {
      await expect(page.getByPlaceholder("พิมพ์คำถาม…")).toBeVisible();
      await page.getByText("รายงานยอดขายไตรมาส 1").first().click();
      await expect(page.getByText("สรุปยอดขายไตรมาส 1/2569").first()).toBeVisible();
    }
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(DIR!, `${name}.png`) });
    if (ui === "v5" && isMobile(testInfo)) {
      await page.getByTestId("open-nav").click();
      await page.waitForTimeout(400);
      await page.screenshot({ path: path.join(DIR!, `${name}-menu.png`) });
    }
    if (!isMobile(testInfo)) {
      await page.setViewportSize({ width: 820, height: 1180 });
      await page.waitForTimeout(500);
      await page.screenshot({ path: path.join(DIR!, `tablet-${ui}.png`) });
    }
  });
}

test("streaming with status — v5", async ({ page }, testInfo) => {
  await resetMock(page, { ui: { 1: true } });
  await signIn(page);
  await page.goto("/chat");
  await page.getByLabel("ข้อความ").fill("ค้นเว็บ ราคาทองวันนี้ ตอบช้า ๆ");
  await page.getByTestId("send").click();
  await expect(page.getByTestId("status-label")).toHaveText("กำลังเรียบเรียงคำตอบ…");
  await page.screenshot({ path: path.join(DIR!, `${testInfo.project.name}-v5-status.png`) });
  await expect(page.getByTestId("msg-bot").last()).toContainText("คำตอบยาว");
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(DIR!, `${testInfo.project.name}-v5-streaming.png`) });
  await page.getByTestId("stop").click();
});

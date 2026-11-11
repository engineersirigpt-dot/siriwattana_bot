// QA round 2 (2026-11-11): the board's failing items, against the mock API.
//   C-02/C-03  reload mid-answer → the answer is finished (pending meanwhile), not "หยุดแล้ว"
//              หยุด → POST /chat/stop, the answer keeps its "หยุดแล้ว" note
//   O-02/F-05  errors in Thai, never raw JSON
//   A-02 demoted admin · A-03 typed text survives a 401 · A-04 SSO error → MI portal
//   F-01/F-04  HEIC explained, .doc/.ppt accepted · C-07 an over-long paste
import { expect, test, type Page } from "@playwright/test";
import { isMobile, MOCK, openSession, resetMock, signIn } from "./helpers";

async function requests(page: Page): Promise<string[]> {
  return (await page.request.get(`${MOCK}/__mock/requests`, { headers: { Authorization: "Bearer tok-admin" } })).json();
}

test.describe("V5", () => {
  test.beforeEach(async ({ page }, testInfo) => {
    test.skip(isMobile(testInfo), "desktop is enough");
    await resetMock(page);
    await signIn(page);
  });

  test("C-02 reload mid-answer → shown as pending, then the whole answer", async ({ page }, testInfo) => {
    await page.goto("/chat");
    await page.getByLabel("ข้อความ").fill("ค่อย ๆ ตอบ รีเฟรช");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("msg-bot").last()).toContainText("คำตอบยาว");
    await page.reload();
    await openSession(page, testInfo, "ค่อย ๆ ตอบ รีเฟรช");
    await expect(page.getByText("กำลังตอบต่อจากครั้งก่อน")).toBeVisible();
    await expect(page.getByTestId("stop")).toBeVisible();
    const bot = page.getByTestId("msg-bot").last();
    await expect(bot).toContainText("จบคำตอบแล้ว", { timeout: 20_000 });
    await expect(bot.getByTestId("stopped-note")).toHaveCount(0);
    await expect(page.getByTestId("send")).toBeVisible();
  });

  test("หยุด stops it for real and keeps the note", async ({ page }, testInfo) => {
    await page.goto("/chat");
    await page.getByLabel("ข้อความ").fill("ค่อย ๆ ตอบ แล้วกดหยุด");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("msg-bot").last()).toContainText("คำตอบยาว");
    await page.getByTestId("stop").click();
    await expect(page.getByTestId("msg-bot").last().getByTestId("stopped-note")).toBeVisible();
    expect(await requests(page)).toContain("POST /chat/stop");
    await page.waitForTimeout(800);
    await page.reload();
    await openSession(page, testInfo, "ค่อย ๆ ตอบ แล้วกดหยุด");
    const bot = page.getByTestId("msg-bot").last();
    await expect(bot.getByTestId("stopped-note")).toBeVisible();
    await expect(bot).not.toContainText("จบคำตอบแล้ว");
    await expect(page.getByText("กำลังตอบต่อจากครั้งก่อน")).toHaveCount(0);
  });

  test("หยุด on the pending answer after a reload", async ({ page }, testInfo) => {
    await page.goto("/chat");
    await page.getByLabel("ข้อความ").fill("ค่อย ๆ ตอบ รีเฟรชแล้วหยุด");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("msg-bot").last()).toContainText("คำตอบยาว");
    await page.reload();
    await openSession(page, testInfo, "ค่อย ๆ ตอบ รีเฟรชแล้วหยุด");
    await expect(page.getByText("กำลังตอบต่อจากครั้งก่อน")).toBeVisible();
    await page.getByTestId("stop").click();
    const bot = page.getByTestId("msg-bot").last();
    await expect(bot.getByTestId("stopped-note")).toBeVisible({ timeout: 10_000 });
    await expect(bot).not.toContainText("จบคำตอบแล้ว");
  });

  test("O-02 / F-05 errors are Thai, never raw JSON", async ({ page }) => {
    await page.goto("/chat");
    await page.route("**/chat/stream", (r) =>
      r.fulfill({ status: 429, contentType: "application/json", body: '{"detail":"Too many requests. Please try again later."}' }));
    await page.getByLabel("ข้อความ").fill("สวัสดี");
    await page.getByTestId("send").click();
    const bot = page.getByTestId("msg-bot").last();
    await expect(bot).toContainText("ส่งคำขอถี่เกินไป");
    await expect(bot).not.toContainText("{");
    await expect(bot).not.toContainText("Too many");

    await page.route("**/chat", (r) =>
      r.fulfill({ status: 400, contentType: "application/json", body: '{"detail":"ไฟล์ใหญ่เกิน 100MB"}' }));
    await page.getByTestId("file-input").setInputFiles({ name: "big.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
    await page.getByLabel("ข้อความ").fill("สรุปไฟล์นี้");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("msg-bot").last()).toContainText("⚠️ ไฟล์ใหญ่เกิน 100MB");
    await expect(page.getByTestId("msg-bot").last()).not.toContainText("detail");

    await page.route("**/chat", (r) => r.fulfill({ status: 502, contentType: "text/html", body: "<html><body>Bad Gateway</body></html>" }));
    await page.getByTestId("file-input").setInputFiles({ name: "a.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
    await page.getByLabel("ข้อความ").fill("สรุปไฟล์นี้");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("msg-bot").last()).toContainText("ระบบไม่ตอบสนองชั่วคราว");
    await expect(page.getByTestId("msg-bot").last()).not.toContainText("<html>");
  });

  test("A-03 a message being sent when the session runs out comes back after sign-in", async ({ page }) => {
    await page.goto("/chat");
    await page.route("**/chat/stream", (r) =>
      r.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"Invalid or expired token"}' }));
    const text = "ข้อความยาวที่พิมพ์ไว้ก่อน token หมดอายุ ต้องไม่หาย";
    await page.getByLabel("ข้อความ").fill(text);
    await page.getByTestId("send").click();
    await expect(page).toHaveURL(/\/login/);
    await page.unroute("**/chat/stream");
    await page.evaluate(() => {
      localStorage.setItem("chatbot_token", "tok-admin");
      localStorage.setItem("chatbot_role", "admin");
    });
    await page.goto("/chat");
    await expect(page.getByLabel("ข้อความ")).toHaveValue(text);
  });

  test("A-02 a demoted admin loses the admin menu and page", async ({ page }) => {
    await resetMock(page, { roles: { 1: "user" } });
    await page.goto("/chat");
    await expect(page.getByTestId("send")).toBeVisible();
    await expect(page.getByRole("button", { name: "Admin Dashboard" })).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem("chatbot_role"))).toBe("user");
    await page.evaluate(() => localStorage.setItem("chatbot_role", "admin")); // stale again
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/chat/);
  });

  test("F-01 / F-04 HEIC explained, .doc and .ppt accepted", async ({ page }) => {
    await page.goto("/chat");
    await page.getByTestId("file-input").setInputFiles([
      { name: "old.doc", mimeType: "application/msword", buffer: Buffer.from("doc") },
      { name: "slides.ppt", mimeType: "application/vnd.ms-powerpoint", buffer: Buffer.from("ppt") },
    ]);
    await expect(page.getByText("old.doc")).toBeVisible();
    await expect(page.getByText("slides.ppt")).toBeVisible();
    await page.getByTestId("file-input").setInputFiles({ name: "IMG_0001.HEIC", mimeType: "image/heic", buffer: Buffer.from("heic") });
    await expect(page.getByRole("dialog")).toContainText("HEIC");
    await expect(page.getByRole("dialog")).toContainText("JPG");
  });

  test("C-07 an over-long paste is refused before sending", async ({ page }) => {
    await page.goto("/chat");
    await page.getByLabel("ข้อความ").fill("ก".repeat(120_001));
    await page.getByTestId("send").click();
    await expect(page.getByRole("dialog")).toContainText("ข้อความยาวเกินไป");
    expect((await requests(page)).filter((r) => r.includes("/chat/stream"))).toEqual([]);
    await expect(page.getByLabel("ข้อความ")).not.toHaveValue(""); // kept, to cut or attach
  });
});

test.describe("classic", () => {
  test.beforeEach(async ({ page }, testInfo) => {
    test.skip(isMobile(testInfo), "the classic page is the desktop rollback");
    await resetMock(page, { ui: { 1: false } });
    await signIn(page);
  });

  test("C-02 classic: reload mid-answer → pending, then the whole answer", async ({ page }) => {
    await page.goto("/chat");
    await page.getByPlaceholder("พิมพ์คำถาม…").fill("ค่อย ๆ ตอบ หน้าเดิม");
    await page.locator("form").getByRole("button", { name: "ส่ง" }).click();
    await expect(page.getByText(/คำตอบยาว/).first()).toBeVisible();
    await page.reload();
    await page.getByText("ค่อย ๆ ตอบ หน้าเดิม", { exact: true }).first().click();
    await expect(page.getByText("กำลังตอบต่อจากครั้งก่อน")).toBeVisible();
    await expect(page.getByText(/จบคำตอบแล้ว/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/หยุดก่อนตอบจบ/)).toHaveCount(0);
  });

  test("classic: หยุด calls /chat/stop", async ({ page }) => {
    await page.goto("/chat");
    await page.getByPlaceholder("พิมพ์คำถาม…").fill("ค่อย ๆ ตอบ หยุดหน้าเดิม");
    await page.locator("form").getByRole("button", { name: "ส่ง" }).click();
    await expect(page.getByText(/คำตอบยาว/).first()).toBeVisible();
    await page.getByRole("button", { name: /หยุด/ }).first().click();
    await expect.poll(async () => (await requests(page)).includes("POST /chat/stop")).toBe(true);
  });
});

test("A-04 SSO error → back to the MI portal; Back onto /sso when signed in → the chat", async ({ page }) => {
  await resetMock(page);
  await page.goto("/sso?token=expired");
  await expect(page.getByText("เข้าสู่ระบบไม่สำเร็จ")).toBeVisible();
  await expect(page.getByTestId("sso-back")).toHaveText("กลับไปหน้า MI");
  await expect(page.getByTestId("sso-back")).toHaveAttribute("href", /\/mi-portal$/);
  await page.evaluate(() => {
    localStorage.setItem("chatbot_token", "tok-staff");
    localStorage.setItem("chatbot_role", "user");
  });
  await page.goto("/sso");
  await expect(page).toHaveURL(/\/chat/);
});

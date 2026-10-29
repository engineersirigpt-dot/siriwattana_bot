// "ออกจากระบบ": a user who came in from the MI portal (SSO) goes back to it
// (backend MI_PORTAL_URL); one who logged in on our own page goes to /login.
import { expect, test } from "@playwright/test";
import { MOCK, openNav, resetMock, signIn } from "./helpers";

const PORTAL = `${MOCK}/mi-portal`;

test("SSO user on V5 → back to the MI portal", async ({ page }, testInfo) => {
  await resetMock(page, { ui: { 2: true } });
  await page.goto("/sso?token=mi-jwt-staff");
  await expect(page.locator('[data-ui="v5"]')).toBeVisible();
  await openNav(page, testInfo);
  await page.getByRole("button", { name: "ออกจากระบบ" }).click();
  await expect(page).toHaveURL(PORTAL);
  await expect(page.getByText("MI Authentication System")).toBeVisible();
  // signed out for real: coming back means logging in again
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login/);
});

test("SSO user on the classic page → back to the MI portal", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "the classic page is the desktop rollback");
  await resetMock(page, { ui: { 2: false } });
  await page.goto("/sso?token=mi-jwt-staff");
  await expect(page.getByPlaceholder("พิมพ์คำถาม…")).toBeVisible();
  await page.getByRole("button", { name: "ออกจากระบบ" }).click();
  await expect(page).toHaveURL(PORTAL);
});

test("a user who logged in on our page → our login page", async ({ page }, testInfo) => {
  await resetMock(page);
  await signIn(page); // a token from /login, no MI return address
  await page.goto("/chat");
  await expect(page.locator('[data-ui="v5"]')).toBeVisible();
  await openNav(page, testInfo);
  await page.getByRole("button", { name: "ออกจากระบบ" }).click();
  await expect(page).toHaveURL(/\/login/);
});

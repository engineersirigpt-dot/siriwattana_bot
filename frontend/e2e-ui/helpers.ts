import { expect, type Page, type TestInfo } from "@playwright/test";

export const MOCK = "http://127.0.0.1:4010";

export async function resetMock(page: Page, state?: Record<string, unknown>) {
  await page.request.post(`${MOCK}/__mock/reset`);
  if (state) await page.request.post(`${MOCK}/__mock/state`, { data: state });
}

// Sign in by putting the token where lib/api.ts keeps it (no login round trip).
export async function signIn(page: Page, who: "admin" | "staff" = "admin", ui?: "classic", theme: "light" | "dark" = "light") {
  const user = who === "admin" ? { token: "tok-admin", role: "admin", name: "admin" } : { token: "tok-staff", role: "user", name: "staff" };
  await page.addInitScript(([u, pref, th]) => {
    // once per test: a reload must keep what the page saved (e.g. "back to classic")
    if (sessionStorage.getItem("__e2e_init")) return;
    sessionStorage.setItem("__e2e_init", "1");
    localStorage.setItem("chatbot_token", u.token);
    localStorage.setItem("chatbot_role", u.role);
    localStorage.setItem("chatbot_username", u.name);
    if (pref) localStorage.setItem("siri_ui", pref);
    else localStorage.removeItem("siri_ui");
    localStorage.setItem("theme", th);
  }, [user, ui ?? null, theme] as const);
}

export function isMobile(testInfo: TestInfo) {
  return testInfo.project.name === "mobile";
}

// Open the sidebar on phones (it's a drawer there); no-op on desktop.
export async function openNav(page: Page, testInfo: TestInfo) {
  if (isMobile(testInfo)) {
    await page.getByTestId("open-nav").click();
    await expect(page.getByTestId("sidebar")).toBeInViewport();
  }
}

export async function openSession(page: Page, testInfo: TestInfo, title: string) {
  await openNav(page, testInfo);
  await page.getByTestId("session-row").filter({ hasText: title }).first().click();
}

// Nothing may stick out sideways (the old page overflowed on phones).
export async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, width } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    width: window.innerWidth,
  }));
  expect(scroll, "page wider than the screen").toBeLessThanOrEqual(width + 1);
}

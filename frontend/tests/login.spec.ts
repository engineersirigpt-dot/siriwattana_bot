import { test, expect } from "@playwright/test";

// Test 1: หน้า login โหลดได้
test("เปิดหน้า login ได้", async ({ page }) => {
  await page.goto("/login");
  await expect(page).toHaveTitle(/Siriwattan/i);
});

// Test 2: login ด้วย account ที่มีอยู่
test("login สำเร็จแล้วไปหน้า chat", async ({ page }) => {
  await page.goto("/login");

  await page.fill('input[placeholder="ชื่อผู้ใช้"]', "admin");
  await page.fill('input[placeholder="รหัสผ่าน"]', "admin1234");
  await page.click('button[type="submit"]');

  await expect(page).toHaveURL(/.*chat/, { timeout: 10000 });
});

// Test 3: login ด้วย password ผิด → ต้องแสดง error ไม่ redirect
test("login password ผิด แสดง error message", async ({ page }) => {
  await page.goto("/login");

  await page.fill('input[placeholder="ชื่อผู้ใช้"]', "admin");
  await page.fill('input[placeholder="รหัสผ่าน"]', "wrongpassword999");
  await page.click('button[type="submit"]');

  // ต้องยังอยู่หน้า login ไม่ redirect ไป chat
  await expect(page).toHaveURL(/.*login/, { timeout: 5000 });

  // ต้องแสดง error box สีแดง
  const errorBox = page.locator(".text-red-700");
  await expect(errorBox).toBeVisible({ timeout: 5000 });
});

// Test 4: login ด้วย username ที่ไม่มีในระบบ → ต้องแสดง error
test("login username ไม่มีในระบบ แสดง error message", async ({ page }) => {
  await page.goto("/login");

  await page.fill('input[placeholder="ชื่อผู้ใช้"]', "userที่ไม่มีในระบบ");
  await page.fill('input[placeholder="รหัสผ่าน"]', "somepassword123");
  await page.click('button[type="submit"]');

  await expect(page).toHaveURL(/.*login/, { timeout: 5000 });

  const errorBox = page.locator(".text-red-700");
  await expect(errorBox).toBeVisible({ timeout: 5000 });
});

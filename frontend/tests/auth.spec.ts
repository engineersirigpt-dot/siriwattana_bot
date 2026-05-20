import { test, expect } from "@playwright/test";

// Test 1: เข้า /chat โดยไม่ login → ต้อง redirect กลับ /login
test("เข้า /chat โดยไม่ login ต้อง redirect ไป /login", async ({ page }) => {
  // Playwright เริ่มด้วย browser ใหม่ ไม่มี token ใน localStorage อยู่แล้ว
  await page.goto("/chat");

  // ต้อง redirect กลับหน้า login
  await expect(page).toHaveURL(/.*login/, { timeout: 5000 });
});

// Test 2: ล้าง token แล้วเข้า /chat → ต้อง redirect
test("ล้าง token แล้วเข้า /chat ต้อง redirect ไป /login", async ({ page }) => {
  // Login ก่อน
  await page.goto("/login");
  await page.fill('input[placeholder="ชื่อผู้ใช้"]', "admin");
  await page.fill('input[placeholder="รหัสผ่าน"]', "admin1234");
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/.*chat/, { timeout: 10000 });

  // ล้าง token ออกจาก localStorage (จำลองการ logout หรือ token หมดอายุ)
  await page.evaluate(() => localStorage.clear());

  // เข้า /chat อีกครั้ง
  await page.goto("/chat");

  // ต้อง redirect กลับ /login
  await expect(page).toHaveURL(/.*login/, { timeout: 5000 });
});

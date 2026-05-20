import { test, expect } from "@playwright/test";

// ทำ login ก่อนทุก test ใน file นี้
test.beforeEach(async ({ page }) => {
  await page.goto("/login");
  await page.fill('input[placeholder="ชื่อผู้ใช้"]', "admin");
  await page.fill('input[placeholder="รหัสผ่าน"]', "admin1234");
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/.*chat/, { timeout: 10000 });
});

test("ส่งข้อความแล้วได้รับคำตอบ", async ({ page }) => {
  // พิมพ์คำถาม
  await page.fill('input[placeholder*="พิมพ์คำถาม"]', "บริษัทเปิดกี่โมง");
  await page.click('button:has-text("ส่ง")');

  // รอคำตอบ (OpenAI อาจช้า ให้ timeout 30 วินาที)
  await expect(page.locator("text=08:30")).toBeVisible({ timeout: 30000 });
  console.log("✅ แชทบอทตอบได้ถูกต้อง");
});

test("กด แชทใหม่ แล้วหน้าจอเคลียร์", async ({ page }) => {
  await page.click('button:has-text("แชทใหม่")');
  await expect(page.locator("text=พิมพ์คำถามเกี่ยวกับบริษัท")).toBeVisible();
});

test("ลบแชทแล้วข้อมูลหายออกจาก sidebar", async ({ page }) => {
  // ส่งข้อความเพื่อให้ backend บันทึก session
  await page.fill('input[placeholder*="พิมพ์คำถาม"]', "บริษัทเปิดกี่โมง");
  await page.click('button:has-text("ส่ง")');

  // รอ bot ตอบ (session ถูกบันทึกหลัง bot ตอบ)
  await expect(page.locator("text=08:30")).toBeVisible({ timeout: 30000 });

  // session ที่ active จะมีสีม่วง (bg-purple-400) ใน sidebar
  const activeSession = page.locator(".bg-purple-400").first();
  await expect(activeSession).toBeVisible({ timeout: 5000 });

  // hover เพื่อให้ปุ่มลบโผล่ (hidden group-hover:flex)
  await activeSession.hover();

  // คลิกปุ่ม trash icon
  await activeSession.locator('button[title="ลบ"]').click();

  // modal ยืนยันต้องแสดง
  await expect(page.locator("text=ลบบทสนทนานี้?")).toBeVisible();

  // กดยืนยัน "ลบ" ใน modal
  await page.locator('button:has-text("ลบ")').last().click();

  // session ต้องหายออกจาก sidebar
  await expect(activeSession).not.toBeVisible({ timeout: 5000 });
});

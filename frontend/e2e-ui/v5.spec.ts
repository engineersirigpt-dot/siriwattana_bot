// V5 chat UI against the mock API. Runs on desktop (1440×900) and phone (390×844).
import { expect, test } from "@playwright/test";
import { expectNoHorizontalOverflow, isMobile, MOCK, openNav, openSession, resetMock, signIn } from "./helpers";

test.beforeEach(async ({ page }) => {
  await resetMock(page);
});

// ── rollout / rollback ─────────────────────────────────────────────────────
test.describe("rollout flag", () => {
  test("flag off → the classic page, with no V5 offer", async ({ page }) => {
    await resetMock(page, { ui: { 1: false } });
    await signIn(page);
    await page.goto("/chat");
    await expect(page.getByPlaceholder("พิมพ์คำถาม…")).toBeVisible();
    await expect(page.locator('[data-ui="v5"]')).toHaveCount(0);
    await expect(page.getByText("ลองหน้าแชทแบบใหม่")).toHaveCount(0);
  });

  test("flag on → V5, with no way back to classic in the menu", async ({ page }, testInfo) => {
    await signIn(page, "admin", "classic"); // a "classic" choice saved by the old button no longer counts
    await page.goto("/chat");
    await expect(page.locator('[data-ui="v5"]')).toBeVisible();
    await openNav(page, testInfo);
    await expect(page.getByRole("button", { name: "ออกจากระบบ" })).toBeVisible();
    await expect(page.getByText("กลับไปหน้าแชทแบบเดิม")).toHaveCount(0);
  });

  test("backend without /ui/config → classic", async ({ page }) => {
    await page.route("**/ui/config", (r) => r.fulfill({ status: 404, body: "{}" }));
    await signIn(page);
    await page.goto("/chat");
    await expect(page.getByPlaceholder("พิมพ์คำถาม…")).toBeVisible();
    await expect(page.locator('[data-ui="v5"]')).toHaveCount(0);
  });
});

// ── chat & streaming ───────────────────────────────────────────────────────
test.describe("chat and streaming", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
    await page.goto("/chat");
    await expect(page.getByTestId("empty-state")).toBeVisible();
  });

  test("an answer streams in with the backend's real status steps", async ({ page }) => {
    await page.getByLabel("ข้อความ").fill("ค้นเว็บ ราคาทองวันนี้");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("status-label")).toHaveText("กำลังค้นข้อมูลจากเว็บ…");
    await expect(page.getByTestId("status-label")).toHaveText("กำลังเรียบเรียงคำตอบ…");
    await expect(page.getByText("ค้นเว็บแล้ว")).toBeVisible(); // the finished step stays as a chip
    const bot = page.getByTestId("msg-bot").last();
    await expect(bot).toHaveAttribute("data-streaming", "0");
    await expect(bot.locator("strong", { hasText: "ตัวหนา" })).toBeVisible();
    await expect(bot.getByTestId("step-chips")).toHaveText("ค้นเว็บแล้ว"); // still shown above the answer
    await expect(bot.getByTestId("msg-actions")).toBeVisible();
    await expect(page.getByLabel("ข้อความ")).toBeFocused();
  });

  test("without status events it only says 'กำลังคิด…'", async ({ page }) => {
    await page.route("**/chat/stream", async (route) => {
      await new Promise((r) => setTimeout(r, 1500));
      await route.fulfill({
        status: 200,
        contentType: "application/x-ndjson",
        body: [{ type: "delta", v: "คำตอบ" }, { type: "done", source: "llm", session_id: 950, session_title: "t", message_id: 1 }]
          .map((o) => JSON.stringify(o)).join("\n") + "\n",
      });
    });
    await page.getByLabel("ข้อความ").fill("สวัสดี");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("status-label")).toHaveText("กำลังคิด…");
    await expect(page.locator(".ui-chip")).toHaveCount(0);
    await expect(page.getByTestId("msg-bot").last()).toContainText("คำตอบ");
  });

  test("หยุด keeps what arrived, stays in its chat, and is still there after a reload", async ({ page }, testInfo) => {
    await page.getByLabel("ข้อความ").fill("ตอบช้า ๆ ยาว ๆ");
    await page.getByTestId("send").click();
    const bot = page.getByTestId("msg-bot").last();
    await expect(bot).toContainText("คำตอบยาวที่ส่งมาช้า");
    await page.getByTestId("stop").click();
    await expect(bot.getByTestId("stopped-note")).toBeVisible();
    await expect(bot).toContainText("คำตอบยาวที่ส่งมาช้า"); // not wiped
    await expect(bot).toHaveAttribute("data-streaming", "0");
    await expect(page.getByTestId("send")).toBeVisible();
    // the new chat got its id from the `session` event before the answer
    await expect(page.getByRole("heading", { name: "ตอบช้า ๆ ยาว ๆ" })).toBeVisible();

    await page.reload();
    await openSession(page, testInfo, "ตอบช้า ๆ ยาว ๆ");
    const saved = page.getByTestId("msg-bot").last();
    await expect(saved).toContainText("คำตอบยาวที่ส่งมาช้า");
    await expect(saved.getByTestId("stopped-note")).toBeVisible(); // the backend's note, shown as the note
    await expect(saved).not.toContainText("หยุดก่อนตอบจบ");
  });

  test("a saved web answer still shows what was used after a reload", async ({ page }, testInfo) => {
    await openSession(page, testInfo, "ขั้นตอนเบิกค่าเดินทาง 1");
    await expect(page.getByTestId("msg-bot").last().getByTestId("step-chips")).toHaveText("ใช้ข้อมูลจากเว็บ");
  });

  test("image generation shows its status, then the picture", async ({ page }) => {
    await page.getByLabel("ข้อความ").fill("วาดรูปแมวสีส้ม");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("status-label")).toHaveText("กำลังสร้างรูป…");
    await expect(page.getByRole("img", { name: "ภาพที่สร้าง.png" })).toBeVisible();
  });

  test("a Document Engine answer: status, then a file card with preview", async ({ page }) => {
    await page.getByLabel("ข้อความ").fill("ทำเอกสาร Word รายงานยอดขาย");
    await page.getByTestId("send").click();
    await expect(page.getByTestId("status-label")).toHaveText("กำลังจัดทำเอกสารและตรวจไฟล์…");
    const card = page.getByTestId("file-card").filter({ hasText: "รายงานยอดขาย.docx" });
    await expect(card).toBeVisible();
    await expect(card.getByRole("button", { name: /ดูตัวอย่าง/ })).toBeVisible();
    // its "_ตัวอย่าง.png" is inside the card, not a separate picture
    await expect(page.getByRole("img", { name: "รายงานยอดขาย_ตัวอย่าง.png" })).toHaveCount(0);
  });
});

test("reading back isn't interrupted by a streaming answer", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/chat");
  await openSession(page, testInfo, "แชทยาว 18 คำถาม");
  await expect(page.getByTestId("msg-bot")).toHaveCount(18);
  await page.getByLabel("ข้อความ").fill("ตอบช้า ๆ");
  await page.getByTestId("send").click();
  await expect(page.getByTestId("msg-bot").last()).toContainText("คำตอบยาว");
  const scroller = page.getByTestId("scroller");
  await scroller.evaluate((el) => el.scrollTo({ top: 0 }));
  await page.waitForTimeout(1200);
  expect(await scroller.evaluate((el) => el.scrollTop)).toBeLessThan(50);
  await page.getByTestId("jump-latest").click();
  const gap = () => scroller.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
  await expect.poll(gap).toBeLessThan(80);
  await page.waitForTimeout(1000); // still streaming: it keeps following from here
  expect(await gap()).toBeLessThan(80);
  await expect(page.getByTestId("msg-bot").last()).toHaveAttribute("data-streaming", "1");
  await page.getByTestId("stop").click();
});

// ── rendering ──────────────────────────────────────────────────────────────
test("tables, code and long links render without widening the page", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/chat");
  await openSession(page, testInfo, "รายงานยอดขายไตรมาส 1");
  const md = page.getByTestId("md").first();
  await expect(md.locator("table")).toBeVisible();
  await expect(md.locator("th").first()).toHaveText("เดือน");
  const code = md.getByTestId("code-block");
  await expect(code).toContainText("python");
  await expect(code.getByRole("button", { name: "คัดลอกโค้ด" })).toBeVisible(); // visible without hover
  expect(await md.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await expectNoHorizontalOverflow(page);
  await expect(page.getByRole("img", { name: "กราฟยอดขาย.png" })).toBeVisible(); // a chart stays a picture
});

// ── files ──────────────────────────────────────────────────────────────────
test.describe("files", () => {
  test.beforeEach(async ({ page }, testInfo) => {
    await signIn(page);
    await page.goto("/chat");
    await openSession(page, testInfo, "รายงานยอดขายไตรมาส 1");
  });

  test("cards show name and type; previews open", async ({ page }) => {
    const card = (name: string) => page.getByTestId("file-card").filter({ hasText: name });
    await expect(card("ยอดขาย_Q1.xlsx")).toContainText("Excel");
    await expect(card("รายงานยอดขาย.docx")).toContainText("Word");
    await expect(card("สรุปผล.pdf")).toContainText("PDF");
    await expect(card("ยอดขาย_Q1.xlsx")).toContainText("สร้างโดย AI");

    await card("รายงานยอดขาย.docx").getByRole("button", { name: /ดูตัวอย่าง/ }).click();
    await expect(page.getByRole("dialog").getByRole("img", { name: "รายงานยอดขาย.docx" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await card("สรุปผล.pdf").getByRole("button", { name: /ดูตัวอย่าง/ }).click();
    await expect(page.getByRole("dialog").locator("iframe")).toBeVisible();
    await page.keyboard.press("Escape");

    await card("ลูกค้า.csv").getByRole("button", { name: /ดูตัวอย่าง/ }).click();
    await expect(page.getByTestId("csv-preview")).toContainText("41,650");
    await page.keyboard.press("Escape");

    // Excel can't be previewed in the browser — only downloaded
    await expect(card("ยอดขาย_Q1.xlsx").getByRole("button", { name: /ดูตัวอย่าง/ })).toHaveCount(0);
  });

  test("download keeps the real (Thai) file name", async ({ page }) => {
    const card = page.getByTestId("file-card").filter({ hasText: "ยอดขาย_Q1.xlsx" });
    const [dl] = await Promise.all([page.waitForEvent("download"), card.getByRole("button", { name: /ดาวน์โหลด/ }).click()]);
    expect(dl.suggestedFilename()).toBe("ยอดขาย_Q1.xlsx");
  });

  test("a file that's gone says so; an admin may open another user's file", async ({ page }) => {
    const gone = page.getByTestId("file-card").filter({ hasText: "ไฟล์เก่า_หมดอายุ.xlsx" });
    await gone.getByRole("button", { name: /ดาวน์โหลด/ }).click();
    await expect(gone.getByRole("alert")).toHaveText(/ไฟล์นี้ไม่มีแล้ว/);
    const theirs = page.getByTestId("file-card").filter({ hasText: "ของคนอื่น.pptx" });
    const [dl] = await Promise.all([page.waitForEvent("download"), theirs.getByRole("button", { name: /ดาวน์โหลด/ }).click()]);
    expect(dl.suggestedFilename()).toBe("ของคนอื่น.pptx");
    await expect(theirs.getByRole("alert")).toHaveCount(0);
  });
});

test("staff: a teammate's file in a shared chat says it isn't theirs", async ({ page }, testInfo) => {
  await resetMock(page, { ui: { 2: true } });
  await signIn(page, "staff");
  await page.goto("/chat");
  await openNav(page, testInfo);
  await page.getByRole("button", { name: /แชร์ร่วมกันในทีม/ }).click();
  await page.getByTestId("session-row").filter({ hasText: "วิธีใช้เครื่องตัด" }).click();
  const card = page.getByTestId("file-card").filter({ hasText: "คู่มือเครื่องตัด.pdf" });
  await card.getByRole("button", { name: /ดาวน์โหลด/ }).click();
  await expect(card.getByRole("alert")).toHaveText(/ไม่มีสิทธิ์/);
});

test("attaching files: typed chips, then cards on both sides", async ({ page }) => {
  await signIn(page);
  await page.goto("/chat");
  await page.getByTestId("file-input").setInputFiles([
    { name: "ใบเสนอราคา.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") },
    { name: "ยอดขาย.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("PK") },
    { name: "slide.pptx", mimeType: "", buffer: Buffer.from("PK") },
  ]);
  const chips = page.getByTestId("pending-file");
  await expect(chips).toHaveCount(3);
  await expect(chips.nth(0)).toContainText("PDF");
  await expect(chips.nth(1)).toContainText("Excel");
  await expect(chips.nth(2)).toContainText("PowerPoint"); // no MIME — known by extension
  await chips.nth(2).getByRole("button").click();
  await expect(chips).toHaveCount(2);
  await page.getByLabel("ข้อความ").fill("excel สรุปไฟล์นี้");
  await page.getByTestId("send").click();
  await expect(page.getByTestId("status-label")).toHaveText("กำลังประมวลผลไฟล์…");
  await expect(page.getByTestId("msg-user").last().getByTestId("file-card")).toHaveCount(2);
  await expect(page.getByTestId("msg-bot").last().getByTestId("file-card")).toContainText("ยอดขาย_Q1.xlsx");
});

// ── history ────────────────────────────────────────────────────────────────
test("history: titles filter as you type, Enter searches inside messages", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/chat");
  await openNav(page, testInfo);
  const searchBox = page.getByLabel("ค้นหาแชท");
  await searchBox.fill("ISO");
  await expect(page.getByTestId("session-row")).toHaveCount(8); // "สรุปเอกสาร ISO n" (team/shared lists are closed)
  await searchBox.fill("ปฏิทิน");
  await expect(page.getByText("ไม่พบชื่อแชทที่ตรงกัน")).toBeVisible();
  await searchBox.press("Enter");
  await page.getByTestId("search-hit").first().click();
  await expect(page.getByRole("heading", { name: "รายงานยอดขายไตรมาส 1" })).toBeVisible();
});

test("keyboard: Ctrl+K searches, a chat row opens with Enter", async ({ page }, testInfo) => {
  test.skip(isMobile(testInfo), "keyboard shortcuts are a desktop feature");
  await signIn(page);
  await page.goto("/chat");
  await expect(page.getByTestId("empty-state")).toBeVisible(); // hydrated
  await page.keyboard.press("Control+k");
  await expect(page.getByLabel("ค้นหาแชท")).toBeFocused();
  const row = page.getByTestId("session-row").filter({ hasText: "รายงานยอดขายไตรมาส 1" });
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "รายงานยอดขายไตรมาส 1" })).toBeVisible();
  // a row's actions are reachable by keyboard (shown on focus)
  await row.focus();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toHaveAttribute("aria-label", /บันทึก/);
});

// ── who sees what ──────────────────────────────────────────────────────────
test("admin: memory menu works; admin dashboard and team chats shown", async ({ page }, testInfo) => {
  await signIn(page, "admin");
  await page.goto("/chat");
  await openNav(page, testInfo);
  await expect(page.getByRole("button", { name: "Admin Dashboard" })).toBeVisible();
  await expect(page.getByRole("button", { name: /แชทของทีม/ })).toBeVisible();
  await page.getByTestId("memory-menu").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("ฉันอยู่ฝ่ายผลิต กะเช้า");
  await dialog.getByPlaceholder(/เพิ่มข้อมูลที่ให้จำ/).fill("ชอบคำตอบสั้น ๆ");
  await dialog.getByRole("button", { name: "จำ", exact: true }).click();
  await expect(dialog).toContainText("ชอบคำตอบสั้น ๆ");
  await dialog.getByPlaceholder(/เพิ่มข้อมูลที่ให้จำ/).fill("รหัสผ่านอีเมล 1234");
  await dialog.getByRole("button", { name: "จำ", exact: true }).click();
  await expect(dialog).toContainText("ไม่บันทึก"); // the server's refusal is shown
  await dialog.getByRole("button", { name: "ลบ" }).first().click();
  await expect(dialog).not.toContainText("ฉันอยู่ฝ่ายผลิต กะเช้า");
});

test("staff: no memory menu, no admin items, can't see others' chats", async ({ page }, testInfo) => {
  await resetMock(page, { ui: { 2: true } });
  await signIn(page, "staff");
  await page.goto("/chat");
  await expect(page.locator('[data-ui="v5"]')).toBeVisible();
  await openNav(page, testInfo);
  await expect(page.getByTestId("memory-menu")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Admin Dashboard" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /แชทของทีม/ })).toHaveCount(0);
  const reqs: string[] = await (await page.request.get(`${MOCK}/__mock/requests`)).json();
  expect(reqs).not.toContain("GET /admin/chat-history"); // not even asked for
});

// ── layout states ──────────────────────────────────────────────────────────
test("phone: drawer navigation, nothing overflows", async ({ page }, testInfo) => {
  test.skip(!isMobile(testInfo), "phone layout");
  await signIn(page);
  await page.goto("/chat");
  await expect(page.getByTestId("sidebar")).not.toBeInViewport();
  await expectNoHorizontalOverflow(page);
  await openSession(page, testInfo, "รายงานยอดขายไตรมาส 1");
  await expect(page.getByTestId("sidebar")).not.toBeInViewport(); // closes after picking a chat
  await expect(page.getByTestId("send")).toBeInViewport();
  await expectNoHorizontalOverflow(page);
  await page.getByTestId("open-nav").click();
  await page.getByTestId("nav-backdrop").click({ position: { x: 370, y: 400 } });
  await expect(page.getByTestId("sidebar")).not.toBeInViewport();
});

test("phone: a chat's actions sit behind ⋯, so titles keep their width", async ({ page }, testInfo) => {
  test.skip(!isMobile(testInfo), "touch layout");
  await signIn(page);
  await page.goto("/chat");
  await openNav(page, testInfo);
  const row = page.getByTestId("session-row").filter({ hasText: "แชทยาว 18 คำถามเรื่องการผลิต" });
  await expect(row.getByRole("button", { name: "ลบ" })).toBeHidden();
  const title = row.locator(".truncate");
  expect(await title.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true); // not cut off
  await row.getByTestId("row-menu").click();
  const actions = page.getByTestId("row-actions");
  await expect(actions.getByRole("button")).toHaveText(["บันทึกแชทนี้ไว้", "เปลี่ยนชื่อ", "ดาวน์โหลด CSV", "ลบ"]);
  await actions.getByRole("button", { name: "เปลี่ยนชื่อ" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(actions).toHaveCount(0); // closes after use
});

test("dark mode: the logo has no white box; the composer has no stray scrollbar", async ({ page }) => {
  await signIn(page, "admin", undefined, "dark");
  await page.goto("/chat");
  const empty = page.getByTestId("empty-state");
  await expect(empty.locator('img[src="/Logo_siri_t.png"]')).toBeVisible();
  await expect(empty.locator('img[src="/Logo_siri.jpg"]')).toBeHidden();
  const box = page.getByLabel("ข้อความ");
  expect(await box.evaluate((el) => getComputedStyle(el).overflowY)).toBe("hidden");
  await box.fill(Array.from({ length: 20 }, (_, i) => `บรรทัด ${i + 1}`).join("\n"));
  expect(await box.evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto"); // past ~8 lines it scrolls
});

test("desktop and tablet: the sidebar stays put", async ({ page }, testInfo) => {
  test.skip(isMobile(testInfo), "desktop layout");
  await signIn(page);
  await page.goto("/chat");
  await expect(page.getByTestId("sidebar")).toBeInViewport();
  await expect(page.getByTestId("open-nav")).toBeHidden();
  await expect(page.getByTestId("row-menu").first()).toBeHidden(); // a mouse gets hover icons instead
  await page.setViewportSize({ width: 820, height: 1180 }); // tablet portrait
  await expect(page.getByTestId("sidebar")).toBeInViewport();
  await expectNoHorizontalOverflow(page);
});

test("a chat that can't be opened says so and retries", async ({ page }, testInfo) => {
  await signIn(page);
  let fail = true;
  await page.route("**/chat/sessions/101", (route) => (fail ? route.fulfill({ status: 500, body: "{}" }) : route.continue()));
  await page.goto("/chat");
  await openSession(page, testInfo, "รายงานยอดขายไตรมาส 1");
  await expect(page.getByText("เปิดแชทนี้ไม่สำเร็จ")).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "ลองใหม่" }).click();
  await expect(page.getByTestId("md").first()).toBeVisible();
});

test("the chat list shows loading, then an error with retry", async ({ page }, testInfo) => {
  await signIn(page);
  let fail = true;
  await page.route("**/chat/sessions", async (route) => {
    await new Promise((r) => setTimeout(r, 500));
    return fail ? route.fulfill({ status: 500, body: "{}" }) : route.continue();
  });
  await page.goto("/chat");
  await openNav(page, testInfo);
  await expect(page.getByText("โหลดรายการแชทไม่สำเร็จ")).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "ลองใหม่" }).click();
  await expect(page.getByTestId("session-row").filter({ hasText: "รายงานยอดขายไตรมาส 1" })).toBeVisible();
});

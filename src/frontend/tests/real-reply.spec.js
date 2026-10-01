import { expect, test } from "@playwright/test";

test("real model Markdown survives CLI, command logs, API, and browser rendering", async ({ page }) => {
  test.skip(
    process.env.RUN_LIVE_CHAT !== "1" || !process.env.FRONTEND_TEST_URL,
    "Opt in with RUN_LIVE_CHAT=1 and a deployed FRONTEND_TEST_URL; this invokes the model.",
  );
  test.setTimeout(300_000);
  let sessionId;
  let sandboxCreated = false;
  try {
    await page.goto("/");
    sessionId = await page.evaluate(() => sessionStorage.getItem("golden-order-session"));
    await page.locator("#prompt").fill(
      "仅做排版检查，不查询信息、不调用工具、不下单。请输出原始 Markdown：" +
      "二级标题“格式检查”；一个两列表格，表头为“项目”和“数值”，两行数据分别为 A/1、B/2；" +
      "表格下面加粗“格式保留”；最后添加一个 Python 代码块，内容为 print('ok')。" +
      "不要用代码块包裹整段回复。",
    );
    const pending = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/api/chat",
      { timeout: 230_000 },
    );
    await page.locator("#send").click();
    const response = await pending;
    expect(response.status()).toBe(200);
    const result = await response.json();
    expect(result.model).toBe("gpt-6-astra");
    expect(result.sandbox_id).toBeTruthy();
    sandboxCreated = true;
    expect(result.message).toMatch(/^## /m);
    expect(result.message).toMatch(/^\|.*\|$/m);
    expect(result.message).toContain("**格式保留**");
    expect(result.message).not.toMatch(/[┌┬┐└┴┘]/);
    const body = page.locator(".message.assistant .markdown-body").last();
    await expect(body.locator("h2")).toContainText("格式检查");
    await expect(body.locator("thead th")).toHaveCount(2);
    await expect(body.locator("tbody tr")).toHaveCount(2);
    await expect(body.locator("strong")).toContainText("格式保留");
    await expect(body.locator("pre code")).toContainText("print");
    await expect(page.locator("#error")).toBeHidden();
    await page.setViewportSize({ width: 390, height: 844 });
    const width = await page.evaluate(() => ({
      content: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }));
    expect(width.content).toBeLessThanOrEqual(width.viewport);
  } finally {
    if (sessionId) {
      const response = await page.request.delete(`/api/sessions/${sessionId}`, { timeout: 60_000 });
      expect(response.ok()).toBeTruthy();
      const cleanup = await response.json();
      expect(cleanup.session_id).toBe(sessionId);
      if (sandboxCreated) {
        expect(cleanup.deleted).toBe(true);
      }
    }
  }
});

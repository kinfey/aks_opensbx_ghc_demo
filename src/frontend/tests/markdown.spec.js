import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const policy = readFileSync(new URL("../default.conf.template", import.meta.url), "utf8")
  .match(/Content-Security-Policy "([^"]+)"/)[1];

test.beforeEach(async ({ page, baseURL }) => {
  if (!process.env.FRONTEND_TEST_URL) {
    await page.route(`${baseURL}/`, async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        headers: { ...response.headers(), "content-security-policy": policy },
      });
    });
  }
});

async function reply(page, markdown, prompt = "Render this reply") {
  await page.route("**/api/chat", (route) => route.fulfill({
    json: {
      session_id: route.request().postDataJSON().session_id,
      message: markdown,
      sandbox_id: "markdown-test",
      model: "gpt-6-astra",
    },
  }));
  const response = await page.goto("/");
  expect(response.headers()["content-security-policy"]).toBe(policy);
  await page.locator("#prompt").fill(prompt);
  await page.locator("#send").click();
  const body = page.locator(".message.assistant .markdown-body").last();
  await expect(body).toBeVisible();
  await expect(page.locator("#send")).toBeEnabled();
  await expect(page.locator("#error")).toBeHidden();
  return body;
}

test("renders GFM tables and common Markdown formatting under the unchanged CSP", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const body = await reply(page, [
    "# Menu",
    "",
    "**Bold** and *italic* and ~~removed~~ with `inline code`.",
    "",
    "| Item | Price |",
    "| :--- | ---: |",
    "| **Fries** | 13.50 |",
    "| Cola | 10.50 |",
    "",
    "- First",
    "  - Nested",
    "- Second",
    "",
    "1. One",
    "2. Two",
    "",
    "> A quotation",
    "",
    "```js",
    'const sample = "<b>literal</b>";',
    "```",
    "",
    "[Official link](https://example.com/menu)",
    "",
    "First line",
    "Second line",
  ].join("\n"));
  await expect(body.locator("h1")).toHaveText("Menu");
  await expect(body.locator("strong").first()).toHaveText("Bold");
  await expect(body.locator("em")).toHaveText("italic");
  await expect(body.locator("del")).toHaveText("removed");
  await expect(body.locator("thead th")).toHaveCount(2);
  await expect(body.locator("tbody tr")).toHaveCount(2);
  await expect(body.locator("td").nth(1)).toHaveCSS("text-align", "right");
  await expect(body.locator("ul ul li")).toHaveText("Nested");
  await expect(body.locator("ol > li")).toHaveCount(2);
  await expect(body.locator("blockquote")).toHaveText("A quotation");
  await expect(body.locator("pre code")).toContainText("<b>literal</b>");
  await expect(body.locator("pre b")).toHaveCount(0);
  await expect(body.locator("a")).toHaveAttribute("target", "_blank");
  await expect(body.locator("a")).toHaveAttribute("rel", "noopener noreferrer");
  await expect(body.locator("br")).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("keeps user input literal and rejects active HTML and unsafe links", async ({ page }) => {
  const input = '**user text** <img src=x onerror="window.markdownExecuted=1">';
  const body = await reply(page, [
    '<script>window.markdownExecuted=1</script>',
    '<img src=x onerror="window.markdownExecuted=1">',
    '<svg onload="window.markdownExecuted=1"></svg>',
    '<iframe src="https://example.com"></iframe>',
    '<form id="chat-form"><input name="prompt"></form>',
    "",
    "[bad](javascript:alert%281%29)",
    "[encoded](javascript&#58;alert%281%29)",
    "[data](data:text/html,test)",
    "[file](file:///etc/passwd)",
    "[ok](https://example.com)",
    "![tracking](https://example.com/tracking.png)",
  ].join("\n"), input);
  await expect(page.locator(".message.user .message-text")).toHaveText(input);
  await expect(page.locator(".message.user strong, .message.user img")).toHaveCount(0);
  await expect(body.locator("script, img, svg, iframe, form, input")).toHaveCount(0);
  await expect(body.locator("a")).toHaveCount(1);
  await expect(body.locator("a")).toHaveAttribute("href", "https://example.com");
  expect(await page.evaluate(() => window.markdownExecuted)).toBeUndefined();
  await expect(page.locator("#chat-form")).toHaveCount(1);
});

test("scrolls wide tables and code without overflowing the viewport", async ({ page }) => {
  const headers = Array.from({ length: 10 }, (_, index) => `Column ${index}`);
  const body = await reply(page, [
    `| ${headers.join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`,
    `| ${headers.map(() => "A long cell value").join(" | ")} |`,
    "",
    "```text",
    "a".repeat(300),
    "```",
  ].join("\n"));
  for (const selector of [".markdown-table", "pre"]) {
    const dimensions = await body.locator(selector).evaluate((element) => ({
      client: element.clientWidth,
      scroll: element.scrollWidth,
      overflow: getComputedStyle(element).overflowX,
    }));
    expect(dimensions.scroll).toBeGreaterThan(dimensions.client);
    expect(dimensions.overflow).toBe("auto");
  }
  const width = await page.evaluate(() => ({
    content: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(width.content).toBeLessThanOrEqual(width.viewport);
  await expect(body.locator(".markdown-table")).toHaveAttribute("tabindex", "0");
});

test("preserves plain-text replies and paragraph breaks", async ({ page }) => {
  const body = await reply(page, "Plain reply & special < characters.\nNext line.\n\nNew paragraph.");
  await expect(body.locator("p")).toHaveCount(2);
  await expect(body.locator("br")).toHaveCount(1);
  await expect(body).toContainText("Plain reply & special < characters.");
});

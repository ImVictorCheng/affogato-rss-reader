import { expect, test } from "@playwright/test";

test.beforeEach(async ({ request, page }) => {
  await request.post("http://127.0.0.1:18081/__test__/reset");
  await page.addInitScript(() => localStorage.setItem("affogato-rss-reader:locale", "en"));
});

test("desktop layout, keyboard navigation and domain ALL filtering", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.locator(".sidebar")).toBeVisible();
  const briefsButton = page.getByRole("button", { name: "Briefs" });
  await expect(briefsButton).toBeEnabled();
  await briefsButton.click();
  await expect(page.getByRole("heading", { name: "Briefs", level: 1 })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("button", { name: "Back to reader" }).click();
  await expect(page.locator(".entry-list-pane")).toBeVisible();
  await expect(page.locator(".detail-pane")).toBeVisible();
  await expect(page.locator(".entry-card")).toHaveCount(2);
  await page.keyboard.press("j");
  await expect(page.locator(".detail-pane")).toContainText("A provider-independent article");
  const domainGroup = page.locator(".nav-group").filter({ hasText: "DOMAINS" });
  await domainGroup.getByRole("button", { name: "Science 2", exact: true }).click();
  await domainGroup.getByRole("button", { name: "Technology 1", exact: true }).click();
  await domainGroup.getByRole("button", { name: "ALL", exact: true }).click();
  await expect(page.locator(".entry-card")).toHaveCount(1);
});

test("mobile layout stacks navigation, list and detail", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator(".mobile-header")).toBeVisible();
  await expect(page.locator(".detail-pane")).toBeHidden();
  await page.locator(".entry-card").first().click();
  await expect(page.locator(".entry-list-pane")).toBeHidden();
  await expect(page.locator(".detail-pane")).toBeVisible();
  await page.locator(".mobile-back").click();
  await expect(page.locator(".entry-list-pane")).toBeVisible();
  await page.locator(".mobile-header button").first().click();
  await expect(page.locator(".mobile-nav-scrim")).toBeVisible();
});

test("reading state is shared by two browser contexts", async ({ browser }) => {
  const first = await browser.newContext();
  const second = await browser.newContext();
  await first.addInitScript(() => localStorage.setItem("affogato-rss-reader:locale", "en"));
  await second.addInitScript(() => localStorage.setItem("affogato-rss-reader:locale", "en"));
  const a = await first.newPage();
  const b = await second.newPage();
  await a.goto("/");
  await a.locator(".entry-card").first().click();
  await b.goto("/");
  await expect(b.locator(".entry-card")).toHaveCount(1);
  await first.close();
  await second.close();
});

test("translation failure falls back to the original", async ({ page }) => {
  await page.goto("/");
  await page.locator(".entry-card").nth(1).click();
  await expect(page.locator(".detail-pane")).toContainText("Reading works even when translation is unavailable.");
  await expect(page.locator(".detail-pane")).toContainText("Translation failed");
});

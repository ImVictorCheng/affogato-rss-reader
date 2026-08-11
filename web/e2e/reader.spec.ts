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

test("renders article summaries and briefs with local MathJax", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const externalRequests: string[] = [];
  const localMathJaxRequests: string[] = [];
  page.on("request", (outgoing) => {
    if (/math-marker\.invalid|remote-marker\.invalid/.test(outgoing.url())) externalRequests.push(outgoing.url());
    if (outgoing.url().includes("/vendor/mathjax/")) localMathJaxRequests.push(outgoing.url());
  });
  const scriptResponse = await request.get("http://127.0.0.1:4173/vendor/mathjax/tex-chtml.js");
  const safeResponse = await request.get("http://127.0.0.1:4173/vendor/mathjax/ui/safe.js");
  const fontResponse = await request.get("http://127.0.0.1:4173/vendor/mathjax/output/chtml/fonts/woff-v2/MathJax_Main-Regular.woff");
  const licenseResponse = await request.get("http://127.0.0.1:4173/vendor/mathjax/LICENSE");
  expect(scriptResponse.status()).toBe(200);
  expect(safeResponse.status()).toBe(200);
  expect(fontResponse.status()).toBe(200);
  expect(licenseResponse.status()).toBe(200);

  await page.goto("/");
  await page.locator(".entry-card").first().click();
  const abstract = page.locator(".abstract-section");
  await expect(abstract.locator(".abstract-block--translated mjx-container")).toHaveCount(1);
  await expect(abstract.locator(".abstract-block:not(.abstract-block--translated) mjx-container")).toHaveCount(2);
  await expect(abstract.locator(".mathjax-scope[data-mathjax-status=ready]")).toHaveCount(2);

  await page.getByRole("button", { name: "Briefs" }).click();
  const brief = page.locator(".brief-summary");
  await expect(brief.locator("mjx-container")).toHaveCount(3);
  await expect(brief.locator("code", { hasText: "$code_not_math$" })).toHaveCount(1);
  await expect(brief.locator("code mjx-container")).toHaveCount(0);
  await expect(brief.locator(".mathjax-scope")).toHaveAttribute("data-mathjax-status", "ready");
  await expect(brief.locator("mjx-container a")).toHaveCount(0);
  await expect(brief.locator(".math-marker")).toHaveCount(0);
  await expect(brief).toContainText("\\style");
  await expect(brief.locator("img")).toHaveCount(0);
  await expect(brief.getByRole("img", { name: "Remote chart" })).toHaveClass(/brief-image-placeholder/);
  await expect(brief.getByText("Blocked location")).not.toHaveAttribute("href");
  await expect(brief.getByRole("link", { name: "Safe reference" })).toHaveAttribute("href", "https://example.test/report");

  const display = brief.locator("mjx-container[display=true]");
  await expect(display).toHaveCSS("overflow-x", "auto");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const colors = await brief.locator("mjx-container").first().evaluate((element) => ({
    formula: getComputedStyle(element).color,
    parent: getComputedStyle(element.parentElement!).color,
  }));
  expect(colors.formula).toBe(colors.parent);
  expect(externalRequests).toEqual([]);
  expect(localMathJaxRequests.some((url) => url.endsWith("/vendor/mathjax/ui/safe.js"))).toBe(true);
});

test("uses the shared field contract without widening compact controls", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  const search = page.locator(".search-box input");
  await expect.poll(async () => search.evaluate((element) => Number.parseFloat(getComputedStyle(element).height))).toBeLessThan(43);
  await expect(search).toHaveCSS("border-top-width", "0px");

  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();
  await expect(page.getByRole("button", { name: /My feeds \(1\)/ })).toBeVisible();

  const readFieldStyle = (locator) => locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      height: style.height,
      minHeight: style.minHeight,
      padding: style.padding,
      border: style.border,
      fontSize: style.fontSize,
    };
  });

  await page.getByRole("button", { name: "Add feed" }).click();
  const urlInput = page.getByPlaceholder("https://example.com/feed.xml");
  const reference = await readFieldStyle(urlInput);
  expect(reference.minHeight).toBe("43px");
  expect(reference.padding).toBe("10px 12px");
  expect(reference.border).toContain("1px");
  expect(reference.fontSize).toBe("14px");
  await expect(page.locator(".app-combobox__input")).toHaveCSS("min-height", "41px");
  await expect(page.locator(".app-combobox__input")).toHaveCSS("border-top-width", "0px");
  const domainPicker = page.locator(".feed-form .domain-picker");
  const domainOptions = domainPicker.locator(".domain-picker__options");
  await expect(domainPicker).toHaveCSS("border-top-width", "0px");
  await expect(domainOptions).toHaveCSS("display", "flex");
  await expect(domainOptions).toHaveCSS("border-top-style", "solid");
  await expect(domainOptions).toHaveCSS("border-top-width", "1px");
  await expect(domainOptions).toHaveCSS("border-radius", "7px");
  const scienceDomain = domainPicker.getByRole("checkbox", { name: "Science" });
  await domainPicker.getByText("Science", { exact: true }).click();
  await expect(scienceDomain).toBeChecked();
  await expect(scienceDomain.locator("xpath=..")).toHaveClass(/is-selected/);

  await page.getByRole("button", { name: "Categories" }).click();
  const fields = [
    page.getByRole("textbox", { name: "New folder name" }),
    page.getByPlaceholder("Create domain"),
    page.getByPlaceholder("New tag"),
  ];
  for (const field of fields) {
    await expect.poll(() => readFieldStyle(field)).toEqual(reference);
    await field.focus();
    await expect(field).toHaveCSS("box-shadow", /0px 0px 0px 3px/);
  }
  const tagList = page.locator(".tag-manager-list");
  const tagCards = tagList.locator(".tag-manager-card");
  await expect(tagList).toHaveCSS("display", "flex");
  await expect(tagList).toHaveCSS("flex-wrap", "wrap");
  await expect(tagCards).toHaveCount(3);
  await expect(tagCards.nth(0)).toContainText(/Condensed Matter\s*1/);
  await expect(tagCards.nth(1)).toContainText(/QEC\s*1/);
  await expect(tagCards.nth(2)).toContainText(/Quantum Computing\s*2/);
  await tagCards.nth(1).hover();
  await tagCards.nth(1).getByRole("button", { name: "Rename tag QEC" }).click();
  await expect(page.getByRole("textbox", { name: "Rename tag QEC" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator(".category-manager__create > button").first()).not.toHaveCSS("width", "100%");
  await expect(page.locator(".icon-button").first()).toHaveCSS("width", "36px");

  await page.getByRole("button", { name: "OPML" }).click();
  const opmlCards = page.locator(".opml-card");
  await expect(page.locator(".opml-panel")).toHaveCSS("min-height", "0px");
  await expect(opmlCards).toHaveCount(2);
  await expect(opmlCards.locator("p")).toHaveCount(0);
  const opmlBoxes = await opmlCards.evaluateAll((cards) => cards.map((card) => {
    const box = card.getBoundingClientRect();
    const heading = card.querySelector("h3")?.getBoundingClientRect();
    const button = card.querySelector("button")?.getBoundingClientRect();
    return { top: box.top, height: box.height, headingTop: heading?.top, buttonTop: button?.top };
  }));
  expect(opmlBoxes[0].top).toBe(opmlBoxes[1].top);
  expect(opmlBoxes[0].height).toBe(opmlBoxes[1].height);
  expect(opmlBoxes[0].height).toBeLessThan(120);
  expect(opmlBoxes[0].headingTop).toBe(opmlBoxes[1].headingTop);
  expect(opmlBoxes[0].buttonTop).toBe(opmlBoxes[1].buttonTop);
});

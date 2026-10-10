import { expect, test, type Dialog } from "@playwright/test";

const previewEnabled = process.env.VITE_AUTO_TAG_PREVIEW_ENABLED === "true";

test.beforeEach(async ({ request, page }) => {
  await request.post("http://127.0.0.1:18081/__test__/reset");
  await page.addInitScript(() => localStorage.setItem("affogato-rss-reader:locale", "en"));
});

test("article tag dragging persists descending weights and keeps clicks harmless", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const id of [3, 1, 2]) await request.post(`http://127.0.0.1:18081/api/v1/entries/101/tags/${id}`);
  await page.goto("/");
  await page.locator(".entry-card").first().click();
  const names = page.locator(".tag-editor__name");
  const cardTags = page.locator('[data-entry-id="101"] .entry-card__topics span');
  await expect(cardTags).toHaveText(["Condensed Matter", "QEC"]);
  await expect(names).toHaveText(["Condensed Matter", "QEC", "Quantum Computing"]);
  const firstTag = page.locator('[data-tag-id="1"]');
  const dotBox = (await firstTag.locator(".tag-editor__color").boundingBox())!;
  const closeBox = (await firstTag.locator(".tag-editor__remove").boundingBox())!;
  expect(closeBox.width).toBe(dotBox.width);
  expect(closeBox.height).toBe(dotBox.height);
  await firstTag.locator(".tag-editor__remove").hover();
  await expect(firstTag.locator(".tag-editor__remove")).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await firstTag.screenshot({ path: "test-results/compact-tag-remove.png" });
  await names.first().click();
  await expect(names).toHaveCount(3);
  const save = page.waitForResponse((response) => response.url().endsWith("/entries/101/tags/order") && response.request().method() === "PUT");
  await page.locator('[data-tag-id="1"]').dragTo(page.locator('[data-tag-id="3"]'));
  const saved = await save;
  expect(saved.ok()).toBeTruthy();
  expect((await saved.json()).tags.map((tag: { id: number; weight: number }) => [tag.id, tag.weight])).toEqual([[2, 3], [3, 2], [1, 1]]);
  await expect(names).toHaveText(["QEC", "Quantum Computing", "Condensed Matter"]);
  await expect(cardTags).toHaveText(["QEC", "Quantum Computing"]);
  await page.reload();
  await page.getByRole("button", { name: "All articles", exact: true }).click();
  await page.locator(".entry-card").first().click();
  await expect(names).toHaveText(["QEC", "Quantum Computing", "Condensed Matter"]);
  await expect(cardTags).toHaveText(["QEC", "Quantum Computing"]);
  await page.getByRole("button", { name: "Remove tag QEC", exact: true }).click();
  await expect(names).toHaveText(["Quantum Computing", "Condensed Matter"]);
  expect((await (await request.get("http://127.0.0.1:18081/api/v1/entries/102")).json()).tags).toEqual([]);
});

test("card topic badges stay beside domains within narrow cards", async ({ page, request }) => {
  for (const id of [1, 2, 3]) await request.post(`http://127.0.0.1:18081/api/v1/entries/101/tags/${id}`);
  const domainName = "量子物理";
  await page.route("**/api/v1/entries**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET" || !/^\/api\/v1\/entries(?:\/\d+)?$/.test(path)) return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    for (const item of body.items ?? [body]) item.domains = [{ id: 1, name: domainName }];
    await route.fulfill({ response, json: body });
  });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    const card = page.locator('[data-entry-id="101"]');
    const domain = card.locator(".entry-card__tags");
    const topics = card.locator(".entry-card__topics");
    await expect(domain).toHaveText(domainName);
    await expect(topics.locator("span")).toHaveText(["Condensed Matter", "QEC"]);
    const domainBox = (await domain.boundingBox())!;
    const topicBox = (await topics.boundingBox())!;
    const actionsBox = (await card.locator(".entry-card__actions").boundingBox())!;
    expect(topicBox.x - domainBox.x - domainBox.width).toBeGreaterThanOrEqual(8);
    expect(topicBox.x + topicBox.width).toBeLessThanOrEqual(actionsBox.x);
    expect(Math.abs(domainBox.y - topicBox.y)).toBeLessThan(2);
    expect(await topics.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy();
    await page.screenshot({ path: `test-results/card-top-tags-${width}.png`, fullPage: false });
  }
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

test("journal entries show labeled fallback dates in the list and detail", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/v1/entries**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET" || !/^\/api\/v1\/entries(?:\/\d+)?$/.test(path)) return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    const items = body.items ?? [body];
    for (const entry of items) {
      entry.published_at = null;
      entry.source_updated_at = entry.id === 101 ? "2026-10-08T10:00:00Z" : null;
      entry.created_at = "2026-10-09T12:00:00Z";
    }
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  const cards = page.locator(".entry-card");
  await expect(cards.first().locator("time")).toContainText("Updated");
  await expect(cards.nth(1).locator("time")).toContainText("Added");
  await cards.first().click();
  const date = page.locator(".article-meta-top time");
  await expect(date).toHaveAttribute("datetime", "2026-10-08T10:00:00Z");
  await expect(date).toContainText("Updated");
  await cards.nth(1).click();
  await expect(date).toHaveAttribute("datetime", "2026-10-09T12:00:00Z");
  await expect(date).toContainText("Added");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(date).toBeVisible();
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

test("renders article titles, summaries, and briefs with local MathJax", async ({ page, request }) => {
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
  const firstCard = page.locator(".entry-card").first();
  await expect(firstCard.locator("h3 mjx-container")).toHaveCount(1);
  await expect(firstCard.locator(".entry-card__original mjx-container")).toHaveCount(1);
  await page.locator(".entry-card").first().click();
  await expect(page.locator(".article-title--translated mjx-container")).toHaveCount(1);
  await expect(page.locator(".article-title--original mjx-container")).toHaveCount(1);
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
  const tagList = page.locator(".tag-manager-list:not(.tag-proposals-list)");
  const tagCards = tagList.locator(".tag-manager-card");
  await expect(tagList).toHaveCSS("display", "grid");
  await expect(tagCards).toHaveCount(3);
  await expect(tagCards.nth(0)).toContainText(/Condensed Matter\s*1/);
  await expect(tagCards.nth(1)).toContainText(/QEC\s*1/);
  await expect(tagCards.nth(2)).toContainText(/Quantum Computing\s*2/);
  await tagCards.nth(1).hover();
  await tagCards.nth(1).getByRole("button", { name: "Rename tag QEC" }).click();
  await expect(page.getByRole("textbox", { name: "Rename tag QEC" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator(".auto-tag-policy-note strong").getByText("Threshold growth", { exact: true })).toBeVisible();
  await expect(page.getByText("10 Work / 365 days", { exact: true })).toBeVisible();
  await expect(page.getByText(/Article data sent.*batch-local identifier, title, and summary/i)).toBeVisible();
  const previewButton = page.getByRole("button", { name: "Run 50-article preview" });
  await expect(page.getByRole("button", { name: "Preview cleanup" })).toHaveCount(0);
  if (previewEnabled) {
    await expect(previewButton).toBeEnabled();
    await previewButton.click();
    await expect(page.getByText("Preview result", { exact: true })).toBeVisible();
    await expect(page.getByText("Classified", { exact: true })).toBeVisible();
    await expect(page.getByText(/reproducibility · 91%/)).toBeVisible();
  } else {
    await expect(previewButton).toHaveCount(0);
  }
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

test("auto tagging enables directly without requesting previews", async ({ page }) => {
  test.skip(previewEnabled, "This case covers the default disabled-preview state.");
  const previewRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/auto-tag/previews")) previewRequests.push(request.url());
  });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();
  await expect(page.getByRole("button", { name: "Run 50-article preview" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Approve full run" })).toHaveCount(0);
  const autoTagSection = page.getByRole("heading", { name: "Auto tagging" }).locator("xpath=ancestor::section");
  await autoTagSection.locator("label.toggle").click();
  await expect(autoTagSection.getByRole("checkbox", { name: "On", exact: true })).toBeChecked();
  await page.getByRole("combobox", { name: "Tag-library growth" }).click();
  await page.getByRole("option", { name: "Closed library" }).click();
  await page.getByRole("heading", { name: "Auto tagging" }).locator("xpath=ancestor::section").getByRole("button", { name: "Save", exact: true }).click();
  await expect(autoTagSection.getByRole("checkbox", { name: "On", exact: true })).toBeChecked();
  await expect(autoTagSection.getByText("Needs rebuild", { exact: true }).locator("..").locator("strong")).toHaveText("0");
  await expect(autoTagSection.locator(".auto-tag-rebuild-notice")).toHaveCount(0);
  expect(previewRequests).toEqual([]);
});

test("candidate topics use the tag card grid and support manual promotion", async ({ page, request }, testInfo) => {
  const candidates = ["reproducibility", "Atmospheric Physics", "Computational chemistry"].map((name, index) => ({
    id: 7 + index, name, description: `Research about ${name}.`, status: "active",
    support_count: 4 + index, promoted_tag_id: null, aliases: [],
  }));
  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", { data: { candidates } });
  await page.addInitScript(() => localStorage.setItem("affogato-rss-reader:locale", "zh-CN"));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /设置/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "内容" }).click();
  const region = page.getByRole("region", { name: "候选主题", exact: true });
  await expect(region.locator(".tag-proposal-card")).toHaveCount(3);
  await expect(region.locator(".tag-proposals-list")).toHaveCSS("display", "grid");
  const desktopRows = await region.locator(".tag-proposal-card").evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().top));
  expect(new Set(desktopRows).size).toBe(1);
  expect(await region.evaluate((element) => element.parentElement?.lastElementChild === element)).toBe(true);
  await expect(region.getByText("4 个 Work 支持")).toBeVisible();
  await region.screenshot({ path: testInfo.outputPath("candidate-topics-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileRows = await region.locator(".tag-proposal-card").evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().top));
  expect(new Set(mobileRows).size).toBe(3);
  expect(await region.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await region.screenshot({ path: testInfo.outputPath("candidate-topics-mobile.png") });
  const promotionRequest = page.waitForRequest((request) => request.url().endsWith("/auto-tag/proposals/7/promote"));
  await region.getByRole("button", { name: "手动晋升 reproducibility", exact: true }).click();
  expect((await promotionRequest).method()).toBe("POST");
  await expect(region.locator(".tag-proposal-card")).toHaveCount(2);
  await expect(region.getByText("reproducibility", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: "选择标签 reproducibility", exact: true })).toBeVisible();
  await expect(page.locator(".auto-tag-rebuild-notice")).toHaveCount(0);
});

test("auto tagging previews and approves all 50 results without cleanup", async ({ page }) => {
  test.skip(!previewEnabled, "The preview workflow is dormant by default.");
  const cleanupRequests: string[] = [];
  const proposalRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/auto-tag/cleanup")) cleanupRequests.push(request.url());
    if (request.url().includes("/auto-tag/proposals")) proposalRequests.push(request.url());
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();
  await expect(page.getByText("Topic proposals and promotions", { exact: true })).toHaveCount(0);
  const previewButton = page.getByRole("button", { name: "Run 50-article preview" });
  await expect(previewButton).toBeEnabled();
  await expect(page.getByRole("button", { name: "Preview cleanup" })).toHaveCount(0);
  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
  await expect(page.getByText("Estimated full calls", { exact: true })).toBeVisible();
  await expect(page.locator(".tag-manager-card:not(.tag-proposal-card)")).toHaveCount(3);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Approve full run" }).click();
  await expect(page.getByRole("button", { name: "Approve full run" })).toHaveCount(0);
  await expect(page.locator(".translation-status-card").filter({ hasText: "Queued for tagging" }).locator("strong")).toHaveText("50");
  expect(cleanupRequests).toEqual([]);
  expect(proposalRequests.length).toBeGreaterThan(0);
  expect(proposalRequests.every((url) => new URL(url).searchParams.get("status") === "active")).toBe(true);
});

test("tag selection supports all, invert, cancel, and confirmed deletion on mobile", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();
  const selection = page.getByRole("group", { name: "Tag selection" });
  await page.getByRole("checkbox", { name: "Select tag QEC", exact: true }).check();
  await selection.getByRole("button", { name: "Invert selection" }).click();
  await expect(page.getByRole("checkbox", { name: "Select tag QEC", exact: true })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Select tag Condensed Matter", exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Select tag Quantum Computing", exact: true })).toBeChecked();
  await page.screenshot({ path: testInfo.outputPath("tag-selection-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await selection.getByRole("button", { name: "Select all" }).click();
  await expect(page.getByRole("checkbox", { name: /^Select tag / })).toHaveCount(3);
  await expect(selection.getByRole("button", { name: "Delete selected (3)" })).toBeEnabled();
  await selection.getByRole("button", { name: "Invert selection" }).click();
  await expect(selection.getByRole("button", { name: "Delete selected (0)" })).toBeDisabled();
  await page.getByRole("checkbox", { name: "Select tag QEC", exact: true }).check();
  await selection.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("tag-selection-mobile.png") });
  page.once("dialog", (dialog) => dialog.dismiss());
  await selection.getByRole("button", { name: "Delete selected (1)" }).click();
  await expect(page.locator(".tag-manager-card:not(.tag-proposal-card)")).toHaveCount(3);
  await expect(page.getByRole("checkbox", { name: "Select tag QEC", exact: true })).toBeChecked();
  page.once("dialog", (dialog) => dialog.accept());
  await selection.getByRole("button", { name: "Delete selected (1)" }).click();
  await expect(page.locator(".tag-manager-card:not(.tag-proposal-card)")).toHaveCount(2);
  await expect(page.getByRole("checkbox", { name: "Select tag QEC", exact: true })).toHaveCount(0);
  await expect(selection.getByRole("button", { name: "Delete selected (0)" })).toBeDisabled();
});

test("auto-tag workflow invalidates stale trials after policy and taxonomy changes", async ({ page, request }) => {
  test.skip(!previewEnabled, "The preview workflow is dormant by default.");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();
  const previewButton = page.getByRole("button", { name: "Run 50-article preview" });
  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
  await page.getByRole("combobox", { name: "Tag-library growth" }).click();
  await page.getByRole("option", { name: "Closed library" }).click();
  await page.getByRole("heading", { name: "Auto tagging" }).locator("xpath=ancestor::section").getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);
  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
  const createTagForm = page.locator(".category-manager__create");
  await createTagForm.getByPlaceholder("New tag").fill("New Taxonomy Topic");
  await createTagForm.getByRole("button").click();
  await expect(page.getByText("New Taxonomy Topic", { exact: true })).toBeVisible();
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);
  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
  await request.post("http://127.0.0.1:18081/api/v1/tags", { data: { name: "External taxonomy change" } });
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Approve full run" }).click();
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);
  await expect(previewButton).toBeEnabled();
});

test("tag merging invalidates the old trial without requesting cleanup", async ({ page }) => {
  test.skip(!previewEnabled, "The preview workflow is dormant by default.");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();
  const previewButton = page.getByRole("button", { name: "Run 50-article preview" });
  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
  const acceptMerge = async (dialog: Dialog) => {
    await dialog.accept(dialog.type() === "prompt" ? "3" : undefined);
  };
  page.on("dialog", acceptMerge);
  const mergeSource = page.locator(".tag-manager-card").filter({ hasText: "Condensed Matter" });
  await mergeSource.hover();
  await mergeSource.getByRole("button", { name: "Merge tag Condensed Matter", exact: true }).click();
  await expect(page.getByText("Merged into “Quantum Computing”.", { exact: true })).toBeVisible();
  await expect(page.locator(".tag-manager-card:not(.tag-proposal-card)")).toHaveCount(2);
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);
  await expect(previewButton).toBeEnabled();
  page.off("dialog", acceptMerge);
});

test("auto-tag rebuild and preview error recovery are clear in Chinese", async ({ page, request }) => {
  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", {
    data: { rebuild: true, outdated_count: 23, preview_mode: "failed" },
  });
  await page.addInitScript(() => localStorage.setItem("affogato-rss-reader:locale", "zh-CN"));
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /设置/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "内容" }).click();
  await expect(page.getByText("策略或标签库已变化", { exact: true })).toBeVisible();
  await expect(page.getByText(/23 篇文章的旧结果需要重建/)).toBeVisible();
  const previewButton = page.getByRole("button", { name: "运行 50 篇试跑" });
  await expect(page.getByRole("button", { name: "预览可清理标签" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "全选", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "反选", exact: true })).toBeVisible();
  if (!previewEnabled) {
    await expect(previewButton).toHaveCount(0);
    await expect(page.getByText(/系统会保留现有标签，不会自动重打这些文章/)).toBeVisible();
    await expect(page.getByRole("button", { name: "批准全量处理" })).toHaveCount(0);
    return;
  }
  await expect(previewButton).toBeEnabled();
  await previewButton.click();
  await expect(page.getByText("provider timeout", { exact: true })).toBeVisible();
  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", { data: { preview_mode: "success" } });
  await previewButton.click();
  await expect(page.getByText("试跑结果", { exact: true })).toBeVisible();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
});

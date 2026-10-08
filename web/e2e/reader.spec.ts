import { expect, test, type Dialog } from "@playwright/test";

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
  await expect(page.locator(".auto-tag-policy-note strong").getByText("Threshold growth", { exact: true })).toBeVisible();
  await expect(page.getByText("10 Work / 365 days", { exact: true })).toBeVisible();
  await expect(page.getByText(/Article data sent.*batch-local identifier, title, and summary/i)).toBeVisible();
  const previewButton = page.getByRole("button", { name: "Run 50-article preview" });
  await expect(previewButton).toBeDisabled();
  await page.getByRole("button", { name: "Preview cleanup" }).click();
  await expect(page.getByRole("checkbox", { name: /QEC/ })).toBeEnabled();
  await expect(page.locator(".auto-tag-cleanup-row").nth(1).getByRole("checkbox")).toBeEnabled();
  await expect(page.getByRole("button", { name: "Keep as manual" })).toHaveCount(2);
  await expect(previewButton).toBeDisabled();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Confirm cleanup review" }).click();
  await expect(previewButton).toBeEnabled();
  await previewButton.click();
  await expect(page.getByText("Preview result", { exact: true })).toBeVisible();
  await expect(page.getByText("Classified", { exact: true })).toBeVisible();
  await expect(page.getByText(/reproducibility · 91%/)).toBeVisible();
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

test("governed auto tagging keeps cleanup explicit and reuses all 50 preview results", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();

  await expect(page.getByText(/4 Work · Collecting/)).toBeVisible();
  await expect(page.getByText(/10 Work · Promoted → Quantum Computing/)).toBeVisible();
  const previewButton = page.getByRole("button", { name: "Run 50-article preview" });
  await expect(previewButton).toBeDisabled();
  await page.getByRole("button", { name: "Preview cleanup" }).click();
  await expect(page.locator(".auto-tag-cleanup-row")).toHaveCount(2);
  await expect(previewButton).toBeDisabled();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Confirm cleanup review" }).click();
  await expect(previewButton).toBeEnabled();

  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", {
    data: { invalidate_cleanup_review: true },
  });
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Keep as manual" }).first().click();
  await expect(previewButton).toBeDisabled();
  await expect(page.getByRole("button", { name: "Confirm cleanup review" })).toBeVisible();
  await expect(page.locator(".auto-tag-cleanup-row")).toHaveCount(2);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Confirm cleanup review" }).click();
  await expect(previewButton).toBeEnabled();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Keep as manual" }).first().click();
  await expect(page.locator(".auto-tag-cleanup-row")).toHaveCount(1);
  await expect(page.getByText(/Cleanup review confirmed/)).toBeVisible();
  await expect(previewButton).toBeEnabled();

  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
  await expect(page.getByText("Estimated full calls", { exact: true })).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Approve full run" }).click();
  await expect(page.getByRole("button", { name: "Approve full run" })).toHaveCount(0);
  await expect(page.locator(".translation-status-card").filter({ hasText: "Queued for tagging" }).locator("strong")).toHaveText("50");

  const acceptMerge = async (dialog: Dialog) => {
    await dialog.accept(dialog.type() === "prompt" ? "3" : undefined);
  };
  page.on("dialog", acceptMerge);
  const mergeSource = page.locator(".tag-manager-card").filter({ hasText: "Condensed Matter" });
  await mergeSource.hover();
  await mergeSource.getByRole("button", { name: "Merge tag Condensed Matter", exact: true }).click();
  await expect(page.locator(".tag-manager-card")).toHaveCount(2);
  await expect(page.getByText("Condensed Matter", { exact: true })).toHaveCount(0);
  page.off("dialog", acceptMerge);
});

test("auto-tag workflow invalidates stale trials after races and taxonomy changes", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();

  const previewButton = page.getByRole("button", { name: "Run 50-article preview" });
  await page.getByRole("button", { name: "Preview cleanup" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Confirm cleanup review" }).click();

  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", {
    data: { invalidate_cleanup_after_review_read: true },
  });
  await previewButton.click();
  await expect(previewButton).toBeDisabled();
  await expect(page.getByRole("button", { name: "Confirm cleanup review" })).toBeVisible();
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Confirm cleanup review" }).click();
  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);

  await page.getByRole("combobox", { name: "Tag-library growth" }).click();
  await page.getByRole("option", { name: "Closed library" }).click();
  await page.getByRole("heading", { name: "Auto tagging" }).locator("xpath=ancestor::section").getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);
  await expect(previewButton).toBeEnabled();

  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
  const createTagForm = page.locator(".category-manager__create");
  await createTagForm.getByPlaceholder("New tag").fill("New Taxonomy Topic");
  await createTagForm.getByRole("button").click();
  await expect(page.getByText("New Taxonomy Topic", { exact: true })).toBeVisible();
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);

  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", {
    data: { invalidate_cleanup_after_review_read: true },
  });
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Approve full run" }).click();
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);
  await expect(previewButton).toBeDisabled();
  await expect(page.getByRole("button", { name: "Confirm cleanup review" })).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Confirm cleanup review" }).click();
  await expect(previewButton).toBeEnabled();
});

test("a successful tag merge stays successful when cleanup refresh fails", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /Settings/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "Content" }).click();

  const previewButton = page.getByRole("button", { name: "Run 50-article preview" });
  await page.getByRole("button", { name: "Preview cleanup" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Confirm cleanup review" }).click();
  await previewButton.click();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);

  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", {
    data: { fail_cleanup_preview_once: true },
  });
  const acceptMerge = async (dialog: Dialog) => {
    await dialog.accept(dialog.type() === "prompt" ? "3" : undefined);
  };
  page.on("dialog", acceptMerge);
  const mergeSource = page.locator(".tag-manager-card").filter({ hasText: "Condensed Matter" });
  await mergeSource.hover();
  await mergeSource.getByRole("button", { name: "Merge tag Condensed Matter", exact: true }).click();
  await expect(page.getByText("Merged into “Quantum Computing”.", { exact: true })).toBeVisible();
  await expect(page.locator(".tag-manager-card__body > strong").getByText("Condensed Matter", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Preview result", { exact: true })).toHaveCount(0);
  await expect(previewButton).toBeDisabled();
  page.off("dialog", acceptMerge);

  await page.getByRole("button", { name: "Refresh cleanup preview" }).click();
  await expect(page.locator(".auto-tag-cleanup-row")).toHaveCount(1);
});

test("auto-tag rebuild and preview error recovery are clear in Chinese", async ({ page, request }) => {
  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", {
    data: { rebuild: true, outdated_count: 23, preview_mode: "failed", cleanup_empty: true },
  });
  await page.addInitScript(() => localStorage.setItem("affogato-rss-reader:locale", "zh-CN"));
  await page.goto("/");
  await page.locator(".sidebar__footer").getByRole("button", { name: /设置/ }).click();
  await page.locator(".settings-nav-card").filter({ hasText: "内容" }).click();

  await expect(page.getByText("策略或标签库已变化", { exact: true })).toBeVisible();
  await expect(page.getByText(/23 篇文章的旧结果需要重建/)).toBeVisible();
  const previewButton = page.getByRole("button", { name: "运行 50 篇试跑" });
  await expect(previewButton).toBeDisabled();
  await page.getByRole("button", { name: "预览可清理标签" }).click();
  await expect(page.getByText("没有需要清理的标签。", { exact: true })).toBeVisible();
  await expect(previewButton).toBeDisabled();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "确认已审阅清理预览" }).click();
  await expect(previewButton).toBeEnabled();
  await previewButton.click();
  await expect(page.getByText("provider timeout", { exact: true })).toBeVisible();

  await request.post("http://127.0.0.1:18081/__test__/auto-tag-scenario", {
    data: { preview_mode: "success" },
  });
  await previewButton.click();
  await expect(page.getByText("试跑结果", { exact: true })).toBeVisible();
  await expect(page.locator(".auto-tag-preview-list > div")).toHaveCount(50);
});

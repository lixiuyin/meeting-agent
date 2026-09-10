import { chromium, expect, test, type Page } from "@playwright/test";
import { installReadOnlyApiMock } from "./fixtures/mock-api";

// Generate fixture bytes with Chromium, then exercise the viewer in the selected browser.
// Firefox and WebKit do not implement page.pdf(), but both must render these same PDFs.
async function pdfFixture(html: string) {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.setContent(html);
    return await page.pdf({ preferCSSPageSize: true });
  } finally {
    await browser.close();
  }
}

const pageTexts = ["Introduction", "Another topic", "ChatGPT was released in 2022/11."];
async function installSourceMock(
  page: Page,
  kind = "pptx",
  pdf?: Buffer,
  texts = pageTexts,
  timelineDelay = 0,
) {
  const source = texts.join("\n\n");
  await installReadOnlyApiMock(page);
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/v1/memory")
      return json({
        items: [
          {
            key: "chatgpt.release_date",
            value: "2022/11",
            importance: 4,
            source: "auto_extracted",
            assertion_status: "confirmed",
            fact_type: "fact",
            updated_at: "2026-09-06T00:00:00Z",
            meeting_ids: [7],
            file_ids: [9],
            evidence_excerpt: "released in 2022/11",
            evidence_refs: [
              {
                meeting_id: 7,
                file_id: 9,
                source_revision: "rev-1",
                window_start: 0,
                window_end: source.length,
              },
            ],
          },
        ],
        total: 1,
        next_cursor: null,
      });
    if (path === "/api/v1/meetings/7")
      return json({
        id: 7,
        title: "Source meeting",
        status: "ready",
        files: [
          {
            id: 9,
            file_name: `source.${kind}`,
            file_type: kind,
            status: "ready",
            source_revisions: ["rev-1"],
          },
        ],
      });
    if (path === "/api/v1/meetings/7/transcript")
      return json({ transcript: source, files: [{ file_id: 9, content: source }] });
    if (path === "/api/v1/meetings/7/files/9/evidence-location") {
      const request = route.request().postDataJSON();
      const excerpt = request.excerpt || "";
      const index = request.page
        ? Number(request.page) - 1
        : texts.findIndex((text) => text.includes(excerpt));
      return json({
        status: index < 0 ? "not_found" : "exact",
        meeting_id: 7,
        file_id: 9,
        source_revision: "rev-1",
        parser_revision: "parser-1",
        evidence_id: "fixture-evidence",
        page: index < 0 ? null : index + 1,
        excerpt,
      });
    }
    if (path === "/api/v1/meetings/7/files/9/timeline") {
      if (timelineDelay) await new Promise((resolve) => setTimeout(resolve, timelineDelay));
      return json({
        kind: "pages",
        file_id: 9,
        file_name: `source.${kind}`,
        page_count: texts.length,
        pages: texts.map((text, index) => ({
          page_num: index + 1,
          text,
          heading: `Section ${index + 1}`,
        })),
      });
    }
    if (path.endsWith("/signed-url"))
      return json({
        url: "/api/v1/source-file",
        token: "fixture",
        expires_at: Math.floor(Date.now() / 1000) + 3600,
      });
    if (path === "/api/v1/source-file")
      return route.fulfill({ contentType: "application/pdf", body: pdf ?? "" });
    if (path === "/api/v1/meetings/404") return json({ message: "Source meeting not found" }, 404);
    return route.fallback();
  });
}

test("primary Memory source link resolves its actual slide even outside the meeting list", async ({
  page,
}) => {
  await installSourceMock(page);
  await page.goto("/memory");
  await page.getByRole("button", { name: "Open source material", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Slide 3 Content");
  await expect(page.getByRole("dialog")).toContainText("ChatGPT was released in 2022/11.");
  await expect(page.getByRole("dialog")).toHaveCount(1);
});

const scrollFixtureTexts = [
  "Introduction",
  Array.from({ length: 100 }, (_, i) => `Paragraph ${i}: source content.`).join("\n\n"),
  "A short third page.",
  "Fourth page.",
  "Fifth page.",
  "End of source.",
];
let scrollFixturePdf: Promise<Buffer> | undefined;

function getScrollFixturePdf() {
  scrollFixturePdf ??= pdfFixture(
    `<style>@page {size:800px 1000px; margin:0} body {margin:0} section {height:1000px;break-after:page}</style>${scrollFixtureTexts.map((_, i) => `<section>PDF page ${i + 1}</section>`).join("")}`,
  );
  return scrollFixturePdf;
}

async function makeScrollFixture(page: Page) {
  await installSourceMock(page, "pdf", await getScrollFixturePdf(), scrollFixtureTexts);
  await page.goto("/materials?meetingId=7&fileId=9&pageNumber=2");
  // PDF.js rasterization can exceed Playwright's assertion default in a busy
  // WebKit worker even though the document and its stable page anchors load.
  await expect(page.locator('[data-pdf-pane="pdf"] [data-page-num="2"] canvas')).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.locator('[data-pdf-pane="pdf"] [data-page-num]')).toHaveCount(
    scrollFixtureTexts.length,
  );
  await expect(page.locator('[data-pdf-pane="parsed"] [data-page-num]')).toHaveCount(
    scrollFixtureTexts.length,
  );
  await expectAligned(page, 2);
}

async function anchors(page: Page) {
  return page.locator("[data-pdf-pane]").evaluateAll((containers) =>
    containers.map((container) => {
      const edge = container.getBoundingClientRect().top + container.clientTop;
      const nodes = [...container.querySelectorAll<HTMLElement>("[data-page-num]")];
      if (!nodes.length)
        return {
          pane: (container as HTMLElement).dataset.pdfPane,
          page: 0,
          progress: Number.NaN,
          scroll: container.scrollTop,
        };
      let index = 0;
      for (let i = 0; i < nodes.length; i += 1) {
        if (nodes[i].getBoundingClientRect().top <= edge + 1) index = i;
        else break;
      }
      const rect = nodes[index].getBoundingClientRect();
      const span = nodes[index + 1]
        ? nodes[index + 1].getBoundingClientRect().top - rect.top
        : rect.height;
      return {
        pane: (container as HTMLElement).dataset.pdfPane,
        page: Number(nodes[index].dataset.pageNum),
        progress: (edge - rect.top) / span,
        offsetPixels: edge - rect.top,
        scroll: container.scrollTop,
      };
    }),
  );
}

async function expectAligned(page: Page, expectedPage?: number) {
  await expect
    .poll(
      async () => {
        const [left, right] = await anchors(page);
        const aligned =
          !!left &&
          !!right &&
          left.page === right.page &&
          Math.abs(left.progress - right.progress) < 0.02 &&
          (expectedPage === undefined || left.page === expectedPage);
        return aligned ? "aligned" : JSON.stringify({ left, right, expectedPage });
      },
      { timeout: 10_000 },
    )
    .toBe("aligned");
}

test("independent reading does not move the other pane and citation return realigns both", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await makeScrollFixture(page);
  await page.getByRole("switch", { name: "Synchronize reading position" }).click();
  const before = await anchors(page);
  await page.locator('[data-pdf-pane="parsed"]').hover();
  await page.mouse.wheel(0, 900);
  await expect
    .poll(async () => (await anchors(page))[1].scroll)
    .toBeGreaterThan(before[1].scroll + 500);
  await page.waitForTimeout(250);
  expect((await anchors(page))[0].scroll).toBeCloseTo(before[0].scroll, 0);
  await page.getByRole("button", { name: "Return to citation" }).click();
  await expectAligned(page, 2);
});

test("unequal-height pages stay aligned during bidirectional scrolling, zoom, resize and last-page jumps", async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await makeScrollFixture(page);
  const parsed = page.locator('[data-pdf-pane="parsed"]');
  const pdf = page.locator('[data-pdf-pane="pdf"]');
  await parsed.hover();
  await page.mouse.wheel(0, 900);
  await expectAligned(page, 2);
  await expect.poll(async () => (await anchors(page))[1].progress).toBeGreaterThan(0.1);
  const beforeClick = await anchors(page);
  await parsed.locator("p").filter({ hasText: "Paragraph 30:" }).click();
  await expectAligned(page, 2);
  expect((await anchors(page))[1].scroll).toBeCloseTo(beforeClick[1].scroll, 0);
  for (const [pane, delta] of [
    [pdf, 180],
    [parsed, -400],
    [pdf, 100],
    [parsed, 220],
  ] as const) {
    const previousScroll = await pane.evaluate((element) => element.scrollTop);
    await pane.hover();
    await page.mouse.wheel(0, delta);
    await expect
      .poll(async () =>
        Math.abs((await pane.evaluate((element) => element.scrollTop)) - previousScroll),
      )
      .toBeGreaterThan(1);
    await expectAligned(page);
  }
  const beforeZoom = await anchors(page);
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expectAligned(page, beforeZoom[0].page);
  await expect
    .poll(async () => Math.abs((await anchors(page))[0].progress - beforeZoom[0].progress))
    .toBeLessThan(0.02);
  await page.setViewportSize({ width: 1100, height: 800 });
  await expectAligned(page, beforeZoom[0].page);
  await parsed.getByRole("button", { name: "Go to page 6", exact: true }).click();
  await expectAligned(page, 6);
  await expect
    .poll(async () =>
      (await anchors(page)).every((anchor) => Math.abs(anchor.offsetPixels ?? Infinity) <= 1),
    )
    .toBe(true);
  const settled = await anchors(page);
  // A delayed assertion catches follower echoes and layout-induced snap-back.
  await page.waitForTimeout(700);
  expect(await anchors(page)).toEqual(settled);
  await pdf.hover();
  await page.mouse.wheel(0, 100);
  await expect.poll(async () => (await anchors(page))[0].progress).toBeGreaterThan(0.1);
  await expectAligned(page, 6);
  const endAnchor = (await anchors(page))[0];
  for (let i = 0; i < 3; i += 1)
    await page.getByRole("button", { name: "Zoom out", exact: true }).click();
  await expectAligned(page, 6);
  await expect
    .poll(async () => Math.abs((await anchors(page))[0].progress - endAnchor.progress))
    .toBeLessThan(0.02);
});

test("mobile pane unmounts retain the page and within-page reading anchor", async ({ page }) => {
  await makeScrollFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByText("Parsed", { exact: true }).click();
  const parsed = page.locator('[data-pdf-pane="parsed"]');
  await expect(parsed).toBeVisible();
  await parsed.hover();
  await page.mouse.wheel(0, 1000);
  await expect.poll(async () => (await anchors(page))[0].progress).toBeGreaterThan(0.1);
  const [position] = await anchors(page);
  await page.getByText("PDF", { exact: true }).click();
  await expect(page.locator('[data-pdf-pane="pdf"]')).toBeVisible();
  await expect
    .poll(async () => Math.abs((await anchors(page))[0].progress - position.progress))
    // One CSS-pixel scroll quantum can exceed 2% on a very short mobile page.
    .toBeLessThan(0.025);
  expect((await anchors(page))[0].page).toBe(position.page);
  await page.getByText("Parsed", { exact: true }).click();
  await expect
    .poll(async () => Math.abs((await anchors(page))[0].progress - position.progress))
    .toBeLessThan(0.02);
});

test("the Materials comparison entry uses the same bidirectional sync behavior", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await makeScrollFixture(page);
  await page.goto("/materials?meetingId=7");
  await page.getByRole("button", { name: "View document", exact: true }).click();
  await expect(page.getByText("PDF Comparison — source.pdf")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-pdf-pane="pdf"] [data-page-num="1"] canvas')).toBeVisible({
    timeout: 15_000,
  });
  // Keep real modal animations: a transformed first measurement used to stick
  // at ~20% width even after the animation completed (until closing/reopening).
  await expect
    .poll(async () =>
      page.locator('[data-pdf-pane="pdf"]').evaluate((pane) => {
        const canvas = pane.querySelector("canvas");
        return canvas ? canvas.getBoundingClientRect().width / pane.clientWidth : 0;
      }),
    )
    .toBeGreaterThan(0.9);
  await page.getByRole("button", { name: "Go to page 2", exact: true }).click();
  await expectAligned(page, 2);
  await page.locator('[data-pdf-pane="parsed"]').hover();
  await page.mouse.wheel(0, 900);
  await expect.poll(async () => (await anchors(page))[1].progress).toBeGreaterThan(0.1);
  await expectAligned(page, 2);
  const previous = (await anchors(page))[0].scroll;
  await page.locator('[data-pdf-pane="pdf"]').hover();
  await page.mouse.wheel(0, 150);
  await expect.poll(async () => (await anchors(page))[0].scroll).toBeGreaterThan(previous + 100);
  await expectAligned(page, 2);
});

test("a quotation below the fold lands visibly after delayed parsed content arrives", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const texts = [
    "Intro",
    Array.from({ length: 60 }, (_, i) => `Context paragraph ${i}.`).join("\n\n") +
      "\n\nChatGPT was released in 2022/11.\n\nMore context.",
  ];
  const pdf = await pdfFixture(
    "<style>@page {size:800px 1400px; margin:0} body {margin:0} section {height:1400px;break-after:page} p {margin:0;padding-top:1100px}</style><section>Introduction</section><section><p>ChatGPT was released in 2022/11.</p></section>",
  );
  await installSourceMock(page, "pdf", pdf, texts, 600);
  await page.goto("/memory");
  await page.getByRole("button", { name: "Open source material", exact: true }).click();
  const highlight = page.locator('[data-pdf-pane="parsed"] mark');
  await expect(highlight).toHaveText("released in 2022/11");
  await expect(highlight).toBeInViewport();
  await expect(
    page
      .locator(".react-pdf__Page__textContent span")
      .filter({ hasText: "ChatGPT was released in 2022/11." }),
  ).toBeInViewport();
  expect((await anchors(page)).map((anchor) => anchor.page)).toEqual([2, 2]);
  await page.waitForTimeout(800);
  await expect(highlight).toBeInViewport();
});

test("same-route citations can reopen the same file at another slide", async ({ page }) => {
  await installSourceMock(page);
  await page.goto("/materials?meetingId=7&fileId=9&slideNumber=3");
  await expect(page.getByRole("dialog")).toContainText("Slide 3 Content");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await page.evaluate(() => {
    history.pushState({}, "", "/materials?meetingId=7&fileId=9&slideNumber=2");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByRole("dialog")).toContainText("Slide 2 Content");
});

test("stale revisions do not silently open the current file", async ({ page }) => {
  await installSourceMock(page);
  await page.goto("/materials?meetingId=7&fileId=9&sourceRevision=old-revision");
  await expect(
    page.getByText("The cited source version has changed. Open the current file from Materials."),
  ).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("deleted sources report an actionable error instead of doing nothing", async ({ page }) => {
  await installSourceMock(page);
  await page.goto("/materials?meetingId=404&fileId=9");
  await expect(page.getByText("Source meeting not found")).toBeVisible();
});

test("PDF citation stays on its resolved page after placeholders finish rendering", async ({
  page,
}) => {
  const pdf = await pdfFixture(
    `<style>@page {size: 800px 450px; margin: 0} body {margin:0} section {height:450px; break-after:page}</style>${pageTexts.map((text) => `<section>${text}</section>`).join("")}`,
  );
  await installSourceMock(page, "pdf", pdf);
  await page.goto("/memory");
  await page.getByRole("button", { name: "Open source material", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("page 3");
  await expect(page.getByText("Page 3 / 3", { exact: true })).toBeVisible();
  await expect(page.locator('[data-pdf-pane="pdf"] [data-page-num="3"] canvas')).toBeVisible();
  await expect
    .poll(async () =>
      page.locator('[data-page-num="3"]').evaluateAll((elements) =>
        elements.every((element) => {
          const rect = element.getBoundingClientRect();
          return rect.top >= 0 && rect.top < innerHeight && rect.bottom > 0;
        }),
      ),
    )
    .toBe(true);
});

test("a 100-page PDF keeps bounded canvases while preserving distant page anchors", async ({
  page,
}) => {
  const texts = Array.from({ length: 100 }, (_, index) => `Page ${index + 1} evidence.`);
  const pdf = await pdfFixture(
    `<style>@page {size:800px 1000px;margin:0}body{margin:0}section{height:1000px;break-after:page}</style>${texts.map((text) => `<section>${text}</section>`).join("")}`,
  );
  await installSourceMock(page, "pdf", pdf, texts);
  await page.goto("/materials?meetingId=7&fileId=9&pageNumber=75");
  await expect(page.locator('[data-pdf-pane="pdf"] [data-page-num]')).toHaveCount(100);
  await expect(page.locator('[data-pdf-pane="pdf"] [data-page-num="75"] canvas')).toBeVisible();
  await expectAligned(page, 75);
  await expect.poll(() => page.locator(".react-pdf__Page__canvas").count()).toBeLessThan(10);
  await page.getByRole("button", { name: "Go to page 99", exact: true }).click();
  await expectAligned(page, 99);
  await expect(page.locator('[data-pdf-pane="pdf"] [data-page-num="99"] canvas')).toBeVisible();
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expectAligned(page, 99);
  await expect.poll(() => page.locator(".react-pdf__Page__canvas").count()).toBeLessThan(10);
});

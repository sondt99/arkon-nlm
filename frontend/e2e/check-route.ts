import { expect } from "./fixtures";
import type { Page } from "@playwright/test";
import type { VIEWPORTS } from "./routes";

/** Shared assertion used by every responsive check: no horizontal overflow,
 * no console/page errors, and a screenshot saved under __screenshots__ for
 * manual review. `screenshotDir` defaults to a slug derived from the route. */
export async function checkRoute(
  page: Page,
  route: string,
  viewport: (typeof VIEWPORTS)[number],
  screenshotDir?: string
) {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(err.message));

  await page.setViewportSize(viewport);
  await page.goto(route, { waitUntil: "load" });
  // Prefer networkidle (catches async data fetches), but don't let pages with
  // persistent connections (e.g. the force-graph view) stall the whole run.
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(200);

  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(
    overflow.scrollWidth,
    `horizontal overflow on ${route} @ ${viewport.width}px (scrollWidth=${overflow.scrollWidth} > clientWidth=${overflow.clientWidth})`
  ).toBeLessThanOrEqual(overflow.clientWidth + 1);

  const dir = screenshotDir ?? (route === "/" ? "root" : route.replace(/^\//, "").replace(/\//g, "_"));
  await page.screenshot({ path: `e2e/__screenshots__/${dir}/${viewport.width}.png`, fullPage: true });

  expect(consoleErrors, `console errors on ${route} @ ${viewport.width}px:\n${consoleErrors.join("\n")}`).toEqual([]);
}

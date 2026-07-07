import { test, expect } from "./fixtures";
import { STATIC_ROUTES, VIEWPORTS } from "./routes";

for (const route of STATIC_ROUTES) {
  for (const viewport of VIEWPORTS) {
    test(`${route} @ ${viewport.width}x${viewport.height} has no horizontal overflow or console errors`, async ({
      authedPage: page,
    }) => {
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

      const safeName = route === "/" ? "root" : route.replace(/^\//, "").replace(/\//g, "_");
      await page.screenshot({
        path: `e2e/__screenshots__/${safeName}/${viewport.width}.png`,
        fullPage: true,
      });

      expect(consoleErrors, `console errors on ${route} @ ${viewport.width}px:\n${consoleErrors.join("\n")}`).toEqual(
        []
      );
    });
  }
}

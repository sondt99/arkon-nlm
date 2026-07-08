import { test } from "./fixtures";
import { STATIC_ROUTES, VIEWPORTS } from "./routes";
import { checkRoute } from "./check-route";

for (const route of STATIC_ROUTES) {
  for (const viewport of VIEWPORTS) {
    test(`${route} @ ${viewport.width}x${viewport.height} has no horizontal overflow or console errors`, async ({
      authedPage: page,
    }) => {
      await checkRoute(page, route, viewport);
    });
  }
}

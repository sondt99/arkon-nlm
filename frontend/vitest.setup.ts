/**
 * `@testing-library/jest-dom/vitest` is the entry point that both extends vitest's
 * `expect` and declares the matcher types. Importing `/matchers` and calling
 * `expect.extend` by hand works at runtime but leaves `tsc` unaware of
 * `toBeInTheDocument`, so `npm run typecheck` fails on every assertion using one.
 */
import "@testing-library/jest-dom/vitest";

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

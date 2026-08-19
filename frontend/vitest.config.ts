import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  // No @vitejs/plugin-react: its optional @rolldown/plugin-babel dependency pulls
  // @babel/core@8 and conflicts with babel-plugin-react-compiler's @babel/core@7, so
  // installing it needs --legacy-peer-deps. The plugin only adds Fast Refresh, which
  // tests do not use — esbuild's automatic JSX runtime below is all that is required.
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "react",
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // e2e/ is Playwright's; running its specs under vitest would fail on the
    // @playwright/test imports.
    exclude: ["node_modules", ".next", "e2e"],
  },
});

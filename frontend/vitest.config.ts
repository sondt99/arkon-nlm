import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  // No @vitejs/plugin-react: its optional @rolldown/plugin-babel dependency pulls
  // @babel/core@8 and conflicts with babel-plugin-react-compiler's @babel/core@7, so
  // installing it needs --legacy-peer-deps. The plugin only adds Fast Refresh, which
  // tests do not use — esbuild's automatic JSX runtime below is all that is required.
  //
  // This `esbuild` block is why package.json holds `vite` at ^7 rather than letting
  // vitest 4's peer range (^6 || ^7 || ^8) float to 8. Vite 8 replaces esbuild with
  // rolldown/oxc and then IGNORES these options, warning:
  //   "Both esbuild and oxc options were set. oxc options will be used and esbuild
  //    options will be ignored."
  // The suite still passes there, because oxc's defaults happen to cover automatic JSX
  // — which is exactly the problem: the configuration below would become dead code that
  // reads as load-bearing. Moving to vite 8 means porting this to `oxc` deliberately,
  // not inheriting it from a resolver picking the newest allowed major.
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

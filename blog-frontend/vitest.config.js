import { defineConfig } from "vitest/config";

/**
 * Tests transform JSX with esbuild's automatic runtime rather than
 * @vitejs/plugin-react: the app is pinned to Vite 3 / plugin-react 2, while
 * Vitest bundles Vite 5, and mixing them triggers the plugin's "can't detect
 * preamble" failure. The app build itself is untouched and still uses the
 * plugin (see vite.config.js).
 */
export default defineConfig({
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    include: ["src/test/**/*.test.{js,jsx}"],
  },
});

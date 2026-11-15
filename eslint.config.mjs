import js from "@eslint/js";
import tseslint from "typescript-eslint";
export default tseslint.config(js.configs.recommended, ...tseslint.configs.recommended, {
  ignores: ["**/generated/**", "**/dist/**", "**/node_modules/**"],
}, { files: ["scripts/*.mjs", "playwright.config.ts", "tests/browser/**/*.ts"], languageOptions: { globals: { process: "readonly", console: "readonly" } } });

// Dedicated config for the i18n ratchet (scripts/lint-i18n.mjs).
//
// Deliberately SEPARATE from `next lint`, which has no config in this app
// and prompts interactively — wiring the ratchet into it would mean
// adopting a full Next lint preset in the same change and triaging every
// unrelated rule it turns on. This config does one job.
//
// Rule choice: eslint-plugin-i18next's no-literal-string. The dictionary
// here is a hand-rolled t() from @/lib/i18n, not react-intl or i18next, so
// the library-bound plugins (formatjs, react-intl) do not apply, and
// react/jsx-no-literals — already available via eslint-plugin-react —
// cannot tell a user-visible attribute from className or data-testid.
// This rule can, via its jsx-attributes include-list.
module.exports = {
  root: true,
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  plugins: ["i18next"],
  rules: {
    "i18next/no-literal-string": [
      "error",
      {
        // JSX text nodes plus the four attributes a user actually reads.
        mode: "jsx-only",
        "should-validate-template": false,
        // Only these four attributes are read by a user. Everything else
        // (className, variant, data-testid, href, keys, technical
        // constants) is excluded by NOT being included — the rule
        // validates the include-list and nothing more.
        "jsx-attributes": {
          include: ["title", "placeholder", "aria-label", "alt"],
        },
        message: "User-visible string must go through t() — see src/lib/i18n/dict.ts",
      },
    ],
  },
  ignorePatterns: [
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.spec.ts",
    "**/*.spec.tsx",
    "**/__tests__/**",
    "tests/**",
    "node_modules/**",
    ".next-fresh/**",
    ".next-dev/**",
  ],
};

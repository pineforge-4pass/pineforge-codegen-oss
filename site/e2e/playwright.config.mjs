// Playwright config for the E2E suite. Run it through `npm run e2e` (e2e/run.mjs), which starts the
// site, the Stripe and Resend fakes and passes their URLs in E2E_* env vars.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const siteDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const desktop = { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } };
const mobile = {
  ...devices["Desktop Chrome"],
  viewport: { width: 390, height: 844 },
  screen: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
};

export default defineConfig({
  testDir: path.join(siteDir, "e2e", "specs"),
  testMatch: /.*\.spec\.mjs$/,
  outputDir: path.join(siteDir, "test-results"),
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: Number(process.env.E2E_WORKERS ?? 4),
  reporter: [["list"], ["html", { outputFolder: path.join(siteDir, "playwright-report"), open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "en-US",
    timezoneId: "UTC",
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
  },
  projects: [
    { name: "desktop", use: desktop },
    // Home (decision guide), plans and checkout also run on a phone-sized viewport.
    { name: "mobile", use: mobile, testMatch: /(guide|plans-checkout)\.spec\.mjs$/ },
  ],
});

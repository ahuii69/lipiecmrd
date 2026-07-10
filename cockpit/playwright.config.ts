import { defineConfig, devices } from "@playwright/test";

const isCi = Boolean(process.env.CI);
const realHub = process.env.PLAYWRIGHT_REAL_HUB === "1";

export default defineConfig({
    testDir: "./e2e",
    testIgnore: realHub ? [] : ["**/real-hub.spec.ts"],
    fullyParallel: false,
    forbidOnly: isCi,
    retries: isCi ? 1 : 0,
    workers: 1,
    reporter: [
        ["list"],
        ["html", { open: "never", outputFolder: "playwright-report" }],
    ],
    timeout: 60_000,
    expect: { timeout: 15_000 },
    use: {
        baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3100",
        trace: isCi ? "retain-on-failure" : "on-first-retry",
        screenshot: "only-on-failure",
        video: "retain-on-failure",
        viewport: { width: 1400, height: 900 },
    },
    projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
    webServer: {
        command: "npm run build && PORT=3100 npm start",
        url: "http://127.0.0.1:3100/user",
        reuseExistingServer: process.env.CI ? false : true,
        timeout: 180_000,
    },
});

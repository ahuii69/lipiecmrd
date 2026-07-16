import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

const isCi = Boolean(process.env.CI);
const realHub = process.env.PLAYWRIGHT_REAL_HUB === "1";

function resolveChromiumExecutable(): string | undefined {
    const fromEnv = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?.trim();
    if (fromEnv && existsSync(fromEnv)) return fromEnv;
    for (const candidate of [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
    ]) {
        if (existsSync(candidate)) return candidate;
    }
    return undefined;
}

const chromiumExecutable = resolveChromiumExecutable();

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
    timeout: 120_000,
    expect: { timeout: 30_000 },
    use: {
        baseURL: process.env.PLAYWRIGHT_BASE_URL || (realHub ? "http://127.0.0.1:3001" : "http://127.0.0.1:3100"),
        headless: true,
        trace: isCi ? "retain-on-failure" : "on-first-retry",
        screenshot: "only-on-failure",
        video: "off",
        viewport: { width: 1400, height: 900 },
        launchOptions: chromiumExecutable
            ? {
                  executablePath: chromiumExecutable,
                  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
              }
            : {
                  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
              },
    },
    projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
    webServer: realHub
        ? undefined
        : {
              command: "npm run build && PORT=3100 npm start",
              url: "http://127.0.0.1:3100/user",
              reuseExistingServer: process.env.CI ? false : true,
              timeout: 180_000,
          },
});

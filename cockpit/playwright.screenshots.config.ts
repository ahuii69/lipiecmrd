import { defineConfig, devices } from "@playwright/test";

/** Screenshots against already running cockpit (default :3001). */
export default defineConfig({
    testDir: "./e2e",
    testMatch: /frontend-v3-screenshot.*\.spec\.ts/,
    fullyParallel: false,
    workers: 1,
    reporter: [["list"]],
    timeout: 120_000,
    use: {
        baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3001",
        viewport: { width: 1920, height: 1080 },
    },
    projects: [
        {
            name: "chromium",
            use: {
                ...devices["Desktop Chrome"],
                launchOptions: {
                    executablePath:
                        process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ||
                        "/snap/bin/chromium",
                },
            },
        },
    ],
});

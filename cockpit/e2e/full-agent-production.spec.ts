import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const OUT = path.resolve(__dirname, "../../artifacts/full-agent-verify/screenshots");
const USER = process.env.SCREENSHOT_USER || "screenshot_v3";
const PASS = process.env.SCREENSHOT_PASS || "ScreenshotV3!test99";

test.describe("Full agent production E2E", () => {
    test.describe.configure({ mode: "serial", timeout: 180_000 });

    test.beforeAll(async () => {
        await mkdir(OUT, { recursive: true });
    });

    test("login, chat stream, mobile composer", async ({ page }) => {
        await page.goto("/login?next=/");
        await page.getByPlaceholder("np. jan.kowalski").fill(USER);
        await page.getByPlaceholder("••••••••").fill(PASS);
        await page.getByRole("button", { name: /zaloguj/i }).click();
        await page.waitForURL("/", { timeout: 30_000 });
        await expect(page.getByTestId("user-shell")).toBeVisible();
        await page.screenshot({ path: path.join(OUT, "01-login-home.png"), fullPage: true });

        const input = page.getByTestId("user-chat-input");
        await expect(input).toBeEnabled({ timeout: 30_000 });
        await input.fill("Powiedz krótko, kim jesteś?");
        await page.getByTestId("user-chat-send").click();

        const assistant = page
            .locator('[data-testid="chat-message"][data-role="assistant"]')
            .last();
        await expect(assistant).toBeVisible({ timeout: 30_000 });
        await expect(assistant).toHaveAttribute("data-streaming", "false", {
            timeout: 120_000,
        });
        const text = (await assistant.innerText()).trim();
        expect(text.length).toBeGreaterThan(10);
        await page.screenshot({ path: path.join(OUT, "02-chat-response.png"), fullPage: true });

        await page.setViewportSize({ width: 390, height: 844 });
        await input.fill("Druga linia\nTrzecia linia");
        await page.screenshot({ path: path.join(OUT, "03-mobile-composer.png"), fullPage: true });
    });
});

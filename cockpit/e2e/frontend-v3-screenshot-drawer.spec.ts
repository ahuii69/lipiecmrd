import { expect, test } from "@playwright/test";
import path from "node:path";

const OUT = path.resolve(__dirname, "../../artifacts/frontend-v3");

test("recapture desktop drawer after overlay fix", async ({ page }) => {
    await page.goto("/login?next=/", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("np. jan.kowalski").fill("screenshot_v3");
    await page.getByPlaceholder("••••••••").fill("ScreenshotV3!test99");
    await page.getByRole("button", { name: /zaloguj/i }).click();
    await page.waitForURL("/", { timeout: 30_000 });
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.getByTestId("open-memory-drawer").click();
    await expect(page.getByTestId("memory-drawer")).toBeVisible();
    await page.waitForTimeout(400);
    await page.screenshot({
        path: path.join(OUT, "04-desktop-drawer-memory.png"),
    });
});

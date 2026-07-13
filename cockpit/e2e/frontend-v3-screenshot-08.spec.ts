import { expect, test } from "@playwright/test";
import path from "node:path";

const OUT = path.resolve(__dirname, "../../artifacts/frontend-v3");

test("capture 08 mobile multiline composer", async ({ page }) => {
    await page.goto("/login?next=/", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("np. jan.kowalski").fill("screenshot_v3");
    await page.getByPlaceholder("••••••••").fill("ScreenshotV3!test99");
    await page.getByRole("button", { name: /zaloguj/i }).click();
    await page.waitForURL("/", { timeout: 30_000 });
    await page.setViewportSize({ width: 390, height: 844 });
    const input = page.getByTestId("user-chat-input");
    await expect(input).toBeEnabled({ timeout: 30_000 });
    await input.fill(
        "Pierwsza linia wiadomości\nDruga linia z kontekstem\nTrzecia linia testu composera",
    );
    await page.waitForTimeout(500);
    await page.screenshot({
        path: path.join(OUT, "08-mobile-composer-multiline.png"),
    });
});

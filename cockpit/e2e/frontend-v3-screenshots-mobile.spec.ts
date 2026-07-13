import { expect, test } from "@playwright/test";
import path from "node:path";

const OUT = path.resolve(__dirname, "../../artifacts/frontend-v3");
const USER = process.env.SCREENSHOT_USER || "screenshot_v3";
const PASS = process.env.SCREENSHOT_PASS || "ScreenshotV3!test99";

async function login(page: import("@playwright/test").Page) {
    await page.goto("/login?next=/", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("np. jan.kowalski").fill(USER);
    await page.getByPlaceholder("••••••••").fill(PASS);
    await page.getByRole("button", { name: /zaloguj/i }).click();
    await page.waitForURL("/", { timeout: 30_000 });
    await expect(page.getByTestId("user-shell")).toBeVisible();
}

test("capture mobile screenshots 5-8", async ({ page }) => {
    test.setTimeout(180_000);
    await login(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTestId("user-sidebar-toggle").click();
    await page.getByTestId("user-new-session").click({ force: true });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(OUT, "05-mobile-empty-390x844.png") });

    const input = page.getByTestId("user-chat-input");
    await expect(input).toBeEnabled({ timeout: 30_000 });
    await input.fill("Potwierdź layout mobile V3 — jedno zdanie.");
    await page.getByTestId("user-chat-send").click();
    const assistant = page
        .locator('[data-testid="chat-message"][data-role="assistant"]')
        .last();
    await expect(assistant).toBeVisible({ timeout: 20_000 });
    await expect(assistant).toHaveAttribute("data-streaming", "false", {
        timeout: 90_000,
    });
    await page.screenshot({ path: path.join(OUT, "06-mobile-conversation-390x844.png") });

    await page.getByTestId("user-sidebar-toggle").click();
    await expect(page.getByTestId("user-sidebar")).toBeVisible();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, "07-mobile-sidebar-open.png") });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);

    await input.click();
    await input.fill(
        "Pierwsza linia wiadomości\nDruga linia z kontekstem\nTrzecia linia testu composera",
    );
    await page.waitForTimeout(500);
    await page.screenshot({
        path: path.join(OUT, "08-mobile-composer-multiline.png"),
    });
});

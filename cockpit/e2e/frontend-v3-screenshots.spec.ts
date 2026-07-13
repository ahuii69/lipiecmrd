import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
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

async function seedDemoConversation(page: import("@playwright/test").Page) {
    const input = page.getByTestId("user-chat-input");
    await expect(input).toBeEnabled({ timeout: 30_000 });
    await input.fill(
        "Krótko: potwierdź że widzisz nowy layout chatu V3 — jedno zdanie.",
    );
    await page.getByTestId("user-chat-send").click();
    await expect(
        page.locator('[data-testid="chat-message"][data-role="user"]').first(),
    ).toBeVisible({ timeout: 15_000 });
    const assistant = page
        .locator('[data-testid="chat-message"][data-role="assistant"]')
        .last();
    await expect(assistant).toBeVisible({ timeout: 20_000 });
    await expect(assistant).toHaveAttribute("data-streaming", "false", {
        timeout: 90_000,
    });
}

test.describe("Frontend V3 screenshots", () => {
    test.describe.configure({ mode: "serial", timeout: 300_000 });

    test.beforeAll(async () => {
        await mkdir(OUT, { recursive: true });
    });

    test("capture 8 required screenshots", async ({ page }) => {
        await login(page);

        await page.setViewportSize({ width: 1920, height: 1080 });
        await page.getByTestId("user-new-session").click();
        await page.waitForTimeout(800);
        await page.screenshot({
            path: path.join(OUT, "01-desktop-empty-1920x1080.png"),
        });

        await seedDemoConversation(page);
        await page.screenshot({
            path: path.join(OUT, "02-desktop-conversation-1920x1080.png"),
        });

        await page.evaluate(() => {
            const raw = localStorage.getItem("aihub-chat-ui-v3");
            if (!raw) return;
            const parsed = JSON.parse(raw) as { state?: Record<string, unknown> };
            const st = parsed.state ?? parsed;
            st.sidebarCollapsed = false;
            st.sidebarMobileOpen = false;
            localStorage.setItem(
                "aihub-chat-ui-v3",
                JSON.stringify({ ...parsed, state: st }),
            );
        });
        await page.reload({ waitUntil: "domcontentloaded" });
        await expect(page.getByTestId("user-shell")).toBeVisible();
        await page.screenshot({
            path: path.join(OUT, "03-desktop-sidebar-open.png"),
        });

        await page.getByTestId("open-memory-drawer").click();
        await expect(page.getByTestId("memory-drawer")).toBeVisible();
        await page.waitForTimeout(400);
        await page.screenshot({
            path: path.join(OUT, "04-desktop-drawer-memory.png"),
        });
        await page.keyboard.press("Escape");

        await page.setViewportSize({ width: 390, height: 844 });
        await page.getByTestId("user-sidebar-toggle").click();
        await page.getByTestId("user-new-session").click({ force: true });
        await page.keyboard.press("Escape");
        await page.waitForTimeout(600);
        await page.screenshot({
            path: path.join(OUT, "05-mobile-empty-390x844.png"),
        });

        await seedDemoConversation(page);
        await page.screenshot({
            path: path.join(OUT, "06-mobile-conversation-390x844.png"),
        });

        await page.getByTestId("user-sidebar-toggle").click();
        await expect(page.getByTestId("user-sidebar")).toBeVisible();
        await page.waitForTimeout(400);
        await page.screenshot({
            path: path.join(OUT, "07-mobile-sidebar-open.png"),
        });
        await page.keyboard.press("Escape");

        const input = page.getByTestId("user-chat-input");
        await input.click();
        await input.fill(
            "Pierwsza linia wiadomości\nDruga linia z kontekstem\nTrzecia linia testu composera",
        );
        await page.waitForTimeout(500);
        await page.screenshot({
            path: path.join(OUT, "08-mobile-composer-multiline.png"),
        });
    });
});

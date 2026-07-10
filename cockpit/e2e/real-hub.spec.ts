import { expect, test } from "@playwright/test";

/**
 * E2E przeciwko prawdziwemu backendowi — bez mockHub.
 * Uruchomienie: `npm run test:e2e:real` (ustawia PLAYWRIGHT_REAL_HUB=1).
 * Wymaga: działający hub (LLM + klucz), cockpit/.env z AIHUB_BASE_URL i kluczem zgodnym z backendem.
 * Szczegóły: ../../docs/DEMO_REAL_E2E.md
 */
test.describe("Real hub — /user bez mockHub", () => {
    test.describe.configure({ mode: "serial", timeout: 180_000 });

    test.beforeEach(async ({ page }) => {
        await page.addInitScript(() => {
            try {
                localStorage.clear();
            } catch {
                /* ignore */
            }
        });
    });

    test("UI → BFF → backend → odpowiedź modelu (jedna wiadomość)", async ({
        page,
    }) => {
        await page.goto("/user");
        await expect(page.getByTestId("user-shell")).toBeVisible();

        const prompt =
            "Odpowiedz jednym krótkim zdaniem po polsku: co to jest test integracyjny?";

        await page.getByTestId("user-chat-input").fill(prompt);
        await page.getByTestId("user-chat-send").click();

        await expect(
            page.locator('[data-testid="chat-message"][data-role="user"]').first(),
        ).toContainText("test integracyjny", { timeout: 15_000 });

        const assistant = page
            .locator('[data-testid="chat-message"][data-role="assistant"]')
            .last();

        await expect(assistant).toBeVisible({ timeout: 20_000 });

        // mockHub dokleja "Echo: " — jego obecność na początku = fałszywy dowód realnego LLM
        await expect(assistant).not.toContainText("Echo: Odpowiedz jednym", {
            timeout: 10_000,
        });

        await expect(assistant).toHaveAttribute("data-streaming", "false", {
            timeout: 120_000,
        });

        const text = (await assistant.innerText()).trim();
        expect(text.length).toBeGreaterThan(12);
        expect(text.startsWith("Echo:")).toBe(false);
    });
});

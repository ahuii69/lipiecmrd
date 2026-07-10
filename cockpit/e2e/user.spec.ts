/**
 * E2E z mockHub — deterministyczne, bez prawdziwego LLM ani żywego hubu.
 * Dowód realnego stacku (przeglądarka → BFF → backend → model): `real-hub.spec.ts`
 * oraz `npm run test:e2e:real` — opis: ../../docs/DEMO_REAL_E2E.md
 */
import { expect, test } from "@playwright/test";

import {
    installHubMocks,
    type TurnBody,
} from "./helpers/mockHub";

test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
        try {
            localStorage.clear();
        } catch {
            /* ignore */
        }
    });
});

test("A: zwykły chat — wiadomość user i odpowiedź asystenta", async ({
    page,
}) => {
    const captured: TurnBody[] = [];
    await installHubMocks(page, { capturedTurns: captured });

    await page.goto("/user");
    await expect(page.getByTestId("user-shell")).toBeVisible();

    await page.getByTestId("user-chat-input").fill("Witaj E2E");
    await page.getByTestId("user-chat-send").click();

    await expect(
        page.locator('[data-testid="chat-message"][data-role="user"]'),
    ).toHaveCount(1);
    await expect(
        page.locator('[data-testid="chat-message"][data-role="user"]').first(),
    ).toContainText("Witaj E2E");

    await expect
        .poll(async () =>
            page
                .locator('[data-testid="chat-message"][data-role="assistant"]')
                .last()
                .innerText(),
        )
        .toContain("Echo: Witaj E2E");

    expect(captured.length).toBeGreaterThanOrEqual(1);
    expect(captured[0].message).toBe("Witaj E2E");
});

test("B: streaming — wiele delt SSE składa odpowiedź", async ({ page }) => {
    const captured: TurnBody[] = [];
    await installHubMocks(page, { capturedTurns: captured, slowStream: true });

    await page.goto("/user");
    await page.getByTestId("user-chat-input").fill("stream");
    await page.getByTestId("user-chat-send").click();

    const assistant = page
        .locator('[data-testid="chat-message"][data-role="assistant"]')
        .last();

    await expect(assistant).toBeVisible();
    await expect(assistant).toContainText("SLOW", { timeout: 15_000 });
    await expect(assistant).toHaveAttribute("data-streaming", "false");

    await expect(page.getByTestId("user-shell")).toBeVisible();
});

test("G: upload obrazu — draft, attached_file_ids, chip załącznik", async ({
    page,
}) => {
    const captured: TurnBody[] = [];
    await installHubMocks(page, {
        capturedTurns: captured,
        uploadJsonOverride: {
            file_id: "f_img",
            filename: "shot.png",
            content_type: "image/png",
            status: "image",
            extracted_text_preview: "(plik obrazkowy)",
        },
    });

    await page.goto("/user");
    await page.getByTestId("user-chat-attach").click();
    await page.getByTestId("user-chat-file-input").setInputFiles({
        name: "shot.png",
        mimeType: "image/png",
        buffer: Buffer.from([
            0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
        ]),
    });
    await expect(page.getByText("shot.png")).toBeVisible();
    await expect(
        page.locator("span").filter({ hasText: /^obraz$/ }),
    ).toBeVisible({ timeout: 10_000 });

    await page.getByTestId("user-chat-input").fill("co jest na obrazku?");
    await page.getByTestId("user-chat-send").click();

    await expect
        .poll(() => captured.filter((b) => Array.isArray(b.attached_file_ids)).length)
        .toBeGreaterThanOrEqual(1);
    const withImg = captured.find(
        (b) =>
            Array.isArray(b.attached_file_ids) &&
            (b.attached_file_ids as string[]).includes("f_img"),
    );
    expect(withImg).toBeTruthy();

    const asst = page
        .locator('[data-testid="chat-message"][data-role="assistant"]')
        .last();
    await expect(asst).toContainText("Echo:", { timeout: 15_000 });
    await expect(asst.getByText("załącznik", { exact: true })).toBeVisible();
});

test("I: sidebar zwinięty na starcie, toggle rozwija (desktop)", async ({
    page,
}) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await installHubMocks(page, { capturedTurns: [] });

    await page.goto("/user");
    const sidebar = page.getByTestId("user-sidebar");
    await expect(sidebar).toHaveAttribute("data-sidebar-state", "closed");

    await page.getByTestId("user-sidebar-toggle").click();
    await expect(sidebar).toHaveAttribute("data-sidebar-state", "open");
});

test("I2: szerokość 320 — brak poziomego overflow", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await installHubMocks(page, { capturedTurns: [] });
    await page.goto("/user");
    const docW = await page.evaluate(() => document.documentElement.scrollWidth);
    const innerW = await page.evaluate(() => window.innerWidth);
    expect(docW).toBeLessThanOrEqual(innerW + 8);
});

test("I3: typowe szerokości — brak poziomego overflow", async ({ page }) => {
    await installHubMocks(page, { capturedTurns: [] });
    for (const w of [412, 768, 1024]) {
        await page.setViewportSize({ width: w, height: 800 });
        await page.goto("/user");
        const docW = await page.evaluate(
            () => document.documentElement.scrollWidth,
        );
        const innerW = await page.evaluate(() => window.innerWidth);
        expect(docW, `viewport ${w}px`).toBeLessThanOrEqual(innerW + 8);
        await expect(page.getByTestId("user-chat-send")).toBeVisible();
    }
});

test("J: transkrypt z GET /chat/session/…/history po wejściu na /user", async ({
    page,
}) => {
    await installHubMocks(page, { capturedTurns: [] });
    await page.route("**/api/aihub/chat/session/s_initial/history**", async (route: Route) => {
        if (route.request().method() !== "GET") {
            await route.continue();
            return;
        }
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                messages: [
                    {
                        id: "m_hist_e2e",
                        role: "user",
                        content: "Z historii API",
                        created_at: new Date().toISOString(),
                    },
                ],
            }),
        });
    });

    await page.goto("/user");
    await expect(
        page.locator('[data-testid="chat-message"][data-role="user"]').first(),
    ).toContainText("Z historii API", { timeout: 15_000 });
});

test("H: mobile 390 — mikrofon widoczny, brak overflow", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installHubMocks(page, { capturedTurns: [] });

    await page.goto("/user");
    await expect(page.getByTestId("user-chat-mic")).toBeVisible();
    await page.getByTestId("user-chat-input").click();
    const fs = await page.evaluate(() => {
        const el = document.querySelector(
            '[data-testid="user-chat-input"]',
        ) as HTMLElement | null;
        return el ? Number.parseFloat(getComputedStyle(el).fontSize) : 0;
    });
    expect(fs).toBeGreaterThanOrEqual(16);
    const docW = await page.evaluate(() => document.documentElement.scrollWidth);
    const innerW = await page.evaluate(() => window.innerWidth);
    expect(docW).toBeLessThanOrEqual(innerW + 8);
});

test("F: mobile 375 — layout, kolejność history przy drugiej turze", async ({
    page,
}) => {
    await page.setViewportSize({ width: 375, height: 812 });
    const captured: TurnBody[] = [];
    await installHubMocks(page, { capturedTurns: captured });

    await page.goto("/user");
    await expect(page.getByTestId("user-shell")).toBeVisible();

    await page.getByTestId("user-chat-input").fill("pierwsza");
    await page.getByTestId("user-chat-send").click();
    await expect(
        page.locator('[data-testid="chat-message"][data-role="assistant"]').last(),
    ).toContainText("Echo:", { timeout: 15_000 });

    await page.getByTestId("user-chat-input").fill("druga");
    await page.getByTestId("user-chat-send").click();
    await expect(
        page.locator('[data-testid="chat-message"][data-role="assistant"]').last(),
    ).toContainText("Echo:", { timeout: 15_000 });

    expect(captured.length).toBeGreaterThanOrEqual(2);
    const lastBody = captured[captured.length - 1];
    const hist = lastBody.history as unknown[] | undefined;
    expect(Array.isArray(hist)).toBe(true);
    expect((hist as unknown[]).length).toBeGreaterThanOrEqual(2);

    const docW = await page.evaluate(() => document.documentElement.scrollWidth);
    const innerW = await page.evaluate(() => window.innerWidth);
    expect(docW).toBeLessThanOrEqual(innerW + 8);

    await expect(page.getByTestId("user-message-scroll")).toBeVisible();
    await expect(page.getByTestId("user-chat-input")).toBeVisible();
});

test("B2: Stop podczas oczekiwania nie wywala UI", async ({ page }) => {
    const captured: TurnBody[] = [];
    await installHubMocks(page, {
        capturedTurns: captured,
        stallFirstTurnMs: 4000,
    });

    await page.goto("/user");
    await page.getByTestId("user-chat-input").fill("stop test");
    await page.getByTestId("user-chat-send").click();

    const stop = page.getByTestId("user-chat-stop");
    await expect(stop).toBeVisible({ timeout: 10_000 });
    await stop.click();

    await expect(page.getByTestId("user-shell")).toBeVisible();
    await expect(page.getByTestId("user-chat-input")).toBeEnabled({
        timeout: 15_000,
    });
});

test("C: upload pliku, draft, wysyłka z attached_file_ids i odpowiedź", async ({
    page,
}) => {
    const captured: TurnBody[] = [];
    await installHubMocks(page, { capturedTurns: captured });

    await page.goto("/user");

    await page.getByTestId("user-chat-attach").click();
    await page
        .getByTestId("user-chat-file-input")
        .setInputFiles({
            name: "e2e.txt",
            mimeType: "text/plain",
            buffer: Buffer.from("hello e2e file"),
        });

    await expect(page.getByText("e2e.txt")).toBeVisible();
    await expect(
        page.locator("span").filter({ hasText: /^gotowy$/ }),
    ).toBeVisible();

    await page.getByTestId("user-chat-input").fill("Co jest w pliku?");
    await page.getByTestId("user-chat-send").click();

    await expect
        .poll(() => captured.filter((b) => Array.isArray(b.attached_file_ids)).length)
        .toBeGreaterThanOrEqual(1);
    const withFiles = captured.find(
        (b) =>
            Array.isArray(b.attached_file_ids) &&
            (b.attached_file_ids as string[]).includes("f_e2e"),
    );
    expect(withFiles).toBeTruthy();

    await expect(
        page.locator('[data-testid="chat-message"][data-role="assistant"]').last(),
    ).toContainText("Echo:");
});

test("D: retry zachowuje attached_file_ids", async ({ page }) => {
    const captured: TurnBody[] = [];
    // Pierwszy turn z plikiem kończy się błędem — retry musi wysłać te same attached_file_ids.
    await installHubMocks(page, { capturedTurns: captured, failTurnAt: 1 });

    await page.goto("/user");

    await page.getByTestId("user-chat-attach").click();
    await page.getByTestId("user-chat-file-input").setInputFiles({
        name: "retry.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("x"),
    });
    await expect(
        page.locator("span").filter({ hasText: /^gotowy$/ }),
    ).toBeVisible({ timeout: 10_000 });

    await page.getByTestId("user-chat-input").fill("z plikiem");
    await page.getByTestId("user-chat-send").click();

    await expect(page.getByTestId("user-chat-retry")).toBeEnabled({
        timeout: 15_000,
    });

    const firstBody = captured[0];
    expect(firstBody.message).toBe("z plikiem");
    expect(firstBody.attached_file_ids).toEqual(["f_e2e"]);

    captured.length = 0;
    await page.getByTestId("user-chat-retry").click();

    await expect
        .poll(() => captured.length, { timeout: 15_000 })
        .toBeGreaterThanOrEqual(1);
    const retryBody = captured[captured.length - 1];
    expect(retryBody.message).toBe("z plikiem");
    expect(retryBody.attached_file_ids).toEqual(["f_e2e"]);

    await expect(
        page.locator('[data-testid="chat-message"][data-role="assistant"]').last(),
    ).toContainText("Echo:", { timeout: 15_000 });
});

test("E: sesje — nowa, auto-tytuł, rename, switch, delete", async ({
    page,
}) => {
    const captured: TurnBody[] = [];
    await installHubMocks(page, { capturedTurns: captured });

    await page.goto("/user");

    await page.getByTestId("user-chat-input").fill("pierwsza sesja wiadomość test");
    await page.getByTestId("user-chat-send").click();
    await expect(
        page.locator('[data-testid="chat-message"][data-role="assistant"]').last(),
    ).toContainText("Echo:", { timeout: 15_000 });

    await page.getByTestId("user-sidebar-toggle").click();
    await expect(page.getByTestId("user-new-session")).toBeVisible();
    await page.getByTestId("user-new-session").click();
    await page.keyboard.press("Escape");

    await page.getByTestId("user-chat-input").fill("druga sesja o zupie i kodzie");
    await page.getByTestId("user-chat-send").click();
    await expect(
        page.locator('[data-testid="chat-message"][data-role="assistant"]').last(),
    ).toContainText("Echo:", { timeout: 15_000 });

    await expect
        .poll(async () => page.getByTestId("user-header-title").innerText())
        .not.toBe("Nowa rozmowa");

    const secondTitle = await page.getByTestId("user-header-title").innerText();
    expect(secondTitle.length).toBeGreaterThan(0);

    // Lista sortowana po updatedAt malejąco — aktywna sesja z ostatnią wiadomością jest pierwsza.
    await page.getByTestId("user-sidebar-toggle").click();
    const secondRow = page.getByTestId("user-session-item").first();
    const secondSessionId = await secondRow.getAttribute("data-session-id");
    expect(secondSessionId).toBeTruthy();

    await secondRow.hover();
    await secondRow.getByTestId("user-session-rename").click();
    const renameInput = page.getByTestId("user-session-rename-input");
    await expect(renameInput).toBeVisible();
    await renameInput.fill("Moja nazwa E2E");
    await renameInput.press("Enter");

    await expect(page.getByTestId("user-header-title")).toHaveText(
        "Moja nazwa E2E",
    );

    const pinnedSecond = page.locator(
        `[data-testid="user-session-item"][data-session-id="${secondSessionId}"]`,
    );
    const firstRow = page.getByTestId("user-session-item").nth(1);
    await firstRow.getByTestId("user-session-select").click();

    await expect(page.getByTestId("user-header-title")).not.toHaveText(
        "Moja nazwa E2E",
    );

    await page.getByTestId("user-sidebar-toggle").click();
    await pinnedSecond.getByTestId("user-session-select").click();
    await expect(page.getByTestId("user-header-title")).toHaveText(
        "Moja nazwa E2E",
    );

    await page.getByTestId("user-sidebar-toggle").click();
    await pinnedSecond.hover();
    await pinnedSecond.getByTestId("user-session-delete").click();
    await page.getByTestId("user-session-confirm-ok").click();

    await expect(page.getByTestId("user-shell")).toBeVisible();
    await expect(
        page.getByTestId("user-session-item").filter({ hasText: "Moja nazwa" }),
    ).toHaveCount(0);
    await expect(page.getByTestId("user-header-title")).not.toHaveText(
        "Moja nazwa E2E",
    );
});

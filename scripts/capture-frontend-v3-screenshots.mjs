#!/usr/bin/env node
/**
 * Capture required frontend V3 screenshots against running cockpit (port 3001).
 * Usage: node scripts/capture-frontend-v3-screenshots.mjs
 */
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const OUT = path.join(ROOT, "artifacts", "frontend-v3");
const BASE = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3001";
const USER = process.env.SCREENSHOT_USER || "screenshot_v3";
const PASS = process.env.SCREENSHOT_PASS || "ScreenshotV3!test99";

async function login(page) {
    await page.goto(`${BASE}/login?next=/`, { waitUntil: "networkidle" });
    await page.getByPlaceholder("np. jan.kowalski").fill(USER);
    await page.getByPlaceholder("••••••••").fill(PASS);
    await page.getByRole("button", { name: /zaloguj/i }).click();
    await page.waitForURL(`${BASE}/`, { timeout: 30_000 });
    await page.getByTestId("user-shell").waitFor({ timeout: 30_000 });
}

async function seedDemoMessages(page) {
    await page.evaluate(() => {
        const raw = localStorage.getItem("aihub-cockpit-store");
        if (!raw) return;
        const parsed = JSON.parse(raw);
        const state = parsed.state ?? parsed;
        const sid = state.activeSessionId || state.sessions?.[0]?.id;
        if (!sid) return;
        const sessions = (state.sessions || []).map((s) => {
            if (s.id !== sid) return s;
            const now = Date.now();
            return {
                ...s,
                title: "Przegląd frontu V3",
                messages: [
                    {
                        id: "m_demo_user",
                        role: "user",
                        content: "Sprawdź czy nowy chat wygląda jak komunikator, nie panel admina.",
                        createdAt: now - 60_000,
                    },
                    {
                        id: "m_demo_ai",
                        role: "assistant",
                        content:
                            "Wygląda sensownie, Mordo — wyśrodkowany stage, sidebar bez kafli, composer sticky na dole.\n\n## Co widać\n- User bubble po prawej\n- AI bez wielkiego prostokąta\n- Akcje na hover\n\n```python\nprint('AI-Hub V3')\n```",
                        createdAt: now - 30_000,
                        streaming: false,
                    },
                ],
            };
        });
        localStorage.setItem(
            "aihub-cockpit-store",
            JSON.stringify({ ...parsed, state: { ...state, sessions } }),
        );
    });
    await page.reload({ waitUntil: "networkidle" });
    await page.getByTestId("user-shell").waitFor();
    await page.getByTestId("chat-message").first().waitFor({ timeout: 15_000 });
}

async function main() {
    await mkdir(OUT, { recursive: true });
    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext();
    const page = await ctx.newPage();

    await login(page);

    // 1. Desktop empty
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.getByTestId("user-new-session").click();
    await page.waitForTimeout(800);
    await page.screenshot({
        path: path.join(OUT, "01-desktop-empty-1920x1080.png"),
        fullPage: false,
    });

    // 2. Desktop conversation
    await seedDemoMessages(page);
    await page.screenshot({
        path: path.join(OUT, "02-desktop-conversation-1920x1080.png"),
        fullPage: false,
    });

    // 3. Desktop sidebar expanded (ensure not collapsed)
    await page.evaluate(() => {
        const raw = localStorage.getItem("aihub-chat-ui-v3");
        if (raw) {
            const p = JSON.parse(raw);
            const st = p.state ?? p;
            st.sidebarCollapsed = false;
            st.sidebarMobileOpen = false;
            localStorage.setItem("aihub-chat-ui-v3", JSON.stringify({ ...p, state: st }));
        }
    });
    await page.reload({ waitUntil: "networkidle" });
    await page.getByTestId("user-shell").waitFor();
    await page.screenshot({
        path: path.join(OUT, "03-desktop-sidebar-open.png"),
        fullPage: false,
    });

    // 4. Desktop memory drawer
    await page.getByTestId("open-memory-drawer").click();
    await page.getByTestId("memory-drawer").waitFor();
    await page.waitForTimeout(400);
    await page.screenshot({
        path: path.join(OUT, "04-desktop-drawer-memory.png"),
        fullPage: false,
    });
    await page.keyboard.press("Escape");

    // 5. Mobile empty
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTestId("user-new-session").click();
    await page.waitForTimeout(600);
    await page.screenshot({
        path: path.join(OUT, "05-mobile-empty-390x844.png"),
        fullPage: false,
    });

    // 6. Mobile conversation
    await seedDemoMessages(page);
    await page.screenshot({
        path: path.join(OUT, "06-mobile-conversation-390x844.png"),
        fullPage: false,
    });

    // 7. Mobile sidebar drawer
    await page.getByTestId("user-sidebar-toggle").click();
    await page.getByTestId("user-sidebar").waitFor();
    await page.waitForTimeout(400);
    await page.screenshot({
        path: path.join(OUT, "07-mobile-sidebar-open.png"),
        fullPage: false,
    });
    await page.keyboard.press("Escape");

    // 8. Mobile multiline composer
    const input = page.getByTestId("user-chat-input");
    await input.click();
    await input.fill("Pierwsza linia wiadomości\nDruga linia z kontekstem\nTrzecia linia testu composera");
    await page.waitForTimeout(500);
    await page.screenshot({
        path: path.join(OUT, "08-mobile-composer-multiline.png"),
        fullPage: false,
    });

    await browser.close();
    console.log(`Screenshots saved to ${OUT}`);
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});

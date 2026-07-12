import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const LOGIN_CLASS_MARKERS = [
    "login-animate-page",
    "bg-neutral-950",
    "min-h-[100dvh]",
    "font-sans",
    "rounded-[1.75rem]",
    "backdrop-blur-xl",
    "h-12",
] as const;

const CSS_CLASS_MARKERS = [
    "bg-neutral-950",
    "font-sans",
    "login-animate-page",
    "rounded-\\[1\\.75rem\\]",
    "backdrop-blur-xl",
    ".h-12",
] as const;

function extractStylesheetHref(html: string): string | null {
    const match = html.match(/href="(\/_next\/static\/css\/[^"]+\.css)"/);
    return match?.[1] ?? null;
}

describe("login Tailwind runtime contract", () => {
    it("tailwind content scan includes features/login and styles", () => {
        const configPath = resolve(process.cwd(), "tailwind.config.ts");
        const configSource = readFileSync(configPath, "utf8");
        expect(configSource).toContain("./features/**/*.{ts,tsx}");
        expect(configSource).toContain("./styles/**/*.{css,ts,tsx}");
        expect(configSource).toContain("./app/**/*.{ts,tsx}");
    });

    it("root layout wires Inter sans and imports globals.css", () => {
        const layoutPath = resolve(process.cwd(), "app/layout.tsx");
        const layoutSource = readFileSync(layoutPath, "utf8");
        expect(layoutSource).toContain('@/styles/globals.css');
        expect(layoutSource).toContain("Inter");
        expect(layoutSource).toContain('className="min-h-[100dvh] font-sans antialiased"');
    });

    it("login HTML exposes key className markers and matching CSP nonce", async () => {
        const base = process.env.COCKPIT_BASE_URL || "http://127.0.0.1:3001";
        const response = await fetch(`${base}/login`, {
            headers: { accept: "text/html" },
            cache: "no-store",
        });
        expect(response.status).toBe(200);

        const csp = response.headers.get("content-security-policy") || "";
        expect(csp).not.toContain("upgrade-insecure-requests");

        const html = await response.text();
        for (const className of LOGIN_CLASS_MARKERS) {
            expect(html, `missing class marker ${className}`).toContain(className);
        }
        expect(html).toContain('class="dark ');
        expect(html).toContain("Witaj ponownie");
    });

    it("built CSS bundle contains login utility selectors", async () => {
        const base = process.env.COCKPIT_BASE_URL || "http://127.0.0.1:3001";
        const htmlResponse = await fetch(`${base}/login`, { cache: "no-store" });
        const html = await htmlResponse.text();
        const stylesheetHref = extractStylesheetHref(html);
        expect(stylesheetHref, "login HTML should link a stylesheet").not.toBeNull();

        const cssResponse = await fetch(`${base}${stylesheetHref}`, {
            cache: "no-store",
        });
        expect(cssResponse.status).toBe(200);
        expect(cssResponse.headers.get("content-type")).toMatch(/text\/css/);

        const css = await cssResponse.text();
        for (const marker of CSS_CLASS_MARKERS) {
            expect(css, `missing CSS selector ${marker}`).toMatch(
                new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
            );
        }
    });
});

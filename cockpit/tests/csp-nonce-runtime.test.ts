import { describe, expect, it } from "vitest";

/**
 * Runtime contract: dynamic SSR must emit nonces matching middleware CSP.
 * Full browser verification is manual; this guards against static prerender regression.
 */
describe("CSP nonce SSR contract", () => {
    it("login HTML includes nonce on framework scripts when served dynamically", async () => {
        const base = process.env.COCKPIT_BASE_URL || "http://127.0.0.1:3001";
        const response = await fetch(`${base}/login`, {
            headers: { accept: "text/html" },
            cache: "no-store",
        });
        expect(response.status).toBe(200);
        const csp = response.headers.get("content-security-policy") || "";
        const nonceMatch = csp.match(/'nonce-([^']+)'/);
        expect(nonceMatch, "CSP should contain a nonce").not.toBeNull();
        const cspNonce = nonceMatch![1];

        const html = await response.text();
        expect(html).toContain(`nonce="${cspNonce}"`);
        expect(html).toContain("Nazwa użytkownika");
        expect(html).toContain('src="/_next/static/chunks/main-app-');
        expect(html).not.toContain("BAILOUT_TO_CLIENT_SIDE_RENDERING");
    });
});

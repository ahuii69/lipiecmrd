import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy, middleware } from "../middleware";

describe("dynamic response security headers", () => {
    it("uses a nonce CSP without unsafe-eval", () => {
        const csp = buildContentSecurityPolicy("test-nonce");
        expect(csp).toContain("script-src 'self' 'nonce-test-nonce'");
        expect(csp).toContain("style-src 'self' 'nonce-test-nonce'");
        expect(csp).toContain("frame-ancestors 'none'");
        expect(csp).toContain("object-src 'none'");
        expect(csp).not.toContain("unsafe-eval");
        expect(csp).not.toContain("upgrade-insecure-requests");
    });

    it.each(["/login"])("marks public %s private and non-cacheable", async (path) => {
        const response = await middleware(
            new NextRequest(`https://cockpit.example${path}`),
        );
        expect(response.headers.get("cache-control")).toBe(
            "private, no-store, max-age=0",
        );
        expect(response.headers.get("content-security-policy")).toMatch(
            /'nonce-[^']+'/,
        );
    });
});

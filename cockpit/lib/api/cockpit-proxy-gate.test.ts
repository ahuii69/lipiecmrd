import { describe, expect, it } from "vitest";

import {
    getCockpitProxyAllowlist,
    isCockpitProxyAllowed,
    pathMatchesTemplate,
} from "./cockpit-proxy-gate";

describe("pathMatchesTemplate", () => {
    it("matches static paths", () => {
        expect(pathMatchesTemplate("/system/ping", "/system/ping")).toBe(true);
        expect(pathMatchesTemplate("/system/ping", "/system/pong")).toBe(false);
    });

    it("matches single dynamic segment", () => {
        expect(
            pathMatchesTemplate(
                "/agent/status/{user_id}",
                "/agent/status/alice",
            ),
        ).toBe(true);
    });

    it("matches goal trace path", () => {
        expect(
            pathMatchesTemplate(
                "/agent/goals/{user_id}/{goal_id}/trace",
                "/agent/goals/u1/g1/trace",
            ),
        ).toBe(true);
    });
});

describe("isCockpitProxyAllowed", () => {
    it("allows official ApiClient paths", () => {
        expect(isCockpitProxyAllowed("POST", "/chat/upload")).toBe(true);
        expect(isCockpitProxyAllowed("POST", "/chat/turn")).toBe(true);
        expect(isCockpitProxyAllowed("GET", "/chat/capabilities")).toBe(true);
        expect(isCockpitProxyAllowed("POST", "/chat/capabilities/execute")).toBe(
            true,
        );
        expect(isCockpitProxyAllowed("GET", "/chat/file/file-xyz")).toBe(true);
        expect(isCockpitProxyAllowed("GET", "/system/ping")).toBe(true);
        expect(isCockpitProxyAllowed("GET", "/cockpit/schema-health")).toBe(
            true,
        );
        expect(
            isCockpitProxyAllowed("GET", "/cockpit/psyche-v2/habits/user-1"),
        ).toBe(true);
        expect(
            isCockpitProxyAllowed(
                "GET",
                "/chat/session/sess-abc/history",
            ),
        ).toBe(true);
    });

    it("blocks undeclared surface", () => {
        expect(isCockpitProxyAllowed("GET", "/admin/ping")).toBe(false);
        expect(isCockpitProxyAllowed("GET", "/openapi.json")).toBe(false);
        expect(isCockpitProxyAllowed("GET", "/memory/v2/extra/x")).toBe(false);
    });

    it("allows memory v2 read-only summary for user chat", () => {
        expect(isCockpitProxyAllowed("GET", "/memory/v2/summary/alice")).toBe(
            true,
        );
    });

    it("allows memory v2 retrieval explain and forgetting", () => {
        expect(
            isCockpitProxyAllowed(
                "GET",
                "/memory/v2/retrieval-explain/alice",
            ),
        ).toBe(true);
        expect(
            isCockpitProxyAllowed("POST", "/memory/v2/forgetting/alice"),
        ).toBe(true);
        expect(isCockpitProxyAllowed("POST", "/memory/v2/search")).toBe(true);
    });

    it("enforces HTTP method", () => {
        expect(isCockpitProxyAllowed("POST", "/system/ping")).toBe(false);
        expect(isCockpitProxyAllowed("DELETE", "/chat/turn")).toBe(false);
    });

    it("reject root passthrough", () => {
        expect(isCockpitProxyAllowed("GET", "/")).toBe(false);
    });
});

describe("allowlist file", () => {
    it("has routes array", () => {
        const routes = getCockpitProxyAllowlist();
        expect(routes.length).toBeGreaterThan(10);
        expect(routes.some((r) => r.path === "/chat/turn")).toBe(true);
    });
});

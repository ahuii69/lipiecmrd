import { describe, expect, it } from "vitest";

import { classifyBffRoute } from "@/lib/api/bff-route-policy";

describe("bff route policy", () => {
    it("allows public ping anonymously", () => {
        expect(classifyBffRoute("GET", "/system/ping")).toBe("public");
    });

    it("requires session for memory summary", () => {
        expect(classifyBffRoute("GET", "/memory/v2/summary/test-user")).toBe(
            "user",
        );
    });

    it("requires session for psyche", () => {
        expect(classifyBffRoute("GET", "/psyche/test-user")).toBe("user");
    });

    it("denies unknown routes by default", () => {
        expect(classifyBffRoute("GET", "/unknown/route")).toBe("deny");
    });

    it("classifies memory POST mutations as user-scoped", () => {
        expect(classifyBffRoute("POST", "/memory/v2/item")).toBe("user");
    });

    it("classifies admin schema health as admin-scoped", () => {
        expect(classifyBffRoute("GET", "/cockpit/schema-health")).toBe("admin");
    });
});

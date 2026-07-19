import { describe, expect, it } from "vitest";

import {
    readChatIdFromSearch,
    shouldApplyUrlSession,
    writeChatIdToSearch,
} from "@/lib/chat/chat-url";

describe("chat-url", () => {
    it("odczytuje c z query", () => {
        expect(readChatIdFromSearch("?c=s_abc")).toBe("s_abc");
        expect(readChatIdFromSearch("c=s_abc&x=1")).toBe("s_abc");
        expect(readChatIdFromSearch("")).toBeNull();
    });

    it("zapisuje / usuwa c bez psucia innych parametrów", () => {
        expect(writeChatIdToSearch("?foo=1", "s_1")).toBe("?foo=1&c=s_1");
        expect(writeChatIdToSearch("?c=old&foo=1", "s_2")).toBe("?c=s_2&foo=1");
        expect(writeChatIdToSearch("?c=old", null)).toBe("");
    });

    it("shouldApplyUrlSession tylko przy różnicy", () => {
        expect(shouldApplyUrlSession("a", "a")).toBe(false);
        expect(shouldApplyUrlSession("a", "b")).toBe(true);
        expect(shouldApplyUrlSession(null, "b")).toBe(false);
    });
});

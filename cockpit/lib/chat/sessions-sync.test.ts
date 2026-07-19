import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { publishSessionsSync } from "@/lib/chat/sessions-sync";

describe("sessions-sync", () => {
    let lastPosted: unknown = null;

    beforeEach(() => {
        lastPosted = null;
        vi.stubGlobal(
            "BroadcastChannel",
            class {
                postMessage(data: unknown) {
                    lastPosted = data;
                }
                close() {
                    void 0;
                }
            },
        );
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("publishes sessions-changed events for other tabs", () => {
        publishSessionsSync({ type: "sessions-changed", userId: "u1" });
        expect(lastPosted).toEqual({ type: "sessions-changed", userId: "u1" });
    });

    it("publishes archive-changed events", () => {
        publishSessionsSync({
            type: "archive-changed",
            userId: "u1",
            sessionId: "s1",
            archived: true,
        });
        expect(lastPosted).toEqual({
            type: "archive-changed",
            userId: "u1",
            sessionId: "s1",
            archived: true,
        });
    });
});

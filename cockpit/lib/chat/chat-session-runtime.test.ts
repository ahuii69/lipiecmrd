import { describe, expect, it } from "vitest";

import { chatSessionRuntime } from "@/lib/chat/chat-session-runtime";

describe("chatSessionRuntime", () => {
    it("beginTurn abortuje poprzedni turn tej samej sesji", () => {
        const t1 = chatSessionRuntime.beginTurn("s1");
        const t2 = chatSessionRuntime.beginTurn("s1");
        expect(t1.signal.aborted).toBe(true);
        expect(t2.signal.aborted).toBe(false);
        expect(chatSessionRuntime.isCurrent("s1", t1.generation)).toBe(false);
        expect(chatSessionRuntime.isCurrent("s1", t2.generation)).toBe(true);
        chatSessionRuntime.endTurn("s1", t2.generation);
    });

    it("abortAll unieważnia wszystkie callbacki", () => {
        const t = chatSessionRuntime.beginTurn("s2");
        chatSessionRuntime.abortAll();
        expect(t.signal.aborted).toBe(true);
        expect(chatSessionRuntime.isCurrent("s2", t.generation)).toBe(false);
    });

    it("queueDelta batchuje i ignoruje stale generation", async () => {
        const chunks: string[] = [];
        chatSessionRuntime.setFlushHandler((_s, _m, c) => {
            chunks.push(c);
        });
        const t = chatSessionRuntime.beginTurn("s3");
        chatSessionRuntime.queueDelta("s3", "m1", "a", t.generation);
        chatSessionRuntime.queueDelta("s3", "m1", "b", t.generation);
        chatSessionRuntime.queueDelta("s3", "m1", "x", t.generation - 1);
        await new Promise((r) => setTimeout(r, 20));
        expect(chunks.join("")).toBe("ab");
        chatSessionRuntime.abortAll();
        chatSessionRuntime.setFlushHandler(() => undefined);
    });
});

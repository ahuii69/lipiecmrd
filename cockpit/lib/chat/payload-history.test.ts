import { describe, expect, it } from "vitest";

import type { ChatTurnResponse } from "@/lib/api/types";
import { toChatHistoryPayload } from "@/lib/chat/payload-history";
import type { ChatUIMessage } from "@/lib/store/cockpit-store";

describe("toChatHistoryPayload", () => {
    it("sortuje po createdAt i przekazuje tool_calls z diagnostyki", () => {
        const messages: ChatUIMessage[] = [
            {
                id: "b",
                role: "assistant",
                content: "druga",
                createdAt: 200,
            },
            {
                id: "a",
                role: "user",
                content: "pierwsza",
                createdAt: 100,
            },
        ];
        const h = toChatHistoryPayload(messages);
        expect(h.map((x) => x.content)).toEqual(["pierwsza", "druga"]);
    });

    it("przekazuje tool_calls z diagnostics", () => {
        const messages: ChatUIMessage[] = [
            {
                id: "u",
                role: "user",
                content: "x",
                createdAt: 1,
            },
            {
                id: "as",
                role: "assistant",
                content: "y",
                createdAt: 2,
                diagnostics: {
                    ok: true,
                    response_text: "y",
                    model: "m",
                    provider: "p",
                    tool_calls: [
                        { tool_call_id: "1", name: "t", arguments: {} },
                    ],
                    tool_results: [],
                    selected_mode: "chat",
                    usage: {
                        prompt_tokens: 0,
                        completion_tokens: 0,
                        total_tokens: 0,
                    },
                    trace: {},
                    errors: [],
                } satisfies ChatTurnResponse,
            },
        ];
        const h = toChatHistoryPayload(messages);
        expect(h[1]?.tool_calls?.length).toBe(1);
    });

    it("pomija asystenta w trakcie streamu (pusta bańka)", () => {
        const messages: ChatUIMessage[] = [
            {
                id: "u",
                role: "user",
                content: "hi",
                createdAt: 1,
            },
            {
                id: "a",
                role: "assistant",
                content: "część",
                createdAt: 2,
                streaming: true,
            },
        ];
        const h = toChatHistoryPayload(messages);
        expect(h.map((x) => x.role)).toEqual(["user"]);
    });
});

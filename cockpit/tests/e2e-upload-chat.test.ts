import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChatTurn } from "@/lib/api/chat-turn-stream";
import { uploadChatFile } from "@/lib/api/chat-upload";

class TestFile extends Blob {
    name: string;

    constructor(bits: BlobPart[], filename: string, options?: BlobPropertyBag) {
        super(bits, options);
        this.name = filename;
    }
}

function testTxtFile(): File {
    return new TestFile(["x"], "a.txt", {
        type: "text/plain",
    }) as unknown as File;
}

describe("e2e upload → turn (fetch mock)", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("wysyła attached_file_ids po uploadzie i pokazuje odpowiedź w strumieniu", async () => {
        const turnBodies: unknown[] = [];
        vi.stubGlobal(
            "fetch",
            vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
                const url = String(input);
                if (url.includes("/chat/upload")) {
                    return new Response(
                        JSON.stringify({
                            file_id: "f1",
                            filename: "a.txt",
                            content_type: "text/plain",
                            size: 4,
                            extracted_text_preview: "test content",
                            status: "ok",
                        }),
                        { status: 200, headers: { "content-type": "application/json" } },
                    );
                }
                if (url.includes("/chat/turn")) {
                    turnBodies.push(JSON.parse(String(init?.body ?? "{}")));
                    const sse =
                        'data: {"type":"delta","content":"OK z pliku"}\n\n' +
                        'data: {"type":"done","attachments_summary":"Plik: a.txt","context_chips":["attachment-used"],"result":{"ok":true,"response_text":"OK z pliku","model":"m","provider":"p","tool_calls":[],"tool_results":[],"selected_mode":"chat","usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"reporting_mode":"unavailable"},"trace":{},"errors":[]}}\n\n';
                    return new Response(sse, {
                        status: 200,
                        headers: { "content-type": "text/event-stream" },
                    });
                }
                return new Response("not found", { status: 404 });
            }),
        );

        const up = await uploadChatFile({
            user_id: "u1",
            session_id: "s1",
            file: testTxtFile(),
        });
        expect(up.file_id).toBe("f1");
        expect(up.extracted_text_preview).toContain("test");

        let accumulated = "";
        let doneSummary: string | undefined;
        let doneChips: string[] | undefined;
        await streamChatTurn(
            {
                user_id: "u1",
                session_id: "s1",
                message: "streść",
                mode: "chat",
                attached_file_ids: ["f1"],
            },
            new AbortController().signal,
            {
                onDelta: (c) => {
                    accumulated += c;
                },
                onDone: (_r, s, chips) => {
                    doneSummary = s;
                    doneChips = chips;
                },
            },
        );

        expect(turnBodies).toHaveLength(1);
        expect(
            (turnBodies[0] as { attached_file_ids?: string[] })
                .attached_file_ids,
        ).toEqual(["f1"]);
        expect(accumulated).toContain("OK z pliku");
        expect(doneSummary).toBe("Plik: a.txt");
        expect(doneChips).toEqual(["attachment-used"]);
    });

    it("retry wysyła te same attached_file_ids", async () => {
        const turnBodies: unknown[] = [];
        vi.stubGlobal(
            "fetch",
            vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
                const url = String(input);
                if (url.includes("/chat/turn")) {
                    turnBodies.push(JSON.parse(String(init?.body ?? "{}")));
                    const sse =
                        'data: {"type":"done","result":{"ok":true,"response_text":"retry-ok","model":"m","provider":"p","tool_calls":[],"tool_results":[],"selected_mode":"chat","usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"reporting_mode":"unavailable"},"trace":{},"errors":[]}}\n\n';
                    return new Response(sse, {
                        status: 200,
                        headers: { "content-type": "text/event-stream" },
                    });
                }
                return new Response("not found", { status: 404 });
            }),
        );

        const payload = {
            user_id: "u1",
            session_id: "s1",
            message: "hi",
            mode: "chat" as const,
            attached_file_ids: ["f1"],
        };

        await streamChatTurn(
            payload,
            new AbortController().signal,
            {
                onDelta: () => undefined,
                onDone: () => undefined,
            },
        );
        await streamChatTurn(
            payload,
            new AbortController().signal,
            {
                onDelta: () => undefined,
                onDone: () => undefined,
            },
        );

        expect(turnBodies).toHaveLength(2);
        for (const b of turnBodies) {
            expect(
                (b as { attached_file_ids?: string[] }).attached_file_ids,
            ).toEqual(["f1"]);
        }
    });
});

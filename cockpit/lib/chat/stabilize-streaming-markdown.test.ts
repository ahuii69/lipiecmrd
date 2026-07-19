import { describe, expect, it } from "vitest";

import { stabilizeStreamingMarkdown } from "@/lib/chat/stabilize-streaming-markdown";

describe("stabilizeStreamingMarkdown", () => {
    it("domyka otwarty fence", () => {
        const raw = "Hello\n```js\nconst x = 1;";
        const out = stabilizeStreamingMarkdown(raw);
        expect(out.endsWith("```")).toBe(true);
        expect(out.startsWith("Hello")).toBe(true);
    });

    it("nie zmienia zamkniętego fence", () => {
        const raw = "```js\nconst x = 1;\n```";
        expect(stabilizeStreamingMarkdown(raw)).toBe(raw);
    });

    it("zostawia zwykły tekst", () => {
        expect(stabilizeStreamingMarkdown("cześć")).toBe("cześć");
    });
});

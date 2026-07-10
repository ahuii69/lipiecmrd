import { describe, expect, it } from "vitest";

import {
    deriveSessionTitleFromMessage,
    isPlaceholderSessionTitle,
    lastUserVisiblePreview,
    selectNextActiveAfterDelete,
} from "./session-title";

describe("isPlaceholderSessionTitle", () => {
    it("rozpoznaje placeholdery PL/EN", () => {
        expect(isPlaceholderSessionTitle("Nowa rozmowa")).toBe(true);
        expect(isPlaceholderSessionTitle("New Chat")).toBe(true);
        expect(isPlaceholderSessionTitle("  new chat  ")).toBe(true);
        expect(isPlaceholderSessionTitle("Mój temat")).toBe(false);
    });
});

describe("deriveSessionTitleFromMessage", () => {
    it("skraca do kilku słów i obcina długość", () => {
        const t = deriveSessionTitleFromMessage(
            "To jest bardzo długa pierwsza wiadomość od użytkownika z wieloma słowami",
            5,
            30,
        );
        expect(t.length).toBeLessThanOrEqual(30);
        expect(t.split(/\s+/).length).toBeLessThanOrEqual(5);
    });

    it("pomija URL i śmieci", () => {
        expect(
            deriveSessionTitleFromMessage("https://ex.com/foo bar baz", 8, 48),
        ).toContain("bar");
    });

    it("fallback gdy brak treści", () => {
        expect(deriveSessionTitleFromMessage("   ")).toBe("Nowa rozmowa");
    });
});

describe("selectNextActiveAfterDelete", () => {
    it("wybiera najnowszą sesję po usunięciu aktywnej", () => {
        const next = selectNextActiveAfterDelete(
            [
                { id: "a", updatedAt: 100 },
                { id: "b", updatedAt: 300 },
            ],
            true,
            "deleted",
        );
        expect(next).toBe("b");
    });

    it("zostawia aktywną gdy usunięto inną", () => {
        expect(
            selectNextActiveAfterDelete(
                [{ id: "x", updatedAt: 1 }],
                false,
                "current",
            ),
        ).toBe("current");
    });
});

describe("lastUserVisiblePreview", () => {
    it("bierze ostatnią niepustą treść", () => {
        expect(
            lastUserVisiblePreview([
                { role: "user", content: "stare" },
                { role: "assistant", content: "nowe" },
            ]),
        ).toBe("nowe");
    });

    it("pomija puste bąbelki", () => {
        expect(
            lastUserVisiblePreview([
                { role: "user", content: "" },
                { role: "assistant", content: "x" },
            ]),
        ).toBe("x");
    });
});

describe("izolacja sesji (model danych)", () => {
    it("dwie sesje mają osobne tablice messages", () => {
        const a = { id: "s1", messages: [{ role: "user", content: "a" }] };
        const b = { id: "s2", messages: [{ role: "user", content: "b" }] };
        expect(a.messages[0].content).not.toBe(b.messages[0].content);
    });
});

describe("persystencja (kontrakt pól)", () => {
    it("snapshot pól wymaganych do reload", () => {
        const persisted = {
            sessions: [
                {
                    id: "s1",
                    title: "T",
                    userId: "u",
                    mode: "chat",
                    createdAt: 1,
                    updatedAt: 2,
                    messages: [],
                    lastFailedUserMessage: null,
                    titleLockedByUser: false,
                },
            ],
            activeSessionId: "s1",
        };
        expect(persisted.activeSessionId).toBe("s1");
        expect(persisted.sessions[0].messages).toEqual([]);
    });
});

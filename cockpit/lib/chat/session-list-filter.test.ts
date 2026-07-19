import { describe, expect, it } from "vitest";

import { filterSessionsForSidebar } from "@/lib/chat/session-list-filter";

const sessions = [
    { id: "a", title: "Alpha project", messages: [] },
    { id: "b", title: "Beta notes", messages: [] },
    { id: "c", title: "Archive gamma", messages: [] },
];

describe("filterSessionsForSidebar", () => {
    it("hides archived when not showArchived and no query", () => {
        const out = filterSessionsForSidebar({
            sessions,
            archivedSessionIds: ["c"],
            showArchived: false,
            searchQuery: "",
            previewOf: () => "",
        });
        expect(out.map((s) => s.id)).toEqual(["a", "b"]);
    });

    it("shows only archived when showArchived and no query", () => {
        const out = filterSessionsForSidebar({
            sessions,
            archivedSessionIds: ["c"],
            showArchived: true,
            searchQuery: "",
            previewOf: () => "",
        });
        expect(out.map((s) => s.id)).toEqual(["c"]);
    });

    it("search includes archived hits even when showArchived is false", () => {
        const out = filterSessionsForSidebar({
            sessions,
            archivedSessionIds: ["c"],
            showArchived: false,
            searchQuery: "gamma",
            previewOf: () => "",
        });
        expect(out.map((s) => s.id)).toEqual(["c"]);
    });

    it("search matches active titles", () => {
        const out = filterSessionsForSidebar({
            sessions,
            archivedSessionIds: ["c"],
            showArchived: false,
            searchQuery: "alpha",
            previewOf: () => "",
        });
        expect(out.map((s) => s.id)).toEqual(["a"]);
    });
});

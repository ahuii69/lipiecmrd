import { describe, expect, it } from "vitest";

import { resolveAttachedFileIdsForSend } from "@/lib/chat/resolve-attached-file-ids";

describe("resolveAttachedFileIdsForSend (ghost-attachment guard)", () => {
    it("uses the current draft attachments when present", () => {
        expect(resolveAttachedFileIdsForSend(["f1", "f2"])).toEqual(["f1", "f2"]);
    });

    it("returns an empty list when the draft is empty (no history re-attach)", () => {
        // Regression guard: after a successful send clears the draft, the next text-only message must
        // NOT inherit the previous turn's attachment id. Previously this scanned message history and
        // re-attached forever (the ghost-attachment bug).
        expect(resolveAttachedFileIdsForSend([])).toEqual([]);
    });

    it("filters out falsy ids", () => {
        expect(resolveAttachedFileIdsForSend(["", "f1", ""])).toEqual(["f1"]);
    });

    it("caps at 5 attachments", () => {
        expect(
            resolveAttachedFileIdsForSend(["a", "b", "c", "d", "e", "f", "g"]),
        ).toEqual(["a", "b", "c", "d", "e"]);
    });
});

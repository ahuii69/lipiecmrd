import { describe, expect, it } from "vitest";

import {
    clearDraftAttachments,
    readyDraftFileIds,
} from "@/lib/chat/draft-attachments";
import type { UserDraftAttachment } from "@/features/user-chat/user-message-composer";
import { resolveAttachedFileIdsForSend } from "@/lib/chat/resolve-attached-file-ids";

function draft(
    partial: Partial<UserDraftAttachment> & Pick<UserDraftAttachment, "key" | "filename" | "status">,
): UserDraftAttachment {
    return {
        key: partial.key,
        filename: partial.filename,
        status: partial.status,
        fileId: partial.fileId,
        error: partial.error,
        kind: partial.kind,
        previewUrl: partial.previewUrl,
    };
}

describe("draft-attachments (ghost-attachment guard)", () => {
    it("clearDraftAttachments returns empty list", () => {
        const items = [
            draft({ key: "a", filename: "a.png", status: "ready", fileId: "f1" }),
            draft({ key: "b", filename: "b.txt", status: "error", error: "fail" }),
        ];
        expect(clearDraftAttachments(items)).toEqual([]);
    });

    it("readyDraftFileIds returns only ready items with fileId", () => {
        const items = [
            draft({ key: "a", filename: "a.png", status: "ready", fileId: "f1" }),
            draft({ key: "b", filename: "b.png", status: "uploading" }),
            draft({ key: "c", filename: "c.png", status: "error", fileId: "f2" }),
        ];
        expect(readyDraftFileIds(items)).toEqual(["f1"]);
    });

    it("after clear, send payload has no attachment ids (no history re-attach)", () => {
        const before = [
            draft({ key: "a", filename: "img.png", status: "ready", fileId: "f_old" }),
        ];
        const afterClear = clearDraftAttachments(before);
        const ids = resolveAttachedFileIdsForSend(readyDraftFileIds(afterClear));
        expect(ids).toEqual([]);
    });

    it("failed upload stays visible in draft until user removes it", () => {
        const items = [
            draft({
                key: "bad",
                filename: "broken.png",
                status: "error",
                error: "upload failed",
            }),
        ];
        expect(readyDraftFileIds(items)).toEqual([]);
        expect(resolveAttachedFileIdsForSend(readyDraftFileIds(items))).toEqual([]);
        // Still in draft for UI remove button — not cleared automatically on error.
        expect(items).toHaveLength(1);
    });
});

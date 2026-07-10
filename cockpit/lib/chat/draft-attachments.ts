/**
 * Draft attachment state helpers for the chat composer.
 *
 * Ghost-attachment bug (06.07): after sending a message with a file, the next text-only turn
 * must NOT re-attach the previous file. Payload ids come ONLY from the current draft — never
 * from message history (see resolve-attached-file-ids.ts).
 */

import type { UserDraftAttachment } from "@/features/user-chat/user-message-composer";

/** Revoke preview object URLs and return an empty draft list. */
export function clearDraftAttachments(
    draft: UserDraftAttachment[],
): UserDraftAttachment[] {
    for (const f of draft) {
        if (f.previewUrl) {
            URL.revokeObjectURL(f.previewUrl);
        }
    }
    return [];
}

/** Ready file ids from the current draft (upload finished, has server file_id). */
export function readyDraftFileIds(draft: UserDraftAttachment[]): string[] {
    return draft
        .filter(
            (f) =>
                f.status === "ready" &&
                typeof f.fileId === "string" &&
                Boolean(f.fileId),
        )
        .map((f) => f.fileId!);
}

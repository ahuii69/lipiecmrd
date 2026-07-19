"use client";

/**
 * Legacy composer — superseded by ChatComposer via ChatShell.
 * Re-exports ChatComposer so any stale imports keep working without a second path.
 */
export {
    ChatComposer as MessageComposer,
    type ChatDraftAttachment,
} from "@/features/chat/ChatComposer";

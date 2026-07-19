"use client";

/**
 * Admin chat panel — same production ChatShell runtime as user `/`.
 * Embedded in AppShell; keeps admin mode/debug + diagnostics via adminCapabilities.
 * Legacy message-list / message-composer path removed (replaced, not dual-maintained).
 */
import { ChatShell } from "@/features/chat/ChatShell";

export function ChatPanel() {
    return <ChatShell layout="embedded" adminCapabilities />;
}

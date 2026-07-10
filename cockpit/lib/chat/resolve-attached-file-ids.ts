const MAX_ATTACHED = 5;

/**
 * Attachment ids to send with a NEW chat turn.
 *
 * Source of truth is ONLY the current composer draft. Previously this function fell back to scanning
 * the message history for the last user message with attachments and re-sent those ids — which caused
 * the "ghost attachment" bug: after one image upload, every subsequent text-only message kept
 * re-attaching the same file forever (badge "1 plik" reappeared, backend kept receiving the old image).
 *
 * A follow-up that references a previous attachment ("opisz ten obraz") is resolved server-side via the
 * deictic session-attachment fallback, so we do not need (and must not) re-attach here. Retrying the
 * exact failed message is a separate, explicit path (see UserShell.handleRetry) that passes that
 * message's own ids directly.
 */
export function resolveAttachedFileIdsForSend(draftFileIds: string[]): string[] {
    return draftFileIds.filter(Boolean).slice(0, MAX_ATTACHED);
}

/**
 * Client-side session list filter for ChatSidebar.
 * - Empty query: respect showArchived toggle only.
 * - Non-empty query: search title/preview across active + archived (archived never hidden by toggle).
 */

export function filterSessionsForSidebar<
    T extends { id: string; title: string; messages: unknown[] },
>(opts: {
    sessions: T[];
    archivedSessionIds: string[];
    showArchived: boolean;
    searchQuery: string;
    previewOf: (session: T) => string;
}): T[] {
    const q = opts.searchQuery.trim().toLowerCase();
    const archived = new Set(opts.archivedSessionIds);
    return opts.sessions.filter((s) => {
        const isArchived = archived.has(s.id);
        if (q) {
            const hay =
                `${s.title} ${opts.previewOf(s)}`.toLowerCase();
            return hay.includes(q);
        }
        return opts.showArchived ? isArchived : !isArchived;
    });
}

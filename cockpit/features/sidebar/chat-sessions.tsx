"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useChatUiStore } from "@/features/chat/chat-ui-store";
import { apiClient } from "@/lib/api/client";
import { lastUserVisiblePreview } from "@/lib/chat/session-title";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import { formatTs, shortId } from "@/lib/utils";

export function ChatSessions({ userId }: { userId: string }) {
    const apiKeyOverride = useCockpitStore((s) => s.apiKeyOverride);
    const sessions = useCockpitStore((s) => s.sessions);
    const activeSessionId = useCockpitStore((s) => s.activeSessionId);
    const setActiveSession = useCockpitStore((s) => s.setActiveSession);
    const updateSessionTitle = useCockpitStore((s) => s.updateSessionTitle);
    const deleteSession = useCockpitStore((s) => s.deleteSession);
    const mergeServerSessions = useCockpitStore((s) => s.mergeServerSessions);
    const archivedSessionIds = useChatUiStore((s) => s.archivedSessionIds);
    const replaceArchivedSessionIds = useChatUiStore(
        (s) => s.replaceArchivedSessionIds,
    );

    const [renamingId, setRenamingId] = useState<string | null>(null);
    const [renameDraft, setRenameDraft] = useState("");

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            try {
                const r = await apiClient.getSessions(
                    userId,
                    apiKeyOverride || undefined,
                );
                if (!cancelled) {
                    mergeServerSessions(r.sessions, userId);
                    replaceArchivedSessionIds(
                        r.sessions
                            .filter((s) => s.archived === true)
                            .map((s) => s.id),
                    );
                }
            } catch {
                void 0;
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [userId, apiKeyOverride, mergeServerSessions, replaceArchivedSessionIds]);

    const commitRename = async (sessionId: string, fallbackTitle: string) => {
        const t = renameDraft.trim() || fallbackTitle;
        try {
            await apiClient.renameSession(
                {
                    user_id: userId,
                    session_id: sessionId,
                    title: t,
                },
                apiKeyOverride || undefined,
            );
            updateSessionTitle(sessionId, t);
        } catch {
            void 0;
        } finally {
            setRenamingId(null);
        }
    };

    const onTrash = async (sessionId: string) => {
        if (!confirm("Delete session?")) return;
        try {
            await apiClient.deleteSession(
                { user_id: userId, session_id: sessionId },
                apiKeyOverride || undefined,
            );
        } catch {
            void 0;
        }
        deleteSession(sessionId);
        useChatUiStore.getState().unarchiveSession(sessionId);
    };

    const archivedSet = new Set(archivedSessionIds);

    return (
        <ScrollArea className="h-full rounded-md border border-border/60 p-1">
            <div className="space-y-1">
                {sessions.map((session) => (
                    <div
                        key={session.id}
                        className={`flex cursor-pointer flex-col gap-1 rounded-md px-2 py-1.5 text-xs ${
                            session.id === activeSessionId
                                ? "bg-primary/15"
                                : "hover:bg-muted/60"
                        }`}
                        onClick={() => setActiveSession(session.id)}
                    >
                        <div className="flex items-center justify-between gap-2">
                            <span className="truncate font-medium">
                                {session.title}
                            </span>
                            <div className="flex shrink-0 items-center gap-1">
                                {archivedSet.has(session.id) ? (
                                    <Badge variant="outline">archiwum</Badge>
                                ) : null}
                                <Badge variant="secondary">
                                    {shortId(session.id)}
                                </Badge>
                            </div>
                        </div>
                        <p className="truncate text-[10px] text-muted-foreground">
                            {lastUserVisiblePreview(session.messages) ||
                                formatTs(session.updatedAt)}
                        </p>
                        <div className="flex gap-2">
                            <button
                                type="button"
                                className="text-[10px] text-muted-foreground underline"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    setRenamingId(session.id);
                                    setRenameDraft(session.title);
                                }}
                            >
                                Rename
                            </button>
                            <button
                                type="button"
                                className="text-[10px] text-destructive underline"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    void onTrash(session.id);
                                }}
                            >
                                Delete
                            </button>
                        </div>
                        {renamingId === session.id ? (
                            <input
                                className="rounded border border-border bg-background px-1 py-0.5 text-xs"
                                value={renameDraft}
                                onChange={(e) => setRenameDraft(e.target.value)}
                                onClick={(e) => e.stopPropagation()}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") {
                                        void commitRename(
                                            session.id,
                                            session.title,
                                        );
                                    }
                                }}
                                onBlur={() =>
                                    void commitRename(session.id, session.title)
                                }
                                autoFocus
                            />
                        ) : null}
                    </div>
                ))}
            </div>
        </ScrollArea>
    );
}

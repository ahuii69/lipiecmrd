"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
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
                }
            } catch {
                void 0;
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [userId, apiKeyOverride, mergeServerSessions]);

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
    };

    return (
        <ScrollArea className="h-full rounded-md border border-border/60 p-1">
            <div className="space-y-1">
                {sessions.map((session) => (
                    <div
                        key={session.id}
                        role="presentation"
                        onClick={() => setActiveSession(session.id)}
                        className={`w-full cursor-pointer rounded-md border px-2 py-2 text-left transition ${
                            session.id === activeSessionId
                                ? "border-primary bg-primary/10"
                                : "border-transparent hover:border-border hover:bg-muted/40"
                        }`}
                    >
                        <div className="mb-1 flex items-center justify-between gap-2">
                            {renamingId === session.id ? (
                                <input
                                    autoFocus
                                    className="h-7 min-w-0 flex-1 rounded border border-input bg-background px-1 text-xs"
                                    value={renameDraft}
                                    onChange={(e) =>
                                        setRenameDraft(e.target.value)
                                    }
                                    onClick={(e) => e.stopPropagation()}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter") {
                                            e.preventDefault();
                                            void commitRename(
                                                session.id,
                                                session.title,
                                            );
                                        }
                                    }}
                                />
                            ) : (
                                <p
                                    role="presentation"
                                    className="min-w-0 flex-1 cursor-text truncate text-xs font-semibold"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        setRenamingId(session.id);
                                        setRenameDraft(session.title);
                                    }}
                                >
                                    {session.title}
                                </p>
                            )}
                            <div className="flex shrink-0 items-center gap-1">
                                <Badge
                                    variant="outline"
                                    className="text-[10px]"
                                >
                                    {session.mode}
                                </Badge>
                                <button
                                    type="button"
                                    className="text-sm leading-none opacity-70 hover:opacity-100"
                                    aria-label="Delete session"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        void onTrash(session.id);
                                    }}
                                >
                                    🗑
                                </button>
                            </div>
                        </div>
                        <p className="line-clamp-2 text-[10px] text-muted-foreground">
                            {lastUserVisiblePreview(session.messages) ||
                                shortId(session.id)}
                        </p>
                        <p className="text-[10px] text-muted-foreground">
                            {formatTs(session.updatedAt)}
                        </p>
                    </div>
                ))}
            </div>
        </ScrollArea>
    );
}

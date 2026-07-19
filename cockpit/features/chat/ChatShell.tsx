"use client";

import { useMutation } from "@tanstack/react-query";
import { UploadCloud } from "lucide-react";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { ChatComposer, type ChatDraftAttachment } from "@/features/chat/ChatComposer";
import { ChatConfirmationBar } from "@/features/chat/ChatConfirmationBar";
import { ChatDrawer } from "@/features/chat/ChatDrawer";
import { ChatHeader } from "@/features/chat/ChatHeader";
import { ChatSidebar } from "@/features/chat/ChatSidebar";
import { ChatStage } from "@/features/chat/ChatStage";
import { useChatUiStore } from "@/features/chat/chat-ui-store";
import { Button } from "@/components/ui/button";
import {
    streamChatTurn,
    type PendingConfirmation,
} from "@/lib/api/chat-turn-stream";
import { uploadChatFile } from "@/lib/api/chat-upload";
import { ApiClientError } from "@/lib/api/client";
import { formatChatTurnErrorMessage } from "@/lib/api/hub-auth-errors";
import { chatSessionRuntime } from "@/lib/chat/chat-session-runtime";
import {
    clearDraftAttachments,
    readyDraftFileIds,
} from "@/lib/chat/draft-attachments";
import { toChatHistoryPayload } from "@/lib/chat/payload-history";
import { resolveAttachedFileIdsForSend } from "@/lib/chat/resolve-attached-file-ids";
import { isPlaceholderSessionTitle } from "@/lib/chat/session-title";
import { useChatUrlSync } from "@/lib/chat/use-chat-url-sync";
import { subscribeSessionsSync } from "@/lib/chat/sessions-sync";
import {
    logoutAndRedirect,
    useAuthPrincipal,
} from "@/lib/hooks/use-auth-principal";
import {
    reloadSessionHistoryFromServer,
    useSessionHistoryFromServer,
} from "@/lib/hooks/use-session-history-from-server";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import { apiClient } from "@/lib/api/client";

interface SendVariables {
    text: string;
    sessionId: string;
    userId: string;
    history: ReturnType<typeof toChatHistoryPayload>;
    attachedFileIds: string[];
    keyOverride?: string;
    retry?: boolean;
    sttUsed?: boolean;
    idempotencyKey?: string;
}

function makeMessageId(): string {
    return `m_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

export type ChatShellProps = {
    /**
     * fullscreen — user `/` (sidebar + URL sync).
     * embedded — admin AppShell chat section (no ChatSidebar; parent nav).
     */
    layout?: "fullscreen" | "embedded";
    /** Admin cockpit: session.mode + include_debug for debug mode. */
    adminCapabilities?: boolean;
};

function ChatUrlSyncBridge({ userId }: { userId: string }) {
    useChatUrlSync({ userId });
    return null;
}

export function ChatShell({
    layout = "fullscreen",
    adminCapabilities = false,
}: ChatShellProps = {}) {
    const { principal, loading: authLoading, error: authError } =
        useAuthPrincipal();
    const sessions = useCockpitStore((s) => s.sessions);
    const activeSessionId = useCockpitStore((s) => s.activeSessionId);
    const appendMessage = useCockpitStore((s) => s.appendMessage);
    const appendMessageContent = useCockpitStore((s) => s.appendMessageContent);
    const patchMessage = useCockpitStore((s) => s.patchMessage);
    const truncateSessionMessagesTail = useCockpitStore(
        (s) => s.truncateSessionMessagesTail,
    );
    const retryPayloadForLastFailedMessage = useCockpitStore(
        (s) => s.retryPayloadForLastFailedMessage,
    );
    const setLastFailedUserMessage = useCockpitStore(
        (s) => s.setLastFailedUserMessage,
    );
    const apiKeyOverride = useCockpitStore((s) => s.apiKeyOverride);
    const mergeServerSessions = useCockpitStore((s) => s.mergeServerSessions);
    const authUserId = useCockpitStore((s) => s.authUserId);
    const createSession = useCockpitStore((s) => s.createSession);
    const setActiveSession = useCockpitStore((s) => s.setActiveSession);

    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];
    const userId = authUserId || principal?.userId || session.userId;

    const { setSidebarMobileOpen, openDrawer } = useChatUiStore();

    const [liveActivity, setLiveActivity] = useState<string | null>(null);
    const [draftFiles, setDraftFiles] = useState<ChatDraftAttachment[]>([]);
    const [pendingConfirmation, setPendingConfirmation] =
        useState<PendingConfirmation | null>(null);
    const [dropActive, setDropActive] = useState(false);
    const [suggestion, setSuggestion] = useState<string | null>(null);
    const [sessionsSyncing, setSessionsSyncing] = useState(false);
    const streamingAssistantIdRef = useRef<string | null>(null);
    const dragDepthRef = useRef(0);
    const idempotencyKeyRef = useRef<Map<string, string>>(new Map());
    const turnGenerationRef = useRef<number>(0);
    const [inflightSessionId, setInflightSessionId] = useState<string | null>(
        null,
    );

    useEffect(() => {
        chatSessionRuntime.setFlushHandler((sid, mid, chunk) => {
            appendMessageContent(sid, mid, chunk);
        });
        return () => {
            chatSessionRuntime.abortAll();
            chatSessionRuntime.setFlushHandler(() => undefined);
        };
    }, [appendMessageContent]);

    useSessionHistoryFromServer({
        sessionId: session.id,
        userId,
        apiKeyOverride,
    });

    const syncSessionsFromServer = useCallback(
        async (opts?: { silent?: boolean }) => {
            if (!userId || userId === "default") return;
            if (!opts?.silent) setSessionsSyncing(true);
            try {
                const response = await apiClient.getSessions(
                    userId,
                    apiKeyOverride || undefined,
                );
                mergeServerSessions(response.sessions, userId);
                const serverArchived = response.sessions
                    .filter((s) => s.archived === true)
                    .map((s) => s.id);
                const localArchived =
                    useChatUiStore.getState().archivedSessionIds;
                const serverIds = new Set(response.sessions.map((s) => s.id));
                const toMigrate = localArchived.filter(
                    (id) => serverIds.has(id) && !serverArchived.includes(id),
                );
                for (const id of toMigrate) {
                    try {
                        await apiClient.archiveSession(
                            { user_id: userId, session_id: id },
                            apiKeyOverride || undefined,
                        );
                        serverArchived.push(id);
                    } catch (err) {
                        console.error("[chat-shell] archive migrate failed", err);
                    }
                }
                useChatUiStore
                    .getState()
                    .replaceArchivedSessionIds(serverArchived);
            } catch (err) {
                console.error("[chat-shell] session sync failed", err);
            } finally {
                if (!opts?.silent) setSessionsSyncing(false);
            }
        },
        [userId, apiKeyOverride, mergeServerSessions],
    );

    useEffect(() => {
        let cancelled = false;
        if (!userId || userId === "default") {
            return () => {
                cancelled = true;
            };
        }
        void (async () => {
            await syncSessionsFromServer();
            if (cancelled) return;
        })();
        return () => {
            cancelled = true;
        };
    }, [userId, syncSessionsFromServer]);

    // Multi-tab + focus: no SSE/WebSocket cache for session list — refetch HTTP.
    useEffect(() => {
        if (!userId || userId === "default") return;
        const unsub = subscribeSessionsSync((ev) => {
            if (ev.userId !== userId) return;
            void syncSessionsFromServer({ silent: true });
        });
        const onVisible = () => {
            if (document.visibilityState === "visible") {
                void syncSessionsFromServer({ silent: true });
            }
        };
        document.addEventListener("visibilitychange", onVisible);
        return () => {
            unsub();
            document.removeEventListener("visibilitychange", onVisible);
        };
    }, [userId, syncSessionsFromServer]);

    useEffect(() => {
        setDraftFiles((prev) => clearDraftAttachments(prev));
        setLiveActivity(null);
        setSuggestion(null);
    }, [activeSessionId]);

    const finalizeStreamingBubble = useCallback(
        (sessionId: string) => {
            const aid = streamingAssistantIdRef.current;
            streamingAssistantIdRef.current = null;
            if (!aid) return;
            const current = useCockpitStore
                .getState()
                .sessions.find((s) => s.id === sessionId)
                ?.messages.find((m) => m.id === aid);
            if (current?.streaming) {
                patchMessage(sessionId, aid, { streaming: false });
            }
        },
        [patchMessage],
    );

    const sendMutation = useMutation<
        void,
        ApiClientError | Error | DOMException,
        SendVariables
    >({
        mutationFn: async ({
            text,
            sessionId,
            userId: uid,
            history,
            attachedFileIds,
            keyOverride,
            retry,
            sttUsed,
            idempotencyKey,
        }) => {
            const turn = chatSessionRuntime.beginTurn(sessionId);
            turnGenerationRef.current = turn.generation;
            setInflightSessionId(sessionId);

            if (retry) truncateSessionMessagesTail(sessionId, 1);
            if (!retry) {
                appendMessage(sessionId, {
                    id: makeMessageId(),
                    role: "user",
                    content: text,
                    createdAt: Date.now(),
                    attached_file_ids:
                        attachedFileIds.length > 0 ? [...attachedFileIds] : [],
                    sttUsed: sttUsed === true,
                });
            }
            const assistantId = makeMessageId();
            streamingAssistantIdRef.current = assistantId;
            appendMessage(sessionId, {
                id: assistantId,
                role: "assistant",
                content: "",
                createdAt: Date.now(),
                streaming: true,
            });

            const liveSession = useCockpitStore
                .getState()
                .sessions.find((s) => s.id === sessionId);
            const turnMode =
                adminCapabilities && liveSession?.mode
                    ? liveSession.mode
                    : "chat";
            const includeDebug =
                adminCapabilities && turnMode === "debug";

            const idemKey =
                idempotencyKey ||
                (typeof crypto !== "undefined" && "randomUUID" in crypto
                    ? crypto.randomUUID()
                    : `idem_${Date.now()}_${Math.random().toString(16).slice(2)}`);
            idempotencyKeyRef.current.set(sessionId, idemKey);

            try {
                await streamChatTurn(
                    {
                        user_id: uid,
                        session_id: sessionId,
                        message: text,
                        mode: turnMode,
                        include_debug: includeDebug,
                        history,
                        idempotency_key: idemKey,
                        request_id: idemKey,
                        ...(attachedFileIds.length > 0
                            ? { attached_file_ids: attachedFileIds }
                            : {}),
                        ...(sttUsed === true ? { input_via_stt: true } : {}),
                    },
                    turn.signal,
                    {
                        includeTurnResult: adminCapabilities,
                        onDelta: (chunk) => {
                            if (
                                !chatSessionRuntime.isCurrent(
                                    sessionId,
                                    turn.generation,
                                )
                            ) {
                                return;
                            }
                            chatSessionRuntime.queueDelta(
                                sessionId,
                                assistantId,
                                chunk,
                                turn.generation,
                            );
                        },
                        onReplace: (full) => {
                            if (
                                !chatSessionRuntime.isCurrent(
                                    sessionId,
                                    turn.generation,
                                )
                            ) {
                                return;
                            }
                            patchMessage(sessionId, assistantId, {
                                content: full,
                            });
                        },
                        onStatus: (_stage, labelPl) => {
                            if (
                                !chatSessionRuntime.isCurrent(
                                    sessionId,
                                    turn.generation,
                                )
                            ) {
                                return;
                            }
                            if (labelPl) setLiveActivity(labelPl);
                        },
                        onTool: (name, status) => {
                            if (
                                !chatSessionRuntime.isCurrent(
                                    sessionId,
                                    turn.generation,
                                )
                            ) {
                                return;
                            }
                            setLiveActivity(
                                status === "start"
                                    ? `Narzędzie: ${name}`
                                    : "Przetwarzam wynik…",
                            );
                        },
                        onMemory: (count) => {
                            if (
                                !chatSessionRuntime.isCurrent(
                                    sessionId,
                                    turn.generation,
                                )
                            ) {
                                return;
                            }
                            setLiveActivity(`Pamięć: ${count} dopasowań`);
                        },
                        onDone: (result, attachmentsSummary, contextChips, pendingConfirmations) => {
                            if (
                                !chatSessionRuntime.isCurrent(
                                    sessionId,
                                    turn.generation,
                                )
                            ) {
                                return;
                            }
                            setLiveActivity(null);
                            if (pendingConfirmations && pendingConfirmations.length > 0) {
                                setPendingConfirmation(pendingConfirmations[0]);
                            } else {
                                setPendingConfirmation(null);
                            }
                            patchMessage(sessionId, assistantId, {
                                streaming: false,
                                ...(adminCapabilities && result
                                    ? { diagnostics: result }
                                    : {}),
                                ...(attachmentsSummary
                                    ? {
                                          attachmentsSummary,
                                          attachmentsUsedCount:
                                              attachedFileIds.length > 0
                                                  ? attachedFileIds.length
                                                  : 1,
                                      }
                                    : {}),
                                ...(contextChips && contextChips.length > 0
                                    ? { contextChips }
                                    : {}),
                            });
                            void reloadSessionHistoryFromServer({
                                sessionId,
                                userId: uid,
                                apiKeyOverride: keyOverride ?? "",
                                generation: turn.generation,
                            });
                        },
                    },
                    keyOverride,
                );
            } finally {
                chatSessionRuntime.endTurn(sessionId, turn.generation);
                if (
                    chatSessionRuntime.currentGeneration() === turn.generation
                ) {
                    streamingAssistantIdRef.current = null;
                }
            }
        },
        onSuccess: (_data, { sessionId, text, retry }) => {
            setLastFailedUserMessage(sessionId, null);
            idempotencyKeyRef.current.delete(sessionId);
            setDraftFiles((prev) => clearDraftAttachments(prev));
            const live = useCockpitStore
                .getState()
                .sessions.find((x) => x.id === sessionId);
            if (
                !retry &&
                text &&
                live &&
                !live.titleLockedByUser &&
                isPlaceholderSessionTitle(live.title)
            ) {
                useCockpitStore
                    .getState()
                    .applyAutoTitleFromUserMessage(sessionId, text);
            }
        },
        onError: (err, { sessionId, text }) => {
            idempotencyKeyRef.current.delete(sessionId);

            if (err instanceof DOMException && err.name === "AbortError") {
                finalizeStreamingBubble(sessionId);
                return;
            }
            console.error("[chat-turn]", err);
            const msg = formatChatTurnErrorMessage(err);
            const aid = streamingAssistantIdRef.current;
            streamingAssistantIdRef.current = null;
            if (aid) {
                const current = useCockpitStore
                    .getState()
                    .sessions.find((s) => s.id === sessionId)
                    ?.messages.find((m) => m.id === aid)?.content;
                patchMessage(sessionId, aid, {
                    streaming: false,
                    error: msg,
                    content: current || "",
                });
            } else {
                appendMessage(sessionId, {
                    id: makeMessageId(),
                    role: "assistant",
                    content: "",
                    createdAt: Date.now(),
                    error: msg,
                });
            }
            setLastFailedUserMessage(sessionId, text);
        },
        onSettled: () => {
            setLiveActivity(null);
            setInflightSessionId(null);
        },
    });

    const uploadOneFile = useCallback(
        (file: File) => {
            const key = `df_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
            const isImage =
                /\.(png|jpe?g|webp)$/i.test(file.name) ||
                (file.type || "").startsWith("image/");
            const previewUrl = isImage ? URL.createObjectURL(file) : undefined;
            setDraftFiles((prev) => [
                ...prev,
                {
                    key,
                    filename: file.name,
                    status: "uploading",
                    kind: isImage ? "image" : "text",
                    previewUrl,
                },
            ]);
            void uploadChatFile(
                { user_id: userId, session_id: session.id, file },
                apiKeyOverride || undefined,
            )
                .then((r) =>
                    setDraftFiles((prev) =>
                        prev.map((d) =>
                            d.key === key
                                ? {
                                      ...d,
                                      fileId: r.file_id,
                                      filename:
                                          typeof r.filename === "string"
                                              ? r.filename
                                              : file.name,
                                      status:
                                          r.status === "ok" || r.status === "image"
                                              ? "ready"
                                              : "error",
                                      error:
                                          r.status === "ok" || r.status === "image"
                                              ? undefined
                                              : r.extract_error ??
                                                `status: ${r.status}`,
                                  }
                                : d,
                        ),
                    ),
                )
                .catch((err: Error) => {
                    console.error("[chat-upload]", err);
                    setDraftFiles((prev) =>
                        prev.map((d) =>
                            d.key === key
                                ? {
                                      ...d,
                                      status: "error",
                                      error: err.message || "Upload nieudany",
                                  }
                                : d,
                        ),
                    );
                });
        },
        [apiKeyOverride, session.id, userId],
    );

    const handlePickFiles = useCallback(
        (files: FileList | null) => {
            const selected = Array.from(files ?? []);
            if (!selected.length) return;
            const capacity = Math.max(0, 5 - draftFiles.length);
            for (const file of selected.slice(0, capacity)) uploadOneFile(file);
        },
        [draftFiles.length, uploadOneFile],
    );

    const handleSend = useCallback(
        async (text: string, opts?: { sttUsed?: boolean }) => {
            const snap =
                useCockpitStore
                    .getState()
                    .sessions.find((s) => s.id === session.id) ?? session;
            const uid = authUserId || principal?.userId || snap.userId;
            const attachedFileIds = resolveAttachedFileIdsForSend(
                readyDraftFileIds(draftFiles),
            );
            // Fire-and-forget from composer perspective — mutation owns the turn.
            await sendMutation.mutateAsync({
                text,
                sessionId: snap.id,
                userId: uid,
                history: toChatHistoryPayload(snap.messages),
                attachedFileIds,
                keyOverride: apiKeyOverride || undefined,
                sttUsed: opts?.sttUsed === true,
            });
        },
        [
            apiKeyOverride,
            authUserId,
            draftFiles,
            principal?.userId,
            sendMutation,
            session,
        ],
    );

    const handleRetry = useCallback(async () => {
        const payload = retryPayloadForLastFailedMessage(session.id);
        if (!payload) return;
        const snap =
            useCockpitStore.getState().sessions.find((s) => s.id === session.id) ??
            session;
        const uid = authUserId || principal?.userId || snap.userId;
        const msgs = snap.messages;
        const userPrev = msgs.length >= 2 ? msgs[msgs.length - 2] : undefined;
        await sendMutation.mutateAsync({
            text: payload,
            sessionId: snap.id,
            userId: uid,
            history: toChatHistoryPayload(msgs.slice(0, -2)),
            attachedFileIds:
                userPrev?.role === "user"
                    ? [...(userPrev.attached_file_ids ?? [])]
                    : [],
            keyOverride: apiKeyOverride || undefined,
            retry: true,
        });
    }, [
        apiKeyOverride,
        authUserId,
        principal?.userId,
        retryPayloadForLastFailedMessage,
        sendMutation,
        session,
    ]);

    const handleStop = useCallback(() => {
        const sid = session.id;
        chatSessionRuntime.abortSession(sid);
        finalizeStreamingBubble(sid);
        setLiveActivity(null);
    }, [finalizeStreamingBubble, session.id]);

    const handleNewChat = useCallback(() => {
        chatSessionRuntime.resetForNewChat();
        useCockpitStore.getState().clearAllStreamingFlags();
        streamingAssistantIdRef.current = null;
        setInflightSessionId(null);
        setLiveActivity(null);
        setDraftFiles((prev) => clearDraftAttachments(prev));
        setSuggestion(null);
        createSession();
        setSidebarMobileOpen(false);
    }, [createSession, setSidebarMobileOpen]);

    const handleSelectSession = useCallback(
        (sessionId: string) => {
            if (sessionId === activeSessionId) {
                setSidebarMobileOpen(false);
                return;
            }
            chatSessionRuntime.abortAll();
            useCockpitStore.getState().clearAllStreamingFlags();
            streamingAssistantIdRef.current = null;
            setInflightSessionId(null);
            setLiveActivity(null);
            setActiveSession(sessionId);
            setSidebarMobileOpen(false);
        },
        [activeSessionId, setActiveSession, setSidebarMobileOpen],
    );

    const onDragEnter = (event: React.DragEvent<HTMLDivElement>) => {
        event.preventDefault();
        dragDepthRef.current += 1;
        if (event.dataTransfer.types.includes("Files")) setDropActive(true);
    };
    const onDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
        event.preventDefault();
        dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
        if (dragDepthRef.current === 0) setDropActive(false);
    };
    const onDrop = (event: React.DragEvent<HTMLDivElement>) => {
        event.preventDefault();
        dragDepthRef.current = 0;
        setDropActive(false);
        handlePickFiles(event.dataTransfer.files);
    };

    const sessionStreaming = session.messages.some((m) => m.streaming === true);
    const loading =
        sessionStreaming ||
        (sendMutation.isPending && inflightSessionId === session.id);
    const userScopedReady =
        Boolean(userId) && userId !== "default" && !authLoading;
    const streamingActive = sessionStreaming;

    if (authError && !principal && layout === "fullscreen") {
        return (
            <div className="chat-shell flex min-h-[100dvh] items-center justify-center px-4">
                <div className="max-w-md space-y-3 text-center">
                    <p className="text-lg font-medium text-[var(--chat-text)]">
                        Sesja niedostępna
                    </p>
                    <p className="text-sm text-[var(--chat-text-muted)]">
                        {authError}
                    </p>
                    <Button
                        type="button"
                        onClick={() => void logoutAndRedirect()}
                        className="bg-[var(--chat-accent)] text-[#090A0C]"
                    >
                        Przejdź do logowania
                    </Button>
                </div>
            </div>
        );
    }

    const shellClass =
        layout === "embedded"
            ? "chat-shell relative flex h-full min-h-0 w-full min-w-0 overflow-hidden antialiased"
            : "chat-shell relative flex h-[100dvh] w-full min-w-0 overflow-hidden antialiased";

    return (
        <div
            className={shellClass}
            data-testid={layout === "embedded" ? "admin-chat-shell" : "user-shell"}
            data-user-id={userId}
            onDragEnter={onDragEnter}
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
        >
            {layout === "fullscreen" ? (
                <Suspense fallback={null}>
                    <ChatUrlSyncBridge userId={userId} />
                </Suspense>
            ) : null}

            {layout === "fullscreen" ? (
                <ChatSidebar
                    username={principal?.username}
                    sessionsSyncing={sessionsSyncing}
                    onNewChat={handleNewChat}
                    onSelectSession={handleSelectSession}
                    onOpenMemory={() => {
                        openDrawer("pamiec");
                        setSidebarMobileOpen(false);
                    }}
                    onOpenFiles={() => {
                        setLiveActivity(
                            "Pliki dołączysz ikoną spinacza w polu wiadomości.",
                        );
                        setSidebarMobileOpen(false);
                    }}
                />
            ) : null}

            <div className="flex min-h-0 min-w-0 flex-1">
                <main className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                    {layout === "fullscreen" ? (
                        <ChatHeader
                            title={session.title}
                            apiKeyOverride={apiKeyOverride || undefined}
                            insightDisabled={!userScopedReady}
                        />
                    ) : (
                        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--chat-border)] px-3 py-2">
                            <p className="truncate text-sm font-medium text-[var(--chat-text)]">
                                {session.title}
                            </p>
                            <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={handleNewChat}
                            >
                                Nowa rozmowa
                            </Button>
                        </div>
                    )}

                    <div className="relative min-h-0 flex-1 overflow-hidden">
                        <ChatStage
                            key={session.id}
                            sessionId={session.id}
                            historyNonce={session.historyNonce ?? 0}
                            historyStatus={session.historyStatus ?? "idle"}
                            messages={session.messages}
                            loading={loading}
                            onSuggestion={(text) => setSuggestion(text)}
                            onRetry={() => void handleRetry()}
                        />
                    </div>

                    <div className="chat-composer-bar shrink-0 px-3 pb-[max(12px,env(safe-area-inset-bottom))] pt-2">
                        {liveActivity ? (
                            <p
                                className="mx-auto mb-2 max-w-[860px] truncate text-center text-xs text-[var(--chat-text-muted)]"
                                role="status"
                                aria-live="polite"
                            >
                                {liveActivity}
                            </p>
                        ) : !userScopedReady ? (
                            <p className="mb-2 text-center text-sm text-[var(--chat-text-muted)]">
                                Przygotowanie sesji…
                            </p>
                        ) : null}
                        {pendingConfirmation ? (
                            <ChatConfirmationBar
                                pending={pendingConfirmation}
                                userId={userId}
                                sessionId={session.id}
                                mode={
                                    adminCapabilities && session.mode
                                        ? session.mode
                                        : "chat"
                                }
                                apiKeyOverride={apiKeyOverride || undefined}
                                onDismiss={() => setPendingConfirmation(null)}
                                onConfirmed={(summary) => {
                                    setPendingConfirmation(null);
                                    appendMessage(session.id, {
                                        id: makeMessageId(),
                                        role: "assistant",
                                        content: summary,
                                        createdAt: Date.now(),
                                    });
                                }}
                            />
                        ) : null}
                        <ChatComposer
                            onSend={handleSend}
                            onRetry={handleRetry}
                            onStop={handleStop}
                            disabled={loading || !userScopedReady}
                            retryDisabled={!session.lastFailedUserMessage}
                            stopVisible={streamingActive}
                            draftFiles={draftFiles}
                            onRemoveDraft={(key) =>
                                setDraftFiles((prev) => {
                                    const hit = prev.find((f) => f.key === key);
                                    if (hit?.previewUrl)
                                        URL.revokeObjectURL(hit.previewUrl);
                                    return prev.filter((f) => f.key !== key);
                                })
                            }
                            onPickFiles={handlePickFiles}
                            attachDisabled={draftFiles.length >= 5}
                            voiceApiKeyOverride={apiKeyOverride || undefined}
                            suggestion={suggestion}
                            onSuggestionConsumed={() => setSuggestion(null)}
                        />
                    </div>
                </main>

                {layout === "fullscreen" ? (
                    <ChatDrawer
                        userId={userId}
                        sessionId={session.id}
                        apiKeyOverride={apiKeyOverride || undefined}
                        messages={session.messages}
                    />
                ) : null}
            </div>

            {dropActive ? (
                <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                    <div className="border border-[var(--chat-border)] bg-[#15181D] px-8 py-6 text-center">
                        <UploadCloud className="mx-auto mb-2 h-8 w-8 text-[var(--chat-accent)]" />
                        <p className="text-[var(--chat-text)]">Upuść pliki</p>
                    </div>
                </div>
            ) : null}
        </div>
    );
}

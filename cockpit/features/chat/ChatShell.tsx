"use client";

import { useMutation } from "@tanstack/react-query";
import { UploadCloud } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    ChatComposer,
    type ChatDraftAttachment,
} from "@/features/chat/ChatComposer";
import { ChatDrawer } from "@/features/chat/ChatDrawer";
import { ChatHeader } from "@/features/chat/ChatHeader";
import { ChatSidebar } from "@/features/chat/ChatSidebar";
import { ChatStage } from "@/features/chat/ChatStage";
import { useChatUiStore } from "@/features/chat/chat-ui-store";
import { Button } from "@/components/ui/button";
import { streamChatTurn } from "@/lib/api/chat-turn-stream";
import { uploadChatFile } from "@/lib/api/chat-upload";
import { ApiClientError } from "@/lib/api/client";
import { formatChatTurnErrorMessage } from "@/lib/api/hub-auth-errors";
import {
    clearDraftAttachments,
    readyDraftFileIds,
} from "@/lib/chat/draft-attachments";
import { toChatHistoryPayload } from "@/lib/chat/payload-history";
import { resolveAttachedFileIdsForSend } from "@/lib/chat/resolve-attached-file-ids";
import { isPlaceholderSessionTitle } from "@/lib/chat/session-title";
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

export function ChatShell() {
    const { principal, loading: authLoading, error: authError } =
        useAuthPrincipal();
    const {
        sessions,
        activeSessionId,
        appendMessage,
        appendMessageContent,
        patchMessage,
        truncateSessionMessagesTail,
        retryPayloadForLastFailedMessage,
        setLastFailedUserMessage,
        apiKeyOverride,
        mergeServerSessions,
        authUserId,
    } = useCockpitStore();
    const session = sessions.find((s) => s.id === activeSessionId) ?? sessions[0];
    const userId = authUserId || principal?.userId || session.userId;

    const { setSidebarMobileOpen, openDrawer } = useChatUiStore();

    const [liveActivity, setLiveActivity] = useState<string | null>(null);
    const [draftFiles, setDraftFiles] = useState<ChatDraftAttachment[]>([]);
    const [dropActive, setDropActive] = useState(false);
    const [suggestion, setSuggestion] = useState<string | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const streamingAssistantIdRef = useRef<string | null>(null);
    const dragDepthRef = useRef(0);
    /** Per-session idempotency key for in-flight / retryable sends. */
    const idempotencyKeyRef = useRef<Map<string, string>>(new Map());

    useSessionHistoryFromServer({
        sessionId: session.id,
        userId,
        apiKeyOverride,
    });

    useEffect(() => {
        let cancelled = false;
        if (!userId || userId === "default") return () => { cancelled = true; };
        void (async () => {
            try {
                const response = await apiClient.getSessions(
                    userId,
                    apiKeyOverride || undefined,
                );
                if (!cancelled) mergeServerSessions(response.sessions, userId);
            } catch (err) {
                console.error("[chat-shell] session sync failed", err);
            }
        })();
        return () => { cancelled = true; };
    }, [userId, apiKeyOverride, mergeServerSessions]);

    useEffect(() => {
        setDraftFiles((prev) => clearDraftAttachments(prev));
    }, [activeSessionId]);

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
            const controller = new AbortController();
            abortRef.current = controller;
            const idemKey =
                idempotencyKey ||
                idempotencyKeyRef.current.get(sessionId) ||
                (typeof crypto !== "undefined" && "randomUUID" in crypto
                    ? crypto.randomUUID()
                    : `idem_${Date.now()}_${Math.random().toString(16).slice(2)}`);
            idempotencyKeyRef.current.set(sessionId, idemKey);
            await streamChatTurn(
                {
                    user_id: uid,
                    session_id: sessionId,
                    message: text,
                    mode: "chat",
                    include_debug: false,
                    history,
                    idempotency_key: idemKey,
                    request_id: idemKey,
                    ...(attachedFileIds.length > 0
                        ? { attached_file_ids: attachedFileIds }
                        : {}),
                    ...(sttUsed === true ? { input_via_stt: true } : {}),
                },
                controller.signal,
                {
                    includeTurnResult: false,
                    onDelta: (chunk) =>
                        appendMessageContent(sessionId, assistantId, chunk),
                    onReplace: (full) =>
                        patchMessage(sessionId, assistantId, { content: full }),
                    onStatus: (_stage, labelPl) => {
                        if (labelPl) setLiveActivity(labelPl);
                    },
                    onTool: (name, status) =>
                        setLiveActivity(
                            status === "start"
                                ? `Narzędzie: ${name}`
                                : "Przetwarzam wynik…",
                        ),
                    onMemory: (count) =>
                        setLiveActivity(`Pamięć: ${count} dopasowań`),
                    onDone: (_result, attachmentsSummary, contextChips) => {
                        setLiveActivity(null);
                        patchMessage(sessionId, assistantId, {
                            streaming: false,
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
                        });
                    },
                },
                keyOverride,
            );
            streamingAssistantIdRef.current = null;
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
            if (err instanceof DOMException && err.name === "AbortError") {
                const aid = streamingAssistantIdRef.current;
                streamingAssistantIdRef.current = null;
                if (aid) patchMessage(sessionId, aid, { streaming: false });
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
            abortRef.current = null;
            setLiveActivity(null);
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

    const handleSend = async (text: string, opts?: { sttUsed?: boolean }) => {
        const snap =
            useCockpitStore.getState().sessions.find((s) => s.id === session.id) ??
            session;
        const uid = authUserId || principal?.userId || snap.userId;
        const attachedFileIds = resolveAttachedFileIdsForSend(
            readyDraftFileIds(draftFiles),
        );
        await sendMutation.mutateAsync({
            text,
            sessionId: snap.id,
            userId: uid,
            history: toChatHistoryPayload(snap.messages),
            attachedFileIds,
            keyOverride: apiKeyOverride || undefined,
            sttUsed: opts?.sttUsed === true,
        });
    };

    const handleRetry = async () => {
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
    };

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

    const loading = sendMutation.isPending;
    const userScopedReady =
        Boolean(userId) && userId !== "default" && !authLoading;
    const lastMsg = session.messages[session.messages.length - 1];
    const streamingActive =
        loading &&
        lastMsg?.role === "assistant" &&
        lastMsg.streaming === true;

    if (authError && !principal) {
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

    return (
        <div
            className="chat-shell relative flex h-[100dvh] w-full min-w-0 overflow-hidden antialiased"
            data-testid="user-shell"
            data-user-id={userId}
            onDragEnter={onDragEnter}
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
        >
            <ChatSidebar
                username={principal?.username}
                onSelectSession={() => setSidebarMobileOpen(false)}
                onOpenMemory={() => {
                    openDrawer("pamiec");
                    setSidebarMobileOpen(false);
                }}
                onOpenFiles={() => {
                    setLiveActivity(
                        "Pliki dołączysz ikoną spinacza w polu wiadomości.",
                    );
                    window.setTimeout(() => setLiveActivity(null), 4000);
                    setSidebarMobileOpen(false);
                }}
            />

            <div className="flex min-h-0 min-w-0 flex-1">
                <main className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                    <ChatHeader
                        title={session.title}
                        apiKeyOverride={apiKeyOverride || undefined}
                        insightDisabled={!userScopedReady}
                    />

                    <div className="relative min-h-0 flex-1 overflow-hidden">
                        <ChatStage
                            key={session.id}
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
                        <ChatComposer
                            onSend={handleSend}
                            onRetry={handleRetry}
                            onStop={() => abortRef.current?.abort()}
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

                <ChatDrawer
                    userId={userId}
                    apiKeyOverride={apiKeyOverride || undefined}
                    messages={session.messages}
                />
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

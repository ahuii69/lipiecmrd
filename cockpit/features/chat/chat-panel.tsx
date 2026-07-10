"use client";

import { useMutation } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MessageComposer } from "@/features/chat/message-composer";
import { MessageList } from "@/features/chat/message-list";
import { streamChatTurn } from "@/lib/api/chat-turn-stream";
import { ApiClientError } from "@/lib/api/client";
import { formatChatTurnErrorMessage } from "@/lib/api/hub-auth-errors";
import { ChatTurnRequest } from "@/lib/api/types";
import { toChatHistoryPayload } from "@/lib/chat/payload-history";
import { isPlaceholderSessionTitle } from "@/lib/chat/session-title";
import { useVisualViewportComposerPad } from "@/lib/chat/use-visual-viewport-composer-pad";
import {
    reloadSessionHistoryFromServer,
    useSessionHistoryFromServer,
} from "@/lib/hooks/use-session-history-from-server";
import { useCockpitStore } from "@/lib/store/cockpit-store";

interface SendVariables {
    text: string;
    sessionId: string;
    userId: string;
    mode: ChatTurnRequest["mode"];
    history: ReturnType<typeof toChatHistoryPayload>;
    keyOverride?: string;
    retry?: boolean;
}

export function ChatPanel() {
    const {
        sessions,
        activeSessionId,
        appendMessage,
        appendMessageContent,
        patchMessage,
        truncateSessionMessagesTail,
        retryPayloadForLastFailedMessage,
        apiKeyOverride,
        selectMessage,
        setLastFailedUserMessage,
    } = useCockpitStore();

    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    useSessionHistoryFromServer({
        sessionId: session.id,
        userId: session.userId,
        apiKeyOverride: apiKeyOverride,
    });

    useVisualViewportComposerPad();

    const abortRef = useRef<AbortController | null>(null);
    const streamingAssistantIdRef = useRef<string | null>(null);
    const [liveActivity, setLiveActivity] = useState<string | null>(null);
    const [scrollKick, setScrollKick] = useState(0);

    const sendMutation = useMutation<
        void,
        ApiClientError | Error | DOMException,
        SendVariables
    >({
        mutationFn: async ({
            text,
            sessionId,
            userId,
            mode,
            history: historyFromCaller,
            keyOverride,
            retry,
        }: SendVariables) => {
            const history = retry
                ? historyFromCaller
                : toChatHistoryPayload(
                      useCockpitStore
                          .getState()
                          .sessions.find((s) => s.id === sessionId)?.messages ??
                          [],
                  );
            if (retry) {
                /* Jedna para user+assistant na próbę — usuń błędną odpowiedź zanim dołożysz nową bańkę streamu. */
                truncateSessionMessagesTail(sessionId, 1);
            }
            if (!retry) {
                const userMsgId = `m_${Date.now()}_${Math.random().toString(16).slice(2, 7)}`;
                appendMessage(sessionId, {
                    id: userMsgId,
                    role: "user",
                    content: text,
                    createdAt: Date.now(),
                });
            }

            const assistantId = `m_${Date.now()}_${Math.random().toString(16).slice(2, 7)}`;
            streamingAssistantIdRef.current = assistantId;
            appendMessage(sessionId, {
                id: assistantId,
                role: "assistant",
                content: "",
                createdAt: Date.now(),
                streaming: true,
            });
            selectMessage(assistantId);
            setScrollKick((n) => n + 1);

            const payload = {
                user_id: userId,
                session_id: sessionId,
                message: text,
                mode,
                include_debug: mode === "debug",
                history,
            } as const;

            abortRef.current = new AbortController();

            await streamChatTurn(
                payload,
                abortRef.current.signal,
                {
                    includeTurnResult: true,
                    onDelta: (chunk) => {
                        appendMessageContent(sessionId, assistantId, chunk);
                    },
                    onReplace: (full) => {
                        patchMessage(sessionId, assistantId, { content: full });
                    },
                    onStatus: (_stage, labelPl) => {
                        if (labelPl) setLiveActivity(labelPl);
                    },
                    onTool: (name, st) => {
                        setLiveActivity(
                            st === "start"
                                ? `Narzędzie: ${name}`
                                : "Przetwarzam wynik narzędzia…",
                        );
                    },
                    onMemory: (n) => {
                        setLiveActivity(`Uwzględniam wcześniejszy kontekst (${n})`);
                    },
                    onDone: (result) => {
                        setLiveActivity(null);
                        patchMessage(sessionId, assistantId, {
                            streaming: false,
                            diagnostics: result,
                        });
                        void reloadSessionHistoryFromServer({
                            sessionId,
                            userId,
                            apiKeyOverride: keyOverride ?? "",
                        });
                    },
                },
                keyOverride,
            );

            streamingAssistantIdRef.current = null;
        },
        onSuccess: (_data, variables) => {
            setLastFailedUserMessage(variables.sessionId, null);

            const snap = useCockpitStore.getState().sessions.find(
                (s) => s.id === variables.sessionId,
            );
            if (
                snap &&
                !variables.retry &&
                !snap.titleLockedByUser &&
                isPlaceholderSessionTitle(snap.title)
            ) {
                useCockpitStore
                    .getState()
                    .applyAutoTitleFromUserMessage(
                        variables.sessionId,
                        variables.text,
                    );
            }
        },
        onError: (err, variables) => {
            if (err instanceof DOMException && err.name === "AbortError") {
                const aid = streamingAssistantIdRef.current;
                streamingAssistantIdRef.current = null;
                if (aid) {
                    patchMessage(variables.sessionId, aid, {
                        streaming: false,
                    });
                }
                return;
            }
            const message = formatChatTurnErrorMessage(err);
            const aid = streamingAssistantIdRef.current;
            streamingAssistantIdRef.current = null;
            if (aid) {
                patchMessage(variables.sessionId, aid, {
                    streaming: false,
                    error: message,
                    content:
                        useCockpitStore
                            .getState()
                            .sessions.find((s) => s.id === variables.sessionId)
                            ?.messages.find((m) => m.id === aid)?.content || "",
                });
            } else {
                appendMessage(variables.sessionId, {
                    id: `m_${Date.now()}_err`,
                    role: "assistant",
                    content: "Nie udało się dokończyć tury.",
                    createdAt: Date.now(),
                    error: message,
                });
            }
            setLastFailedUserMessage(variables.sessionId, variables.text);
        },
        onSettled: () => {
            abortRef.current = null;
            setLiveActivity(null);
        },
    });

    const canRetry = useMemo(
        () => Boolean(retryPayloadForLastFailedMessage(session.id)),
        [retryPayloadForLastFailedMessage, session.id],
    );

    const lastMsg = session.messages[session.messages.length - 1];
    const streamingActive =
        sendMutation.isPending &&
        lastMsg?.role === "assistant" &&
        lastMsg?.streaming === true;

    const send = async (text: string) => {
        const sessionSnapshot =
            useCockpitStore.getState().sessions.find(
                (s) => s.id === activeSessionId,
            ) ?? session;
        await sendMutation.mutateAsync({
            text,
            sessionId: sessionSnapshot.id,
            userId: sessionSnapshot.userId,
            mode: sessionSnapshot.mode,
            /* Rzeczywista historia jest liczona w mutationFn z getState() tuż przed zapisem (spójność 1:1). */
            history: [],
            keyOverride: apiKeyOverride || undefined,
        });
    };

    const retry = async () => {
        const text = retryPayloadForLastFailedMessage(session.id);
        if (!text) return;
        const sessionSnapshot =
            useCockpitStore.getState().sessions.find(
                (s) => s.id === activeSessionId,
            ) ?? session;
        const msgs = sessionSnapshot.messages;
        await sendMutation.mutateAsync({
            text,
            sessionId: sessionSnapshot.id,
            userId: sessionSnapshot.userId,
            mode: sessionSnapshot.mode,
            history: toChatHistoryPayload(
                msgs.length >= 2 ? msgs.slice(0, -2) : msgs,
            ),
            keyOverride: apiKeyOverride || undefined,
            retry: true,
        });
    };

    const stopStream = () => {
        abortRef.current?.abort();
    };

    return (
        <Card className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <CardHeader className="shrink-0 space-y-0 pb-2">
                <CardTitle>Czat operacyjny</CardTitle>
            </CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden pt-0">
                {liveActivity ? (
                    <p className="shrink-0 text-xs text-muted-foreground">
                        {liveActivity}
                    </p>
                ) : null}
                <MessageList
                    key={session.id}
                    messages={session.messages}
                    loading={sendMutation.isPending}
                    forceScrollBottomNonce={scrollKick}
                />
                <div className="shrink-0 pb-[var(--cockpit-composer-vv-pad,0px)]">
                    <MessageComposer
                        onSend={send}
                        onRetry={retry}
                        onStop={stopStream}
                        disabled={sendMutation.isPending}
                        retryDisabled={!canRetry}
                        stopVisible={streamingActive}
                    />
                </div>
            </CardContent>
        </Card>
    );
}

"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { ChatTurnResponse } from "@/lib/api/types";
import {
    deriveSessionTitleFromMessage,
    isPlaceholderSessionTitle,
    selectNextActiveAfterDelete,
} from "@/lib/chat/session-title";
import { ChatMode, CockpitSection, SessionSummary } from "@/lib/types/ui";

/** Treść wiadomości (UI + historia); to samo co „text” w specyfikacji produktowej. */
export interface ChatUIMessage {
    id: string;
    role: "user" | "assistant";
    content: string;
    createdAt: number;
    error?: string;
    diagnostics?: ChatTurnResponse;
    /** Assistant message: odpowiedź w trakcie strumienia SSE */
    streaming?: boolean;
    /** Krótki podpis z backendu (np. użyte pliki) — bez debug payloadu */
    attachmentsSummary?: string;
    /** Pliki wysłane z tą wiadomością użytkownika (retry / persystencja). */
    attached_file_ids?: string[];
    /** Liczba plików uwzględnionych w odpowiedzi (asystent). */
    attachmentsUsedCount?: number;
    /** Backend / SSE: skąd brała się odpowiedź (attachment, web, memory, …). */
    contextChips?: string[];
    /** Użytkownik wstawił treść przez STT w tej turze. */
    sttUsed?: boolean;
}

/** Limit wiadomości w UI i w payloadzie history — musi pokryć sensowną sesję bez gubienia kontekstu. */
const MAX_MESSAGES_PER_SESSION = 200;

function normalizeChatMessage(m: ChatUIMessage): ChatUIMessage {
    return {
        ...m,
        content: typeof m.content === "string" ? m.content : "",
        createdAt: Number(m.createdAt ?? Date.now()),
        streaming: m.streaming === true,
        attached_file_ids: Array.isArray(m.attached_file_ids)
            ? m.attached_file_ids
            : [],
    };
}

function trimMessages(messages: ChatUIMessage[]): ChatUIMessage[] {
    if (messages.length <= MAX_MESSAGES_PER_SESSION) {
        return messages;
    }
    return messages.slice(-MAX_MESSAGES_PER_SESSION);
}

export interface SessionState extends SessionSummary {
    messages: ChatUIMessage[];
    lastFailedUserMessage: string | null;
    /** Po ręcznym rename — nie nadpisuj auto-tytułu z pierwszej wiadomości. */
    titleLockedByUser?: boolean;
    /** Inkrement po załadowaniu transkryptu z backendu (scroll / remount listy). */
    historyNonce?: number;
}

interface CockpitState {
    sections: CockpitSection[];
    currentSection: CockpitSection;
    sessions: SessionState[];
    activeSessionId: string;
    selectedMessageId?: string;
    inspectorOpen: boolean;
    apiKeyOverride: string;
    /** Authenticated principal.user_id — sole identity source after login. */
    authUserId: string | null;

    setSection: (section: CockpitSection) => void;
    createSession: () => void;
    setActiveSession: (sessionId: string) => void;
    setSessionMode: (sessionId: string, mode: ChatMode) => void;
    appendMessage: (sessionId: string, msg: ChatUIMessage) => void;
    appendMessageContent: (sessionId: string, messageId: string, chunk: string) => void;
    patchMessage: (
        sessionId: string,
        messageId: string,
        patch: Partial<ChatUIMessage>,
    ) => void;
    updateSessionTitle: (sessionId: string, title: string) => void;
    applyAutoTitleFromUserMessage: (sessionId: string, messageText: string) => void;
    mergeServerSessions: (
        rows: {
            id: string;
            title: string;
            created_at: number;
            updated_at: number;
        }[],
        defaultUserId: string,
    ) => void;
    /** Nadpisuje wiadomości z GET /chat/session/.../history (backend = źródło prawdy). */
    replaceSessionMessagesFromServer: (
        sessionId: string,
        rows: Array<{
            id: string;
            role: string;
            content: string;
            created_at: string;
        }>,
    ) => void;
    /** Usuwa ostatnie ``count`` wiadomości z sesji (np. retry: zdjąć błędną odpowiedź asystenta). */
    truncateSessionMessagesTail: (sessionId: string, count: number) => void;
    deleteSession: (sessionId: string) => void;
    clearSessionMessages: (sessionId: string) => void;
    setLastFailedUserMessage: (
        sessionId: string,
        payload: string | null,
    ) => void;
    selectMessage: (id?: string) => void;
    setInspectorOpen: (open: boolean) => void;
    setApiKeyOverride: (value: string) => void;
    /**
     * Bind all session scopes to principal.user_id from GET /auth/me.
     * Clears legacy random localStorage user ids that caused ownership 403.
     */
    bindAuthPrincipal: (userId: string) => string;
    /** @deprecated Use bindAuthPrincipal — kept as alias for gradual migration. */
    ensureUserScope: () => string;
    retryPayloadForLastFailedMessage: (sessionId: string) => string | null;
}

/** Kolejność zsynchronizowana z `left-sidebar.tsx` navItems (CockpitSection). */
const sections: CockpitSection[] = [
    "overview",
    "chat",
    "memory",
    "psyche",
    "research",
    "planner",
    "reasoning",
    "goals",
    "runtime",
    "capabilities",
    "system",
    "agent-control",
    "consistency",
    "reflections",
    "policy",
    "simulations",
    "memory-v2",
    "psyche-v2",
    "identity",
    "contradictions",
    "procedures",
    "calibration",
];

/**
 * Bootstrap session for SSR + first client paint must be **deterministic**:
 * `Date.now()` / `Math.random()` in the store initializer run in different
 * processes (Node vs browser) and cause hydration mismatches.
 */
const INITIAL_SESSION_ID = "s_initial";
/** Legacy key that generated random u_* ids — causes ownership 403 vs auth principal. */
const LEGACY_USER_SCOPE_KEY = "aihub-cockpit-user-scope-v1";

function clearLegacyUserScopeStorage(): void {
    const g = globalThis as unknown as { localStorage?: Storage };
    const ls = g.localStorage;
    if (!ls) return;
    try {
        ls.removeItem(LEGACY_USER_SCOPE_KEY);
    } catch {
        // ignore quota / private mode
    }
}

function isPlaceholderUserId(userId: string | null | undefined): boolean {
    const value = (userId || "").trim();
    if (!value || value === "default" || value === "test-user") return true;
    if (value.startsWith("u_") && value.includes("_")) return true;
    return false;
}

function createInitialSession(idx = 1, userId = "default"): SessionState {
    if (idx === 1) {
        return {
            id: INITIAL_SESSION_ID,
            title: "Nowa rozmowa",
            userId,
            mode: "chat",
            createdAt: 0,
            updatedAt: 0,
            messages: [],
            lastFailedUserMessage: null,
        };
    }
    const ts = Date.now();
    return {
        id: `s_${ts}_${Math.random().toString(16).slice(2, 8)}`,
        title: "Nowa rozmowa",
        userId,
        mode: "chat",
        createdAt: ts,
        updatedAt: ts,
        messages: [],
        lastFailedUserMessage: null,
    };
}

function normalizeSession(session: SessionState): SessionState {
    const raw = Array.isArray(session.messages) ? session.messages : [];
    const messages = trimMessages(
        raw.map((x) =>
            normalizeChatMessage({
                ...(x as ChatUIMessage),
                streaming: false,
            }),
        ),
    );
    return {
        ...session,
        messages,
        lastFailedUserMessage: session.lastFailedUserMessage ?? null,
        titleLockedByUser: Boolean(session.titleLockedByUser),
        historyNonce: Number(session.historyNonce ?? 0),
        updatedAt: Number(session.updatedAt ?? Date.now()),
        createdAt: Number(session.createdAt ?? Date.now()),
        mode: session.mode,
    };
}

export const useCockpitStore = create<CockpitState>()(
    persist(
        (set, get) => {
            const initial = createInitialSession(1);

            return {
                sections,
                currentSection: "chat",
                sessions: [initial],
                activeSessionId: initial.id,
                inspectorOpen: false,
                apiKeyOverride: "",
                authUserId: null,

                setSection: (section) => set({ currentSection: section }),

                createSession: () =>
                    set((state) => {
                        const emptyPlaceholder = state.sessions.find(
                            (s) =>
                                s.messages.length === 0 &&
                                isPlaceholderSessionTitle(s.title) &&
                                !s.titleLockedByUser,
                        );
                        if (emptyPlaceholder) {
                            return {
                                activeSessionId: emptyPlaceholder.id,
                                currentSection: "chat",
                                selectedMessageId: undefined,
                            };
                        }
                        const uid =
                            state.authUserId ||
                            state.sessions.find((s) => s.id === state.activeSessionId)
                                ?.userId ||
                            state.sessions[0]?.userId ||
                            "default";
                        const next = createInitialSession(state.sessions.length + 1, uid);
                        return {
                            sessions: [next, ...state.sessions],
                            activeSessionId: next.id,
                            currentSection: "chat",
                            selectedMessageId: undefined,
                        };
                    }),

                setActiveSession: (sessionId) =>
                    set((state) => {
                        const exists = state.sessions.some(
                            (s) => s.id === sessionId,
                        );
                        if (!exists) return {};
                        return {
                            activeSessionId: sessionId,
                            selectedMessageId: undefined,
                        };
                    }),

                setSessionMode: (sessionId, mode) =>
                    set((state) => ({
                        sessions: state.sessions.map((s) =>
                            s.id === sessionId
                                ? { ...s, mode, updatedAt: Date.now() }
                                : s,
                        ),
                    })),

                appendMessage: (sessionId, msg) =>
                    set((state) => ({
                        sessions: state.sessions.map((s) => {
                            if (s.id !== sessionId) return s;
                            const next = trimMessages([
                                ...s.messages,
                                normalizeChatMessage(msg),
                            ]);
                            return {
                                ...s,
                                messages: next,
                                updatedAt: Date.now(),
                            };
                        }),
                    })),

                appendMessageContent: (sessionId, messageId, chunk) =>
                    set((state) => ({
                        sessions: state.sessions.map((s) => {
                            if (s.id !== sessionId) return s;
                            return {
                                ...s,
                                messages: s.messages.map((m) =>
                                    m.id === messageId
                                        ? { ...m, content: m.content + chunk }
                                        : m,
                                ),
                                updatedAt: Date.now(),
                            };
                        }),
                    })),

                patchMessage: (sessionId, messageId, patch) =>
                    set((state) => ({
                        sessions: state.sessions.map((s) => {
                            if (s.id !== sessionId) return s;
                            return {
                                ...s,
                                messages: s.messages.map((m) =>
                                    m.id === messageId ? { ...m, ...patch } : m,
                                ),
                                updatedAt: Date.now(),
                            };
                        }),
                    })),

                updateSessionTitle: (sessionId, title) =>
                    set((state) => ({
                        sessions: state.sessions.map((s) =>
                            s.id === sessionId
                                ? {
                                      ...s,
                                      title,
                                      titleLockedByUser: true,
                                      updatedAt: Date.now(),
                                  }
                                : s,
                        ),
                    })),

                applyAutoTitleFromUserMessage: (sessionId, messageText) =>
                    set((state) => ({
                        sessions: state.sessions.map((s) => {
                            if (s.id !== sessionId) return s;
                            if (s.titleLockedByUser) return s;
                            if (!isPlaceholderSessionTitle(s.title)) return s;
                            const title = deriveSessionTitleFromMessage(
                                messageText,
                            );
                            return { ...s, title, updatedAt: Date.now() };
                        }),
                    })),

                replaceSessionMessagesFromServer: (sessionId, rows) =>
                    set((state) => {
                        const prevSession = state.sessions.find(
                            (x) => x.id === sessionId,
                        );
                        const mergePool = new Map<string, ChatUIMessage[]>();
                        for (const pm of prevSession?.messages ?? []) {
                            const k = `${pm.role}\0${(pm.content || "").trim()}`;
                            const arr = mergePool.get(k);
                            if (arr) arr.push(pm);
                            else mergePool.set(k, [pm]);
                        }
                        const takeMergedDecor = (
                            role: "user" | "assistant",
                            content: string,
                        ): Partial<ChatUIMessage> => {
                            const k = `${role}\0${content.trim()}`;
                            const arr = mergePool.get(k);
                            const prevMsg = arr?.shift();
                            if (!prevMsg) return {};
                            const out: Partial<ChatUIMessage> = {};
                            if (prevMsg.attachmentsSummary) {
                                out.attachmentsSummary = prevMsg.attachmentsSummary;
                            }
                            if (
                                Array.isArray(prevMsg.contextChips) &&
                                prevMsg.contextChips.length > 0
                            ) {
                                out.contextChips = prevMsg.contextChips;
                            }
                            if (
                                typeof prevMsg.attachmentsUsedCount === "number" &&
                                prevMsg.attachmentsUsedCount > 0
                            ) {
                                out.attachmentsUsedCount =
                                    prevMsg.attachmentsUsedCount;
                            }
                            if (
                                Array.isArray(prevMsg.attached_file_ids) &&
                                prevMsg.attached_file_ids.length > 0
                            ) {
                                out.attached_file_ids = [
                                    ...prevMsg.attached_file_ids,
                                ];
                            }
                            if (prevMsg.sttUsed === true) {
                                out.sttUsed = true;
                            }
                            return out;
                        };

                        return {
                            sessions: state.sessions.map((s) => {
                                if (s.id !== sessionId) return s;
                                const seen = new Set<string>();
                                const ordered = rows
                                    .map((row, idx) => ({ row, idx }))
                                    .sort((a, b) => {
                                        const ta = Date.parse(a.row.created_at);
                                        const tb = Date.parse(b.row.created_at);
                                        const na = Number.isFinite(ta) ? ta : 0;
                                        const nb = Number.isFinite(tb) ? tb : 0;
                                        if (na !== nb) return na - nb;
                                        // Stable tie-break: preserve backend order for equal timestamps.
                                        return a.idx - b.idx;
                                    })
                                    .map((x) => x.row);
                                const messages: ChatUIMessage[] = [];
                                for (const m of ordered) {
                                    const id = String(m.id);
                                    if (seen.has(id)) continue;
                                    seen.add(id);
                                    const t = Date.parse(m.created_at);
                                    const role =
                                        m.role === "assistant"
                                            ? "assistant"
                                            : "user";
                                    const content =
                                        typeof m.content === "string"
                                            ? m.content
                                            : "";
                                    const rowAf = (
                                        m as {
                                            attached_file_ids?: string[];
                                        }
                                    ).attached_file_ids;
                                    const decor = takeMergedDecor(role, content);
                                    const msg: ChatUIMessage = {
                                        id,
                                        role,
                                        content,
                                        createdAt: Number.isFinite(t)
                                            ? t
                                            : Date.now(),
                                        streaming: false,
                                        ...decor,
                                    };
                                    if (
                                        Array.isArray(rowAf) &&
                                        rowAf.length > 0
                                    ) {
                                        msg.attached_file_ids = [...rowAf];
                                    }
                                    messages.push(msg);
                                }
                                return {
                                    ...s,
                                    messages,
                                    historyNonce: (s.historyNonce ?? 0) + 1,
                                };
                            }),
                        };
                    }),

                truncateSessionMessagesTail: (sessionId, count) =>
                    set((state) => {
                        const n = Math.max(0, Math.floor(count));
                        if (n === 0) return state;
                        return {
                            sessions: state.sessions.map((s) => {
                                if (s.id !== sessionId) return s;
                                const cur = s.messages;
                                if (cur.length === 0) return s;
                                const next = trimMessages(cur.slice(0, -n));
                                return {
                                    ...s,
                                    messages: next,
                                    updatedAt: Date.now(),
                                };
                            }),
                        };
                    }),

                mergeServerSessions: (rows, defaultUserId) =>
                    set((state) => {
                        const toMs = (t: number) =>
                            t < 1e12 ? Math.round(t * 1000) : Math.round(t);
                        const fromServer = rows.map((r) => {
                            const existing = state.sessions.find(
                                (s) => s.id === r.id,
                            );
                            const ca = toMs(r.created_at);
                            const ua = toMs(r.updated_at);
                            if (existing) {
                                return {
                                    ...existing,
                                    title: r.title,
                                    createdAt: ca,
                                    updatedAt: ua,
                                    titleLockedByUser:
                                        existing.titleLockedByUser ?? false,
                                };
                            }
                            return {
                                id: r.id,
                                title: r.title,
                                userId: defaultUserId,
                                mode: "chat" as ChatMode,
                                createdAt: ca,
                                updatedAt: ua,
                                messages: [],
                                lastFailedUserMessage: null,
                                titleLockedByUser: false,
                            };
                        });
                        const serverIds = new Set(rows.map((r) => r.id));
                        const localOnly = state.sessions.filter(
                            (s) => !serverIds.has(s.id),
                        );
                        return { sessions: [...fromServer, ...localOnly] };
                    }),

                deleteSession: (sessionId) =>
                    set((state) => {
                        const filtered = state.sessions.filter((s) => s.id !== sessionId);
                        if (filtered.length === 0) {
                            const newSession = createInitialSession(1);
                            return {
                                sessions: [newSession],
                                activeSessionId: newSession.id,
                                selectedMessageId: undefined,
                            };
                        }
                        const deletedWasActive =
                            state.activeSessionId === sessionId;
                        const nextActiveId = selectNextActiveAfterDelete(
                            filtered,
                            deletedWasActive,
                            state.activeSessionId,
                        );
                        return {
                            sessions: filtered,
                            activeSessionId: nextActiveId,
                            selectedMessageId: undefined,
                        };
                    }),

                clearSessionMessages: (sessionId) =>
                    set((state) => ({
                        sessions: state.sessions.map((s) =>
                            s.id === sessionId
                                ? {
                                      ...s,
                                      messages: [],
                                      lastFailedUserMessage: null,
                                      updatedAt: Date.now(),
                                  }
                                : s,
                        ),
                        selectedMessageId: undefined,
                    })),

                setLastFailedUserMessage: (sessionId, payload) =>
                    set((state) => ({
                        sessions: state.sessions.map((s) =>
                            s.id === sessionId
                                ? {
                                      ...s,
                                      lastFailedUserMessage: payload,
                                      updatedAt: Date.now(),
                                  }
                                : s,
                        ),
                    })),

                selectMessage: (id) => set({ selectedMessageId: id }),

                setInspectorOpen: (open) => set({ inspectorOpen: open }),

                setApiKeyOverride: () => set({ apiKeyOverride: "" }),

                bindAuthPrincipal: (userId) => {
                    const principalId = (userId || "").trim();
                    if (!principalId || isPlaceholderUserId(principalId)) {
                        throw new Error("bindAuthPrincipal requires principal.user_id");
                    }
                    clearLegacyUserScopeStorage();
                    set((state) => {
                        const mismatched = state.sessions.some(
                            (s) => (s.userId || "").trim() !== principalId,
                        );
                        if (!mismatched && state.authUserId === principalId) {
                            return {};
                        }
                        // Drop foreign/legacy scoped sessions — ownership would 403.
                        const owned = state.sessions.filter(
                            (s) => (s.userId || "").trim() === principalId,
                        );
                        const sessions =
                            owned.length > 0
                                ? owned.map((s) => ({ ...s, userId: principalId }))
                                : [createInitialSession(1, principalId)];
                        const activeSessionId = sessions.some(
                            (s) => s.id === state.activeSessionId,
                        )
                            ? state.activeSessionId
                            : sessions[0].id;
                        return {
                            authUserId: principalId,
                            sessions,
                            activeSessionId,
                            selectedMessageId: undefined,
                        };
                    });
                    return principalId;
                },

                ensureUserScope: () => {
                    const authUserId = get().authUserId;
                    if (authUserId && !isPlaceholderUserId(authUserId)) {
                        return get().bindAuthPrincipal(authUserId);
                    }
                    return "default";
                },

                retryPayloadForLastFailedMessage: (sessionId) => {
                    const session = get().sessions.find(
                        (s) => s.id === sessionId,
                    );
                    if (!session) return null;
                    return session.lastFailedUserMessage;
                },
            };
        },
        {
            name: "aihub-cockpit-store",
            version: 7,
            skipHydration: true,
            storage: createJSONStorage(() => localStorage),
            /** Transkrypt czatu = backend (GET history); tu tylko metadane sesji — brak „fanfiku” w localStorage. */
            partialize: (state) => ({
                currentSection: state.currentSection,
                sessions: state.sessions.map((s) => ({
                    ...s,
                    messages: [],
                })),
                activeSessionId: state.activeSessionId,
                authUserId: state.authUserId,
            }),
            migrate: (persistedState, oldVersion) => {
                const state = persistedState as
                    | Partial<CockpitState>
                    | undefined;
                if (!state) return state as unknown;

                const rawSessions = Array.isArray(state.sessions)
                    ? (state.sessions as SessionState[])
                    : [];

                let sessions =
                    rawSessions.length > 0
                        ? rawSessions.map((s) => normalizeSession(s))
                        : [createInitialSession(1)];

                if (oldVersion < 5) {
                    sessions = sessions.map((s) => ({ ...s, messages: [] }));
                }

                // v7: drop random localStorage user scopes — wait for auth/me bind.
                if (oldVersion < 7) {
                    clearLegacyUserScopeStorage();
                    sessions = [createInitialSession(1, "default")];
                }

                const activeSessionId = sessions.some(
                    (s) => s.id === state.activeSessionId,
                )
                    ? (state.activeSessionId as string)
                    : sessions[0].id;

                return {
                    ...state,
                    sessions,
                    activeSessionId,
                    apiKeyOverride: "",
                    authUserId:
                        oldVersion < 7
                            ? null
                            : typeof state.authUserId === "string"
                              ? state.authUserId
                              : null,
                };
            },
            merge: (persistedState, currentState) => {
                const persisted = persistedState as
                    | Partial<CockpitState>
                    | undefined;
                if (!persisted) return currentState;

                const rawSessions = Array.isArray(persisted.sessions)
                    ? (persisted.sessions as SessionState[])
                    : [];
                const sessions =
                    rawSessions.length > 0
                        ? rawSessions.map((s) => normalizeSession(s))
                        : currentState.sessions;

                const activeSessionId = sessions.some(
                    (s) => s.id === persisted.activeSessionId,
                )
                    ? (persisted.activeSessionId as string)
                    : sessions[0].id;

                const authUserId =
                    typeof persisted.authUserId === "string" &&
                    !isPlaceholderUserId(persisted.authUserId)
                        ? persisted.authUserId.trim()
                        : null;

                return {
                    ...currentState,
                    ...persisted,
                    sessions,
                    activeSessionId,
                    selectedMessageId: undefined,
                    apiKeyOverride: "",
                    authUserId,
                };
            },
        },
    ),
);

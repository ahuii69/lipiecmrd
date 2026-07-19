import { beforeEach, describe, expect, it } from "vitest";

import { useCockpitStore } from "@/lib/store/cockpit-store";

describe("cockpit-store — server-backed transcript", () => {
    let sessionId: string;

    beforeEach(() => {
        const st = useCockpitStore.getState();
        const first = st.sessions[0];
        if (!first) throw new Error("expected default session");
        sessionId = first.id;
        useCockpitStore.setState({
            sessions: st.sessions.map((s, i) =>
                i === 0
                    ? {
                          ...s,
                          messages: [],
                          historyNonce: 0,
                      }
                    : s,
            ),
            activeSessionId: sessionId,
        });
    });

    it("replaceSessionMessagesFromServer sortuje po created_at i zachowuje kolejność backendu przy remisach", () => {
        useCockpitStore.getState().replaceSessionMessagesFromServer(sessionId, [
            {
                id: "b",
                role: "assistant",
                content: "B",
                created_at: "2020-01-01T00:00:02Z",
            },
            {
                id: "a",
                role: "user",
                content: "A",
                created_at: "2020-01-01T00:00:01Z",
            },
        ]);
        const msgs = useCockpitStore
            .getState()
            .sessions.find((s) => s.id === sessionId)!.messages;
        expect(msgs.map((m) => m.id)).toEqual(["a", "b"]);
        expect(msgs.map((m) => m.content)).toEqual(["A", "B"]);
    });

    it("replaceSessionMessagesFromServer nie przestawia kolejności gdy created_at jest takie samo", () => {
        useCockpitStore.getState().replaceSessionMessagesFromServer(sessionId, [
            {
                id: "srv_u",
                role: "user",
                content: "pierwszy",
                created_at: "2020-01-01T00:00:01Z",
            },
            {
                id: "srv_a",
                role: "assistant",
                content: "drugi",
                created_at: "2020-01-01T00:00:01Z",
            },
        ]);
        const msgs = useCockpitStore
            .getState()
            .sessions.find((s) => s.id === sessionId)!.messages;
        expect(msgs.map((m) => m.id)).toEqual(["srv_u", "srv_a"]);
    });

    it("replaceSessionMessagesFromServer odrzuca zduplikowane id", () => {
        useCockpitStore.getState().replaceSessionMessagesFromServer(sessionId, [
            {
                id: "x",
                role: "user",
                content: "first",
                created_at: "2020-01-01T00:00:01Z",
            },
            {
                id: "x",
                role: "user",
                content: "dup",
                created_at: "2020-01-01T00:00:02Z",
            },
        ]);
        const msgs = useCockpitStore
            .getState()
            .sessions.find((s) => s.id === sessionId)!.messages;
        expect(msgs).toHaveLength(1);
        expect(msgs[0].content).toBe("first");
    });

    it("truncateSessionMessagesTail obcina koniec listy", () => {
        const append = useCockpitStore.getState().appendMessage;
        append(sessionId, {
            id: "u1",
            role: "user",
            content: "hi",
            createdAt: 1,
        });
        append(sessionId, {
            id: "a1",
            role: "assistant",
            content: "err",
            createdAt: 2,
            error: "x",
        });
        useCockpitStore.getState().truncateSessionMessagesTail(sessionId, 1);
        const msgs = useCockpitStore
            .getState()
            .sessions.find((s) => s.id === sessionId)!.messages;
        expect(msgs).toHaveLength(1);
        expect(msgs[0].id).toBe("u1");
    });

    it("replaceSessionMessagesFromServer inkrementuje historyNonce", () => {
        const s0 = useCockpitStore
            .getState()
            .sessions.find((s) => s.id === sessionId)!;
        const n0 = s0.historyNonce ?? 0;
        useCockpitStore.getState().replaceSessionMessagesFromServer(sessionId, [
            {
                id: "z",
                role: "user",
                content: "z",
                created_at: "2020-01-01T00:00:00Z",
            },
        ]);
        const n1 = useCockpitStore
            .getState()
            .sessions.find((s) => s.id === sessionId)!.historyNonce;
        expect(n1).toBe(n0 + 1);
    });

    it("bindAuthPrincipal ustawia principal.user_id i czyści legacy random scope", () => {
        localStorage.setItem("aihub-cockpit-user-scope-v1", "u_deadbeef_old");
        useCockpitStore.setState((st) => ({
            authUserId: null,
            sessions: st.sessions.map((s, i) =>
                i === 0
                    ? {
                          ...s,
                          userId: "u_deadbeef_old",
                          title: "Stara sesja",
                      }
                    : s,
            ),
        }));
        const principalId = "afec6ec8-a1d6-40a3-b28d-90fad20aadc8";
        const bound = useCockpitStore.getState().bindAuthPrincipal(principalId);
        expect(bound).toBe(principalId);
        expect(useCockpitStore.getState().authUserId).toBe(principalId);
        const after = useCockpitStore.getState().sessions;
        expect(after.every((s) => s.userId === principalId)).toBe(true);
        expect(localStorage.getItem("aihub-cockpit-user-scope-v1")).toBeNull();
    });

    it("ensureUserScope bez authUserId nie generuje losowego user_id", () => {
        useCockpitStore.setState({ authUserId: null });
        localStorage.removeItem("aihub-cockpit-user-scope-v1");
        const scoped = useCockpitStore.getState().ensureUserScope();
        expect(scoped).toBe("default");
        expect(localStorage.getItem("aihub-cockpit-user-scope-v1")).toBeNull();
    });

    it("createSession zawsze tworzy nowe ID (nie reuse pustych po rehydrate)", () => {
        const principalId = "11111111-2222-3333-4444-555555555555";
        useCockpitStore.getState().bindAuthPrincipal(principalId);
        // Symuluj rehydrate: puste messages + placeholder title na starej sesji serwerowej
        useCockpitStore.setState((st) => ({
            sessions: [
                {
                    ...st.sessions[0],
                    id: "s_old_server",
                    title: "Nowa rozmowa",
                    messages: [],
                    historyStatus: "idle",
                    userId: principalId,
                },
            ],
            activeSessionId: "s_old_server",
        }));
        const before = useCockpitStore.getState().activeSessionId;
        const created = useCockpitStore.getState().createSession();
        expect(created).not.toBe(before);
        expect(created).not.toBe("s_old_server");
        expect(useCockpitStore.getState().activeSessionId).toBe(created);
        const active = useCockpitStore
            .getState()
            .sessions.find((s) => s.id === created)!;
        expect(active.messages).toEqual([]);
        expect(active.userId).toBe(principalId);
    });

    it("createSession dziedziczy authUserId, nie localStorage", () => {
        const principalId = "11111111-2222-3333-4444-555555555555";
        useCockpitStore.getState().bindAuthPrincipal(principalId);
        useCockpitStore.setState((st) => ({
            sessions: st.sessions.map((s) => ({
                ...s,
                messages: [
                    {
                        id: "u1",
                        role: "user",
                        content: "hi",
                        createdAt: 1,
                    },
                ],
                title: "Zajęta",
                historyStatus: "ready" as const,
            })),
        }));
        useCockpitStore.getState().createSession();
        const sessions = useCockpitStore.getState().sessions;
        expect(sessions.every((s) => s.userId === principalId)).toBe(true);
    });

    it("replaceSessionMessagesFromServer ustawia historyStatus ready", () => {
        useCockpitStore.getState().setSessionHistoryStatus(sessionId, "loading");
        useCockpitStore.getState().replaceSessionMessagesFromServer(sessionId, [
            {
                id: "z",
                role: "user",
                content: "z",
                created_at: "2020-01-01T00:00:00Z",
            },
        ]);
        const s = useCockpitStore
            .getState()
            .sessions.find((x) => x.id === sessionId)!;
        expect(s.historyStatus).toBe("ready");
    });

    it("nie przyjmuje ani nie utrwala klucza API w przeglądarce", async () => {
        useCockpitStore.getState().setApiKeyOverride("browser-secret");
        await Promise.resolve();
        expect(useCockpitStore.getState().apiKeyOverride).toBe("");
        expect(
            localStorage.getItem("aihub-cockpit-store") ?? "",
        ).not.toContain("browser-secret");
    });
});

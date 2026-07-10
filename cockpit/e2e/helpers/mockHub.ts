/**
 * Mocks Playwright dla **izolowanych testów UI** (sztuczne odpowiedzi SSE / upload).
 * Nie służy jako dowód działania produkcyjnego stacku LLM.
 *
 * Dowód prawdziwego backendu + modelu: `scripts/smoke_runtime.sh` (POST /chat/turn do API)
 * lub ręczny czat przy uruchomionym hubie (`./start.sh`).
 */
import type { Page, Route } from "@playwright/test";

export type TurnBody = Record<string, unknown>;

type MockHistoryRow = {
    id: string;
    role: string;
    content: string;
    created_at: string;
};

/** Symuluje persystencję transkryptu — `reloadSessionHistoryFromServer` po turze nie może dostać pustej listy. */
function createMockSessionHistoryStore(): {
    appendTurn: (
        sessionId: string,
        userText: string,
        assistantText: string,
    ) => void;
    get: (sessionId: string) => MockHistoryRow[];
} {
    const sessionHistory = new Map<string, MockHistoryRow[]>();
    return {
        appendTurn(sessionId, userText, assistantText) {
            if (!sessionId) return;
            const list = sessionHistory.get(sessionId) ?? [];
            const t = new Date().toISOString();
            list.push({
                id: `${sessionId}_u_${list.length}`,
                role: "user",
                content: userText,
                created_at: t,
            });
            list.push({
                id: `${sessionId}_a_${list.length}`,
                role: "assistant",
                content: assistantText,
                created_at: t,
            });
            sessionHistory.set(sessionId, list);
        },
        get(sessionId) {
            return sessionHistory.get(sessionId) ?? [];
        },
    };
}

function sessionIdFromHistoryRequestUrl(url: string): string | null {
    try {
        const u = new URL(url);
        const m = u.pathname.match(/\/chat\/session\/([^/]+)\/history/);
        return m?.[1] ? decodeURIComponent(m[1]) : null;
    } catch {
        return null;
    }
}

function sseLine(obj: object): string {
    return `data: ${JSON.stringify(obj)}\n\n`;
}

function fulfillSseString(route: Route, events: object[]): Promise<void> {
    const body = events.map((e) => sseLine(e)).join("");
    return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream; charset=utf-8" },
        body,
    });
}

export interface HubMockOptions {
    failTurnAt?: number;
    slowStream?: boolean;
    /** Opóźnienie przed odpowiedzią (pierwszy turn) — UI w stanie „Piszę…”, przycisk Stop. */
    stallFirstTurnMs?: number;
    capturedTurns: TurnBody[];
    /** Nadpisanie JSON z mocka uploadu (np. obraz). */
    uploadJsonOverride?: Record<string, unknown>;
}

export async function installHubMocks(
    page: Page,
    options: HubMockOptions,
): Promise<void> {
    let turnIndex = 0;
    const mockHistory = createMockSessionHistoryStore();

    await page.route("**/api/aihub/chat/sessions**", async (route: Route) => {
        if (route.request().method() !== "GET") {
            await route.continue();
            return;
        }
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ sessions: [] }),
        });
    });

    await page.route("**/api/aihub/chat/session/**/history**", async (route: Route) => {
        if (route.request().method() !== "GET") {
            await route.continue();
            return;
        }
        const sid = sessionIdFromHistoryRequestUrl(route.request().url());
        const messages = sid ? mockHistory.get(sid) : [];
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ messages }),
        });
    });

    await page.route("**/api/aihub/chat/upload", async (route: Route) => {
        if (route.request().method() !== "POST") {
            await route.continue();
            return;
        }
        const defaultUpload = {
            file_id: "f_e2e",
            filename: "e2e.txt",
            content_type: "text/plain",
            size: 12,
            extracted_text_preview: "e2e content",
            status: "ok",
        };
        const uploadBody = options.uploadJsonOverride
            ? { ...defaultUpload, ...options.uploadJsonOverride }
            : defaultUpload;
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(uploadBody),
        });
    });

    await page.route("**/api/aihub/chat/turn**", async (route: Route) => {
        if (route.request().method() !== "POST") {
            await route.continue();
            return;
        }
        let post: TurnBody = {};
        try {
            post = route.request().postDataJSON() as TurnBody;
        } catch {
            post = {};
        }
        options.capturedTurns.push(post);
        turnIndex += 1;

        if (
            options.failTurnAt !== undefined &&
            turnIndex === options.failTurnAt
        ) {
            await route.fulfill({
                status: 503,
                contentType: "application/json",
                body: JSON.stringify({ detail: "e2e_mock_fail" }),
            });
            return;
        }

        const text =
            typeof post.message === "string" ? post.message : "ok";

        if (
            options.stallFirstTurnMs &&
            turnIndex === 1 &&
            options.stallFirstTurnMs > 0
        ) {
            await new Promise((r) =>
                setTimeout(r, options.stallFirstTurnMs),
            );
        }

        if (options.slowStream && turnIndex === 1) {
            const sid =
                typeof post.session_id === "string" ? post.session_id : "";
            mockHistory.appendTurn(sid, text, "SLOW");
            await fulfillSseString(route, [
                { type: "delta", content: "S" },
                { type: "delta", content: "L" },
                { type: "delta", content: "O" },
                { type: "delta", content: "W" },
                { type: "done" },
            ]);
            return;
        }

        const summary =
            Array.isArray(post.attached_file_ids) &&
            (post.attached_file_ids as string[]).length > 0
                ? "Plik: e2e"
                : undefined;
        const ids = post.attached_file_ids as string[] | undefined;
        const contextChips =
            Array.isArray(ids) && ids.length > 0
                ? ["attachment-used"]
                : undefined;

        const sid = typeof post.session_id === "string" ? post.session_id : "";
        mockHistory.appendTurn(sid, text, `Echo: ${text}`);

        await fulfillSseString(route, [
            { type: "delta", content: `Echo: ${text}` },
            {
                type: "done",
                ...(summary ? { attachments_summary: summary } : {}),
                ...(contextChips ? { context_chips: contextChips } : {}),
            },
        ]);
    });
}

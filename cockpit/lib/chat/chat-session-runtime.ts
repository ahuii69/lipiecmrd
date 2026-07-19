/**
 * Isolates in-flight chat turns across session switches.
 * One AbortController + generation token per active turn — stale SSE callbacks no-op.
 */

export type ChatTurnGeneration = number;

export interface ChatTurnHandle {
    sessionId: string;
    generation: ChatTurnGeneration;
    signal: AbortSignal;
}

class ChatSessionRuntime {
    private generation = 0;
    private controllers = new Map<string, AbortController>();
    private activeSessionId: string | null = null;
    private deltaBuffers = new Map<string, string>();
    private flushScheduled = false;
    private onFlush:
        | ((sessionId: string, messageId: string, chunk: string) => void)
        | null = null;
    private pendingFlushTargets = new Map<
        string,
        { sessionId: string; messageId: string }
    >();

    /** Register store append callback for batched deltas. */
    setFlushHandler(
        fn: (sessionId: string, messageId: string, chunk: string) => void,
    ): void {
        this.onFlush = fn;
    }

    nextGeneration(): ChatTurnGeneration {
        this.generation += 1;
        return this.generation;
    }

    currentGeneration(): ChatTurnGeneration {
        return this.generation;
    }

    isCurrent(sessionId: string, generation: ChatTurnGeneration): boolean {
        return (
            this.generation === generation &&
            this.activeSessionId === sessionId &&
            this.controllers.has(sessionId)
        );
    }

    beginTurn(sessionId: string): ChatTurnHandle {
        this.abortSession(sessionId);
        this.generation += 1;
        const generation = this.generation;
        const controller = new AbortController();
        this.controllers.set(sessionId, controller);
        this.activeSessionId = sessionId;
        return { sessionId, generation, signal: controller.signal };
    }

    endTurn(sessionId: string, generation: ChatTurnGeneration): void {
        if (this.generation !== generation) return;
        const c = this.controllers.get(sessionId);
        if (c) {
            this.controllers.delete(sessionId);
        }
        this.flushDeltaBufferNow();
    }

    abortSession(sessionId: string): void {
        const c = this.controllers.get(sessionId);
        if (c) {
            c.abort();
            this.controllers.delete(sessionId);
        }
        this.clearDeltaBuffer(sessionId);
    }

    abortAll(): void {
        for (const [id, c] of this.controllers) {
            c.abort();
            this.controllers.delete(id);
        }
        this.deltaBuffers.clear();
        this.pendingFlushTargets.clear();
        this.generation += 1;
        this.activeSessionId = null;
    }

    /** Full reset before starting a brand-new chat. */
    resetForNewChat(): void {
        this.abortAll();
    }

    getAbortController(sessionId: string): AbortController | null {
        return this.controllers.get(sessionId) ?? null;
    }

    /** True while a turn AbortController is registered for the session. */
    hasInflightTurn(sessionId: string): boolean {
        return this.controllers.has(sessionId);
    }

    queueDelta(
        sessionId: string,
        messageId: string,
        chunk: string,
        generation: ChatTurnGeneration,
    ): void {
        if (!this.isCurrent(sessionId, generation)) return;
        if (!chunk) return;
        const key = `${sessionId}\0${messageId}`;
        this.deltaBuffers.set(key, (this.deltaBuffers.get(key) ?? "") + chunk);
        this.pendingFlushTargets.set(key, { sessionId, messageId });
        this.scheduleFlush();
    }

    private scheduleFlush(): void {
        if (this.flushScheduled) return;
        this.flushScheduled = true;
        if (typeof requestAnimationFrame === "function") {
            requestAnimationFrame(() => {
                this.flushScheduled = false;
                this.flushDeltaBufferNow();
            });
        } else {
            queueMicrotask(() => {
                this.flushScheduled = false;
                this.flushDeltaBufferNow();
            });
        }
    }

    private flushDeltaBufferNow(): void {
        const handler = this.onFlush;
        if (!handler) {
            this.deltaBuffers.clear();
            this.pendingFlushTargets.clear();
            return;
        }
        for (const [key, chunk] of this.deltaBuffers) {
            if (!chunk) continue;
            const target = this.pendingFlushTargets.get(key);
            if (!target) continue;
            handler(target.sessionId, target.messageId, chunk);
        }
        this.deltaBuffers.clear();
        this.pendingFlushTargets.clear();
    }

    private clearDeltaBuffer(sessionId: string): void {
        for (const key of [...this.deltaBuffers.keys()]) {
            if (key.startsWith(`${sessionId}\0`)) {
                this.deltaBuffers.delete(key);
                this.pendingFlushTargets.delete(key);
            }
        }
    }
}

/** Process-wide singleton — one chat shell per tab. */
export const chatSessionRuntime = new ChatSessionRuntime();

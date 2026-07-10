import { MemoryContextResult } from "@/lib/api/types";

export interface MemoryViewModel {
    total: number;
    stmCount: number;
    episodicCount: number;
    semanticCount: number;
    denseCount: number;
    graphCount: number;
    stm: Array<Record<string, unknown>>;
    episodic: Array<Record<string, unknown>>;
    semantic: Array<Record<string, unknown>>;
    denseHits: Array<Record<string, unknown>>;
    graphHits: Array<Record<string, unknown>>;
}

function asRows(value: unknown): Array<Record<string, unknown>> {
    if (!Array.isArray(value)) return [];
    return value.filter((v): v is Record<string, unknown> => !!v && typeof v === "object");
}

export function toMemoryViewModel(
    payload?: MemoryContextResult,
): MemoryViewModel {
    const stm = asRows(payload?.stm);
    const episodic = asRows(payload?.episodic);
    const semantic = asRows(payload?.semantic);
    const denseHits = asRows(payload?.dense_hits);
    const graphHits = asRows(payload?.graph_hits);

    const totalFromPayload =
        typeof payload?.total === "number" && Number.isFinite(payload.total)
            ? payload.total
            : 0;

    return {
        total: Math.max(totalFromPayload, episodic.length + semantic.length),
        stmCount: stm.length,
        episodicCount: episodic.length,
        semanticCount: semantic.length,
        denseCount: denseHits.length,
        graphCount: graphHits.length,
        stm,
        episodic,
        semantic,
        denseHits,
        graphHits,
    };
}

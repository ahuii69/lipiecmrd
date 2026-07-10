import {
    ResearchQueryResult,
    ResearchUrlResult,
    WebFetchResult,
} from "@/lib/api/types";

export interface ResearchViewModel {
    totalResults: number;
    totalFacts: number;
    rows: Array<{
        title: string;
        url: string;
        relevance: number;
        factsExtracted: number;
        source: string;
    }>;
}

export function toResearchViewModel(
    payload?: ResearchQueryResult,
): ResearchViewModel {
    const rows = Array.isArray(payload?.results)
        ? payload!.results
              .filter((r) => !!r && typeof r === "object")
              .map((r) => ({
                  title: typeof r.title === "string" ? r.title : "(bez tytułu)",
                  url: typeof r.url === "string" ? r.url : "",
                  relevance:
                      typeof r.relevance === "number" &&
                      Number.isFinite(r.relevance)
                          ? r.relevance
                          : 0,
                  factsExtracted:
                      typeof r.facts_extracted === "number" &&
                      Number.isFinite(r.facts_extracted)
                          ? r.facts_extracted
                          : 0,
                  source: typeof r.source === "string" ? r.source : "unknown",
              }))
        : [];

    const totalResults =
        typeof payload?.total_results === "number" &&
        Number.isFinite(payload.total_results)
            ? payload.total_results
            : rows.length;

    const totalFacts =
        typeof payload?.total_facts === "number" &&
        Number.isFinite(payload.total_facts)
            ? payload.total_facts
            : rows.reduce((acc, r) => acc + r.factsExtracted, 0);

    return { totalResults, totalFacts, rows };
}

export function summarizeResearchUrl(payload?: ResearchUrlResult): {
    url: string;
    status: number | null;
    bytes: number | null;
    preview: string;
} {
    return {
        url: typeof payload?.url === "string" ? payload.url : "",
        status:
            typeof payload?.status === "number" &&
            Number.isFinite(payload.status)
                ? payload.status
                : null,
        bytes:
            typeof payload?.bytes === "number" && Number.isFinite(payload.bytes)
                ? payload.bytes
                : null,
        preview: typeof payload?.preview === "string" ? payload.preview : "",
    };
}

export function summarizeWebFetch(payload?: WebFetchResult): {
    ok: boolean;
    url: string;
    status: number | null;
    bytes: number | null;
    textPreview: string;
} {
    const text = typeof payload?.text === "string" ? payload.text : "";
    return {
        ok: payload?.ok === true,
        url: typeof payload?.url === "string" ? payload.url : "",
        status:
            typeof payload?.status === "number" &&
            Number.isFinite(payload.status)
                ? payload.status
                : null,
        bytes:
            typeof payload?.bytes === "number" && Number.isFinite(payload.bytes)
                ? payload.bytes
                : null,
        textPreview: text.slice(0, 1200),
    };
}

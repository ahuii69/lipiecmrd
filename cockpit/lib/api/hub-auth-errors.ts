import { ApiClientError } from "@/lib/api/client";

const RAW_PATTERNS =
    /forbidden user scope|column reference is ambiguous|traceback|sqlstate|failed to fetch|networkerror|syntax error at or near/i;

function mapStatusAndText(status: number | null, message: string): string | null {
    const m = message.toLowerCase();
    if (
        status === 401 ||
        m.includes("authentication required") ||
        m.includes("sesja wygasła")
    ) {
        return "Sesja wygasła. Zaloguj się ponownie.";
    }
    if (
        m.includes("forbidden user scope") ||
        m.includes("ownership") ||
        status === 403
    ) {
        return "Ta rozmowa nie należy do bieżącego konta.";
    }
    if (status === 502 || status === 503) {
        return "Backend chwilowo nie odpowiada. Spróbuj ponownie za moment.";
    }
    if (status === 504 || m.includes("timeout") || m.includes("timed out")) {
        return "Odpowiedź trwała zbyt długo. Możesz ponowić wysłanie.";
    }
    if (status === 429) {
        return "Zbyt wiele żądań. Spróbuj ponownie za chwilę.";
    }
    if (
        m.includes("failed to fetch") ||
        m.includes("networkerror") ||
        m.includes("network")
    ) {
        return "Nie udało się połączyć z AI-Hub.";
    }
    return null;
}

/** Map raw/backend error text to a short Polish user message. */
export function formatUserFacingError(message: string, status?: number): string {
    if (RAW_PATTERNS.test(message)) {
        console.error("[hub-auth-errors]", message);
        const mapped = mapStatusAndText(status ?? null, message);
        return mapped ?? "Coś się wywaliło po drodze. Szczegóły zapisano w logach.";
    }
    const mapped = mapStatusAndText(status ?? null, message);
    if (mapped) return mapped;
    if (message.length > 120) {
        console.error("[hub-auth-errors]", message);
        return "Coś się wywaliło po drodze. Szczegóły zapisano w logach.";
    }
    return message;
}

/** User-facing chat/transport errors — never raw ownership/backend dumps. */
export function formatChatTurnErrorMessage(err: unknown): string {
    if (err instanceof ApiClientError) {
        return formatUserFacingError(err.message, err.status);
    }
    if (err instanceof TypeError) {
        return "Nie udało się połączyć z AI-Hub.";
    }
    if (err instanceof Error) {
        return formatUserFacingError(err.message);
    }
    return "Coś się wywaliło po drodze. Szczegóły zapisano w logach.";
}

export function formatMemoryErrorMessage(err: unknown): string {
    if (err instanceof ApiClientError) {
        if (err.status === 401) return "Sesja wygasła. Zaloguj się ponownie.";
        if (err.status === 403) {
            return "Nie można odczytać pamięci dla tego konta.";
        }
        if (err.status === 502 || err.status === 503) {
            return "Backend chwilowo nie odpowiada. Spróbuj ponownie za moment.";
        }
        if (err.status >= 500) {
            return "Backend chwilowo nie odpowiada. Spróbuj ponownie za moment.";
        }
    }
    console.error("[memory-error]", err);
    return "Nie udało się pobrać pamięci.";
}

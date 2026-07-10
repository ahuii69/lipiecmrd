import { useCockpitStore } from "@/lib/store/cockpit-store";

/** 401 + komunikat FastAPI o złym kluczu hubu. */
export function isInvalidHubApiKeyResponse(
    status: number,
    detail: string,
): boolean {
    if (status !== 401) return false;
    const m = detail.toLowerCase();
    return m.includes("invalid api key");
}

/**
 * Gdy backend odrzuca klucz, a użytkownik ma zapisany nadpisany klucz w sidebarze —
 * czyścimy go, żeby kolejne żądanie poszło z kluczem z env serwera (cockpit/.env).
 */
export function clearApiKeyOverrideAfterHubAuthFailure(
    status: number,
    detail: string,
): void {
    if (!isInvalidHubApiKeyResponse(status, detail)) return;
    const { apiKeyOverride, setApiKeyOverride } = useCockpitStore.getState();
    if (apiKeyOverride.trim()) {
        setApiKeyOverride("");
    }
}

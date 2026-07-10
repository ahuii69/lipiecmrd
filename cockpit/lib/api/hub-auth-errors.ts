import { ApiClientError } from "@/lib/api/client";

/** Rozszerza znany błąd autoryzacji hubu o konkretne wskazówki (Cockpit ↔ FastAPI). */
export function formatChatTurnErrorMessage(err: unknown): string {
    if (err instanceof ApiClientError) {
        const m = err.message.toLowerCase();
        if (
            err.status === 401 &&
            (m.includes("invalid api key") || m.includes("api key"))
        ) {
            return (
                `${err.message}\n\n` +
                "Backend wymaga tego samego sekretu co `API_KEY` (lub jawnego `AIHUB_PROXY_TOKEN`).\n" +
                "Next wysyła `x-api-key` i `X-AIHub-Proxy-Token` z env Cockpit / `../.env`.\n" +
                "Ustaw w `cockpit/.env` lub `morda/.env`: `API_KEY` albo `AIHUB_API_KEY` albo `AIHUB_PROXY_TOKEN` (te same wartości co na FastAPI).\n" +
                "Opcjonalnie: pole „API Key” w sidebarze (tylko gdy nadpisujesz ręcznie).\n\n" +
                "Zły zapisany klucz — wyczyść pole w sidebarze i odśwież stronę."
            );
        }
        return err.message;
    }
    if (err instanceof Error) return err.message;
    return String(err);
}

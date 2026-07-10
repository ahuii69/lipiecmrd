/**
 * Klucze typu OpenAI / wielu providerów — to NIE jest hub `API_KEY` do FastAPI.
 * Wysłanie ich w override dawało 401 „invalid api key”.
 */
export function looksLikeLlmProviderSecret(raw: string): boolean {
    const t = raw.trim().toLowerCase();
    return t.startsWith("sk-");
}

export function normalizeOptionalApiKeyOverride(
    value: string | undefined | null,
): string | undefined {
    const t = value == null ? "" : String(value).trim();
    if (t.length === 0) return undefined;
    if (looksLikeLlmProviderSecret(t)) return undefined;
    return t;
}

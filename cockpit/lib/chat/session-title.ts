/** Tytuły startowe / zastępcze — nie nadpisywać po ręcznej edycji. */
const PLACEHOLDER_LOWER = new Set([
    "",
    "new chat",
    "nowa rozmowa",
    "nowa",
]);

export function isPlaceholderSessionTitle(title: string): boolean {
    return PLACEHOLDER_LOWER.has(title.trim().toLowerCase());
}

/**
 * Pierwsze sensowne słowa z pierwszej wiadomości (bez LLM), max ~8 słów / 48 znaków.
 */
export function deriveSessionTitleFromMessage(
    raw: string,
    maxWords = 8,
    maxLen = 48,
): string {
    const singleLine = raw.replace(/\r\n|\r|\n/g, " ").replace(/\s+/g, " ").trim();
    const words = singleLine
        .split(/\s+/)
        .filter((w) => {
            if (!w) return false;
            if (/^https?:\/\//i.test(w)) return false;
            if (/^[@#`>*\-_]{1,3}$/.test(w)) return false;
            return true;
        })
        .slice(0, maxWords);
    let out = words.join(" ").trim();
    if (!out) return "Nowa rozmowa";
    if (out.length > maxLen) {
        out = `${out.slice(0, Math.max(1, maxLen - 1))}…`;
    }
    return out;
}

/** Po usunięciu sesji: następna aktywna = najświeżej aktualizowana. */
export function selectNextActiveAfterDelete(
    remaining: { id: string; updatedAt: number }[],
    deletedWasActive: boolean,
    previousActiveId: string,
): string {
    if (!deletedWasActive) return previousActiveId;
    if (remaining.length === 0) return "";
    const sorted = [...remaining].sort((a, b) => b.updatedAt - a.updatedAt);
    return sorted[0].id;
}

/** Ostatnia wiadomość do podglądu na liście (krótko). */
export function lastUserVisiblePreview(
    messages: { role: string; content: string }[],
    maxLen = 56,
): string {
    for (let i = messages.length - 1; i >= 0; i--) {
        const m = messages[i];
        const t = (m.content ?? "").replace(/\s+/g, " ").trim();
        if (t) {
            return t.length > maxLen ? `${t.slice(0, maxLen - 1)}…` : t;
        }
    }
    return "";
}

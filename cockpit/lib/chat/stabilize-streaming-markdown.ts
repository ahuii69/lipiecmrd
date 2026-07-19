/**
 * Tolerate incomplete markdown fences / math during token streaming.
 * Does not strip legal content — only closes open fences for stable render.
 */
export function stabilizeStreamingMarkdown(raw: string): string {
    const text = raw ?? "";
    if (!text) return text;

    const fenceMatches = text.match(/(^|\n)```/g);
    const fenceCount = fenceMatches ? fenceMatches.length : 0;
    if (fenceCount % 2 === 1) {
        return `${text}\n\`\`\``;
    }

    // Unclosed inline $…$ pairs are rare mid-stream; leave as-is (KaTeX handles fail).
    return text;
}

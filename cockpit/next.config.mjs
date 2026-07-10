import { parse } from "dotenv";
import { existsSync, readFileSync } from "fs";
import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Z ``morda/.env``: uzupełnij ``process.env`` tam, gdzie Cockpit jeszcze nie ma wartości
 * (``.env.local`` / shell wygrywają). Dzięki temu w Node są LLM_*, BRAVE_*, VOYAGE_*,
 * modele, web — bez kopiowania całego pliku do ``cockpit/``.
 *
 * Nie kopiujemy zmiennych, które w tym repo oznaczają **backend** (uvicorn), żeby Next
 * przypadkiem nie wstał na porcie 8080 zamiast 3000.
 */
const PARENT_ENV_BLOCKLIST = new Set([
    "PORT",
    "HOST",
    "WORKERS",
]);

function mergeParentEnvIfMissing() {
    const parentEnv = resolve(__dirname, "..", ".env");
    if (!existsSync(parentEnv)) return;
    let text;
    try {
        text = readFileSync(parentEnv, "utf8");
    } catch {
        return;
    }
    const parsed = parse(text);
    for (const [key, v] of Object.entries(parsed)) {
        if (PARENT_ENV_BLOCKLIST.has(key)) continue;
        if ((process.env[key] || "").trim()) continue;
        if (v == null || String(v).trim() === "") continue;
        process.env[key] = v;
    }
}

mergeParentEnvIfMissing();

/** @type {import('next').NextConfig} */
const nextConfig = {
    // Gdy nad `cockpit/` jest inny `package-lock.json` (np. `/root`), Next źle wybiera root — ostrzeżenie + tracing.
    outputFileTracingRoot: __dirname,
    reactStrictMode: true,
    poweredByHeader: false,
    compress: true,
    productionBrowserSourceMaps: false,
    images: {
        unoptimized: true,
    },
    headers: async () => [
        {
            source: "/(.*)",
            headers: [
                {
                    key: "X-Content-Type-Options",
                    value: "nosniff",
                },
                {
                    key: "X-Frame-Options",
                    value: "DENY",
                },
                {
                    key: "X-XSS-Protection",
                    value: "1; mode=block",
                },
                {
                    key: "Referrer-Policy",
                    value: "strict-origin-when-cross-origin",
                },
                {
                    key: "Cache-Control",
                    value:
                        process.env.NODE_ENV === "production"
                            ? "public, max-age=31536000, immutable"
                            : "no-cache, no-store, must-revalidate",
                },
            ],
        },
    ],
};

export default nextConfig;

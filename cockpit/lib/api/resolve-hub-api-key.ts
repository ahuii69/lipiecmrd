import { parse } from "dotenv";
import { existsSync, readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

import type { NextRequest } from "next/server";

import { looksLikeLlmProviderSecret } from "@/lib/api/normalize-api-key-override";

import codebaseDevHub from "../../../config/codebase_dev_hub.json";
import rawHubKeyEnvNames from "../../../config/hub_key_env_names.json";

type CodebaseDevHubFile = { enable_env?: string; hub_key?: string };

function codebaseDevHubKeyWhenFlagged(): string {
    const spec = codebaseDevHub as CodebaseDevHubFile;
    const flag = (spec.enable_env ?? "").trim();
    const key = (spec.hub_key ?? "").trim();
    if (!flag || !key) return "";
    if (process.env[flag] !== "1") return "";
    return key;
}

if (
    !Array.isArray(rawHubKeyEnvNames) ||
    !rawHubKeyEnvNames.every((x): x is string => typeof x === "string")
) {
    throw new Error("config/hub_key_env_names.json must be a JSON array of strings");
}

/** Env names for hub key resolution (source: `morda/config/hub_key_env_names.json`). */
export const HUB_KEY_ENV_NAMES = rawHubKeyEnvNames as readonly string[];

/** Katalog główny repozytorium (nad `cockpit/`), niezależny od `process.cwd()`. */
const MORDA_REPO_ROOT_DIR = join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "..",
    "..",
);

export { normalizeOptionalApiKeyOverride } from "./normalize-api-key-override";

function canonicalMordaDotenvPath(): string {
    const alt = process.env.MORDA_ENV_FILE?.trim();
    if (alt) return alt;
    const rr = process.env.MORDA_REPO_ROOT?.trim();
    if (rr) return join(rr, ".env");
    return join(MORDA_REPO_ROOT_DIR, ".env");
}

function canonicalCockpitDotenvPath(): string {
    const rr = process.env.MORDA_REPO_ROOT?.trim();
    if (rr) return join(rr, "cockpit", ".env");
    return join(MORDA_REPO_ROOT_DIR, "cockpit", ".env");
}

function tryParseDotenvFile(path: string): Record<string, string> | null {
    if (!existsSync(path)) return null;
    try {
        const o = parse(readFileSync(path, "utf8")) as Record<string, string>;
        return o && typeof o === "object" ? o : null;
    } catch {
        return null;
    }
}

/**
 * Resolves the AI-Hub hub API key for the Next.js BFF → backend forward.
 *
 * Priority (first non-empty wins):
 * 1. UI override header `x-aihub-api-key-override` (caller must omit when empty)
 * 2. Incoming `x-api-key` on the request to `/api/aihub/*`
 * 3. `Authorization: Bearer <token>` only if token is plausibly a hub key (not a JWT)
 * 4. First non-empty among env names from `config/hub_key_env_names.json` (shared with Python).
 */

function firstNonEmptyTrimmed(
    values: readonly (string | undefined)[],
): string {
    for (const raw of values) {
        const t = (raw ?? "").trim();
        if (t) return t;
    }
    return "";
}

export type ResolveHubApiKeyInput = {
    overrideHeader: string | null;
    incomingApiKey: string | null;
    authorization: string | null;
    envAihub: string | undefined;
    envApiKey: string | undefined;
    envProxyToken: string | undefined;
};

/** True if token looks like a JWT (avoid using session tokens as hub keys). */
export function isLikelyJwtBearerToken(token: string): boolean {
    const t = token.trim();
    if (!t) return false;
    if (t.startsWith("eyJ")) return true;
    const parts = t.split(".");
    if (parts.length !== 3) return false;
    return parts.every((p) => p.length >= 20);
}

export function resolveHubApiKey(input: ResolveHubApiKeyInput): string {
    const o = input.overrideHeader?.trim();
    if (o) return o;

    const incoming = input.incomingApiKey?.trim();
    if (incoming) return incoming;

    const auth = input.authorization?.trim();
    if (auth?.toLowerCase().startsWith("bearer ")) {
        const token = auth.slice(7).trim();
        if (token && !isLikelyJwtBearerToken(token)) return token;
    }

    const aihub = (input.envAihub ?? "").trim();
    if (aihub) return aihub;

    const api = (input.envApiKey ?? "").trim();
    if (api) return api;

    return (input.envProxyToken ?? "").trim();
}

export function hubKeyFromProcessEnv(): string {
    return firstNonEmptyTrimmed(
        HUB_KEY_ENV_NAMES.map((k) => process.env[k]),
    );
}

function dotenvPathsToTry(): string[] {
    const out: string[] = [];
    const push = (p: string | undefined) => {
        const t = (p ?? "").trim();
        if (t && !out.includes(t)) out.push(t);
    };
    push(process.env.MORDA_ENV_FILE);
    const root = process.env.MORDA_REPO_ROOT?.trim();
    if (root) push(join(root, ".env"));
    push(join(process.cwd(), "..", ".env"));
    push(join(process.cwd(), ".env"));
    push(join(process.cwd(), "..", "..", ".env"));
    return out;
}

/** Nagłówek override: pusty albo wygląda jak klucz LLM → ignoruj, użyj env hubu. */
export function sanitizeHubKeyOverrideHeader(raw: string | null): string | null {
    const t = raw?.trim();
    if (!t) return null;
    if (looksLikeLlmProviderSecret(t)) return null;
    return t;
}

/**
 * Łączy ``morda/.env`` i ``cockpit/.env``. **Korzeń repozytorium wygrywa** przy tych samych
 * kluczach (edytujesz ``morda/.env`` — nie musisz restartować Next, żeby BFF widział nowy hub key).
 * Gdy brak ścieżek kanonicznych, skan ``dotenvPathsToTry`` z pierwszeństwem wcześniejszych plików.
 */
export function loadMordaDotenvFromDisk(): Record<string, string> | null {
    const m = tryParseDotenvFile(canonicalMordaDotenvPath());
    const c = tryParseDotenvFile(canonicalCockpitDotenvPath());
    if (m || c) {
        const merged = { ...c, ...m };
        return Object.keys(merged).length ? merged : null;
    }
    let acc: Record<string, string> = {};
    let any = false;
    for (const p of dotenvPathsToTry()) {
        const chunk = tryParseDotenvFile(p);
        if (!chunk || !Object.keys(chunk).length) continue;
        any = true;
        acc = { ...chunk, ...acc };
    }
    return any ? acc : null;
}

/** Gdy Next nie ma klucza w process.env — odczyt z morda/.env (``preloaded`` = już sparsowany plik z route). */
export function hubKeyFromMordaEnvFiles(
    preloaded?: Record<string, string> | null,
): string {
    let p: Record<string, string> | null;
    if (preloaded !== undefined) {
        p = preloaded;
    } else {
        p = loadMordaDotenvFromDisk();
    }
    if (!p) return "";
    return firstNonEmptyTrimmed(HUB_KEY_ENV_NAMES.map((k) => p[k]));
}

/** Dla każdej nazwy z kolejności hub: najpierw ``process.env``, potem jeden złączony blob z dysku. */
export function hubKeyMergedFromProcAndDisk(
    disk: Record<string, string> | null | undefined,
): string {
    for (const k of HUB_KEY_ENV_NAMES) {
        const a = (process.env[k] ?? "").trim();
        const b = (disk?.[k] ?? "").trim();
        const v = a || b;
        if (v) return v;
    }
    return "";
}

/**
 * Klucz hubu: **morda/.env na dysku**, potem ``process.env`` (Next przy starcie), potem ``cockpit/.env``.
 * Dzięki temu po zmianie ``morda/.env`` proxy działa bez restartu dev servera.
 */
export function hubKeyMergedMordaProcCockpit(
    morda: Record<string, string> | null | undefined,
    cockpit: Record<string, string> | null | undefined,
): string {
    const M = morda ?? {};
    const C = cockpit ?? {};
    for (const k of HUB_KEY_ENV_NAMES) {
        const vm = (M[k] ?? "").trim();
        if (vm) return vm;
        const vp = (process.env[k] ?? "").trim();
        if (vp) return vp;
        const vc = (C[k] ?? "").trim();
        if (vc) return vc;
    }
    return "";
}

export function resolveHubApiKeyFromNextRequest(req: NextRequest): string {
    const m = tryParseDotenvFile(canonicalMordaDotenvPath());
    const c = tryParseDotenvFile(canonicalCockpitDotenvPath());
    let fromMerged = hubKeyMergedMordaProcCockpit(m, c);
    if (!fromMerged) {
        fromMerged = hubKeyMergedFromProcAndDisk(loadMordaDotenvFromDisk());
    }
    const fromEnv = fromMerged || codebaseDevHubKeyWhenFlagged();
    const rawOverride = req.headers.get("x-aihub-api-key-override");
    const overrideHeader = sanitizeHubKeyOverrideHeader(rawOverride);
    return resolveHubApiKey({
        overrideHeader,
        incomingApiKey: req.headers.get("x-api-key"),
        authorization: req.headers.get("authorization"),
        envAihub: fromEnv,
        envApiKey: "",
        envProxyToken: "",
    });
}

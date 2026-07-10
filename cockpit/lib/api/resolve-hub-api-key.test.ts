import { describe, expect, it, vi } from "vitest";

import {
    hubKeyFromProcessEnv,
    hubKeyMergedFromProcAndDisk,
    hubKeyMergedMordaProcCockpit,
    isLikelyJwtBearerToken,
    normalizeOptionalApiKeyOverride,
    resolveHubApiKey,
    sanitizeHubKeyOverrideHeader,
    type ResolveHubApiKeyInput,
} from "./resolve-hub-api-key";

const base: ResolveHubApiKeyInput = {
    overrideHeader: null,
    incomingApiKey: null,
    authorization: null,
    envAihub: undefined,
    envApiKey: undefined,
    envProxyToken: undefined,
};

describe("resolveHubApiKey", () => {
    it("uses non-empty UI override first", () => {
        expect(
            resolveHubApiKey({
                ...base,
                overrideHeader: "  from-ui  ",
                incomingApiKey: "from-x",
                envAihub: "env-a",
                envApiKey: "env-b",
                envProxyToken: "env-p",
            }),
        ).toBe("from-ui");
    });

    it("ignores empty or whitespace-only override", () => {
        expect(
            resolveHubApiKey({
                ...base,
                overrideHeader: "   ",
                envAihub: "env-a",
            }),
        ).toBe("env-a");
    });

    it("uses incoming x-api-key before env", () => {
        expect(
            resolveHubApiKey({
                ...base,
                incomingApiKey: "client-key",
                envAihub: "env-a",
                envApiKey: "env-b",
            }),
        ).toBe("client-key");
    });

    it("uses Bearer token when not JWT-shaped, before env", () => {
        expect(
            resolveHubApiKey({
                ...base,
                authorization: "Bearer hub-secret",
                envAihub: "env-a",
            }),
        ).toBe("hub-secret");
    });

    it("skips JWT-like Bearer so env fallback works (empty override)", () => {
        const jwt =
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U";
        expect(
            resolveHubApiKey({
                ...base,
                authorization: `Bearer ${jwt}`,
                envAihub: "correct-env-key",
                envApiKey: "fallback-b",
            }),
        ).toBe("correct-env-key");
    });

    it("prefers AIHUB_API_KEY over API_KEY when both set", () => {
        expect(
            resolveHubApiKey({
                ...base,
                envAihub: "aihub-wins",
                envApiKey: "api-key-loses",
            }),
        ).toBe("aihub-wins");
    });

    it("falls back to API_KEY when AIHUB empty", () => {
        expect(
            resolveHubApiKey({
                ...base,
                envAihub: "",
                envApiKey: "only-api-key",
            }),
        ).toBe("only-api-key");
    });

    it("returns empty string when nothing is set", () => {
        expect(resolveHubApiKey({ ...base })).toBe("");
    });

    it("falls back to AIHUB_PROXY_TOKEN when AIHUB_API_KEY and API_KEY empty", () => {
        expect(
            resolveHubApiKey({
                ...base,
                envAihub: "",
                envApiKey: "",
                envProxyToken: "proxy-only",
            }),
        ).toBe("proxy-only");
    });
});

describe("isLikelyJwtBearerToken", () => {
    it("detects typical JWT", () => {
        const jwt =
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig";
        expect(isLikelyJwtBearerToken(jwt)).toBe(true);
    });

    it("does not flag short dot-separated secrets", () => {
        expect(isLikelyJwtBearerToken("a.b.c")).toBe(false);
    });
});

describe("normalizeOptionalApiKeyOverride", () => {
    it("returns undefined for empty, whitespace, null, undefined", () => {
        expect(normalizeOptionalApiKeyOverride(undefined)).toBeUndefined();
        expect(normalizeOptionalApiKeyOverride(null)).toBeUndefined();
        expect(normalizeOptionalApiKeyOverride("")).toBeUndefined();
        expect(normalizeOptionalApiKeyOverride("   ")).toBeUndefined();
    });

    it("trims and returns non-empty hub-like values", () => {
        expect(normalizeOptionalApiKeyOverride("  ab12cd34  ")).toBe("ab12cd34");
    });

    it("drops sk- LLM keys so they are not sent as hub override", () => {
        expect(normalizeOptionalApiKeyOverride("sk-proj-xxx")).toBeUndefined();
        expect(normalizeOptionalApiKeyOverride("  sk-secret  ")).toBeUndefined();
    });
});

describe("sanitizeHubKeyOverrideHeader", () => {
    it("nullifies sk- prefixes for BFF", () => {
        expect(sanitizeHubKeyOverrideHeader("sk-abc")).toBeNull();
        expect(sanitizeHubKeyOverrideHeader("  hub-real-key  ")).toBe("hub-real-key");
    });
});

describe("hubKeyMergedMordaProcCockpit", () => {
    it("prefers morda API_KEY over process.env and cockpit", () => {
        vi.stubEnv("AIHUB_API_KEY", "");
        vi.stubEnv("HUB_API_KEY", "");
        vi.stubEnv("API_KEY", "from-proc-stale");
        vi.stubEnv("AIHUB_PROXY_TOKEN", "");
        try {
            expect(
                hubKeyMergedMordaProcCockpit(
                    { API_KEY: "from-morda" },
                    { API_KEY: "from-cockpit" },
                ),
            ).toBe("from-morda");
        } finally {
            vi.unstubAllEnvs();
        }
    });

    it("uses process.env when morda has no hub key", () => {
        vi.stubEnv("AIHUB_API_KEY", "");
        vi.stubEnv("HUB_API_KEY", "");
        vi.stubEnv("API_KEY", "proc-only");
        vi.stubEnv("AIHUB_PROXY_TOKEN", "");
        try {
            expect(
                hubKeyMergedMordaProcCockpit({}, { API_KEY: "cockpit" }),
            ).toBe("proc-only");
        } finally {
            vi.unstubAllEnvs();
        }
    });

    it("uses cockpit when morda and proc empty", () => {
        vi.stubEnv("AIHUB_API_KEY", "");
        vi.stubEnv("HUB_API_KEY", "");
        vi.stubEnv("API_KEY", "");
        vi.stubEnv("AIHUB_PROXY_TOKEN", "");
        try {
            expect(
                hubKeyMergedMordaProcCockpit(null, { API_KEY: "cock-only" }),
            ).toBe("cock-only");
        } finally {
            vi.unstubAllEnvs();
        }
    });
});

describe("hubKeyMergedFromProcAndDisk", () => {
    it("uses disk API_KEY when process env hub vars empty", () => {
        vi.stubEnv("AIHUB_API_KEY", "");
        vi.stubEnv("HUB_API_KEY", "");
        vi.stubEnv("API_KEY", "");
        vi.stubEnv("AIHUB_PROXY_TOKEN", "");
        try {
            expect(
                hubKeyMergedFromProcAndDisk({
                    API_KEY: "from-disk-only",
                }),
            ).toBe("from-disk-only");
        } finally {
            vi.unstubAllEnvs();
        }
    });

    it("prefers process.env over disk for same key order slot", () => {
        vi.stubEnv("AIHUB_API_KEY", "");
        vi.stubEnv("HUB_API_KEY", "");
        vi.stubEnv("API_KEY", "from-proc");
        vi.stubEnv("AIHUB_PROXY_TOKEN", "");
        try {
            expect(
                hubKeyMergedFromProcAndDisk({
                    API_KEY: "from-disk",
                }),
            ).toBe("from-proc");
        } finally {
            vi.unstubAllEnvs();
        }
    });
});

describe("hubKeyFromProcessEnv", () => {
    it("returns first non-empty env in canonical order", () => {
        vi.stubEnv("AIHUB_API_KEY", "");
        vi.stubEnv("HUB_API_KEY", "hub-alias");
        vi.stubEnv("API_KEY", "api");
        try {
            expect(hubKeyFromProcessEnv()).toBe("hub-alias");
        } finally {
            vi.unstubAllEnvs();
        }
    });

    it("skips to API_KEY when AIHUB and HUB empty", () => {
        vi.stubEnv("AIHUB_API_KEY", "");
        vi.stubEnv("HUB_API_KEY", "");
        vi.stubEnv("API_KEY", "only-api");
        vi.stubEnv("AIHUB_PROXY_TOKEN", "");
        try {
            expect(hubKeyFromProcessEnv()).toBe("only-api");
        } finally {
            vi.unstubAllEnvs();
        }
    });
});

describe("process.env integration (proxy env order)", () => {
    it("reads AIHUB_API_KEY and API_KEY from env in resolveHubApiKey", () => {
        vi.stubEnv("AIHUB_API_KEY", "from-aihub");
        vi.stubEnv("API_KEY", "from-api");
        try {
            expect(
                resolveHubApiKey({
                    overrideHeader: null,
                    incomingApiKey: null,
                    authorization: null,
                    envAihub: process.env.AIHUB_API_KEY,
                    envApiKey: process.env.API_KEY,
                    envProxyToken: process.env.AIHUB_PROXY_TOKEN,
                }),
            ).toBe("from-aihub");
        } finally {
            vi.unstubAllEnvs();
        }
    });
});

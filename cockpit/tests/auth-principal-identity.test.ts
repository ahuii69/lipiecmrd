import { describe, expect, it } from "vitest";

import {
    formatChatTurnErrorMessage,
    formatUserFacingError,
} from "@/lib/api/hub-auth-errors";
import { ApiClientError } from "@/lib/api/client";

describe("auth identity & error mapping", () => {
    it("maps forbidden user scope to ownership message", () => {
        expect(formatUserFacingError("forbidden user scope")).toBe(
            "Ta rozmowa nie należy do bieżącego konta.",
        );
        expect(
            formatChatTurnErrorMessage(
                new ApiClientError("forbidden user scope", 403),
            ),
        ).toBe("Ta rozmowa nie należy do bieżącego konta.");
    });

    it("maps 401 to session expired", () => {
        expect(
            formatChatTurnErrorMessage(
                new ApiClientError("authentication required", 401),
            ),
        ).toBe("Sesja wygasła. Zaloguj się ponownie.");
    });

    it("maps network errors", () => {
        expect(formatChatTurnErrorMessage(new TypeError("Failed to fetch"))).toBe(
            "Nie udało się połączyć z AI-Hub.",
        );
    });

    it("shell source uses auth/me principal binding", async () => {
        const { readFileSync } = await import("node:fs");
        const { resolve } = await import("node:path");
        const shell = readFileSync(
            resolve(process.cwd(), "features/chat/ChatShell.tsx"),
            "utf8",
        );
        const store = readFileSync(
            resolve(process.cwd(), "lib/store/cockpit-store.ts"),
            "utf8",
        );
        expect(shell).toContain("useAuthPrincipal");
        expect(shell).toContain("authUserId");
        expect(shell).not.toContain("ensureUserScope()");
        expect(store).toContain("bindAuthPrincipal");
        expect(store).toContain("LEGACY_USER_SCOPE_KEY");
        expect(store).not.toContain("Math.random().toString(16).slice(2, 10)");
    });

    it("runtime dock tiles are not mounted in AI-OS shell", async () => {
        const { readFileSync } = await import("node:fs");
        const { resolve } = await import("node:path");
        const shell = readFileSync(
            resolve(process.cwd(), "features/chat/ChatShell.tsx"),
            "utf8",
        );
        expect(shell).not.toContain("UserContextDock");
        expect(shell).toContain("ChatDrawer");
        expect(shell).toContain("ChatHeader");
    });

    it("composer avoids inline style attributes", async () => {
        const { readFileSync } = await import("node:fs");
        const { resolve } = await import("node:path");
        const composer = readFileSync(
            resolve(process.cwd(), "features/chat/ChatComposer.tsx"),
            "utf8",
        );
        expect(composer).not.toContain("style={{");
        expect(composer).toContain("composer-textarea");
    });
});

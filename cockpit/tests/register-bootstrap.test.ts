import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { classifyBffRoute } from "@/lib/api/bff-route-policy";

describe("bootstrap registration UI contract", () => {
    it("classifies auth register endpoints as public", () => {
        expect(classifyBffRoute("POST", "/auth/register")).toBe("public");
        expect(classifyBffRoute("GET", "/auth/registration-status")).toBe("public");
        expect(classifyBffRoute("POST", "/auth/login")).toBe("public");
    });

    it("login form links to register when status is open", () => {
        const source = readFileSync(
            resolve(process.cwd(), "features/login/login-form.tsx"),
            "utf8",
        );
        expect(source).toContain("/api/aihub/auth/registration-status");
        expect(source).toContain('href="/register"');
        expect(source).toContain("Nie masz jeszcze konta?");
        expect(source).toContain("Utwórz konto");
    });

    it("register page uses register screen and posts to register endpoint", () => {
        const page = readFileSync(
            resolve(process.cwd(), "app/register/page.tsx"),
            "utf8",
        );
        const form = readFileSync(
            resolve(process.cwd(), "features/register/register-form.tsx"),
            "utf8",
        );
        expect(page).toContain("RegisterScreen");
        expect(form).toContain("/api/aihub/auth/register");
        expect(form).toContain("Powtórz hasło");
        expect(form).toContain("Utwórz pierwsze konto");
        expect(form).toContain("min. 12");
    });

    it("middleware treats /register as a public path", () => {
        const source = readFileSync(resolve(process.cwd(), "middleware.ts"), "utf8");
        expect(source).toContain('"/register"');
        expect(source).toContain("/api/aihub/auth/register");
        expect(source).toContain("/api/aihub/auth/registration-status");
    });
});

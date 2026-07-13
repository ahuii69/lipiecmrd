"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { useCockpitStore } from "@/lib/store/cockpit-store";

export type AuthPrincipal = {
    userId: string;
    username: string;
    role: string;
};

type AuthMeResponse = {
    principal?: {
        user_id?: string;
        username?: string;
        role?: string;
    };
};

/**
 * Loads GET /api/aihub/auth/me and binds cockpit sessions to principal.user_id.
 * Never invents random localStorage user ids.
 */
export function useAuthPrincipal(): {
    principal: AuthPrincipal | null;
    loading: boolean;
    error: string | null;
} {
    const router = useRouter();
    const bindAuthPrincipal = useCockpitStore((s) => s.bindAuthPrincipal);
    const [principal, setPrincipal] = useState<AuthPrincipal | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await fetch("/api/aihub/auth/me", {
                    headers: { accept: "application/json" },
                    cache: "no-store",
                });
                if (response.status === 401) {
                    if (!cancelled) router.replace("/login");
                    return;
                }
                if (!response.ok) {
                    throw new Error(`auth/me failed (${response.status})`);
                }
                const body = (await response.json()) as AuthMeResponse;
                const userId = (body.principal?.user_id || "").trim();
                if (!userId) {
                    throw new Error("auth/me missing principal.user_id");
                }
                bindAuthPrincipal(userId);
                if (!cancelled) {
                    setPrincipal({
                        userId,
                        username: (body.principal?.username || "").trim() || userId,
                        role: (body.principal?.role || "user").trim() || "user",
                    });
                    setError(null);
                }
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : "Błąd sesji");
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [bindAuthPrincipal, router]);

    return { principal, loading, error };
}

export async function logoutAndRedirect(): Promise<void> {
    try {
        await fetch("/api/aihub/auth/logout", {
            method: "POST",
            headers: { accept: "application/json" },
            cache: "no-store",
        });
    } catch {
        // Cookie may already be invalid — still leave the UI.
    }
    try {
        localStorage.removeItem("aihub-cockpit-user-scope-v1");
    } catch {
        // ignore
    }
    window.location.assign("/login");
}

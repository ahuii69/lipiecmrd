"use client";

import { Loader2, Lock, User } from "lucide-react";
import { FormEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { LoginBackendStatus } from "./login-backend-status";

export function LoginForm() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(false);

    async function onSubmit(event: FormEvent) {
        event.preventDefault();
        setPending(true);
        setError(null);
        try {
            const response = await fetch("/api/aihub/auth/login", {
                method: "POST",
                headers: {
                    "content-type": "application/json",
                    accept: "application/json",
                },
                body: JSON.stringify({ username, password }),
            });
            if (!response.ok) {
                const body = (await response.json().catch(() => null)) as
                    | { detail?: string }
                    | null;
                setError(body?.detail || "Logowanie nie powiodło się");
                return;
            }
            const next = searchParams.get("next") || "/";
            router.replace(next);
            router.refresh();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Błąd połączenia");
        } finally {
            setPending(false);
        }
    }

    return (
        <form
            onSubmit={onSubmit}
            className="login-animate-card w-full space-y-6"
            noValidate
        >
            <div className="space-y-2 text-center lg:text-left">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-emerald-400/25 bg-emerald-400/10 text-emerald-300 lg:mx-0">
                    <span className="text-lg font-black tracking-tighter">AI</span>
                </div>
                <h1 className="text-balance text-2xl font-bold tracking-tight text-neutral-50 sm:text-3xl">
                    Witaj ponownie
                </h1>
                <p className="text-pretty text-sm leading-relaxed text-neutral-400">
                    Zaloguj się, aby korzystać z pamięci, psyche i czatu AI-Hub.
                </p>
            </div>

            <div className="space-y-4">
                <label className="block space-y-2">
                    <span className="text-sm font-medium text-neutral-300">
                        Nazwa użytkownika
                    </span>
                    <div className="relative">
                        <User
                            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-500"
                            aria-hidden
                        />
                        <Input
                            value={username}
                            onChange={(event) => setUsername(event.target.value)}
                            autoComplete="username"
                            autoCapitalize="none"
                            autoCorrect="off"
                            spellCheck={false}
                            required
                            disabled={pending}
                            aria-invalid={error ? true : undefined}
                            className={cn(
                                "h-12 rounded-xl border-white/10 bg-white/[0.04] pl-10 text-base text-neutral-100 shadow-none",
                                "placeholder:text-neutral-600",
                                "focus-visible:border-emerald-400/40 focus-visible:ring-2 focus-visible:ring-emerald-400/25",
                            )}
                            placeholder="np. jan.kowalski"
                        />
                    </div>
                </label>

                <label className="block space-y-2">
                    <span className="text-sm font-medium text-neutral-300">Hasło</span>
                    <div className="relative">
                        <Lock
                            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-500"
                            aria-hidden
                        />
                        <Input
                            type="password"
                            value={password}
                            onChange={(event) => setPassword(event.target.value)}
                            autoComplete="current-password"
                            required
                            disabled={pending}
                            aria-invalid={error ? true : undefined}
                            className={cn(
                                "h-12 rounded-xl border-white/10 bg-white/[0.04] pl-10 text-base text-neutral-100 shadow-none",
                                "placeholder:text-neutral-600",
                                "focus-visible:border-emerald-400/40 focus-visible:ring-2 focus-visible:ring-emerald-400/25",
                            )}
                            placeholder="••••••••"
                        />
                    </div>
                </label>
            </div>

            {error ? (
                <p
                    role="alert"
                    className="rounded-xl border border-red-400/25 bg-red-500/10 px-3 py-2.5 text-sm leading-relaxed text-red-200"
                >
                    {error}
                </p>
            ) : null}

            <div className="space-y-4">
                <Button
                    type="submit"
                    disabled={pending}
                    className={cn(
                        "h-12 w-full rounded-xl border-0 text-base font-semibold shadow-lg shadow-emerald-950/30",
                        "bg-emerald-400 text-neutral-950 hover:bg-emerald-300",
                        "focus-visible:ring-2 focus-visible:ring-emerald-400/40 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950",
                        "disabled:opacity-70",
                    )}
                >
                    {pending ? (
                        <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
                            Logowanie…
                        </>
                    ) : (
                        "Zaloguj się"
                    )}
                </Button>
                <LoginBackendStatus />
            </div>
        </form>
    );
}

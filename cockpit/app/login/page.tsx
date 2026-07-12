"use client";

import { Suspense, FormEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function LoginForm() {
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
            className="w-full max-w-md space-y-4 rounded-3xl border border-white/10 bg-neutral-900/80 p-8 shadow-2xl"
        >
            <div>
                <h1 className="text-2xl font-black tracking-tight">AI-Hub</h1>
                <p className="mt-2 text-sm text-neutral-400">
                    Zaloguj się, aby korzystać z pamięci, psyche i czatu.
                </p>
            </div>
            <label className="block space-y-2">
                <span className="text-sm text-neutral-300">Nazwa użytkownika</span>
                <Input
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    autoComplete="username"
                    required
                />
            </label>
            <label className="block space-y-2">
                <span className="text-sm text-neutral-300">Hasło</span>
                <Input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="current-password"
                    required
                />
            </label>
            {error ? <p className="text-sm text-red-400">{error}</p> : null}
            <Button type="submit" className="w-full" disabled={pending}>
                {pending ? "Logowanie…" : "Zaloguj"}
            </Button>
        </form>
    );
}

export default function LoginPage() {
    return (
        <main className="flex min-h-screen items-center justify-center bg-neutral-950 px-4 text-neutral-100">
            <Suspense
                fallback={
                    <div className="w-full max-w-md rounded-3xl border border-white/10 bg-neutral-900/80 p-8 text-sm text-neutral-400">
                        Ładowanie…
                    </div>
                }
            >
                <LoginForm />
            </Suspense>
        </main>
    );
}

"use client";

import { Loader2, Lock, User } from "lucide-react";
import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

function mapRegisterError(detail: string | undefined, status: number): string {
    const value = (detail || "").toLowerCase();
    if (status === 403 || value.includes("registration closed")) {
        return "Rejestracja jest zamknięta — konto już istnieje. Zaloguj się.";
    }
    if (status === 409 || value.includes("already exists")) {
        return "Ta nazwa użytkownika jest już zajęta.";
    }
    if (value.includes("12") || value.includes("password")) {
        return "Hasło musi mieć co najmniej 12 znaków.";
    }
    if (value.includes("username") || status === 422) {
        return "Nieprawidłowa nazwa użytkownika (min. 3 znaki, litery/cyfry).";
    }
    if (status === 429) {
        return "Zbyt wiele prób. Spróbuj ponownie za chwilę.";
    }
    return detail || "Rejestracja nie powiodła się";
}

export function RegisterForm() {
    const router = useRouter();
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [passwordConfirm, setPasswordConfirm] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(false);
    const [checking, setChecking] = useState(true);
    const [open, setOpen] = useState(false);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await fetch("/api/aihub/auth/registration-status", {
                    headers: { accept: "application/json" },
                    cache: "no-store",
                });
                const body = (await response.json().catch(() => null)) as
                    | { open?: boolean }
                    | null;
                if (cancelled) return;
                if (!response.ok || !body?.open) {
                    setOpen(false);
                    router.replace("/login");
                    return;
                }
                setOpen(true);
            } catch {
                if (!cancelled) {
                    setError("Nie udało się sprawdzić dostępności rejestracji");
                    setOpen(false);
                }
            } finally {
                if (!cancelled) setChecking(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [router]);

    async function onSubmit(event: FormEvent) {
        event.preventDefault();
        setError(null);
        if (password !== passwordConfirm) {
            setError("Hasła muszą być identyczne.");
            return;
        }
        if (password.length < 12) {
            setError("Hasło musi mieć co najmniej 12 znaków.");
            return;
        }
        setPending(true);
        try {
            const response = await fetch("/api/aihub/auth/register", {
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
                setError(mapRegisterError(body?.detail, response.status));
                return;
            }
            router.replace("/");
            router.refresh();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Błąd połączenia");
        } finally {
            setPending(false);
        }
    }

    if (checking) {
        return (
            <div className="login-animate-card space-y-4" aria-busy="true">
                <div className="h-7 w-48 animate-pulse rounded-lg bg-white/10" />
                <div className="h-4 w-full animate-pulse rounded bg-white/5" />
                <p className="text-sm text-neutral-500">Sprawdzam dostępność rejestracji…</p>
            </div>
        );
    }

    if (!open) {
        return (
            <div className="login-animate-card space-y-4 text-center lg:text-left">
                <h1 className="text-2xl font-bold text-neutral-50">Rejestracja zamknięta</h1>
                <p className="text-sm text-neutral-400">
                    Pierwsze konto już istnieje. Zaloguj się istniejącymi danymi.
                </p>
                <Link
                    href="/login"
                    className="inline-flex text-sm font-medium text-emerald-300 hover:text-emerald-200"
                >
                    Przejdź do logowania
                </Link>
            </div>
        );
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
                    Utwórz pierwsze konto
                </h1>
                <p className="text-pretty text-sm leading-relaxed text-neutral-400">
                    To konto otrzyma rolę admina. Po utworzeniu rejestracja zostanie
                    zamknięta.
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
                            minLength={3}
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
                    <span className="text-sm font-medium text-neutral-300">
                        Hasło (min. 12 znaków)
                    </span>
                    <div className="relative">
                        <Lock
                            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-500"
                            aria-hidden
                        />
                        <Input
                            type="password"
                            value={password}
                            onChange={(event) => setPassword(event.target.value)}
                            autoComplete="new-password"
                            required
                            minLength={12}
                            disabled={pending}
                            aria-invalid={error ? true : undefined}
                            className={cn(
                                "h-12 rounded-xl border-white/10 bg-white/[0.04] pl-10 text-base text-neutral-100 shadow-none",
                                "placeholder:text-neutral-600",
                                "focus-visible:border-emerald-400/40 focus-visible:ring-2 focus-visible:ring-emerald-400/25",
                            )}
                            placeholder="••••••••••••"
                        />
                    </div>
                </label>

                <label className="block space-y-2">
                    <span className="text-sm font-medium text-neutral-300">
                        Powtórz hasło
                    </span>
                    <div className="relative">
                        <Lock
                            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-500"
                            aria-hidden
                        />
                        <Input
                            type="password"
                            value={passwordConfirm}
                            onChange={(event) => setPasswordConfirm(event.target.value)}
                            autoComplete="new-password"
                            required
                            minLength={12}
                            disabled={pending}
                            aria-invalid={error ? true : undefined}
                            className={cn(
                                "h-12 rounded-xl border-white/10 bg-white/[0.04] pl-10 text-base text-neutral-100 shadow-none",
                                "placeholder:text-neutral-600",
                                "focus-visible:border-emerald-400/40 focus-visible:ring-2 focus-visible:ring-emerald-400/25",
                            )}
                            placeholder="••••••••••••"
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
                            Tworzenie konta…
                        </>
                    ) : (
                        "Utwórz konto"
                    )}
                </Button>
                <p className="text-center text-sm text-neutral-500">
                    Masz już konto?{" "}
                    <Link
                        href="/login"
                        className="font-medium text-emerald-300 hover:text-emerald-200"
                    >
                        Zaloguj się
                    </Link>
                </p>
            </div>
        </form>
    );
}

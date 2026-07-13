"use client";

import { Suspense } from "react";

import { LoginBrandPanel } from "./login-brand-panel";
import { LoginForm } from "./login-form";

function LoginFormFallback() {
    return (
        <div
            className="login-animate-card w-full max-w-[420px] space-y-4 rounded-[1.75rem] border border-white/10 bg-neutral-900/70 p-8 backdrop-blur-xl"
            aria-busy="true"
            aria-live="polite"
        >
            <div className="mx-auto h-12 w-12 animate-pulse rounded-2xl bg-white/10 lg:mx-0" />
            <div className="space-y-2">
                <div className="mx-auto h-7 w-48 animate-pulse rounded-lg bg-white/10 lg:mx-0" />
                <div className="mx-auto h-4 w-full max-w-sm animate-pulse rounded bg-white/5 lg:mx-0" />
            </div>
            <div className="space-y-3 pt-2">
                <div className="h-12 animate-pulse rounded-xl bg-white/5" />
                <div className="h-12 animate-pulse rounded-xl bg-white/5" />
                <div className="h-12 animate-pulse rounded-xl bg-emerald-400/20" />
            </div>
            <p className="text-center text-sm text-neutral-500">Ładowanie…</p>
        </div>
    );
}

export function LoginScreen() {
    return (
        <div className="login-animate-page flex min-h-[100dvh] w-full overflow-x-hidden overflow-y-auto bg-neutral-950 text-neutral-100 antialiased">
            <LoginBrandPanel />

            <section className="relative flex min-h-[100dvh] min-w-0 flex-1 flex-col">
                <div
                    className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(16,185,129,0.10),transparent_28rem)] lg:hidden"
                    aria-hidden
                />

                <div className="relative z-10 flex flex-1 flex-col items-center justify-center px-4 py-8 sm:px-6 lg:px-10">
                    <div className="mb-8 flex items-center gap-3 lg:hidden">
                        <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-emerald-400/25 bg-emerald-400/10 text-emerald-300">
                            <span className="text-sm font-black">AI</span>
                        </div>
                        <div>
                            <p className="text-sm font-black tracking-tight text-neutral-50">
                                AI-Hub
                            </p>
                            <p className="text-xs text-neutral-500">AI operating system</p>
                        </div>
                    </div>

                    <div className="w-full max-w-[420px] rounded-[1.75rem] border border-white/[0.08] bg-neutral-900/75 p-6 shadow-[0_0_0_1px_rgba(255,255,255,0.03),0_24px_80px_rgba(0,0,0,0.45)] backdrop-blur-xl sm:p-8">
                        <Suspense fallback={<LoginFormFallback />}>
                            <LoginForm />
                        </Suspense>
                    </div>
                </div>
            </section>
        </div>
    );
}

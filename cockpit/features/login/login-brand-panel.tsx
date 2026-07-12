import { Brain, Globe2, Shield, Sparkles } from "lucide-react";

const FEATURES = [
    {
        icon: Brain,
        title: "Pamięć",
        description: "Kontekst sesji, procedury i trwała wiedza użytkownika.",
    },
    {
        icon: Sparkles,
        title: "Psyche",
        description: "Profil behawioralny i adaptacja odpowiedzi w czasie rzeczywistym.",
    },
    {
        icon: Globe2,
        title: "Web / Research",
        description: "Pobieranie, analiza i włączanie źródeł z sieci do rozmowy.",
    },
] as const;

export function LoginBrandPanel() {
    return (
        <aside className="relative hidden min-h-[100dvh] flex-col justify-between overflow-hidden border-r border-white/[0.06] bg-neutral-950 p-10 xl:p-14 lg:flex lg:w-[min(52%,720px)]">
            <div
                className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_12%_8%,rgba(16,185,129,0.18),transparent_42rem),radial-gradient(circle_at_88%_18%,rgba(59,130,246,0.12),transparent_36rem),radial-gradient(circle_at_50%_100%,rgba(16,185,129,0.08),transparent_30rem)]"
                aria-hidden
            />
            <div
                className="pointer-events-none absolute -left-24 top-1/3 h-72 w-72 rounded-full bg-emerald-400/[0.07] blur-3xl"
                aria-hidden
            />
            <div
                className="pointer-events-none absolute bottom-0 right-0 h-64 w-64 rounded-full bg-sky-500/[0.06] blur-3xl"
                aria-hidden
            />

            <div className="relative z-10">
                <div className="flex items-center gap-3">
                    <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-emerald-400/25 bg-emerald-400/10 text-emerald-300">
                        <Shield className="h-5 w-5" aria-hidden />
                    </div>
                    <div>
                        <p className="text-sm font-black tracking-[0.18em] text-emerald-300/90 uppercase">
                            AI-Hub
                        </p>
                        <p className="text-xs text-neutral-500">Cockpit · Memory V2</p>
                    </div>
                </div>
            </div>

            <div className="relative z-10 max-w-lg space-y-8">
                <div className="space-y-4">
                    <h2 className="text-balance text-4xl font-bold leading-[1.08] tracking-tight text-neutral-50 xl:text-5xl">
                        Inteligentna warstwa rozmowy z pamięcią i kontekstem.
                    </h2>
                    <p className="text-pretty text-base leading-relaxed text-neutral-400">
                        Bezpieczny cockpit do pracy z modelem, historią sesji, uploadem
                        plików i głęboką integracją backendu AI-Hub.
                    </p>
                </div>

                <ul className="space-y-4">
                    {FEATURES.map(({ icon: Icon, title, description }) => (
                        <li
                            key={title}
                            className="flex gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.03] p-4 backdrop-blur-sm"
                        >
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-emerald-300">
                                <Icon className="h-5 w-5" aria-hidden />
                            </div>
                            <div className="min-w-0">
                                <p className="font-semibold text-neutral-100">{title}</p>
                                <p className="mt-1 text-sm leading-relaxed text-neutral-500">
                                    {description}
                                </p>
                            </div>
                        </li>
                    ))}
                </ul>
            </div>

            <p className="relative z-10 text-xs text-neutral-600">
                Sesje chronione · signed principal · ownership po stronie backendu
            </p>
        </aside>
    );
}

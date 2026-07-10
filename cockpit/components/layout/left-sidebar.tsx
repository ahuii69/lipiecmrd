"use client";

import { Plus, Sparkles, Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ChatSessions } from "@/features/sidebar/chat-sessions";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import { CockpitNavItem } from "@/lib/types/ui";

const navItems: CockpitNavItem[] = [
    { id: "overview", label: "Overview", description: "Cross-section dashboard" },
    { id: "chat", label: "Czat", description: "Rozmowa i tool-calling" },
    {
        id: "memory",
        label: "Context Memory",
        description: "Rdzeń kontekstu: search, facts, episodes",
    },
    {
        id: "psyche",
        label: "Cognitive State",
        description: "Rdzeń stanu: sentiment, reflect, evolve",
    },
    {
        id: "research",
        label: "Web/Research Layer",
        description: "Rdzeń wiedzy z webu i researchu",
    },
    { id: "planner", label: "Planer", description: "Preview task graph" },
    { id: "reasoning", label: "Rozumowanie", description: "Preview-only" },
    { id: "goals", label: "Cele", description: "Lifecycle i trace" },
    { id: "runtime", label: "Runtime", description: "Status i trace" },
    {
        id: "capabilities",
        label: "Capability Registry",
        description: "Kontrakty warstwy wykonawczej",
    },
    { id: "system", label: "System", description: "Health i diagnostyka" },
    {
        id: "agent-control",
        label: "Agent Control",
        description: "Uruchamianie i sterowanie cyklami agenta",
    },
    { id: "consistency", label: "Consistency", description: "Consistency checks" },
    { id: "reflections", label: "Reflections", description: "Reflection history" },
    { id: "policy", label: "Policy", description: "Policy state" },
    { id: "simulations", label: "Simulations", description: "Simulation runs" },
    { id: "memory-v2", label: "Memory V2", description: "Rich user memory" },
    { id: "psyche-v2", label: "Psyche V2", description: "Personality & state" },
    { id: "identity", label: "Identity", description: "Unified identity view" },
    { id: "contradictions", label: "Contradictions", description: "Memory conflicts" },
    { id: "procedures", label: "Procedures", description: "Learned workflows" },
    { id: "calibration", label: "Calibration", description: "Behavior debug & thresholds" },
];

export function LeftSidebar() {
    const {
        sessions,
        activeSessionId,
        currentSection,
        setSection,
        createSession,
        setSessionMode,
        apiKeyOverride,
        setApiKeyOverride,
    } = useCockpitStore();

    const activeSession =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    return (
        <div className="flex h-full flex-col gap-3 rounded-xl border border-border bg-card/70 p-3 shadow-panel">
            <div>
                <div className="flex items-center justify-between">
                    <p className="text-sm font-bold tracking-wide">AI-Hub</p>
                    <Badge variant="secondary">cockpit</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                    Operacyjny panel runtime, bez korpo mgły.
                </p>
            </div>

            <Button size="sm" onClick={createSession}>
                <Plus className="mr-1 h-4 w-4" />
                Nowa sesja
            </Button>

            <div className="space-y-1">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                    Tryb sesji
                </p>
                <Select
                    value={activeSession.mode}
                    onValueChange={(v: string) =>
                        setSessionMode(
                            activeSession.id,
                            v as typeof activeSession.mode,
                        )
                    }
                >
                    <SelectTrigger>
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="chat">chat</SelectItem>
                        <SelectItem value="agent">agent</SelectItem>
                        <SelectItem value="readonly">readonly</SelectItem>
                        <SelectItem value="debug">debug</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            <div className="space-y-1">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                    Hub API_KEY (opcjonalnie)
                </p>
                <p className="text-[10px] leading-snug text-muted-foreground">
                    Jak w <code className="text-[10px]">morda/.env</code> — nie
                    klucz LLM. Puste = z env serwera.
                </p>
                <input
                    value={apiKeyOverride}
                    onChange={(e) => setApiKeyOverride(e.target.value)}
                    onBlur={() => {
                        if (apiKeyOverride.trim() === "") {
                            setApiKeyOverride("");
                        }
                    }}
                    placeholder="puste = .env serwera"
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-xs"
                    autoComplete="off"
                />
            </div>

            <Separator />

            <div className="space-y-1">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                    Sekcje
                </p>
                <div className="grid gap-1">
                    {navItems.map((item) => (
                        <button
                            type="button"
                            key={item.id}
                            className={`rounded-md px-2 py-2 text-left transition ${currentSection === item.id ? "bg-primary/20 text-primary" : "hover:bg-muted/60"}`}
                            onClick={() => setSection(item.id)}
                        >
                            <div className="flex items-center justify-between">
                                <span className="text-xs font-semibold">
                                    {item.label}
                                </span>
                                {currentSection === item.id ? (
                                    <Sparkles className="h-3.5 w-3.5" />
                                ) : null}
                            </div>
                            <p className="text-[11px] text-muted-foreground">
                                {item.description}
                            </p>
                        </button>
                    ))}
                </div>
            </div>

            <Separator />

            <div className="min-h-0 flex-1">
                <p className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground">
                    Sesje
                </p>
                <ChatSessions userId={activeSession.userId} />
            </div>

            <div className="flex items-center gap-2 rounded-md border border-border bg-background/60 p-2 text-[11px] text-muted-foreground">
                <Wrench className="h-3.5 w-3.5" />
                <span>
                    Symulacja dojdzie jako kolejna sekcja, bez przebudowy
                    layoutu.
                </span>
            </div>
        </div>
    );
}

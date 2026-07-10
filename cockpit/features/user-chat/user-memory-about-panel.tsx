"use client";

import { Brain, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { apiClient, ApiClientError } from "@/lib/api/client";
import type { MemoryV2SummaryItem, MemoryV2SummaryResponse } from "@/lib/api/types";
import { formatTs } from "@/lib/utils";

function itemTitle(row: MemoryV2SummaryItem): string {
    const t = row.title ?? row.label ?? "";
    return typeof t === "string" && t.trim() ? t.trim() : "(bez tytułu)";
}

function itemBody(row: MemoryV2SummaryItem): string {
    const c = row.content ?? "";
    return typeof c === "string" ? c : "";
}

function typeLabel(row: MemoryV2SummaryItem): string {
    const raw = row.memory_type ?? row.type ?? "";
    return typeof raw === "string" && raw ? raw : "—";
}

function sourceLine(row: MemoryV2SummaryItem): string | null {
    const sk = row.source_kind;
    const sr = row.source_ref;
    const parts: string[] = [];
    if (typeof sk === "string" && sk) parts.push(sk);
    if (typeof sr === "string" && sr.trim()) parts.push(sr.trim());
    return parts.length ? parts.join(" · ") : null;
}

function Section({
    title,
    items,
    emptyHint,
}: {
    title: string;
    items: MemoryV2SummaryItem[];
    emptyHint: string;
}) {
    if (!items.length) {
        return (
            <div className="rounded-lg border border-white/10 bg-black/20 px-3 py-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
                    {title}
                </p>
                <p className="mt-1 text-sm text-neutral-500">{emptyHint}</p>
            </div>
        );
    }
    return (
        <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
                {title}
            </p>
            <ul className="space-y-2">
                {items.map((row, idx) => {
                    const id =
                        typeof row.id === "string" && row.id
                            ? row.id
                            : `row-${idx}`;
                    const ts =
                        typeof row.updated_ts === "number"
                            ? row.updated_ts
                            : typeof row.created_ts === "number"
                              ? row.created_ts
                              : null;
                    const src = sourceLine(row);
                    return (
                        <li
                            key={id}
                            className="rounded-lg border border-white/10 bg-neutral-900/80 px-3 py-2 text-sm"
                        >
                            <div className="flex flex-wrap items-baseline gap-2">
                                <span className="font-medium text-neutral-100">
                                    {itemTitle(row)}
                                </span>
                                <span className="text-[11px] text-neutral-500">
                                    {typeLabel(row)}
                                </span>
                            </div>
                            {itemBody(row) ? (
                                <p className="mt-1 whitespace-pre-wrap text-neutral-300">
                                    {itemBody(row)}
                                </p>
                            ) : null}
                            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-neutral-500">
                                {src ? <span>Źródło: {src}</span> : null}
                                {ts !== null ? (
                                    <span>Zapis: {formatTs(ts)}</span>
                                ) : null}
                            </div>
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}

export function UserMemoryAboutPanel({
    userId,
    apiKeyOverride,
    disabled,
}: {
    userId: string;
    apiKeyOverride?: string;
    disabled?: boolean;
}) {
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [data, setData] = useState<MemoryV2SummaryResponse | null>(null);

    const load = useCallback(async () => {
        if (!userId || userId === "default") return;
        setLoading(true);
        setError(null);
        try {
            const r = await apiClient.getMemoryV2Summary(userId, apiKeyOverride);
            setData(r);
        } catch (e) {
            const msg =
                e instanceof ApiClientError
                    ? e.message
                    : e instanceof Error
                      ? e.message
                      : "Nie udało się wczytać pamięci";
            setError(msg);
        } finally {
            setLoading(false);
        }
    }, [userId, apiKeyOverride]);

    useEffect(() => {
        if (!open || disabled) return;
        void load();
    }, [open, disabled, load]);

    const ready = Boolean(userId && userId !== "default" && !disabled);

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!ready}
                    className="gap-2 border-white/15 bg-neutral-900/80 text-neutral-100 hover:bg-white/10"
                >
                    <Brain className="h-4 w-4 shrink-0 text-violet-300" />
                    Pamięć
                </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[min(90vh,720px)] max-w-[min(96vw,520px)] border-white/10 bg-neutral-950 text-neutral-100">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2 text-lg">
                        <Brain className="h-5 w-5 text-violet-300" />
                        Twoja pamięć
                    </DialogTitle>
                    <DialogDescription className="text-neutral-400">
                        Fakty, preferencje i ustalenia zapisane dla tego profilu
                        użytkownika (tylko Twój identyfikator).
                    </DialogDescription>
                </DialogHeader>
                {!ready ? (
                    <p className="text-sm text-neutral-500">
                        Wybierz lub aktywuj profil użytkownika, aby zobaczyć
                        pamięć.
                    </p>
                ) : loading ? (
                    <div className="flex items-center gap-2 py-8 text-neutral-400">
                        <Loader2 className="h-5 w-5 animate-spin" />
                        Wczytywanie…
                    </div>
                ) : error ? (
                    <p className="text-sm text-red-300">{error}</p>
                ) : (
                    <ScrollArea className="max-h-[min(70vh,560px)] pr-3">
                        <div className="space-y-4 pb-2">
                            <p className="text-xs text-neutral-500">
                                Łącznie wpisów:{" "}
                                <span className="text-neutral-300">
                                    {data?.total_items ?? 0}
                                </span>
                            </p>
                            <Section
                                title="Trwałe fakty"
                                items={data?.facts ?? []}
                                emptyHint="Brak zapisanych faktów."
                            />
                            <Separator className="bg-white/10" />
                            <Section
                                title="Preferencje"
                                items={data?.preferences ?? []}
                                emptyHint="Brak zapisanych preferencji."
                            />
                            <Separator className="bg-white/10" />
                            <Section
                                title="Ważne ustalenia"
                                items={data?.key_settlements ?? []}
                                emptyHint="Brak wyróżnionych ustaleń."
                            />
                        </div>
                    </ScrollArea>
                )}
            </DialogContent>
        </Dialog>
    );
}

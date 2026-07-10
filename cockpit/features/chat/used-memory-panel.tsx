"use client";

import { ChevronDown } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { apiClient } from "@/lib/api/client";
import type { ChatTurnResponse, MemoryUsedTraceEntry } from "@/lib/api/types";
import { useCockpitStore } from "@/lib/store/cockpit-store";

function isMemoryUsedEntry(x: unknown): x is MemoryUsedTraceEntry {
    if (!x || typeof x !== "object") return false;
    const o = x as Record<string, unknown>;
    const src = o.source;
    return (
        typeof o.id === "string" &&
        typeof o.text === "string" &&
        (src === "stm" || src === "memory_v2" || src === "kg")
    );
}

function sourceBadgeVariant(
    source: MemoryUsedTraceEntry["source"],
): "secondary" | "outline" | "default" {
    switch (source) {
        case "stm":
            return "secondary";
        case "memory_v2":
            return "default";
        default:
            return "outline";
    }
}

function sourceLabel(source: MemoryUsedTraceEntry["source"]): string {
    if (source === "memory_v2") return "v2";
    return source;
}

type V2Overlay = { suppressed: boolean; pinned: boolean };

export function UsedMemoryPanel({
    messageId,
    diagnostics,
}: {
    messageId: string;
    diagnostics?: ChatTurnResponse;
}) {
    const [open, setOpen] = useState(false);
    const [expandedRow, setExpandedRow] = useState<string | null>(null);
    const [ignoredKeys, setIgnoredKeys] = useState<Set<string>>(() => new Set());
    const [archivedKeys, setArchivedKeys] = useState<Set<string>>(() => new Set());
    const [v2Overlay, setV2Overlay] = useState<Record<string, V2Overlay>>({});

    const userId = useCockpitStore((s) => {
        const sess = s.sessions.find((x) => x.id === s.activeSessionId);
        return sess?.userId ?? "default";
    });
    const apiKeyOverride = useCockpitStore((s) => s.apiKeyOverride);

    const entries = useMemo(() => {
        const raw = diagnostics?.trace?.memory_used;
        if (!Array.isArray(raw)) return [];
        return raw.filter(isMemoryUsedEntry);
    }, [diagnostics?.trace?.memory_used]);

    const rowKey = useCallback(
        (e: MemoryUsedTraceEntry) => `${messageId}:${e.source}:${e.id}`,
        [messageId],
    );

    const effectiveV2 = useCallback(
        (e: MemoryUsedTraceEntry, k: string): V2Overlay => {
            const o = v2Overlay[k];
            if (o) return o;
            return {
                suppressed: Boolean(e.is_suppressed),
                pinned: Boolean(e.is_pinned),
            };
        },
        [v2Overlay],
    );

    const visible = useMemo(
        () =>
            entries.filter((e) => {
                const k = rowKey(e);
                if (ignoredKeys.has(k)) return false;
                if (e.source === "memory_v2") {
                    if (archivedKeys.has(k) || Boolean(e.is_archived)) {
                        return false;
                    }
                }
                return true;
            }),
        [entries, ignoredKeys, archivedKeys, rowKey],
    );

    if (visible.length === 0) return null;

    const keyOpt = apiKeyOverride || undefined;

    const onIgnore = (e: MemoryUsedTraceEntry) => {
        setIgnoredKeys((prev) => new Set(prev).add(rowKey(e)));
        setExpandedRow(null);
    };

    const onArchive = async (e: MemoryUsedTraceEntry) => {
        const k = rowKey(e);
        try {
            await apiClient.archiveMemoryV2Item(
                { user_id: userId, memory_id: e.id },
                keyOpt,
            );
            setArchivedKeys((prev) => new Set(prev).add(k));
        } catch (err) {
            console.log("[memory_used archive]", err);
        }
        setExpandedRow(null);
    };

    const onSuppress = async (e: MemoryUsedTraceEntry, suppressed: boolean) => {
        const k = rowKey(e);
        try {
            await apiClient.suppressMemoryV2Item(
                { user_id: userId, memory_id: e.id, suppressed },
                keyOpt,
            );
            setV2Overlay((prev) => ({
                ...prev,
                [k]: {
                    suppressed,
                    pinned: prev[k]?.pinned ?? Boolean(e.is_pinned),
                },
            }));
        } catch (err) {
            console.log("[memory_used suppress]", err);
        }
    };

    const onPin = async (e: MemoryUsedTraceEntry, pinned: boolean) => {
        const k = rowKey(e);
        try {
            await apiClient.pinMemoryV2Item(
                { user_id: userId, memory_id: e.id, pinned },
                keyOpt,
            );
            setV2Overlay((prev) => ({
                ...prev,
                [k]: {
                    suppressed:
                        prev[k]?.suppressed ?? Boolean(e.is_suppressed),
                    pinned,
                },
            }));
        } catch (err) {
            console.log("[memory_used pin]", err);
        }
    };

    return (
        <div
            className="mt-2 border-t border-border/60 pt-2"
            onClick={(ev) => ev.stopPropagation()}
        >
            <button
                type="button"
                className="flex w-full items-center justify-between gap-2 rounded-md px-1 py-1 text-left text-xs font-medium text-muted-foreground hover:bg-muted/40"
                aria-expanded={open}
                onClick={() => setOpen((v) => !v)}
            >
                <span>🧠 Used Memory</span>
                <ChevronDown
                    className={`h-4 w-4 shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
                />
            </button>
            {open ? (
                <ul className="mt-2 space-y-1.5">
                    {visible.map((e) => {
                        const k = rowKey(e);
                        const isEx = expandedRow === k;
                        const v2 = e.source === "memory_v2" ? effectiveV2(e, k) : null;
                        return (
                            <li
                                key={k}
                                className="rounded-md border border-border/50 bg-muted/20 px-2 py-1.5"
                            >
                                <button
                                    type="button"
                                    className="flex w-full items-start gap-2 text-left"
                                    onClick={() =>
                                        setExpandedRow((cur) =>
                                            cur === k ? null : k,
                                        )
                                    }
                                >
                                    <div className="mt-0.5 flex shrink-0 flex-wrap gap-0.5">
                                        <Badge
                                            variant={sourceBadgeVariant(e.source)}
                                            className="px-1.5 py-0 text-[10px]"
                                        >
                                            {sourceLabel(e.source)}
                                        </Badge>
                                        {v2?.suppressed ? (
                                            <Badge
                                                variant="warning"
                                                className="px-1 py-0 text-[9px]"
                                            >
                                                wycisz.
                                            </Badge>
                                        ) : null}
                                        {v2?.pinned ? (
                                            <Badge
                                                variant="secondary"
                                                className="px-1 py-0 text-[9px]"
                                            >
                                                pin
                                            </Badge>
                                        ) : null}
                                    </div>
                                    <span className="line-clamp-2 min-w-0 flex-1 text-xs text-foreground/90">
                                        {e.text}
                                    </span>
                                    <ChevronDown
                                        className={`mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${isEx ? "rotate-180" : ""}`}
                                    />
                                </button>
                                {isEx ? (
                                    <div className="mt-2 flex flex-col gap-2 border-t border-border/40 pt-2">
                                        <div className="flex flex-wrap gap-1.5">
                                            <Button
                                                type="button"
                                                size="sm"
                                                variant="outline"
                                                className="h-7 text-xs"
                                                onClick={() => onIgnore(e)}
                                            >
                                                Ignore
                                            </Button>
                                            {e.source === "memory_v2" ? (
                                                <>
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="outline"
                                                        className="h-7 text-xs"
                                                        onClick={() =>
                                                            void onArchive(e)
                                                        }
                                                    >
                                                        Archive
                                                    </Button>
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="outline"
                                                        className="h-7 text-xs"
                                                        disabled={
                                                            v2?.suppressed ===
                                                            true
                                                        }
                                                        onClick={() =>
                                                            void onSuppress(
                                                                e,
                                                                true,
                                                            )
                                                        }
                                                    >
                                                        Suppress
                                                    </Button>
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="outline"
                                                        className="h-7 text-xs"
                                                        disabled={
                                                            v2?.suppressed !==
                                                            true
                                                        }
                                                        onClick={() =>
                                                            void onSuppress(
                                                                e,
                                                                false,
                                                            )
                                                        }
                                                    >
                                                        Unsuppress
                                                    </Button>
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="outline"
                                                        className="h-7 text-xs"
                                                        disabled={
                                                            v2?.pinned === true
                                                        }
                                                        onClick={() =>
                                                            void onPin(e, true)
                                                        }
                                                    >
                                                        Pin
                                                    </Button>
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="outline"
                                                        className="h-7 text-xs"
                                                        disabled={
                                                            v2?.pinned !== true
                                                        }
                                                        onClick={() =>
                                                            void onPin(e, false)
                                                        }
                                                    >
                                                        Unpin
                                                    </Button>
                                                </>
                                            ) : null}
                                        </div>
                                        {e.source !== "memory_v2" ? (
                                            <p className="text-[10px] text-muted-foreground">
                                                STM / KG: tylko Ignore (brak
                                                bezpiecznego API usuwania w
                                                Cockpit).
                                            </p>
                                        ) : null}
                                    </div>
                                ) : null}
                            </li>
                        );
                    })}
                </ul>
            ) : null}
        </div>
    );
}

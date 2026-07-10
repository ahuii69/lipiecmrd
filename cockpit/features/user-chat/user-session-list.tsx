"use client";

import { Eraser, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { lastUserVisiblePreview } from "@/lib/chat/session-title";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import { formatTs } from "@/lib/utils";

type ConfirmAction = { type: "delete" | "clear"; sessionId: string };

export function UserSessionList({ onSelect }: { onSelect?: () => void }) {
    const {
        sessions,
        activeSessionId,
        createSession,
        setActiveSession,
        updateSessionTitle,
        deleteSession,
        clearSessionMessages,
    } = useCockpitStore();

    const sorted = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt);

    const [renameId, setRenameId] = useState<string | null>(null);
    const [renameValue, setRenameValue] = useState("");
    const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);

    const startRename = (sessionId: string, currentTitle: string) => {
        setRenameId(sessionId);
        setRenameValue(currentTitle);
    };

    const cancelRename = () => {
        setRenameId(null);
        setRenameValue("");
    };

    const commitRename = () => {
        if (!renameId) return;
        const trimmed = renameValue.trim();
        if (!trimmed) {
            cancelRename();
            return;
        }
        updateSessionTitle(renameId, trimmed);
        cancelRename();
    };

    const handleRenameKey = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            e.preventDefault();
            commitRename();
        }
        if (e.key === "Escape") {
            cancelRename();
        }
    };

    const executeConfirm = () => {
        if (!confirmAction) return;
        if (confirmAction.type === "delete") {
            deleteSession(confirmAction.sessionId);
            onSelect?.();
        } else {
            clearSessionMessages(confirmAction.sessionId);
        }
        setConfirmAction(null);
    };

    return (
        <div className="flex h-full flex-col gap-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <p className="text-sm font-semibold tracking-tight text-neutral-100">
                        Rozmowy
                    </p>
                    <p className="mt-0.5 text-[13px] leading-snug text-neutral-500">
                        Wybierz wątek lub utwórz nowy
                    </p>
                </div>
                <Button
                    className="h-10 shrink-0 rounded-xl border border-white/[0.12] bg-neutral-900/90 px-3.5 text-sm font-semibold text-neutral-100 shadow-sm hover:bg-neutral-800/95 focus-visible:ring-2 focus-visible:ring-white/15"
                    variant="outline"
                    onClick={createSession}
                    aria-label="Nowa sesja"
                    data-testid="user-new-session"
                >
                    <Plus className="mr-1.5 h-4 w-4" />
                    Nowa sesja
                </Button>
            </div>

            <Separator className="bg-white/[0.08]" />

            <ScrollArea className="min-h-0 flex-1">
                <div className="space-y-1.5 pr-2">
                    {sorted.length === 0 ? (
                        <p className="rounded-xl border border-dashed border-white/10 px-3 py-6 text-center text-sm text-neutral-500">
                            Brak rozmów. Użyj „+ Nowa sesja”.
                        </p>
                    ) : null}
                    {sorted.map((s) => {
                        const isActive = s.id === activeSessionId;
                        const isRenaming = renameId === s.id;
                        return (
                            <div
                                key={s.id}
                                data-testid="user-session-item"
                                data-session-id={s.id}
                                className={`group/item flex items-center gap-1 rounded-xl border px-2 py-2 text-sm transition-colors sm:rounded-2xl sm:px-2.5 sm:py-2 ${
                                    isActive
                                        ? "border-white/[0.14] bg-neutral-900/95 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05)]"
                                        : "border-transparent bg-transparent hover:border-white/[0.08] hover:bg-neutral-900/55"
                                }`}
                            >
                                {isRenaming ? (
                                    <Input
                                        autoFocus
                                        value={renameValue}
                                        onChange={(e) =>
                                            setRenameValue(e.target.value)
                                        }
                                        onBlur={commitRename}
                                        onKeyDown={handleRenameKey}
                                        className="h-9 min-w-0 flex-1 rounded-lg border-white/20 bg-neutral-950 px-2 text-base text-neutral-100"
                                        maxLength={60}
                                        data-testid="user-session-rename-input"
                                    />
                                ) : (
                                    <button
                                        type="button"
                                        className="min-w-0 flex-1 rounded-xl py-0.5 text-left transition-colors"
                                        data-testid="user-session-select"
                                        onClick={() => {
                                            setActiveSession(s.id);
                                            onSelect?.();
                                        }}
                                    >
                                        <p
                                            className={`truncate text-base text-neutral-100 ${
                                                isActive ? "font-bold" : "font-medium"
                                            }`}
                                        >
                                            {s.title}
                                        </p>
                                        <p className="mt-1 line-clamp-2 text-sm text-neutral-500">
                                            {lastUserVisiblePreview(s.messages) ||
                                                "Brak wiadomości"}
                                        </p>
                                        <p className="mt-0.5 truncate text-xs text-neutral-600">
                                            {formatTs(s.updatedAt)}
                                        </p>
                                    </button>
                                )}

                                {!isRenaming && (
                                    <div className="flex shrink-0 items-center gap-0.5 opacity-100 sm:opacity-0 sm:transition-opacity sm:focus-within:opacity-100 sm:group-hover/item:opacity-100">
                                        <button
                                            type="button"
                                            className="rounded-lg p-1.5 text-neutral-500 hover:bg-white/10 hover:text-neutral-200"
                                            data-testid="user-session-rename"
                                            onClick={() =>
                                                startRename(s.id, s.title)
                                            }
                                            aria-label="Zmień nazwę sesji"
                                            title="Zmień nazwę"
                                        >
                                            <Pencil className="h-4 w-4" />
                                        </button>
                                        <button
                                            className="rounded-lg p-1.5 text-neutral-500 hover:bg-white/10 hover:text-neutral-200 disabled:pointer-events-none disabled:opacity-30"
                                            onClick={() =>
                                                setConfirmAction({
                                                    type: "clear",
                                                    sessionId: s.id,
                                                })
                                            }
                                            aria-label="Wyczyść wiadomości"
                                            title="Wyczyść wiadomości"
                                            disabled={s.messages.length === 0}
                                        >
                                            <Eraser className="h-4 w-4" />
                                        </button>
                                        <button
                                            type="button"
                                            className="rounded-lg p-1.5 text-neutral-500 hover:bg-red-950/50 hover:text-red-400"
                                            data-testid="user-session-delete"
                                            onClick={() =>
                                                setConfirmAction({
                                                    type: "delete",
                                                    sessionId: s.id,
                                                })
                                            }
                                            aria-label="Usuń sesję"
                                            title="Usuń sesję"
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </button>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </ScrollArea>

            <Dialog
                open={confirmAction !== null}
                onOpenChange={(open) => {
                    if (!open) setConfirmAction(null);
                }}
            >
                <DialogContent className="max-w-xs border-white/10 bg-neutral-950 text-neutral-100">
                    <DialogHeader>
                        <DialogTitle>
                            {confirmAction?.type === "delete"
                                ? "Usunąć sesję?"
                                : "Wyczyścić wiadomości?"}
                        </DialogTitle>
                    </DialogHeader>
                    <DialogDescription className="text-neutral-400">
                        {confirmAction?.type === "delete"
                            ? "Ta rozmowa zostanie trwale usunięta."
                            : "Wiadomości znikną, sesja pozostanie na liście."}
                    </DialogDescription>
                    <DialogFooter>
                        <Button
                            variant="ghost"
                            size="sm"
                            data-testid="user-session-confirm-cancel"
                            onClick={() => setConfirmAction(null)}
                        >
                            Anuluj
                        </Button>
                        <Button
                            variant={
                                confirmAction?.type === "delete"
                                    ? "destructive"
                                    : "default"
                            }
                            size="sm"
                            data-testid="user-session-confirm-ok"
                            onClick={executeConfirm}
                        >
                            {confirmAction?.type === "delete"
                                ? "Usuń"
                                : "Wyczyść"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

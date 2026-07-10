"use client";

import { RotateCcw, Send, Square } from "lucide-react";
import { FormEvent, KeyboardEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function MessageComposer({
    onSend,
    onRetry,
    onStop,
    disabled,
    retryDisabled,
    stopVisible = false,
    showOperatorHints = true,
}: {
    onSend: (text: string) => Promise<void>;
    onRetry: () => Promise<void>;
    onStop?: () => void;
    disabled: boolean;
    retryDisabled: boolean;
    stopVisible?: boolean;
    showOperatorHints?: boolean;
}) {
    const [value, setValue] = useState("");

    const submit = async (e: FormEvent) => {
        e.preventDefault();
        const text = value.trim();
        if (!text || disabled) return;
        const prev = value;
        try {
            await onSend(text);
            setValue("");
        } catch {
            setValue(prev);
        }
    };

    return (
        <form onSubmit={submit} className="space-y-2">
            <Textarea
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={async (e: KeyboardEvent<HTMLTextAreaElement>) => {
                    const sendByEnter =
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        !e.ctrlKey &&
                        !e.metaKey &&
                        !e.altKey;

                    if (sendByEnter) {
                        e.preventDefault();
                        const text = value.trim();
                        if (!text || disabled) return;
                        const prev = value;
                        try {
                            await onSend(text);
                            setValue("");
                        } catch {
                            setValue(prev);
                        }
                    }
                }}
                placeholder="Napisz wiadomość do AI-Hub..."
                className="min-h-[72px] touch-manipulation sm:min-h-[96px]"
                disabled={disabled}
            />
            <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                    {showOperatorHints
                        ? "Enter = wyślij, Shift+Enter = nowa linia. Runtime trace po prawej."
                        : "Enter — wyślij, Shift+Enter — nowa linia."}
                </p>
                <div className="flex items-center gap-2">
                    {stopVisible && onStop ? (
                        <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            onClick={onStop}
                        >
                            <Square className="mr-1 h-4 w-4" />
                            Stop
                        </Button>
                    ) : null}
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={onRetry}
                        disabled={disabled || retryDisabled}
                    >
                        <RotateCcw className="mr-1 h-4 w-4" />
                        Ponów
                    </Button>
                    <Button
                        type="submit"
                        size="sm"
                        disabled={disabled || !value.trim()}
                    >
                        <Send className="mr-1 h-4 w-4" />
                        Wyślij
                    </Button>
                </div>
            </div>
        </form>
    );
}

"use client";

import { AlertTriangle, Bot, Check, Copy, FileText, UserRound } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ChatUIMessage } from "@/lib/store/cockpit-store";
import { formatTs } from "@/lib/utils";

function filesUsedLabel(n: number): string {
    if (n <= 0) return "";
    if (n === 1) return "1 plik";
    if (n >= 2 && n <= 4) return `${n} pliki`;
    return `${n} plików`;
}

const CHIP_LABELS: Record<string, string> = {
    "attachment-used": "załącznik",
    "attachment-failed": "załącznik — błąd",
    "image-used": "obraz",
    "image-attached": "obraz",
    "memory-used": "pamięć",
    "web-used": "sieć",
    "stt-input": "dyktowanie",
};

function chipLabel(id: string): string {
    if (id.startsWith("strat-")) return `strategia: ${id.slice(6)}`;
    return CHIP_LABELS[id] ?? id;
}

function formatFriendlyError(error: string): string {
    const low = error.toLowerCase();
    if (low.includes("invalid api key") || low.includes("invalid_api_key")) return "Nieprawidłowy klucz API. Sprawdź połączenie z Hubem.";
    if (low.includes("timeout")) return "Przekroczono czas oczekiwania. Spróbuj ponownie.";
    if (low.includes("network") || low.includes("connection")) return "Problem z połączeniem z backendem.";
    if (low.includes("401") || low.includes("403") || low.includes("auth")) return "Backend odrzucił dostęp. Sprawdź klucz API.";
    if (low.includes("500") || low.includes("502") || low.includes("503")) return "Backend zwrócił błąd serwera.";
    return error.length > 180 ? `${error.slice(0, 180)}…` : error;
}

export function UserMessageItem({ message }: { message: ChatUIMessage }) {
    const [copied, setCopied] = useState(false);
    const isUser = message.role === "user";
    const body = (message.content ?? "").trim();

    const copy = async () => {
        if (!body) return;
        await navigator.clipboard.writeText(message.content ?? "");
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
    };

    return (
        <div className="group/message w-full py-5 sm:py-7" data-testid="chat-message" data-role={message.role} data-streaming={message.streaming === true ? "true" : "false"}>
            <div className={`mx-auto flex w-full max-w-3xl gap-3 px-4 sm:gap-4 sm:px-6 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
                <div className={`mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border shadow-lg sm:h-10 sm:w-10 ${isUser ? "border-white/12 bg-white text-neutral-950 shadow-white/5" : "border-emerald-300/20 bg-emerald-300/10 text-emerald-200 shadow-emerald-950/30"}`} aria-hidden>
                    {isUser ? <UserRound className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                </div>
                <article className={`min-w-0 flex-1 ${isUser ? "items-end text-right" : "items-start text-left"}`}>
                    <div className={`mb-2 flex flex-wrap items-center gap-2 ${isUser ? "justify-end" : "justify-start"}`}>
                        <span className="text-sm font-semibold text-neutral-200">{isUser ? "Ty" : "AI-Hub"}</span>
                        <span className="text-xs text-neutral-600">{formatTs(message.createdAt)}</span>
                        {message.error ? <Badge variant="danger" className="text-[11px]">błąd</Badge> : null}
                        {isUser && (message.attached_file_ids?.length ?? 0) > 0 ? <Badge variant="outline" className="border-white/15 bg-white/5 text-[11px] text-neutral-300"><FileText className="mr-1 h-3 w-3" />{filesUsedLabel(message.attached_file_ids?.length ?? 0)}</Badge> : null}
                        {isUser && message.sttUsed ? <Badge variant="outline" className="border-white/15 bg-white/5 text-[11px] text-neutral-300">dyktowanie</Badge> : null}
                        {!isUser && message.contextChips?.length ? <span className="flex max-w-full flex-wrap gap-1">{message.contextChips.slice(0, 4).map((c) => <Badge key={c} variant="secondary" className="border-white/10 bg-white/5 text-[11px] font-normal text-neutral-300" title={c}>{chipLabel(c)}</Badge>)}</span> : null}
                        {!isUser && message.attachmentsSummary ? <Badge variant="outline" className="max-w-[220px] truncate border-white/15 text-[11px] font-normal text-neutral-400" title={message.attachmentsSummary}>załącznik</Badge> : null}
                    </div>
                    <div className={`relative rounded-[1.45rem] border px-4 py-3.5 shadow-xl sm:px-5 sm:py-4 ${isUser ? "ml-auto max-w-[min(42rem,92%)] border-white/12 bg-white text-left text-neutral-950 shadow-black/25" : "mr-auto max-w-full border-white/10 bg-neutral-900/80 text-neutral-100 shadow-black/35"}`}>
                        <Button className={`absolute right-2 top-2 h-8 w-8 rounded-xl opacity-0 transition-opacity hover:bg-black/10 group-hover/message:opacity-100 ${isUser ? "text-neutral-600" : "text-neutral-400 hover:bg-white/10 hover:text-neutral-100"}`} variant="ghost" size="icon" onClick={() => void copy()} disabled={!body} aria-label="Kopiuj wiadomość">
                            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                        </Button>
                        {isUser ? <p className="break-words whitespace-pre-wrap pr-8 text-[15px] leading-7 sm:text-base">{message.content}</p> : message.streaming ? <div className="min-h-[1.7rem] pr-8 text-[15px] leading-7 sm:text-base"><span className="whitespace-pre-wrap break-words">{message.content}</span><span className="ml-1 inline-block h-[1.05em] w-0.5 translate-y-[0.18em] animate-pulse rounded-sm bg-emerald-300" aria-hidden />{!message.content ? <span className="text-neutral-500">Łączenie ze strumieniem…</span> : null}</div> : body ? <div className="prose prose-invert max-w-none break-words pr-6 text-[15px] leading-7 prose-p:my-3 prose-pre:max-w-full prose-pre:overflow-x-auto prose-pre:rounded-2xl prose-pre:border prose-pre:border-white/10 prose-pre:bg-black/50 prose-code:text-[0.9em] sm:text-base"><ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>{message.content || ""}</ReactMarkdown></div> : null}
                        {message.error ? <div className="mt-3 rounded-2xl border border-red-400/35 bg-red-500/10 p-3 text-sm leading-relaxed text-red-100"><div className="mb-1 flex items-center gap-2 font-semibold"><AlertTriangle className="h-4 w-4 shrink-0" />Nie udało się wysłać</div>{formatFriendlyError(message.error)}</div> : null}
                    </div>
                </article>
            </div>
        </div>
    );
}

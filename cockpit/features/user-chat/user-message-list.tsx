"use client";

import { BrainCircuit, MessageSquareText, Sparkles } from "lucide-react";
import { useRef } from "react";

import { UserMessageItem } from "@/features/user-chat/user-message-item";
import { useStickToBottomScroll } from "@/lib/chat/use-stick-to-bottom-scroll";
import type { ChatUIMessage } from "@/lib/store/cockpit-store";

const SUGGESTIONS = [
    "Podsumuj aktualny stan projektu i wskaż największe ryzyka.",
    "Sprawdź pamięć: co już o mnie wiesz i czego brakuje?",
    "Zrób plan refaktoru backendu bez wycinania ważnych funkcji.",
];

export function UserMessageList({ messages, loading, onSuggestion }: { messages: ChatUIMessage[]; loading: boolean; onSuggestion?: (text: string) => void }) {
    const scrollRef = useRef<HTMLDivElement | null>(null);
    const contentRef = useRef<HTMLDivElement | null>(null);
    const stickToBottomRef = useRef(true);
    const lastId = messages.length ? messages[messages.length - 1].id : "";
    const streamSig = messages.map((m) => `${m.id}:${m.content.length}:${m.streaming ? 1 : 0}`).join("|");
    const { onScroll } = useStickToBottomScroll({ scrollRef, contentRef, stickToBottomRef, messagesLength: messages.length, lastMessageId: lastId, streamSig, loading, streamingBubble: messages.some((m) => m.streaming === true) });

    return (
        <div ref={scrollRef} onScroll={onScroll} data-testid="user-message-scroll" className="h-full overflow-y-auto overflow-x-hidden scroll-smooth bg-[radial-gradient(circle_at_top,rgba(16,185,129,0.09),transparent_28rem)] [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.18)_transparent]">
            <div ref={contentRef} className="min-h-full">
                {messages.length === 0 ? (
                    <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col justify-center px-4 py-10 sm:px-6">
                        <div className="mx-auto max-w-2xl text-center">
                            <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-[1.35rem] border border-emerald-300/20 bg-emerald-300/10 text-emerald-200 shadow-2xl shadow-emerald-950/30"><BrainCircuit className="h-8 w-8" /></div>
                            <p className="text-sm font-semibold uppercase tracking-[0.24em] text-emerald-200/80">AI-Hub Morda</p>
                            <h1 className="mt-3 text-balance text-3xl font-black tracking-tight text-neutral-50 sm:text-5xl">Czysty chat z pamięcią, narzędziami i realnym backendem.</h1>
                            <p className="mx-auto mt-4 max-w-xl text-pretty text-base leading-7 text-neutral-400 sm:text-lg">Streaming, historia sesji, upload plików, dyktowanie i Memory V2 są podpięte przez BFF do AI-Hub. Wygląd jak nowoczesny GPT/Grok, bez pływających absurdów.</p>
                        </div>
                        <div className="mx-auto mt-8 grid w-full max-w-3xl gap-3 sm:grid-cols-3">{SUGGESTIONS.map((s) => <button key={s} type="button" onClick={() => onSuggestion?.(s)} className="rounded-3xl border border-white/10 bg-white/[0.045] p-4 text-left text-sm leading-6 text-neutral-300 shadow-xl shadow-black/15 transition hover:-translate-y-0.5 hover:border-emerald-300/25 hover:bg-emerald-300/10 hover:text-neutral-100"><Sparkles className="mb-3 h-4 w-4 text-emerald-200" />{s}</button>)}</div>
                    </div>
                ) : <div className="pb-8 pt-2">{messages.map((m) => <UserMessageItem key={m.id} message={m} />)}</div>}
                {loading && messages.length === 0 ? <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-6 text-sm text-neutral-500 sm:px-6"><MessageSquareText className="h-4 w-4 animate-pulse" />Trwa przygotowanie odpowiedzi…</div> : null}
            </div>
        </div>
    );
}
